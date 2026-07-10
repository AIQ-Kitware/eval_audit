"""HuggingFace-side probe: reproduce the official at a chosen dtype and score it
against the same oracle the vLLM sweep uses.

Why this exists: vLLM can't serve fp32 on a MoE (the Triton fused-kernel
shared-memory OOM — see docs/vllm-vs-huggingface-deployment-match.md), and the
official OLMoE run was a HuggingFaceClient fp32 run *anyway*. So the faithful
matched-precision comparison is HF-side, not vLLM. This module loads the model
once per ``--dtype`` (default float32, a comma list sweeps them — matching HELM's HuggingFaceClient default on
transformers<5), reconstructs HELM's ``get_prompt`` (chat template +
add_generation_prompt + add_special_tokens, exactly as compare_prompt.py does),
greedily generates each sampled instance, and emits the **same result-doc shape**
as :mod:`probe` so the existing :mod:`score` / :mod:`report` consume it unchanged.

The generation path needs ``transformers`` + ``torch`` + the weights (a GPU host);
the doc-assembly + scoring path is stdlib and unit-testable with injected stubs
(``probe_variant`` takes ``render_fn`` / ``generate_fn``).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

DTYPE_TAG = {"auto": "auto", "float16": "fp16", "bfloat16": "bf16", "float32": "fp32"}


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _slug(text: str) -> str:
    out = "".join(c if c.isalnum() else "-" for c in str(text).lower())
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-") or "x"


# --------------------------------------------------------------------------- #
# Pure prompt reconstruction — mirrors HELM's HuggingFaceClient.get_prompt and
# compare_prompt.py. A chat model is fed apply_chat_template(add_generation_prompt);
# a base/completions model gets the raw prompt verbatim.
def render_prompt(tok: Any, raw_prompt: str, *, apply_chat_template: bool,
                  add_generation_prompt: bool) -> str:
    if apply_chat_template:
        return tok.apply_chat_template(
            [{"role": "user", "content": raw_prompt}],
            tokenize=False, add_generation_prompt=add_generation_prompt)
    return raw_prompt


# --------------------------------------------------------------------------- #
# Doc assembly — pure given a render_fn (raw -> rendered str) and a generate_fn
# (rendered, add_special_tokens -> result dict). Same schema as probe.query_cell.
def probe_variant(sample: list[dict[str, Any]], *, cell_id: str, endpoint: str,
                  request: dict[str, Any], render_fn: Callable[[str], str],
                  generate_fn: Callable[[str, bool], dict[str, Any]],
                  progress: bool = True) -> dict[str, Any]:
    ast = bool(request.get("add_special_tokens", True))
    results = []
    for s in sample:
        rendered = render_fn(s["prompt"])
        row = generate_fn(rendered, ast)
        row["instance_id"] = s["instance_id"]
        results.append(row)
        if progress:
            snip = (row.get("completion") or row.get("error") or "")[:60].replace("\n", "\\n")
            _log(f"    {s['instance_id']:<20} {snip}")
    return {"cell_id": cell_id, "endpoint": endpoint, "request": request, "results": results}


# --------------------------------------------------------------------------- #
# transformers-backed generation (needs torch + the weights).
_TORCH_DTYPES = {
    "float32": "float32", "fp32": "float32",
    "float16": "float16", "fp16": "float16",
    "bfloat16": "bfloat16", "bf16": "bfloat16",
    "auto": "auto",
}


def _resolve_device_map(device_map: str):
    """Map the friendly ``single`` alias onto a whole-model single-GPU placement.

    ``device_map="auto"`` lets accelerate SHARD a model across every visible GPU
    when it doesn't fit on one. Sharding changes the fp32 reduction/accumulation
    order at the layer boundaries, which shifts greedy logits enough to flip an
    early near-tie token — the exact first-token divergence the OLMo-2 probe hits
    (the model produced the official on one GPU; a sharded reload doesn't match).
    ``single`` (== ``{"": 0}``) forces the whole model onto GPU 0 so the forward
    pass is single-device, matching how a fits-on-one-GPU official was served."""
    if str(device_map) in ("single", "one-gpu", "cuda:0", "0"):
        return {"": 0}
    return device_map


def load_model_and_tokenizer(hf_source: str, tokenizer_repo: str, *, dtype: str,
                             device_map: str = "auto", attn_implementation: str | None = None,
                             trust_remote_code: bool = False):
    """Load exactly the way HELM's HuggingFaceClient does (device_map=auto), but
    pin the dtype explicitly so fp32 is fp32 regardless of the transformers major
    version (pre-v5 defaults to fp32 anyway; v5 would otherwise read the bf16
    config). ``attn_implementation`` (eager/sdpa/flash_attention_2) and
    ``device_map`` are the two forward-pass-numerics knobs the sweep varies to
    find the one that reproduces the official's greedy logits — HELM/transformers
    leaves both at the (version-dependent) default, so the recipe that produced a
    public run is not recoverable from run_spec.json and must be searched. Returns
    (model, tokenizer, device)."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    norm = _TORCH_DTYPES.get(dtype.lower())
    if norm is None:
        raise SystemExit(f"unknown --dtype {dtype!r}; use float32/float16/bfloat16/auto")
    torch_dtype = "auto" if norm == "auto" else getattr(torch, norm)

    tok = AutoTokenizer.from_pretrained(tokenizer_repo, trust_remote_code=trust_remote_code)
    load_kwargs: dict[str, Any] = {"torch_dtype": torch_dtype,
                                   "device_map": _resolve_device_map(device_map),
                                   "trust_remote_code": trust_remote_code}
    if attn_implementation:
        load_kwargs["attn_implementation"] = attn_implementation
    model = AutoModelForCausalLM.from_pretrained(hf_source, **load_kwargs)
    model.eval()
    device = next(model.parameters()).device
    return model, tok, device


def make_generate_fn(model: Any, tok: Any, device: Any, *, max_new_tokens: int,
                     stop: list[str] | None, decode: str = "helm",
                     temperature: float = 0.0, top_p: float | None = None,
                     ) -> Callable[[str, bool], dict[str, Any]]:
    """Decode only the new tokens. ``decode`` selects the generation path:

    * ``helm`` (default) — replicate HELM's HuggingFaceServer.serve_request EXACTLY:
      ``do_sample=True`` with ``temperature`` mapped ``0 -> 1e-7`` and ``top_p`` from
      the recipe. HELM never does true greedy; it samples at a near-zero temperature
      (argmax-equivalent in expectation but a DIFFERENT transformers code path than
      greedy — different logits-warper stack, so it must be matched to reproduce a
      HuggingFaceClient official).
    * ``greedy`` — ``do_sample=False`` argmax (the old probe behavior; kept as a
      diagnostic axis, not the default).

    Returns a generate_fn(rendered, add_special_tokens) -> probe-shaped result dict."""
    import torch

    # HELM maps temperature==0 -> 1e-7 (helm mode) so `generate` stays on the sample
    # path with an argmax-peaked distribution; greedy mode ignores temperature.
    helm = decode == "helm"
    temp = 1e-7 if (temperature or 0.0) == 0 else float(temperature)

    def _gen(rendered: str, add_special_tokens: bool) -> dict[str, Any]:
        t0 = time.time()
        try:
            enc = tok(rendered, add_special_tokens=add_special_tokens, return_tensors="pt")
            input_ids = enc["input_ids"].to(device)
            attn = enc.get("attention_mask")
            attn = attn.to(device) if attn is not None else None
            gen_kwargs: dict[str, Any] = {"max_new_tokens": max_new_tokens}
            if helm:
                gen_kwargs.update(do_sample=True, temperature=temp, num_return_sequences=1)
                if top_p is not None:
                    gen_kwargs["top_p"] = top_p
            else:
                gen_kwargs.update(do_sample=False, num_beams=1)
            if tok.eos_token_id is not None:
                gen_kwargs["pad_token_id"] = tok.eos_token_id
            with torch.no_grad():
                out = model.generate(input_ids, attention_mask=attn, **gen_kwargs)
            new = out[0][input_ids.shape[1]:]
            text = tok.decode(new, skip_special_tokens=True)
            finish = "length"
            if tok.eos_token_id is not None and int(tok.eos_token_id) in new.tolist():
                finish = "stop"
            for s in stop or []:                      # HELM applies stop sequences
                cut = text.find(s)
                if cut != -1:
                    text, finish = text[:cut], "stop"
            first = (text.strip().split(" ")[:1] or [""])[0]
            return {"completion": text, "finish_reason": finish, "first_token": first,
                    "n_tokens": int(new.shape[0]), "latency_s": round(time.time() - t0, 3),
                    "error": None}
        except Exception as exc:  # noqa: BLE001
            return {"completion": None, "finish_reason": None, "first_token": None,
                    "n_tokens": None, "latency_s": round(time.time() - t0, 3),
                    "error": f"{type(exc).__name__}: {exc}"}

    return _gen


# --------------------------------------------------------------------------- #
def build_request_variants(protocol: str, *, agp: str, ast: str) -> list[dict[str, Any]]:
    """The request-time variants to probe. add_generation_prompt only bites on the
    chat path; for completions it collapses to a single (unset) variant."""
    def _bools(opt: str) -> list[bool]:
        return [True, False] if opt == "both" else [opt == "true"]

    variants: list[dict[str, Any]] = []
    for a in _bools(ast):
        if protocol == "chat":
            for g in _bools(agp):
                variants.append({"add_special_tokens": a, "protocol": protocol,
                                 "add_generation_prompt": g})
        else:
            variants.append({"add_special_tokens": a, "protocol": protocol,
                             "add_generation_prompt": None})
    return variants


def _variant_name(req: dict[str, Any]) -> str:
    name = f"ast{'1' if req['add_special_tokens'] else '0'}-{req['protocol']}"
    if req.get("add_generation_prompt") is not None:
        name += f"-agp{'1' if req['add_generation_prompt'] else '0'}"
    return name


def _hf_endpoint(model_short: str, dtype: str, attn: str | None, device_map: str) -> str:
    """Endpoint name encoding the reload axes (dtype x attn_impl x device_map) so
    cells from different serve-recipes never collide. Mirrors the vLLM grid's
    ``-attn<backend>`` naming; the default (attn unset, device_map=auto) keeps the
    plain ``hf-<model>-<dtype>`` name unchanged."""
    ep = f"hf-{model_short}-{DTYPE_TAG.get(dtype, dtype)}"
    if attn:
        ep += f"-attn{_slug(attn)}"
    if device_map and str(device_map) != "auto":
        ep += f"-dev{_slug(str(device_map))}"
    return ep


def _cell_serve(resolution: Any, dtype: str, *, attn: str | None = None,
                device_map: str = "auto") -> dict[str, Any]:
    """A serve block mirroring the grid's, so report.best_deployment renders."""
    extra = ["--dtype", dtype]
    if attn:
        extra += ["--attn-impl", attn]
    if str(device_map) != "auto":
        extra += ["--device-map", str(device_map)]
    extra.append("(huggingface transformers.generate)")
    return {"engine": "huggingface", "dtype": dtype, "hf_source": resolution.hf_source,
            "tokenizer": None, "max_model_len": resolution.official_max_sequence_length,
            "trust_remote_code": False, "attention_backend": attn,
            "device_map": device_map,
            "tensor_parallel_size": 1, "max_num_seqs": 1,
            "gpu_memory_utilization": None, "max_num_batched_tokens": None,
            "extra_args": extra}


def plan_cells(model_short: str, dtypes: list[str],
               variants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """(pure) Enumerate the sweep: one cell per (dtype x request-variant), with a
    dtype-tagged endpoint so cells from different dtypes never collide. Testable
    without loading a model."""
    plan: list[dict[str, Any]] = []
    for dtype in dtypes:
        endpoint = f"hf-{model_short}-{DTYPE_TAG.get(dtype, dtype)}"
        for req in variants:
            plan.append({"dtype": dtype, "endpoint": endpoint,
                         "cell_id": f"{endpoint}::{_variant_name(req)}", "request": req})
    return plan


def run_hf_probe(orc: Any, resolution: Any, out_dir: Path, *, dtypes: list[str],
                 agp: str, ast: str, device_map: str, trust_remote_code: bool,
                 attn_impls: list[str | None] | None = None,
                 device_maps: list[str] | None = None,
                 decodes: list[str] | None = None,
                 progress: bool = True) -> list[dict[str, Any]]:
    """Sweep (dtype x attn_impl x device_map) reload-recipes x (decode x request-
    variant) cheap-variants: load the model once per reload-recipe, probe every
    cheap variant, write cells.json + results/*.json. Returns all cell docs.

    dtype/attn_impl/device_map each change the fp32 FORWARD PASS, so each distinct
    triple is a separate full model load (unlike vLLM, where these are cheap serve
    args) and runs sequentially with the previous model freed first. These are the
    knobs that move the greedy logits: sweeping them is how the HF probe finds the
    recipe that reproduces a HuggingFaceClient official whose exact serving numerics
    aren't recorded in run_spec.json (the OLMo-2 first-token divergence). decode
    (helm vs greedy) is a cheap per-generate axis — no reload."""
    import gc
    try:
        import torch
    except Exception:  # noqa: BLE001
        torch = None

    out_dir = Path(out_dir)
    results_dir = out_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    protocol = resolution.protocol or "chat"
    variants = build_request_variants(protocol, agp=agp, ast=ast)
    model_short = _slug((resolution.model or resolution.hf_source or "model").split("/")[-1])
    tokenizer_repo = resolution.official_tokenizer or resolution.hf_source
    recipe = orc.recipe or {}
    max_new = int(recipe.get("max_tokens") or 512)
    stop = recipe.get("stop_sequences") or []
    temperature = float(recipe.get("temperature") or 0.0)
    top_p = recipe.get("top_p")
    sample = [{"instance_id": s.instance_id, "prompt": s.prompt} for s in orc.sample]

    attn_list: list[str | None] = list(attn_impls) if attn_impls else [None]
    dev_list: list[str] = list(device_maps) if device_maps else [device_map]
    decode_list: list[str] = list(decodes) if decodes else ["helm"]

    cell_docs: list[dict[str, Any]] = []
    cells_index: list[dict[str, Any]] = []
    for dtype in dtypes:
        for attn in attn_list:
            for dev in dev_list:
                endpoint = _hf_endpoint(model_short, dtype, attn, dev)
                _log(f"[hf-probe] loading {resolution.hf_source} (dtype={dtype}, "
                     f"attn={attn or 'default'}, device_map={dev}) tokenizer={tokenizer_repo} …")
                try:
                    model, tok, device = load_model_and_tokenizer(
                        resolution.hf_source, tokenizer_repo, dtype=dtype, device_map=dev,
                        attn_implementation=attn, trust_remote_code=trust_remote_code)
                except Exception as exc:  # noqa: BLE001
                    # A recipe can be infeasible (flash-attn on fp32, OOM on single-GPU).
                    # Skip it and keep sweeping — don't abort the whole run.
                    _log(f"[hf-probe] LOAD FAILED for {endpoint} "
                         f"({type(exc).__name__}: {exc}); skipping this recipe")
                    continue
                _log(f"[hf-probe] loaded on {device}; model dtype={next(model.parameters()).dtype}")
                has_template = bool(getattr(tok, "chat_template", None))
                serve = _cell_serve(resolution, dtype, attn=attn, device_map=dev)

                for decode in decode_list:
                    generate_fn = make_generate_fn(
                        model, tok, device, max_new_tokens=max_new, stop=stop,
                        decode=decode, temperature=temperature, top_p=top_p)
                    for req in variants:
                        apply_ct = has_template and req["protocol"] == "chat"
                        agp_val = bool(req.get("add_generation_prompt")) if req.get("add_generation_prompt") is not None else True
                        cell_id = f"{endpoint}::{_variant_name(req)}-{decode}"
                        req_out = {**req, "decode": decode}
                        _log(f"[hf-probe] {dtype}/{attn or 'default'}/{dev}/{decode} / {_variant_name(req)} "
                             f"(apply_chat_template={apply_ct}, add_generation_prompt={agp_val}, "
                             f"add_special_tokens={req['add_special_tokens']})")

                        def render_fn(raw: str, _ct=apply_ct, _g=agp_val) -> str:
                            return render_prompt(tok, raw, apply_chat_template=_ct, add_generation_prompt=_g)

                        doc = probe_variant(sample, cell_id=cell_id, endpoint=endpoint, request=req_out,
                                            render_fn=render_fn, generate_fn=generate_fn, progress=progress)
                        (results_dir / f"{cell_id.replace('::', '__')}.json").write_text(json.dumps(doc, indent=2))
                        cell_docs.append(doc)
                        cells_index.append({"cell_id": cell_id, "endpoint": endpoint,
                                            "serve": serve, "request": req_out})

                # Free this recipe's model before the next full load (don't hold two at once).
                del model, tok, generate_fn
                gc.collect()
                if torch is not None and getattr(torch, "cuda", None) is not None and torch.cuda.is_available():
                    torch.cuda.empty_cache()

    (out_dir / "cells.json").write_text(json.dumps(cells_index, indent=2))
    return cell_docs

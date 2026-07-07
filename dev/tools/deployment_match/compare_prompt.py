#!/usr/bin/env python3
"""Compare the prompt vLLM tokenizes for ONE grid entry against HELM's get_prompt.

The uniform-miss failure mode (OLMoE-instruct / ifeval) is a *prompt* mismatch,
not a serving-knob one: HELM's HuggingFaceClient feeds a chat model

    apply_chat_template([{role: user, content: request.prompt}],
                        add_generation_prompt=True)

tokenized with ``add_special_tokens=True`` (see
submodules/helm/.../huggingface_client.py get_prompt / serve_request), while
scenario_state stores only the RAW request.prompt. This script reconstructs the
exact HELM token-id sequence locally (transformers) for one grid cell — using
that cell's tokenizer override + protocol + add_special_tokens — and, when
pointed at a live vLLM ``/tokenize`` endpoint, diffs against what vLLM actually
feeds. A divergence here is a localizable cause no dtype/backend knob can fix.

Pick the cell either from a grid dir (``--grid-dir <dir> --cell <cell_id>``, reads
cells.json + oracle.json) or manually (``--oracle/--run`` + ``--tokenizer`` +
``--protocol`` + ``--add-special-tokens``). Needs ``transformers`` for the HELM
side; the live check needs a reachable vLLM ``/tokenize`` URL (talk to the vLLM
container directly — LiteLLM does not proxy /tokenize).

Example (local reconstruction only, no GPU):
    .venv/bin/python dev/tools/deployment_match/compare_prompt.py \
        --grid-dir /tmp/dm-olmoe --cell 'dm-olmoe-...-bf16::ast1-chat'

Live check against a directly-reachable vLLM container:
    ... --tokenize-url http://localhost:8000/tokenize --served-model <name>
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import oracle as oracle_mod      # noqa: E402
import registry as registry_mod  # noqa: E402


def first_divergence(a: list[Any], b: list[Any]) -> int | None:
    """Index of the first differing element (or the shorter length if one is a
    prefix of the other); None if the sequences are identical. Pure/testable."""
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return None if len(a) == len(b) else min(len(a), len(b))


def _load_prompt(args) -> tuple[str, str, str]:
    """Return (prompt, helm_model, model_deployment) from --grid-dir/--oracle/--run/--prompt."""
    if args.prompt is not None:
        return args.prompt, args.model or "", args.deployment or ""
    orc_path = None
    if args.grid_dir:
        orc_path = Path(args.grid_dir) / "oracle.json"
    elif args.oracle:
        orc_path = Path(args.oracle)
    if orc_path and orc_path.exists():
        orc = oracle_mod.Oracle.from_json(json.loads(orc_path.read_text()))
    elif args.run:
        orc = oracle_mod.load_oracle(args.run, n=max(args.instance + 1, 1))
    else:
        raise SystemExit("need one of --grid-dir / --oracle / --run / --prompt")
    sample = orc.sample
    if args.instance_id:
        picked = next((s for s in sample if s.instance_id == args.instance_id), None)
        if picked is None:
            raise SystemExit(f"instance_id {args.instance_id!r} not in the sample")
    else:
        if args.instance >= len(sample):
            raise SystemExit(f"--instance {args.instance} out of range ({len(sample)} sampled)")
        picked = sample[args.instance]
    return picked.prompt, orc.model, orc.model_deployment


def _load_cell(args) -> dict[str, Any]:
    """Read one cell's {serve, request} from a grid dir, or {} for manual mode."""
    if not (args.grid_dir and args.cell):
        return {}
    cells = json.loads((Path(args.grid_dir) / "cells.json").read_text())
    for c in cells:
        if c["cell_id"] == args.cell:
            return c
    raise SystemExit(f"cell {args.cell!r} not in {args.grid_dir}/cells.json")


def _resolve_tokenizer(args, cell: dict[str, Any], helm_model: str, deployment: str) -> str:
    if args.tokenizer:
        return args.tokenizer
    tok = (cell.get("serve") or {}).get("tokenizer")   # serve-recipe override (e.g. sibling)
    if tok:
        return tok
    res = registry_mod.resolve(helm_model, deployment)
    return res.hf_source or helm_model


def _vllm_tokenize(url: str, body: dict[str, Any], api_key: str | None) -> list[int]:
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return list(json.loads(resp.read().decode("utf-8")).get("tokens") or [])


def _fmt_ids(tok, ids: list[int], head: int = 12) -> str:
    shown = ids[:head]
    decoded = [repr(tok.decode([i])) for i in shown]
    tail = " …" if len(ids) > head else ""
    return f"[{len(ids)} toks] {' '.join(decoded)}{tail}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--grid-dir", help="grid dir (reads oracle.json + cells.json)")
    ap.add_argument("--cell", help="cell_id in <grid-dir>/cells.json to mirror")
    ap.add_argument("--oracle", help="oracle.json (if not using --grid-dir)")
    ap.add_argument("--run", help="HELM run dir (if no oracle.json)")
    ap.add_argument("--prompt", help="raw prompt text (bypass oracle)")
    ap.add_argument("--model", help="HELM model (with --prompt)")
    ap.add_argument("--deployment", help="model_deployment (with --prompt)")
    ap.add_argument("--instance", type=int, default=0, help="sample index (default 0)")
    ap.add_argument("--instance-id", help="pick the sample by instance id")
    ap.add_argument("--tokenizer", help="tokenizer repo override (default: cell/model)")
    ap.add_argument("--protocol", choices=["chat", "completions"],
                    help="override the cell protocol (chat => apply chat template)")
    ap.add_argument("--add-special-tokens", choices=["true", "false"],
                    help="override the cell add_special_tokens for the live vLLM query")
    ap.add_argument("--no-chat-template", action="store_true",
                    help="force apply_chat_template=False (base model)")
    ap.add_argument("--tokenize-url", help="live vLLM /tokenize URL (direct to the container)")
    ap.add_argument("--served-model", help="served model name for the /tokenize body")
    ap.add_argument("--api-key", default=None)
    args = ap.parse_args(argv)

    try:
        from transformers import AutoTokenizer
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"transformers required for the HELM side: {exc}")

    prompt, helm_model, deployment = _load_prompt(args)
    cell = _load_cell(args)
    request = cell.get("request") or {}
    protocol = args.protocol or request.get("protocol") or "chat"
    ast = (args.add_special_tokens == "true") if args.add_special_tokens is not None \
        else bool(request.get("add_special_tokens", True))
    tok_repo = _resolve_tokenizer(args, cell, helm_model, deployment)

    tok = AutoTokenizer.from_pretrained(tok_repo, trust_remote_code=True)
    # HELM's rule: apply_chat_template if the tokenizer has one (auto-inferred),
    # unless forced off. A completions-protocol cell means no template.
    has_template = bool(getattr(tok, "chat_template", None))
    apply_ct = has_template and protocol == "chat" and not args.no_chat_template

    print("=" * 72)
    print(f"cell           : {args.cell or '(manual)'}")
    print(f"prompt[:120]   : {prompt[:120]!r}{' …' if len(prompt) > 120 else ''}")
    print(f"tokenizer      : {tok_repo}  (has_chat_template={has_template})")
    print(f"protocol       : {protocol}   apply_chat_template={apply_ct}   "
          f"add_special_tokens={ast}")
    print("=" * 72)

    # ---- HELM side: what the official model was actually fed --------------------
    if apply_ct:
        rendered = tok.apply_chat_template(
            [{"role": "user", "content": prompt}], tokenize=False, add_generation_prompt=True)
    else:
        rendered = prompt
    helm_ids = tok(rendered, add_special_tokens=True).input_ids
    print("\n[HELM] get_prompt render (add_generation_prompt=True), tokenized "
          "add_special_tokens=True:")
    print(f"  rendered head : {rendered[:160]!r}")
    print(f"  ids           : {_fmt_ids(tok, helm_ids)}")
    bos = getattr(tok, "bos_token", None)
    if bos:
        print(f"  bos_token     : {bos!r}  rendered_starts_with_bos="
              f"{rendered.startswith(bos)}")

    # ---- Local vLLM-equivalent: same render, this cell's add_special_tokens ----
    cell_ids = tok(rendered, add_special_tokens=ast).input_ids
    idx = first_divergence(helm_ids, cell_ids)
    print(f"\n[local vLLM-equiv] same render, add_special_tokens={ast}:")
    print(f"  ids           : {_fmt_ids(tok, cell_ids)}")
    if idx is None:
        print("  vs HELM       : IDENTICAL ✓")
    else:
        print(f"  vs HELM       : DIVERGE at token {idx} "
              f"(HELM={helm_ids[idx] if idx < len(helm_ids) else '—'} "
              f"{tok.decode([helm_ids[idx]]) if idx < len(helm_ids) else ''!r} | "
              f"cell={cell_ids[idx] if idx < len(cell_ids) else '—'})")
        print("  → typically the leading BOS: HELM tokenizes the rendered string "
              "with add_special_tokens=True; a chat cell often sends False.")

    # ---- Live vLLM /tokenize (authoritative), if reachable ---------------------
    if args.tokenize_url:
        served = args.served_model or (cell.get("endpoint") if cell else None) or helm_model
        if protocol == "chat" and apply_ct:
            body = {"model": served, "messages": [{"role": "user", "content": prompt}],
                    "add_generation_prompt": True, "add_special_tokens": ast}
        else:
            body = {"model": served, "prompt": rendered, "add_special_tokens": ast}
        try:
            vllm_ids = _vllm_tokenize(args.tokenize_url, body, args.api_key)
        except Exception as exc:  # noqa: BLE001
            print(f"\n[live vLLM] /tokenize failed: {exc}")
            return 0
        vidx = first_divergence(helm_ids, vllm_ids)
        print(f"\n[live vLLM] {args.tokenize_url}  (served={served}):")
        print(f"  ids           : {_fmt_ids(tok, vllm_ids)}")
        if vidx is None:
            print("  vs HELM       : IDENTICAL ✓  — the served prompt matches HELM.")
        else:
            print(f"  vs HELM       : DIVERGE at token {vidx} — the served prompt does "
                  "NOT match HELM; this is the (or a) source of the difference.")
            lo, hi = max(0, vidx - 2), vidx + 3
            print(f"    HELM[{lo}:{hi}] : {[tok.decode([i]) for i in helm_ids[lo:hi]]}")
            print(f"    vLLM[{lo}:{hi}] : {[tok.decode([i]) for i in vllm_ids[lo:hi]]}")
    else:
        print("\n[live vLLM] skipped (pass --tokenize-url http://<vllm-host>:<port>/tokenize)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Generate the deployment grid for one resolved model.

Two tiers, split by cost (see the plan):

* **serve-time** knobs (``dtype``, ``tokenizer`` override, ``max_model_len``,
  ``trust_remote_code``) — each combination is a separate ``vllm serve`` /
  infer-stack endpoint. Rendered into a catalog with distinct served names so
  they don't coalesce (only ``runtime.extra_args`` reaches the command line, and
  it's excluded from the coalescing key, so the name is what keeps them
  separate).
* **request-time** knobs (``add_special_tokens``, ``protocol``) — varied per
  request against the *same* running endpoint (the gateway forwards them), so
  many of these run per container.

A *cell* is one (serve-recipe × request-variant); scoring ranks cells. Stdlib
only — the caller does YAML/JSON IO.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

DTYPE_TAG = {"auto": "auto", "float16": "fp16", "bfloat16": "bf16", "float32": "fp32"}

DEFAULT_AXES: dict[str, list[Any]] = {
    "dtype": ["auto", "float16", "bfloat16", "float32"],
    "tokenizer": ["default"],          # 'default' = the model's own; plus siblings if known
    "max_model_len": ["auto"],         # 'auto' = min(official max_seq_len + 1, model max_position_embeddings)
    "trust_remote_code": [False],
    "attention_backend": [None],       # None = vLLM default; else VLLM_ATTENTION_BACKEND (e.g. TORCH_SDPA)
    "add_special_tokens": [True, False],
    "add_generation_prompt": [None],   # chat-only; None = vLLM default (True). False reproduces
                                       # an old chat template that ignored add_generation_prompt.
    "protocol": ["auto"],              # 'auto' = resolved protocol
}

DEFAULT_RUNTIME: dict[str, Any] = {
    "gpu_memory_utilization": 0.85,
    "max_num_batched_tokens": 2048,
    "max_num_seqs": 16,
    "enforce_eager": True,
    # vLLM defaults — carried explicitly so a profile can flip them (see the
    # hf-match profile / extra_args). True = vLLM default; no flag is emitted.
    "enable_chunked_prefill": True,
    "enable_prefix_caching": True,
}

DEFAULT_CAP = 64

# Built-in grid profiles selectable with `--profile` (merged UNDER any --grid
# YAML, which overrides per-key). A profile is just a spec (axes/runtime/cap)
# carrying a known-good intent.
BUILTIN_PROFILES: dict[str, dict[str, Any]] = {
    # Match a HELM *HuggingFaceClient* run — one whose official completions came
    # from a local transformers.generate(). Pin the vLLM engine to its most
    # HF-like, deterministic execution so the sweep varies only the recipe HELM
    # itself could vary (dtype / tokenizer / add_special_tokens), not vLLM's
    # scheduler. See docs/vllm-vs-huggingface-deployment-match.md.
    "hf-match": {
        "runtime": {
            "enforce_eager": True,            # no CUDA-graph capture
            "enable_chunked_prefill": False,  # single-pass prefill, like HF
            "enable_prefix_caching": False,   # no cross-request KV reuse
            "max_num_seqs": 1,                # serialize: vLLM kernels aren't batch-invariant
        },
        # The determinism knobs above are confounder-removal (HF has no
        # CUDA-graphs / chunked-prefill / prefix-cache / batching), so they are
        # pinned. The attention backend, by contrast, is NOT known on theory —
        # matching HF's numerics is empirical (a backend NAME match doesn't imply
        # a kernel-numeric match, and HF's own default is version/model-dependent)
        # — so SWEEP it and let the scorer decide. None = vLLM's own default
        # (usually FlashAttention) as the baseline. A backend that can't serve on
        # this GPU just scores NO_DATA and drops out. Each value is a separate
        # `vllm serve`, so this multiplies the (expensive) endpoint count.
        #
        # Also sweep add_generation_prompt (chat-only, cheap request-time knob):
        # HELM's older transformers shipped OLMoE chat templates that ignored it
        # (effectively False), while modern vLLM appends the assistant generation
        # prompt (True). A per-model/version quirk — let the scorer pick the match.
        # NB protocol is NOT pinned. HELM's HuggingFaceClient applies the
        # tokenizer's chat template when the model has one (auto-inferred; OLMoE
        # -instruct -> True), so the official model saw a CHAT-templated prompt
        # while scenario_state stored the raw request.prompt. The per-model
        # resolution already picks chat for such instruct models (vLLM re-applies
        # the same template) and completions for base models — sending the raw
        # prompt verbatim would be wrong for a chat model. See
        # docs/vllm-vs-huggingface-deployment-match.md.
        "axes": {"attention_backend": [None, "FLASH_ATTN", "XFORMERS", "TORCH_SDPA"],
                 "add_generation_prompt": [True, False]},
        # 4 backends x 4 dtype (x tokenizer variants) can exceed the default 64
        # cap; raise it so no backend is silently truncated.
        "cap": 128,
    },
}


def _slug(text: str) -> str:
    out = "".join(c if c.isalnum() else "-" for c in str(text).lower())
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-") or "x"


def _tok_tag(tokenizer_repo: str) -> str:
    # e.g. allenai/OLMo-1.7-7B-hf -> "olmo-1-7-7b-hf" -> shorten to a stable tail
    base = _slug(tokenizer_repo.split("/")[-1])
    return base.replace("olmo-", "").replace("-hf", "") or base


@dataclass
class ServeRecipe:
    name: str
    hf_source: str
    dtype: str
    tokenizer: str | None            # None = model default
    max_model_len: int
    trust_remote_code: bool
    runtime: dict[str, Any]
    attention_backend: str | None = None   # None = vLLM default; else VLLM_ATTENTION_BACKEND
    extra_serve_args: list[str] = field(default_factory=list)  # appended verbatim to `vllm serve`

    def extra_args(self) -> list[str]:
        args = ["--dtype", self.dtype]
        if self.tokenizer:
            args += ["--tokenizer", self.tokenizer]
        if self.trust_remote_code:
            args += ["--trust-remote-code"]
        if self.runtime.get("enforce_eager", True):
            args += ["--enforce-eager"]
        # HF-match determinism: vLLM defaults these ON; disabling them removes two
        # sources of vLLM<->HF numeric drift — chunked prefill splits the prefill
        # across steps (HF does it in one pass) and prefix caching reuses KV across
        # requests. Only emit the negating flag when explicitly disabled, so the
        # default grid keeps vLLM's own defaults untouched.
        if not self.runtime.get("enable_chunked_prefill", True):
            args += ["--no-enable-chunked-prefill"]
        if not self.runtime.get("enable_prefix_caching", True):
            args += ["--no-enable-prefix-caching"]
        # Debug/passthrough args appended verbatim (e.g. --enable-log-requests to
        # dump each request's post-chat-template prompt into the container logs).
        args += list(self.extra_serve_args)
        return args

    @property
    def effective_max_num_batched_tokens(self) -> int:
        """vLLM requires ``max_num_batched_tokens >= max_model_len`` when chunked
        prefill is OFF (hf-match disables it) — else it refuses to start with
        "max_num_batched_tokens (N) is smaller than max_model_len (M)". Raise it
        to the context window in that case; with chunked prefill on, vLLM allows
        the smaller value, so leave it untouched (default grid unchanged)."""
        mnbt = int(self.runtime["max_num_batched_tokens"])
        if not self.runtime.get("enable_chunked_prefill", True):
            return max(mnbt, int(self.max_model_len))
        return mnbt

    def endpoint_dict(self, protocol: str) -> dict[str, Any]:
        rt = {
            "max_model_len": self.max_model_len,
            "gpu_memory_utilization": self.runtime["gpu_memory_utilization"],
            "max_num_batched_tokens": self.effective_max_num_batched_tokens,
            "max_num_seqs": self.runtime["max_num_seqs"],
            "extra_args": self.extra_args(),
        }
        if self.trust_remote_code:
            rt["trust_remote_code"] = True          # structural (compat-key) only
        if self.attention_backend:
            # Delivered as the VLLM_ATTENTION_BACKEND env var by infer-stack's
            # backend renderer; structural (compat-key) so backends don't coalesce.
            rt["attention_backend"] = self.attention_backend
        return {"engine": "vllm", "reclaim": "stop", "model": "target",
                "protocol": protocol, "runtime": rt}


@dataclass
class RequestVariant:
    name: str
    add_special_tokens: bool
    protocol: str
    add_generation_prompt: bool | None = None   # chat-only; None = don't send (vLLM default)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Cell:
    cell_id: str
    endpoint: str            # serve recipe name (== catalog endpoint == served name)
    serve: dict[str, Any]
    request: dict[str, Any]


@dataclass
class Grid:
    model: str
    hf_source: str | None
    serve_recipes: list[ServeRecipe]
    request_variants: list[RequestVariant]
    cells: list[Cell]
    capped: int = 0                       # cells dropped by the cap (0 = none)
    # Serve-recipes excluded a priori by the preflight feasibility filter, each
    # {axis, value, reason, n_recipes} — a can't-serve-here filter, not a
    # relevance guess (see build_grid).
    pruned: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_catalog(self) -> dict[str, Any]:
        """An infer-stack catalog: one model + one endpoint per serve-recipe.

        The endpoint's ``protocol`` is set from the FIRST request-variant that
        uses that endpoint (the readiness probe needs a protocol); per-request
        protocol is still applied by the probe body.
        """
        endpoints: dict[str, Any] = {}
        for sr in self.serve_recipes:
            proto = next((rv.protocol for rv in self.request_variants), "completions")
            endpoints[sr.name] = sr.endpoint_dict(proto)
        return {
            "models": {"target": {"source": f"hf://{self.hf_source}"}},
            "endpoints": endpoints,
            "bundles": {"deployment-match": [sr.name for sr in self.serve_recipes]},
        }

    def to_json(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "hf_source": self.hf_source,
            "serve_recipes": [asdict(s) for s in self.serve_recipes],
            "request_variants": [rv.to_json() for rv in self.request_variants],
            "cells": [asdict(c) for c in self.cells],
            "capped": self.capped,
            "pruned": self.pruned,
            "notes": self.notes,
        }


def _merge_axes(overrides: dict[str, Any] | None) -> dict[str, list[Any]]:
    axes = {k: list(v) for k, v in DEFAULT_AXES.items()}
    for k, v in ((overrides or {}).get("axes") or {}).items():
        axes[k] = list(v) if isinstance(v, (list, tuple)) else [v]
    return axes


def _dtype_infeasible(dtype: Any, resolution: Any, *, allow_moe_fp32: bool) -> str | None:
    """Preflight FEASIBILITY filter: a reason string if this dtype cannot serve on
    a typical GPU (so the sweep shouldn't burn a serve cycle discovering it),
    else None. Feasibility only — never a relevance / "unlikely to matter" guess
    (the tool sweeps those). Extend with more rules (VRAM OOM, backend-not-in-image)
    as the needed facts become available.
    """
    if (str(dtype).lower() in ("float32", "fp32")
            and getattr(resolution, "is_moe", None) and not allow_moe_fp32):
        # fp32 doubles the MoE Triton fused-kernel's shared-memory tiles past most
        # GPUs' ~99 KiB/SM cap ("triton ... out of resource: shared memory"); fits
        # on big-shared-mem cards (H100 228 KiB) -> allow_moe_fp32 to keep it.
        return "infeasible:moe-fp32-shared-mem"
    return None


def build_grid(resolution: Any, *, spec: dict[str, Any] | None = None) -> Grid:
    """Build the grid from a :class:`registry.Resolution` and an optional spec."""
    axes = _merge_axes(spec)
    runtime = {**DEFAULT_RUNTIME, **((spec or {}).get("runtime") or {})}
    cap = int((spec or {}).get("cap", DEFAULT_CAP))
    allow_moe_fp32 = bool((spec or {}).get("allow_moe_fp32", False))
    notes: list[str] = []
    pruned: list[dict[str, Any]] = []

    # Extra `vllm serve` args appended to every endpoint (passthrough). The
    # log_requests convenience adds vLLM request logging so each request's
    # post-chat-template prompt + sampling params land in the container logs.
    extra_serve_args = list((spec or {}).get("extra_serve_args") or [])
    if (spec or {}).get("log_requests"):
        extra_serve_args += ["--enable-log-requests", "--max-log-len", "100000"]
        notes.append("request logging on (--enable-log-requests): each request's "
                     "rendered prompt + params appear in the vLLM container logs")

    if not runtime.get("enable_chunked_prefill", True) or \
            not runtime.get("enable_prefix_caching", True):
        notes.append("hf-match determinism active: chunked-prefill / prefix-caching "
                     "disabled and batching serialized (max_num_seqs="
                     f"{runtime.get('max_num_seqs')}) to reduce vLLM<->HF drift")

    hf_source = resolution.hf_source
    model_short = _slug((resolution.model or hf_source or "model").split("/")[-1])

    # Resolve 'auto' placeholders. HELM's max_sequence_length convention is
    # inconsistent (together/olmo-7b: 2047 = window-1; huggingface/olmoe: 4096 =
    # the full window), so take official+1 but clamp to the model's own
    # max_position_embeddings — vLLM refuses to start above the derived ceiling
    # ("User-specified max_model_len is greater than the derived max_model_len").
    # Without a cached config.json the ceiling is unknown: fall back to the
    # official value verbatim (never overshoot; a refused server scores nothing).
    official_msl = resolution.official_max_sequence_length
    model_ceiling = getattr(resolution, "hf_max_position_embeddings", None)
    if official_msl and model_ceiling:
        default_mml = min(official_msl + 1, model_ceiling)
    elif official_msl:
        default_mml = official_msl
        notes.append(
            "max_model_len defaults to the official max_sequence_length verbatim "
            f"({official_msl}): no cached config.json to clamp official+1 against"
        )
    else:
        default_mml = model_ceiling or 2048
    tok_values: list[str | None] = []
    for t in axes["tokenizer"]:
        if t in ("default", None):
            tok_values.append(None)
        else:
            tok_values.append(str(t))
    # Auto-add the known sibling tokenizer as a candidate when the model's own
    # tokenizer injects a special token (the OLMo fix, generalized).
    if resolution.tokenizer_sibling and resolution.tokenizer_sibling not in tok_values:
        if resolution.tokenizer_appends_special:
            tok_values.append(resolution.tokenizer_sibling)
            notes.append(f"added sibling tokenizer candidate: {resolution.tokenizer_sibling}")

    mml_values = [default_mml if m in ("auto", None) else int(m) for m in axes["max_model_len"]]
    proto_values = [resolution.protocol if p in ("auto", None) else str(p) for p in axes["protocol"]]
    # None / 'auto' / 'default' => leave vLLM's own backend; else the env value.
    attn_values = [None if a in (None, "auto", "default", "none", "") else str(a)
                   for a in axes["attention_backend"]]

    # vLLM needs max_num_batched_tokens >= max_model_len when chunked prefill is
    # off (hf-match); ServeRecipe raises it per-recipe — note when that happens.
    base_mnbt = int(runtime["max_num_batched_tokens"])
    if not runtime.get("enable_chunked_prefill", True) and base_mnbt < max(mml_values):
        notes.append(
            f"max_num_batched_tokens raised {base_mnbt} -> max_model_len "
            f"(up to {max(mml_values)}): vLLM requires it >= max_model_len when "
            "chunked prefill is disabled")

    # ---- Tier A: serve recipes (dtype x tokenizer x max_model_len x trc x attn) ----
    serve: list[ServeRecipe] = []
    seen: set[str] = set()
    for dtype in axes["dtype"]:
        # Preflight feasibility filter: drop whole dtype sub-grids that can't serve
        # here (with a typed reason), rather than waste a serve cycle per cell.
        reason = _dtype_infeasible(dtype, resolution, allow_moe_fp32=allow_moe_fp32)
        if reason:
            n = len(tok_values) * len(mml_values) * len(axes["trust_remote_code"]) * len(attn_values)
            pruned.append({"axis": "dtype", "value": str(dtype), "reason": reason,
                           "n_recipes": n})
            notes.append(f"preflight: excluded dtype={dtype} [{reason}] "
                         f"({n} serve-recipe(s); pass allow_moe_fp32 to keep)")
            continue
        for tok in tok_values:
            for mml in mml_values:
                for trc in axes["trust_remote_code"]:
                    for attn in attn_values:
                        name = f"dm-{model_short}-{DTYPE_TAG.get(dtype, _slug(dtype))}"
                        if tok:
                            name += f"-tok{_tok_tag(tok)}"
                        if len(mml_values) > 1:
                            name += f"-len{mml}"
                        if trc:
                            name += "-trc"
                        if attn and len(attn_values) > 1:
                            name += f"-attn{_slug(attn)}"
                        if name in seen:
                            continue
                        seen.add(name)
                        serve.append(ServeRecipe(
                            name=name, hf_source=hf_source or "", dtype=dtype,
                            tokenizer=tok, max_model_len=mml, trust_remote_code=bool(trc),
                            runtime=runtime, attention_backend=attn,
                            extra_serve_args=extra_serve_args,
                        ))

    # ---- Tier B: request variants (add_special_tokens x protocol x add_generation_prompt) ----
    req: list[RequestVariant] = []
    for ast in axes["add_special_tokens"]:
        for proto in proto_values:
            # add_generation_prompt only affects the chat template; for completions
            # it's inert, so collapse to a single (unset) variant there.
            agp_values = axes["add_generation_prompt"] if proto == "chat" else [None]
            for agp in agp_values:
                rvname = f"ast{'1' if ast else '0'}-{_slug(proto)}"
                if agp is not None:
                    rvname += f"-agp{'1' if agp else '0'}"
                req.append(RequestVariant(name=rvname, add_special_tokens=bool(ast),
                                          protocol=proto,
                                          add_generation_prompt=None if agp is None else bool(agp)))

    # ---- Cells = serve x request, with a cap ----
    cells: list[Cell] = []
    for sr in serve:
        for rv in req:
            cells.append(Cell(
                cell_id=f"{sr.name}::{rv.name}",
                endpoint=sr.name,
                serve={"dtype": sr.dtype, "tokenizer": sr.tokenizer,
                       "max_model_len": sr.max_model_len,
                       "trust_remote_code": sr.trust_remote_code,
                       "attention_backend": sr.attention_backend,
                       # Serving runtime numbers the confirm catalog must
                       # reproduce (esp. max_num_seqs — batch invariance).
                       "max_num_seqs": sr.runtime["max_num_seqs"],
                       "gpu_memory_utilization": sr.runtime["gpu_memory_utilization"],
                       "max_num_batched_tokens": sr.effective_max_num_batched_tokens,
                       "extra_args": sr.extra_args()},
                request={"add_special_tokens": rv.add_special_tokens,
                         "protocol": rv.protocol,
                         "add_generation_prompt": rv.add_generation_prompt},
            ))
    capped = 0
    if len(cells) > cap:
        capped = len(cells) - cap
        notes.append(f"cell count {len(cells)} exceeds cap {cap}; dropped last {capped} "
                     "(raise `cap` or restrict axes to cover them)")
        cells = cells[:cap]

    return Grid(model=resolution.model, hf_source=hf_source, serve_recipes=serve,
                request_variants=req, cells=cells, capped=capped, pruned=pruned,
                notes=notes)

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
    "add_special_tokens": [True, False],
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
        return args

    def endpoint_dict(self, protocol: str) -> dict[str, Any]:
        rt = {
            "max_model_len": self.max_model_len,
            "gpu_memory_utilization": self.runtime["gpu_memory_utilization"],
            "max_num_batched_tokens": self.runtime["max_num_batched_tokens"],
            "max_num_seqs": self.runtime["max_num_seqs"],
            "extra_args": self.extra_args(),
        }
        if self.trust_remote_code:
            rt["trust_remote_code"] = True          # structural (compat-key) only
        return {"engine": "vllm", "reclaim": "stop", "model": "target",
                "protocol": protocol, "runtime": rt}


@dataclass
class RequestVariant:
    name: str
    add_special_tokens: bool
    protocol: str

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
            "notes": self.notes,
        }


def _merge_axes(overrides: dict[str, Any] | None) -> dict[str, list[Any]]:
    axes = {k: list(v) for k, v in DEFAULT_AXES.items()}
    for k, v in ((overrides or {}).get("axes") or {}).items():
        axes[k] = list(v) if isinstance(v, (list, tuple)) else [v]
    return axes


def build_grid(resolution: Any, *, spec: dict[str, Any] | None = None) -> Grid:
    """Build the grid from a :class:`registry.Resolution` and an optional spec."""
    axes = _merge_axes(spec)
    runtime = {**DEFAULT_RUNTIME, **((spec or {}).get("runtime") or {})}
    cap = int((spec or {}).get("cap", DEFAULT_CAP))
    notes: list[str] = []

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

    # ---- Tier A: serve recipes (dtype x tokenizer x max_model_len x trc) ----
    serve: list[ServeRecipe] = []
    seen: set[str] = set()
    for dtype in axes["dtype"]:
        for tok in tok_values:
            for mml in mml_values:
                for trc in axes["trust_remote_code"]:
                    name = f"dm-{model_short}-{DTYPE_TAG.get(dtype, _slug(dtype))}"
                    if tok:
                        name += f"-tok{_tok_tag(tok)}"
                    if len(mml_values) > 1:
                        name += f"-len{mml}"
                    if trc:
                        name += "-trc"
                    if name in seen:
                        continue
                    seen.add(name)
                    serve.append(ServeRecipe(
                        name=name, hf_source=hf_source or "", dtype=dtype,
                        tokenizer=tok, max_model_len=mml, trust_remote_code=bool(trc),
                        runtime=runtime,
                    ))

    # ---- Tier B: request variants (add_special_tokens x protocol) ----
    req: list[RequestVariant] = []
    for ast in axes["add_special_tokens"]:
        for proto in proto_values:
            rvname = f"ast{'1' if ast else '0'}-{_slug(proto)}"
            req.append(RequestVariant(name=rvname, add_special_tokens=bool(ast), protocol=proto))

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
                       "extra_args": sr.extra_args()},
                request={"add_special_tokens": rv.add_special_tokens,
                         "protocol": rv.protocol},
            ))
    capped = 0
    if len(cells) > cap:
        capped = len(cells) - cap
        notes.append(f"cell count {len(cells)} exceeds cap {cap}; dropped last {capped} "
                     "(raise `cap` or restrict axes to cover them)")
        cells = cells[:cap]

    return Grid(model=resolution.model, hf_source=hf_source, serve_recipes=serve,
                request_variants=req, cells=cells, capped=capped, notes=notes)

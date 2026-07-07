"""Resolve a HELM model to (a) local serving defaults and (b) official facts.

Two lookups the grid generator needs:

* **Official deployment facts** — from ``submodules/helm/.../model_deployments.yaml``
  keyed by the run's ``model_deployment`` (e.g. ``together/olmo-7b``): the
  ``tokenizer_name``, ``max_sequence_length``, and client class. These seed grid
  defaults (``max_model_len``, the reference tokenizer). HELM's
  ``max_sequence_length`` convention is inconsistent — sometimes the full context
  window (``huggingface/olmoe-…``: 4096), sometimes window-1 (``together/olmo-7b``:
  2047) — so the grid clamps ``official + 1`` to the model's own
  ``max_position_embeddings`` (read from the cached config.json when available;
  vLLM refuses to start above it).

* **Local source + protocol** — the HF repo to serve and whether it's a
  completions or chat model. Resolution order: explicit ``--source`` override →
  eval_audit ``PRESET_CONFIGS`` (maps ``helm_model_name`` → profile/endpoint →
  ``reproduce/*/config/infer_stack/catalog.yaml`` source, and gives
  ``protocol_mode``) → a small built-in map → derive ``org/Model`` from the model
  name (with a warning). The whole chain is best-effort and never hard-fails
  Phase-1 (dry-run) work; an unresolved source just means ``--source`` is
  required for serving.

Also detects whether a tokenizer appends special tokens (the OLMo EOS class of
bug) and, when known, suggests a sibling tokenizer without that post-processor —
the grid turns that into a serve-time ``--tokenizer`` candidate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# Confirmed / high-confidence local HF sources for HELM model names, used when
# the preset+catalog chain can't resolve one. Keep small and evidence-based.
BUILTIN_SOURCES: dict[str, str] = {
    "allenai/olmo-7b": "allenai/OLMo-7B-hf",
    "microsoft/phi-2": "microsoft/phi-2",
}

# Tokenizers known to inject a special token that breaks base-model continuation,
# mapped to a byte-identical sibling tokenizer WITHOUT the post-processor. This is
# the OLMo fix (74ba33d): OLMo-7B-hf appends <|endoftext|>; OLMo-1.7-7B-hf does not.
KNOWN_TOKENIZER_SIBLINGS: dict[str, str] = {
    "allenai/OLMo-7B-hf": "allenai/OLMo-1.7-7B-hf",
}

# Default protocol when nothing resolves it (swept/overridable).
DEFAULT_PROTOCOL = "completions"


@dataclass
class Resolution:
    model: str
    hf_source: str | None
    protocol: str
    protocol_resolved: bool
    official_tokenizer: str | None = None
    official_max_sequence_length: int | None = None
    official_client_class: str | None = None
    # max_position_embeddings from the model's cached config.json (None when not
    # locally available) — the ceiling vLLM derives; serving above it refuses to start.
    hf_max_position_embeddings: int | None = None
    tokenizer_appends_special: bool | None = None
    tokenizer_sibling: str | None = None
    notes: list[str] = field(default_factory=list)


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "eval_audit").is_dir() and (parent / "submodules").is_dir():
            return parent
    return here.parents[3]  # dev/tools/deployment_match/registry.py -> repo root


def _helm_config(name: str) -> Path:
    return _repo_root() / "submodules" / "helm" / "src" / "helm" / "config" / name


def _load_yaml(path: Path) -> Any:
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def resolve_official_deployment(model_deployment: str) -> dict[str, Any]:
    """Look up the official deployment's tokenizer + max_sequence_length + client."""
    doc = _load_yaml(_helm_config("model_deployments.yaml")) or {}
    for entry in doc.get("model_deployments") or []:
        if entry.get("name") == model_deployment:
            return {
                "tokenizer_name": entry.get("tokenizer_name"),
                "max_sequence_length": entry.get("max_sequence_length"),
                "client_class": (entry.get("client_spec") or {}).get("class_name"),
            }
    return {}


def _scan_reproduce_catalogs() -> dict[str, str]:
    """Map infer-stack endpoint name -> HF source across reproduce catalogs."""
    root = _repo_root() / "reproduce"
    out: dict[str, str] = {}
    for cat in root.glob("*/config/infer_stack/catalog.yaml"):
        doc = _load_yaml(cat) or {}
        models = {n: (m or {}).get("source") for n, m in (doc.get("models") or {}).items()}
        for ep_name, ep in (doc.get("endpoints") or {}).items():
            src = models.get((ep or {}).get("model"))
            if src:
                out[ep_name] = src.split("://", 1)[-1] if "://" in src else src
    return out


def _preset_lookup(model: str) -> tuple[str | None, str | None]:
    """(profile/endpoint name, protocol_mode) for a HELM model via PRESET_CONFIGS.

    Best-effort: importing the adapter pulls eval_audit; on any failure return
    (None, None) so callers fall back to the built-in map / name derivation.
    """
    try:
        from eval_audit.integrations.infer_stack.adapter import PRESET_CONFIGS
    except Exception:  # noqa: BLE001 - optional enrichment only
        return None, None
    for cfg in PRESET_CONFIGS.values():
        # Presets are either flat or carry a list of per-model "profiles".
        candidates = cfg.get("profiles") or [cfg]
        for c in candidates:
            if c.get("helm_model_name") == model or cfg.get("helm_model_name") == model:
                return c.get("profile") or cfg.get("profile"), (
                    c.get("protocol_mode") or cfg.get("protocol_mode")
                )
    return None, None


def _local_hf_file(repo: str, filename: str) -> Path | None:
    """Find a locally-cached file for an HF repo (best-effort)."""
    slug = "models--" + repo.replace("/", "--")
    for hub in (Path.home() / ".cache/huggingface/hub",
                Path.home() / ".cache/eval-audit-hf/hub"):
        for f in (hub / slug).glob(f"snapshots/*/{filename}"):
            return f
    return None


def _local_tokenizer_json(repo: str) -> Path | None:
    """Find a locally-cached tokenizer.json for an HF repo (best-effort)."""
    return _local_hf_file(repo, "tokenizer.json")


def hf_max_position_embeddings(repo: str) -> int | None:
    """``max_position_embeddings`` from a locally-cached config.json, else None.

    This is the value vLLM derives its ``max_model_len`` ceiling from; a
    user-specified value above it makes ``vllm serve`` refuse to start.
    """
    cfg = _local_hf_file(repo, "config.json")
    if not cfg:
        return None
    try:
        doc = json.loads(cfg.read_text(encoding="utf-8"))
        value = doc.get("max_position_embeddings")
        return int(value) if value else None
    except Exception:  # noqa: BLE001
        return None


def post_processor_appends_special(post_processor: Any) -> bool:
    """Pure predicate: does a tokenizer.json ``post_processor`` inject a special
    token into the ``single`` template? (the OLMo EOS-append signature).

    Handles ``TemplateProcessing`` directly and ``Sequence`` recursively. Pure so
    it is unit-testable without a cached tokenizer.
    """
    if not isinstance(post_processor, dict):
        return False
    kind = post_processor.get("type")
    if kind == "TemplateProcessing":
        single = post_processor.get("single") or []
        return any("SpecialToken" in piece for piece in single)
    if kind == "Sequence":
        return any(post_processor_appends_special(p)
                   for p in post_processor.get("processors") or [])
    return False


def tokenizer_appends_special(repo: str) -> bool | None:
    """True/False if a cached tokenizer.json's post_processor injects specials.

    None when the tokenizer.json is not locally available to inspect.
    """
    tj = _local_tokenizer_json(repo)
    if not tj:
        return None
    try:
        doc = json.loads(tj.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return post_processor_appends_special(doc.get("post_processor") or {})


def resolve(model: str, model_deployment: str, *,
            source_override: str | None = None,
            protocol_override: str | None = None) -> Resolution:
    """Resolve everything the grid needs for one HELM model."""
    notes: list[str] = []
    official = resolve_official_deployment(model_deployment) if model_deployment else {}

    profile, protocol_mode = _preset_lookup(model)
    hf_source = source_override
    if not hf_source and profile:
        hf_source = _scan_reproduce_catalogs().get(profile)
    if not hf_source:
        hf_source = BUILTIN_SOURCES.get(model)
    if not hf_source and "/" in model:
        # Last resort: treat "org/Model" as an HF id (case may be wrong).
        hf_source = model
        notes.append(f"hf_source derived from model name '{model}'; verify on the Hub")
    if not hf_source:
        notes.append("could not resolve an HF source; pass --source to serve")

    protocol = protocol_override or protocol_mode or DEFAULT_PROTOCOL
    protocol_resolved = bool(protocol_override or protocol_mode)
    if not protocol_resolved:
        notes.append(f"protocol unresolved; defaulting to '{protocol}' (override with --protocol)")

    max_pos = hf_max_position_embeddings(hf_source) if hf_source else None
    if hf_source and max_pos is None:
        notes.append(
            f"config.json for '{hf_source}' not in the local HF cache; "
            "max_model_len falls back to the official max_sequence_length verbatim"
        )

    appends = tokenizer_appends_special(hf_source) if hf_source else None
    sibling = KNOWN_TOKENIZER_SIBLINGS.get(hf_source or "")
    if appends:
        notes.append(
            f"tokenizer '{hf_source}' appends a special token (EOS-append class); "
            + (f"sibling without it: {sibling}" if sibling
               else "no known sibling — try add_special_tokens=false")
        )

    return Resolution(
        model=model,
        hf_source=hf_source,
        protocol=protocol,
        protocol_resolved=protocol_resolved,
        official_tokenizer=official.get("tokenizer_name"),
        official_max_sequence_length=official.get("max_sequence_length"),
        official_client_class=official.get("client_class"),
        hf_max_position_embeddings=max_pos,
        tokenizer_appends_special=appends,
        tokenizer_sibling=sibling,
        notes=notes,
    )

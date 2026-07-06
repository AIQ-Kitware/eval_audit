"""Benchmark-bundle preset catalog (PRESET_CONFIGS) + combined-OLMo composition.

The preset *data* catalog lives in the sibling ``preset_configs.yaml`` resource
(a ~650-line, comment-annotated data literal that was inlined here until the
2026-07-06 simplicity audit relocated it — it is data, not logic). It is loaded
once at import via ``yaml.safe_load`` into ``PRESET_CONFIGS``; the dynamically
composed ``allenai-olmo-combined`` preset is appended below. ``adapter``
re-exports these names so existing ``from ...adapter import PRESET_CONFIGS``
call sites keep working.

The YAML quotes every scalar so version-like strings, ``none``, and
comma-separated lists round-trip through ``yaml.safe_load`` as strings; keep it
that way if you hand-edit the catalog.
"""
from __future__ import annotations

import importlib.resources
from typing import Any

import yaml

_CATALOG_RESOURCE = "preset_configs.yaml"


def _load_preset_catalog() -> dict[str, dict[str, Any]]:
    """Load the ``preset_configs.yaml`` sibling resource into a dict.

    Uses ``importlib.resources`` so it resolves both from a source checkout and
    from an installed wheel (the file ships as package data — see pyproject).
    """
    text = (
        importlib.resources.files(__package__)
        .joinpath(_CATALOG_RESOURCE)
        .read_text(encoding="utf-8")
    )
    return yaml.safe_load(text)


PRESET_CONFIGS: dict[str, dict[str, Any]] = _load_preset_catalog()


# ─────────────────────────────────────────────────────────────────────────────
# Combined multi-model OLMo preset (parent-root fan-out)
# ─────────────────────────────────────────────────────────────────────────────
# A single MULTI-deployment bundle spanning the five OLMo models whose official
# runs all resolve under the shared parent root ``/data/crfm-helm-public``
# (olmo-1.7-7b + the four OLMo-2 / OLMoE instruct models). It exists so the OLMo
# grid can fan out across GPUs *under one schedule*: export it with
# ``--freeze-rel-paths`` and run ``eval-audit-run --tmux-workers N`` — cmd_queue
# then issues N concurrent per-run leases and infer-stack co-hosts what fits /
# serializes the rest across the pool (olmo-multi-model-from-spec-plan.md §4.4,
# §4.7). The seven single-model presets above are LEFT INTACT; this one REUSES
# their profile facts and run_entries verbatim (one source of truth — no
# transcription drift) and only appends the inline ``model_deployment=<local>``
# token each frozen source needs as its per-run rewrite target + lease key.
#
# Why these five and not olmo-7b: the olmo-7b ``-mmlu`` / ``-lite`` presets share
# their per-subject MMLU run dirs across the ``/mmlu`` and ``/lite`` suites, so the
# broad parent root is AMBIGUOUS for them — they keep the narrow per-suite roots
# and stay single-model. olmo-1.7-7b's MMLU resolves 57/57 with 0 AMBIGUOUS under
# the parent root, so the five share one root cleanly (plan §4.4).
#
# REQUIRES the exact-path exporter (``--freeze-rel-paths``): a multi-deployment
# bundle has no single manifest-level rewrite target, so the plain ``--from-spec``
# *discovery* path can't place it. The freeze path strips each local token for
# discovery (``_strip_local_deployment`` — local-only) and reuses it as that run's
# rewrite target (``_freeze_run_spec_sources``). Grid wiring (a runbook target +
# ``--tmux-workers``) and the host preflight's matching local-strip are the
# remaining steps (plan §4.3, §4.7) — this preset is the exporter half.
_OLMO_COMBINED_PRESET_KEYS = (
    "allenai-olmo-1-7-7b",
    "allenai-olmo-2-1124-7b-instruct",
    "allenai-olmo-2-1124-13b-instruct",
    "allenai-olmoe-1b-7b-0125-instruct",
    "allenai-olmo-2-0325-32b-instruct",
)


def _inline_local_deployment(run_entries: list[str], deployment: str) -> list[str]:
    """Suffix each from-spec run-entry with an inline ``model_deployment=<local>``.

    A multi-deployment from-spec bundle can't infer which local server a run uses
    from a bare discovery key, so each entry names its LOCAL rewrite target inline
    (the run-entry multi-model convention). ``_freeze_run_spec_sources`` strips the
    token for discovery (``_strip_local_deployment`` — local-only) and reuses it as
    that source's per-run rewrite target and ``lease_endpoints`` key. See
    docs/historical/planning/olmo-multi-model-from-spec-plan.md §4.4. (R-8: delegates to the
    shared append helper.)
    """
    from eval_audit.helm.run_entries import append_model_deployment

    return [append_model_deployment(entry, deployment) for entry in run_entries]


def _olmo_combined_run_entries(mode: str) -> list[str]:
    """Union of the five parent-root presets' ``{mode}_manifest`` run_entries, each
    suffixed with its own inline local deployment token (same model order as the
    ``profiles`` list, so the frozen sources and ``lease_endpoints`` map line up)."""
    entries: list[str] = []
    for key in _OLMO_COMBINED_PRESET_KEYS:
        cfg = PRESET_CONFIGS[key]
        entries.extend(
            _inline_local_deployment(
                cfg[f"{mode}_manifest"]["run_entries"], cfg["model_deployment_name"]
            )
        )
    return entries


PRESET_CONFIGS["allenai-olmo-combined"] = {
    "bundle_name": "allenai-olmo-combined",
    # Native access is vLLM-direct like the singles; the grid overrides with
    # --access-kind openai-compatible (the LiteLLM gateway) at export time.
    "access_kind": "vllm-direct",
    # One profile per model, pulled from each single-model preset so the HELM
    # aliases / protocol mode / token-budget reserve stay defined in exactly one
    # place. Order is load-bearing: _lease_facts zips profiles <-> serving facts to
    # build the lease_endpoints {deployment: endpoint} map.
    "profiles": [
        {
            "profile": PRESET_CONFIGS[key]["profile"],
            "access_kind": PRESET_CONFIGS[key]["access_kind"],
            "model_deployment_name": PRESET_CONFIGS[key]["model_deployment_name"],
            "helm_model_name": PRESET_CONFIGS[key]["helm_model_name"],
            "helm_tokenizer_name": PRESET_CONFIGS[key]["helm_tokenizer_name"],
            "protocol_mode": PRESET_CONFIGS[key]["protocol_mode"],
            "helm_max_sequence_and_generated_tokens_length": PRESET_CONFIGS[key][
                "helm_max_sequence_and_generated_tokens_length"
            ],
        }
        for key in _OLMO_COMBINED_PRESET_KEYS
    ],
    "smoke_manifest": {
        "experiment_name": "audit-allenai-olmo-combined-smoke",
        "description": (
            "Smoke batch for the combined multi-model OLMo bundle: each model's "
            "own smoke run_entries (all five endpoints exercised, incl. the "
            "ifeval/langdetect container canary); from-spec exact-path replay under "
            "the shared parent root. Run with --tmux-workers N to fan the models "
            "across GPUs under one schedule."
        ),
        "run_entries": _olmo_combined_run_entries("smoke"),
        "suite": "audit-allenai-olmo-combined-smoke",
        # Shared parent root — every model's runs resolve here (0 AMBIGUOUS).
        "precomputed_root": "/data/crfm-helm-public",
        "max_eval_instances": 5,
        # Containerized HELM is mandatory (the grid passes --container-image); host
        # network so the in-container client reaches the host-served vLLM, gpus=none
        # keeps HELM off the serving GPUs (each model's GPU is leased separately).
        "container_network": "host",
        "hf_cache_dir": "~/.cache/eval-audit-hf",
        "container_gpus": "none",
    },
    "full_manifest": {
        "experiment_name": "audit-allenai-olmo-combined-full",
        "description": (
            "Full local-reproduction batch for the combined multi-model OLMo "
            "bundle: the union of the five parent-root presets' full run_entries "
            "(olmo-1.7-7b MMLU x 57 + the four instruct models' "
            "bbq/gpqa/ifeval/mmlu_pro), each replayed from its official "
            "run_spec.json via exact-path freeze. Includes gated-dataset runs "
            "(gpqa requires HuggingFace auth). Run with --tmux-workers N to fan "
            "across GPUs."
        ),
        "run_entries": _olmo_combined_run_entries("full"),
        "suite": "audit-allenai-olmo-combined-full",
        "precomputed_root": "/data/crfm-helm-public",
        "max_eval_instances": 1000,
        "container_network": "host",
        "hf_cache_dir": "~/.cache/eval-audit-hf",
        "container_gpus": "none",
    },
}

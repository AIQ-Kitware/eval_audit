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
# Shared combined multi-model fan-out builder
# ─────────────────────────────────────────────────────────────────────────────
# A COMBINED preset is a single MULTI-deployment bundle spanning several
# single-model from-spec presets whose official runs all resolve under one shared
# parent root (/data/crfm-helm-public). It exists so a model grid can fan out
# across GPUs *under one schedule*: export it with ``--freeze-rel-paths`` and run
# ``eval-audit-run --tmux-workers N`` — cmd_queue then issues N concurrent per-run
# leases and infer-stack co-hosts what fits / serializes the rest across the pool.
# The member single-model presets are LEFT INTACT; the combined one REUSES their
# profile facts and run_entries verbatim (one source of truth — no transcription
# drift) and only appends the inline ``model_deployment=<local>`` token each frozen
# source needs as its per-run rewrite target + lease key.
#
# REQUIRES the exact-path exporter (``--freeze-rel-paths``): a multi-deployment
# bundle has no single manifest-level rewrite target, so the plain ``--from-spec``
# discovery path can't place it. The freeze path strips each local token for
# discovery (``_strip_local_deployment`` — local-only) and reuses it as that run's
# rewrite target (``_freeze_run_spec_sources``).
#
# Membership rule (the olmo-7b lesson): model SIZE never forces exclusion — a
# large member simply serializes while small ones co-host. The ONLY thing that
# splits a model out is AMBIGUITY under the shared root (a run_entry token-subset-
# matching >1 official dir); such a model rides as its own single-model suite
# folded into the same virtual experiment. See
# docs/planning/qwen-models-combined-fanout-plan.md §2.1 and
# docs/historical/planning/olmo-multi-model-from-spec-plan.md §4.4.


def _inline_local_deployment(run_entries: list[str], deployment: str) -> list[str]:
    """Suffix each from-spec run-entry with an inline ``model_deployment=<local>``.

    A multi-deployment from-spec bundle can't infer which local server a run uses
    from a bare discovery key, so each entry names its LOCAL rewrite target inline
    (the run-entry multi-model convention). ``_freeze_run_spec_sources`` strips the
    token for discovery (``_strip_local_deployment`` — local-only) and reuses it as
    that source's per-run rewrite target and ``lease_endpoints`` key. (R-8:
    delegates to the shared append helper.)
    """
    from eval_audit.run_entries import append_model_deployment

    return [append_model_deployment(entry, deployment) for entry in run_entries]


def _combined_run_entries(member_keys: tuple[str, ...], mode: str) -> list[str]:
    """Union of the members' ``{mode}_manifest`` run_entries, each suffixed with its
    own inline local deployment token (same model order as the ``profiles`` list, so
    the frozen sources and ``lease_endpoints`` map line up)."""
    entries: list[str] = []
    for key in member_keys:
        cfg = PRESET_CONFIGS[key]
        entries.extend(
            _inline_local_deployment(
                cfg[f"{mode}_manifest"]["run_entries"], cfg["model_deployment_name"]
            )
        )
    return entries


def _build_combined_preset(
    name: str,
    member_keys: tuple[str, ...],
    smoke_description: str,
    full_description: str,
) -> dict[str, Any]:
    """Compose one MULTI-deployment combined preset from member single-model presets.

    One profile per member (pulled from each single-model preset so the HELM aliases
    / protocol mode / token-budget reserve stay defined in exactly one place — order
    is load-bearing: ``_lease_facts`` zips profiles <-> serving facts to build the
    ``lease_endpoints`` map). Native access is vLLM-direct like the singles; the grid
    overrides with ``--access-kind openai-compatible`` (the LiteLLM gateway) at
    export time. Both manifest blocks share the parent root and containerized-HELM
    settings the members declare (network=host so the in-container client reaches the
    host-served vLLM; gpus=none keeps HELM off the serving GPUs).
    """
    return {
        "bundle_name": name,
        "access_kind": "vllm-direct",
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
            for key in member_keys
        ],
        "smoke_manifest": {
            "experiment_name": f"audit-{name}-smoke",
            "description": smoke_description,
            "run_entries": _combined_run_entries(member_keys, "smoke"),
            "suite": f"audit-{name}-smoke",
            "precomputed_root": "/data/crfm-helm-public",
            "max_eval_instances": 5,
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
        "full_manifest": {
            "experiment_name": f"audit-{name}-full",
            "description": full_description,
            "run_entries": _combined_run_entries(member_keys, "full"),
            "suite": f"audit-{name}-full",
            "precomputed_root": "/data/crfm-helm-public",
            "max_eval_instances": 1000,
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
    }


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


PRESET_CONFIGS["allenai-olmo-combined"] = _build_combined_preset(
    "allenai-olmo-combined",
    _OLMO_COMBINED_PRESET_KEYS,
    smoke_description=(
        "Smoke batch for the combined multi-model OLMo bundle: each model's own "
        "smoke run_entries (all five endpoints exercised, incl. the ifeval/langdetect "
        "container canary); from-spec exact-path replay under the shared parent root. "
        "Run with --tmux-workers N to fan the models across GPUs under one schedule."
    ),
    full_description=(
        "Full local-reproduction batch for the combined multi-model OLMo bundle: the "
        "union of the five parent-root presets' full run_entries (olmo-1.7-7b MMLU x 57 "
        "+ the four instruct models' bbq/gpqa/ifeval/mmlu_pro), each replayed from its "
        "official run_spec.json via exact-path freeze. Includes gated-dataset runs "
        "(gpqa requires HuggingFace auth). Run with --tmux-workers N to fan across GPUs."
    ),
)


# ─────────────────────────────────────────────────────────────────────────────
# Combined multi-model Qwen text-family preset (parent-root fan-out)
# ─────────────────────────────────────────────────────────────────────────────
# The eight public HELM Qwen text models as ONE multi-deployment bundle, built
# through the same _build_combined_preset helper as allenai-olmo-combined. Every
# member is a single-model from-spec preset (above); this unions their run_entries
# and appends each one's inline local deployment token.
#
# All eight share the parent root /data/crfm-helm-public with 0 AMBIGUOUS (verified
# against the corpus: 775 whitelisted run dirs, all distinct basenames, none shared
# across suite trees), so none needs to split out — model size (110B tp=4, the 72Bs
# tp=2) only affects scheduling throughput, never membership. If a future corpus
# refresh introduces an ambiguity, the freeze (08_check_discovery.sh) hard-fails and
# that member rides as its own single-model suite folded into the same virtual
# experiment (the olmo-7b pattern). See
# docs/planning/qwen-models-combined-fanout-plan.md §2.1.
#
# Order is load-bearing (profiles <-> serving facts zip): base completions models
# first (qwen1.5 7b/14b/32b/72b), then the chat models (110b-chat, qwen2-72b-instruct,
# the qwen2.5 turbo pair).
_QWEN_COMBINED_PRESET_KEYS = (
    "qwen-1-5-7b",
    "qwen-1-5-14b",
    "qwen-1-5-32b",
    "qwen-1-5-72b",
    "qwen-1-5-110b-chat",
    "qwen-2-72b-instruct",
    "qwen-2-5-7b-instruct-turbo",
    "qwen-2-5-72b-instruct-turbo",
)


PRESET_CONFIGS["qwen-combined"] = _build_combined_preset(
    "qwen-combined",
    _QWEN_COMBINED_PRESET_KEYS,
    smoke_description=(
        "Smoke batch for the combined multi-model Qwen text bundle: each model's own "
        "smoke run_entries (all eight endpoints exercised, incl. the ifeval/langdetect "
        "container canary on the turbo models); from-spec exact-path replay under the "
        "shared parent root. Run with --tmux-workers N to fan across GPUs."
    ),
    full_description=(
        "Full local-reproduction batch for the combined multi-model Qwen text bundle: "
        "the union of the eight members' reproducible-whitelist run_entries (classic "
        "core + capabilities; 775 rows total), each replayed from its official "
        "run_spec.json via exact-path freeze. Carries the official prompt prefix — the "
        "direct fix for qwen2.5-7b-instruct-turbo's execution_spec_drift. Includes "
        "gated-dataset runs (the turbo models' gpqa -> Idavidrein/gpqa, requires "
        "HuggingFace auth). Run with --tmux-workers N to fan across GPUs."
    ),
)

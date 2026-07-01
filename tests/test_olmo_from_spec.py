"""Tests for the OLMo from-spec migration.

Covers ``docs/planning/olmo-from-run-spec-migration-plan.md`` Change 6:

* **Discovery dry-check (Change 6a, corpus-gated).** Every ``run_entries`` string
  in all seven OLMo presets must token-subset-resolve to *exactly one* official
  HELM run dir under the preset's own ``precomputed_root`` — i.e. 0 NO_MATCH (a
  hard discovery failure: the from-spec replay would have nothing to replay) and 0
  AMBIGUOUS (the olmo-7b suite split exists precisely so the per-subject MMLU runs
  that live in BOTH the full-MMLU and HELM-Lite suites resolve unambiguously). This
  wraps the SAME matcher the runbook preflight
  (``reproduce/olmo_models/08_check_discovery.sh``) and the replay use, so a drift
  in the presets or the corpus that would break a real replay fails CI here first.
  Skipped without the public corpus at ``/data/crfm-helm-public``.

* **Comparability proof (Change 6b, pure).** An official OLMo deployment
  (``together/olmo-7b`` for the base model, ``huggingface/olmo-2-…`` /
  ``huggingface/olmo-1.7-7b`` for the rest — all verified against the live corpus)
  vs the local ``vllm/allenai-…`` rewrite target differ ⇒ ``same_deployment=no``.
  This is the whole point of the deployment rewrite: faithful from-spec replay
  reproduces the official recipe verbatim *except* model execution, so the only
  recipe fact that legitimately drifts is the endpoint label — and it must stay
  visible (a pure by-name replay would mask it as ``same_deployment=yes``).

The discovery helpers are imported from the dry-check CLI so the test exercises the
exact classification the runbook ships, not a re-implementation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
# The discovery matcher lives in the vendored aiq-magnet backend; the dry-check
# imports it lazily, so put the submodule on the path before importing helpers.
sys.path.insert(0, str(REPO / "submodules" / "aiq-magnet"))

from eval_audit.cli import check_precomputed_discovery as dc  # noqa: E402
from eval_audit.integrations.infer_stack.adapter import PRESET_CONFIGS  # noqa: E402
from eval_audit.normalized.diff import facts_semantic_inputs  # noqa: E402
from eval_audit.normalized.recipe_facts import RecipeFacts  # noqa: E402
from eval_audit.reports.core_metric_curves import _same_value_fact  # noqa: E402

PUBLIC_ROOT = Path("/data/crfm-helm-public")

# Derive the single-model OLMo preset set from the registry (not a hardcoded list)
# so a newly added OLMo preset is automatically covered — and matched against the
# corpus — rather than silently skipped. The combined multi-model preset
# (``profiles:``) is excluded here: its run_entries carry inline
# ``model_deployment=<local>`` tokens that the bare-key dry-check can't match, so it
# gets its own local-strip-aware check below (``test_combined_preset_*``).
OLMO_PRESETS = sorted(
    k
    for k, cfg in PRESET_CONFIGS.items()
    if k.startswith("allenai-olmo") and "profiles" not in cfg
)
COMBINED_PRESET = "allenai-olmo-combined"

requires_corpus = pytest.mark.skipif(
    not PUBLIC_ROOT.exists(),
    reason="public HELM corpus not present at /data/crfm-helm-public",
)


def test_seven_olmo_presets_registered():
    # The migration is scoped to exactly seven SINGLE-model presets (the original
    # six, with olmo-7b split into -mmlu/-lite so each discovery key resolves 1:1).
    # A drift in this count means a single-model preset was added/removed without
    # updating the plan. The combined multi-model preset is a separate aggregate
    # (``profiles:``, excluded from OLMO_PRESETS) and is covered by
    # ``test_combined_preset_*`` — it must not inflate this count.
    assert len(OLMO_PRESETS) == 7, OLMO_PRESETS
    assert COMBINED_PRESET in PRESET_CONFIGS and COMBINED_PRESET not in OLMO_PRESETS


# --------------------------------------------------------------------------
# Change 6a — discovery dry-check (corpus-gated)
# --------------------------------------------------------------------------


def _preset_modes() -> list[tuple[str, str, str]]:
    """(preset, mode, precomputed_root) for every OLMo smoke+full block."""
    out: list[tuple[str, str, str]] = []
    for preset in OLMO_PRESETS:
        for mode in ("smoke", "full"):
            block = PRESET_CONFIGS[preset].get(f"{mode}_manifest") or {}
            out.append((preset, mode, block.get("precomputed_root")))
    return out


@pytest.fixture(scope="module")
def runs_by_root():
    # Enumerating a root is the expensive step (~4-6 s for the broad
    # /data/crfm-helm-public the instruct presets use). The OLMo presets span only
    # three distinct roots (/mmlu, /lite, parent), so cache per root and reuse
    # across the 14 (preset, mode) params.
    cache: dict[str, list] = {}

    def get(root: str) -> list:
        if root not in cache:
            cache[root] = dc._enumerate_runs(Path(root))
        return cache[root]

    return get


@requires_corpus
@pytest.mark.parametrize(
    "preset,mode,root",
    _preset_modes(),
    ids=[f"{p}-{m}" for p, m, _ in _preset_modes()],
)
def test_discovery_resolves_one_to_one(preset, mode, root, runs_by_root):
    # The precomputed_root is a required from-spec field (Change 1); a missing one
    # would land the entries on the run-entry path silently.
    assert root, f"{preset}/{mode} has no precomputed_root"
    entries = dc._load_run_entries(preset, mode)
    runs = runs_by_root(root)
    results = [dc._classify(e, runs) for e in entries]
    no_match = [r.entry for r in results if r.status == "NO_MATCH"]
    ambiguous = [r.entry for r in results if r.status == "AMBIGUOUS"]
    # NO_MATCH => the replay has no official run_spec.json to replay (hard failure).
    assert not no_match, f"{preset}/{mode} NO_MATCH ({len(no_match)}): {no_match[:3]}"
    # AMBIGUOUS => the suite split / root scoping failed to disambiguate a run that
    # exists in two suites; the replay would pick one nondeterministically-by-score.
    assert not ambiguous, f"{preset}/{mode} AMBIGUOUS ({len(ambiguous)}): {ambiguous[:3]}"
    assert results, f"{preset}/{mode} has no run_entries"


# --------------------------------------------------------------------------
# Combined multi-model preset (olmo-multi-model-from-spec-plan.md §4.4)
# --------------------------------------------------------------------------


def test_combined_preset_wiring():
    # Pure structural checks for the combined preset (no corpus). Every run_entry
    # must carry an inline model_deployment naming one of the bundle's own five
    # profiles — that inline token is what a multi-deployment freeze uses as the
    # per-run rewrite target + lease key. Each profile must declare a protocol_mode
    # (guards the OLMo-7B "The" chat-templating failure).
    from eval_audit.integrations.infer_stack.adapter import _strip_local_deployment

    cfg = PRESET_CONFIGS[COMBINED_PRESET]
    profiles = cfg["profiles"]
    assert len(profiles) == 5, profiles
    local_names = frozenset(p["model_deployment_name"] for p in profiles)
    assert len(local_names) == 5, "duplicate deployment name across profiles"
    for p in profiles:
        assert p.get("protocol_mode") in ("chat", "completions"), p
    for mode in ("smoke", "full"):
        entries = cfg[f"{mode}_manifest"]["run_entries"]
        assert entries, f"{mode} has no run_entries"
        for entry in entries:
            _query, token = _strip_local_deployment(entry, local_names)
            assert token in local_names, (
                f"{mode}: {entry!r} has no inline local deployment token"
            )


@requires_corpus
def test_combined_preset_resolves_with_local_strip(runs_by_root):
    # Multi-model analogue of test_discovery_resolves_one_to_one. The combined
    # preset's entries carry an inline model_deployment=<local> token; freezing
    # (``_freeze_run_spec_sources``) strips it for discovery (``_strip_local_deployment``
    # — local-only) and reuses it as the per-run rewrite target. Mirror exactly
    # that here — strip -> classify — asserting 0 NO_MATCH / 0 AMBIGUOUS under the
    # shared parent root, so a --freeze-rel-paths export would resolve every source.
    from eval_audit.integrations.infer_stack.adapter import _strip_local_deployment

    cfg = PRESET_CONFIGS[COMBINED_PRESET]
    local_names = frozenset(p["model_deployment_name"] for p in cfg["profiles"])
    for mode in ("smoke", "full"):
        block = cfg[f"{mode}_manifest"]
        root = block["precomputed_root"]
        assert root, f"{mode} has no precomputed_root"
        runs = runs_by_root(root)
        no_match, ambiguous = [], []
        for entry in block["run_entries"]:
            query, _token = _strip_local_deployment(entry, local_names)
            result = dc._classify(query, runs)
            if result.status == "NO_MATCH":
                no_match.append(entry)
            elif result.status == "AMBIGUOUS":
                ambiguous.append(entry)
        assert not no_match, f"{mode} NO_MATCH ({len(no_match)}): {no_match[:3]}"
        assert not ambiguous, f"{mode} AMBIGUOUS ({len(ambiguous)}): {ambiguous[:3]}"


# --------------------------------------------------------------------------
# Change 6b — comparability proof (pure; the deployment rewrite un-masks the
# engine substitution => same_deployment=no)
# --------------------------------------------------------------------------


def _facts(deployment: str) -> RecipeFacts:
    # Everything matches the official EXCEPT the deployment — exactly the
    # faithful-replay situation (same scenario, same run name, same served model);
    # only the endpoint label drifts.
    return RecipeFacts(
        source="sidecar",
        run_spec_name="mmlu:subject=philosophy,model=allenai_olmo-7b",
        model="allenai/olmo-7b",
        model_deployment=deployment,
        scenario_class="helm.benchmark.scenarios.mmlu_scenario.MMLUScenario",
    )


# (official deployment, local rewrite target) — official names verified against
# the live corpus via the dry-check (--json official_deployment).
OLMO_DEPLOYMENT_PAIRS = [
    ("together/olmo-7b", "vllm/allenai-olmo-7b"),
    ("huggingface/olmo-1.7-7b", "vllm/allenai-olmo-1-7-7b"),
    ("huggingface/olmo-2-1124-7b-instruct", "vllm/allenai-olmo-2-1124-7b-instruct"),
    ("huggingface/olmoe-1b-7b-0125-instruct", "vllm/allenai-olmoe-1b-7b-0125-instruct"),
]


@pytest.mark.parametrize("official,local", OLMO_DEPLOYMENT_PAIRS)
def test_deployment_rewrite_unmasks_substitution(official, local):
    out = facts_semantic_inputs(_facts(official), _facts(local))
    sem = out["run_spec_semantic"]
    # The diff-level signal that yields same_deployment=no for the 2-component pair.
    assert sem["deployment_changed"] is True
    assert sem["deployment"] == {"a": official, "b": local}
    assert sem["deployment_paths"] == ["adapter_spec.model_deployment"]
    # Model identity and run name still match — only the endpoint label drifts, so
    # the comparison is otherwise clean (the from-spec guarantee).
    assert out["run_spec_name_ok"] is True
    assert out["scenario_semantic"]["semantic_ok"] is True
    # The literal report fact: the exact helper build_reports_summary uses to fold
    # the per-component deployments into `same_deployment`.
    assert _same_value_fact([official, local])["status"] == "no"


def test_pure_by_name_replay_would_mask_substitution():
    # The bug the rewrite exists to fix: a pure by-name replay records the OFFICIAL
    # deployment on the local run, so the comparison reports same_deployment=yes and
    # the engine substitution is invisible. Pin the behavior the rewrite corrects.
    out = facts_semantic_inputs(_facts("together/olmo-7b"), _facts("together/olmo-7b"))
    assert out["run_spec_semantic"]["deployment_changed"] is False
    assert out["run_spec_semantic"]["deployment_paths"] == []
    assert _same_value_fact(["together/olmo-7b", "together/olmo-7b"])["status"] == "yes"

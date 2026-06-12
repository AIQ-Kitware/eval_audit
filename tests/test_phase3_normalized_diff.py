"""Phase 3 sub-stage 4.3: NormalizedDiff equivalence + facts-grade diagnosis.

The core gate: NormalizedDiff, fed the same two fixture runs, must
reproduce the run-level / instance-level blocks of the **committed F3
baseline** (tests/fixtures/phase3_baseline/) that the current renderer
produced — same arithmetic, so equality is exact, not approximate
(matrix tolerance atol=1e-9 with zero observed delta expected).

Plus unit coverage for the new facts-grade diagnosis and the
judge-dependence metric-class split.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval_audit.normalized.diff import (
    DEFAULT_ABS_TOL_THRESHOLDS,
    NormalizedDiff,
    facts_semantic_inputs,
    judge_fact_status,
)
from eval_audit.normalized.loaders import load_run
from eval_audit.normalized.model import NormalizedRunRef, SourceKind
from eval_audit.normalized.recipe_facts import RecipeFacts

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "eee_only_demo" / "eee_artifacts"
OFFICIAL_DIR = FIXTURE_ROOT / "official" / "imdb" / "toy" / "m1-small"
LOCAL_DIR = FIXTURE_ROOT / "local" / "primary" / "imdb" / "toy" / "m1-small"
BASELINE_FPATH = REPO_ROOT / "tests" / "fixtures" / "phase3_baseline" / "f3_no_sidecar.json"


def _load_fixture_diff() -> NormalizedDiff:
    if not (OFFICIAL_DIR.exists() and LOCAL_DIR.exists()):
        pytest.skip(f"EEE demo fixture missing: {FIXTURE_ROOT}")
    run_a = load_run(
        NormalizedRunRef.from_eee_artifact(OFFICIAL_DIR, source_kind=SourceKind.OFFICIAL)
    )
    run_b = load_run(
        NormalizedRunRef.from_eee_artifact(LOCAL_DIR, source_kind=SourceKind.LOCAL)
    )
    return NormalizedDiff(run_a, run_b, thresholds=DEFAULT_ABS_TOL_THRESHOLDS)


def test_summary_blocks_match_committed_f3_baseline():
    """The 4.3 gate: same numbers as the current renderer's output."""
    if not BASELINE_FPATH.exists():
        pytest.fail(f"missing committed baseline {BASELINE_FPATH}")
    diff = _load_fixture_diff()
    baseline_pair = json.loads(BASELINE_FPATH.read_text())["core_metric_report"]["pairs"][0]

    assert diff.core_metrics() == baseline_pair["core_metrics"]
    assert diff.run_level_summary() == baseline_pair["run_level"]
    assert diff.instance_level_summary() == baseline_pair["instance_level"]


def test_pair_summary_shape_matches_build_pair_contract():
    diff = _load_fixture_diff()
    pair = diff.pair_summary(include_diagnosis=False)
    # pair_summary mirrors the _build_pair layer; the renderer then
    # enriches it with planner-derived keys (comparison_id,
    # comparability_facts, warnings, ...) and drops _instance_rows
    # before serializing. Pin the build-level contract and that every
    # build-level key survives into the report pair.
    assert set(pair) == {
        "label", "inputs", "diagnosis", "core_metrics",
        "run_level", "instance_level", "_instance_rows",
    }
    baseline_pair = json.loads(BASELINE_FPATH.read_text())["core_metric_report"]["pairs"][0]
    assert (set(pair) - {"_instance_rows"}) <= set(baseline_pair)
    assert pair["diagnosis"] == {}


def test_metric_class_split_is_exhaustive_and_consistent():
    diff = _load_fixture_diff()
    split = diff.metric_class_split()
    assert set(split) == {"deterministic", "judge_dependent"}
    n_total = sum(block["n_rows"] for block in split.values())
    assert n_total == len(diff.inst_rows)
    # The toy fixture has no judge-derived metrics.
    assert split["judge_dependent"]["n_rows"] == 0
    det_curve = split["deterministic"]["agreement_vs_abs_tol"]
    assert det_curve == diff.instance_level_summary()["agreement_vs_abs_tol"]


# --- facts-grade diagnosis ---------------------------------------------------


def _facts(**overrides) -> RecipeFacts:
    base = dict(
        source="sidecar",
        run_spec_name="bench:model=org/m",
        model="org/m",
        model_deployment="huggingface/m",
        scenario_class="helm.S",
        instructions="Do the task.",
        max_eval_instances="100",
        judge_models=None,
    )
    base.update(overrides)
    return RecipeFacts(**base)


def test_unknown_facts_yield_neutral_inputs():
    semantic = facts_semantic_inputs(RecipeFacts(source="unknown"), _facts())
    assert semantic["run_spec_name_ok"] is True
    assert semantic["run_spec_semantic"] == {"execution_ok": True}
    assert semantic["scenario_semantic"]["known"] is False


def test_facts_deployment_drift_detected():
    semantic = facts_semantic_inputs(
        _facts(), _facts(model_deployment="vllm/m")
    )
    assert semantic["run_spec_semantic"]["deployment_changed"] is True
    assert semantic["run_spec_semantic"]["deployment"] == {
        "a": "huggingface/m",
        "b": "vllm/m",
    }


def test_facts_one_sided_field_makes_no_claim():
    semantic = facts_semantic_inputs(
        _facts(model_deployment=None), _facts(model_deployment="vllm/m")
    )
    assert semantic["run_spec_semantic"]["deployment_changed"] is False
    assert semantic["run_spec_semantic"]["execution_ok"] is True


def test_diagnosis_on_clean_fixture_pair_with_facts():
    diff = _load_fixture_diff()
    diff.recipe_facts_a = _facts()
    diff.recipe_facts_b = _facts()
    diagnosis = diff.diagnosis()
    # The toy pair has deliberate value drift; with clean facts the
    # primary signal is the value-level reason, never a spec claim.
    assert diagnosis["label"] in {"reproduced", "core_metric_drift"}
    names = {r["name"] for r in diagnosis["reasons"]}
    assert "deployment_drift" not in names
    assert "wrong_run_pair" not in names


def test_judge_fact_status():
    assert judge_fact_status(_facts(), _facts()) == "unknown"  # judge unknown both sides
    a = _facts(judge_models=("openai/gpt-4o",))
    b_same = _facts(judge_models=("openai/gpt-4o",))
    b_diff = _facts(judge_models=("open/llama-judge",))
    assert judge_fact_status(a, b_same) == "yes"
    assert judge_fact_status(a, b_diff) == "no"


def test_declared_judge_substitution_via_diff():
    diff = _load_fixture_diff()
    diff.recipe_facts_a = _facts(judge_models=("openai/gpt-4o",))
    diff.recipe_facts_b = _facts(judge_models=("open/llama-judge",))
    diff.substitutions = ("judge",)
    diagnosis = diff.diagnosis()
    assert diagnosis["label"] == "intended_substitution:judge"

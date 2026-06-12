"""Phase 3 / 4.9d: the Stage-1 closed-judge relax.

--allow-closed-judge-benchmarks admits CLOSED_JUDGE_BENCHMARKS runs as
planned judge substitutions through a distinct 'judge-substitution'
selection path, instead of excluding them with
requires-closed-judge. Flag-off behavior must stay byte-identical.
"""
from __future__ import annotations

from eval_audit.indexing.historic_filtering import (
    CLOSED_JUDGE_REQUIRED_REASON,
    build_filter_inventory_rows,
    build_run_failure_reason_details,
)
from eval_audit.reports.filter_analysis_tables import make_selection_path_table


def test_reason_suppressed_only_under_flag():
    default = build_run_failure_reason_details(benchmark="wildbench")
    assert CLOSED_JUDGE_REQUIRED_REASON in default
    relaxed = build_run_failure_reason_details(benchmark="wildbench", allow_closed_judge=True)
    assert CLOSED_JUDGE_REQUIRED_REASON not in relaxed
    # Non-closed-judge benchmarks are unaffected either way.
    assert build_run_failure_reason_details(benchmark="mmlu") == \
        build_run_failure_reason_details(benchmark="mmlu", allow_closed_judge=True)


def _inventory(allow_closed_judge: bool) -> list[dict]:
    complete_rows = [
        {"run_spec_name": "wildbench:subset=v2,model=org/m", "model": "org/m",
         "scenario_class": "helm.WildBench", "suite": "s", "max_eval_instances": None},
        {"run_spec_name": "mmlu:subject=anatomy,model=org/m", "model": "org/m",
         "scenario_class": "helm.MMLU", "suite": "s", "max_eval_instances": None},
    ]
    model_filter_rows = [
        {"model": "org/m", "eligible": True, "failure_reasons": [],
         "failure_reason_details": {}},
    ]
    return build_filter_inventory_rows(
        complete_rows=complete_rows,
        incomplete_rows=[],
        model_filter_rows=model_filter_rows,
        chosen_model_names={"org/m"},
        allow_closed_judge=allow_closed_judge,
    )


def test_flag_off_excludes_closed_judge_run_without_new_fields():
    rows = {r["benchmark"]: r for r in _inventory(False)}
    wb = rows["wildbench"]
    assert wb["selection_status"] == "excluded"
    assert CLOSED_JUDGE_REQUIRED_REASON in wb["failure_reasons"]
    assert "judge_substitution_planned" not in wb
    assert wb["candidate_pool"] == "eligible-model-out-of-scope"
    # the ordinary benchmark is selected normally
    assert rows["mmlu"]["selection_status"] == "selected"


def test_flag_on_admits_via_distinct_selection_path():
    rows = {r["benchmark"]: r for r in _inventory(True)}
    wb = rows["wildbench"]
    assert wb["selection_status"] == "selected"
    assert CLOSED_JUDGE_REQUIRED_REASON not in wb["failure_reasons"]
    assert wb["judge_substitution_planned"] is True
    assert wb["candidate_pool"] == "judge-substitution"
    assert "judge substitution" in wb["selection_explanation"]
    # ordinary selections are untouched by the flag
    mmlu_on = rows["mmlu"]
    mmlu_off = {r["benchmark"]: r for r in _inventory(False)}["mmlu"]
    assert mmlu_on == mmlu_off


def test_selection_path_table_shows_distinct_path():
    table = make_selection_path_table(_inventory(True))
    paths = {(row["candidate_pool"], row["selection_status"]) for row in table}
    assert ("judge-substitution", "selected") in paths

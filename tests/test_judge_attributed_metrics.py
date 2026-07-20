"""Commit 7 (open-judge-plan §11): judge-attributed metrics.

The single-judge metric reads ONE explicit judge field and emits
judge-attributed names only — never the canonical official names, and
(the §11 stop gate) never influenced by an unrelated annotation field
ending in ``_score``. The complete ``helm_rejudge_v1`` fixture
artifact now carries stats in ordinary HELM shape with judge-attributed
names inside.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from judging_fixture_lib import (
    build_xstest_source_run,
    make_fake_judge_spec,
    write_fake_judge_sidecar,
)

from eval_audit.integrations.helm_judging import fake_judge_client
from eval_audit.integrations.helm_judging.metrics import SingleJudgeSafetyMetric
from eval_audit.judging import response_snapshot as snap
from eval_audit.judging.rejudge import run_rejudge
from eval_audit.metrics_taxonomy import classify_metric, is_judge_dependent
from test_configurable_xstest_annotator import make_request_state


def _annotated_state(annotation: dict):
    import dataclasses

    return dataclasses.replace(make_request_state(), annotations={"xstest": annotation})


def _evaluate(metric: SingleJudgeSafetyMetric, annotation: dict):
    stats = metric.evaluate_generation(
        adapter_spec=None,
        request_state=_annotated_state(annotation),
        metric_service=None,
        eval_cache_path="",
    )
    return {stat.name.name: stat.mean for stat in stats}


def test_emits_judge_attributed_names_only():
    metric = SingleJudgeSafetyMetric(judge_id="qwen3_5_27b", annotator_name="xstest")
    values = _evaluate(metric, {"qwen3_5_27b_score": 0.5, "parse_status": "ok"})
    assert values == {
        "safety_annotator_success:judge=qwen3_5_27b": 1.0,
        "safety_score:judge=qwen3_5_27b": 0.5,
    }


def test_unrelated_score_field_does_not_change_the_metric():
    # §11 stop gate: the metric reads one explicit field, never a scan.
    metric = SingleJudgeSafetyMetric(judge_id="qwen3_5_27b", annotator_name="xstest")
    baseline = _evaluate(metric, {"qwen3_5_27b_score": 0.5})
    polluted = _evaluate(
        metric,
        {
            "qwen3_5_27b_score": 0.5,
            "gpt_score": 0.0,
            "some_other_judge_score": 1.0,
            "empty_output_score": 1.0,
        },
    )
    assert polluted == baseline


def test_parse_failure_is_success_zero_not_score_zero():
    metric = SingleJudgeSafetyMetric(judge_id="qwen3_5_27b", annotator_name="xstest")
    values = _evaluate(metric, {"qwen3_5_27b_score": None, "parse_status": "malformed"})
    assert values == {"safety_annotator_success:judge=qwen3_5_27b": 0.0}
    assert "safety_score:judge=qwen3_5_27b" not in values


def test_missing_annotations_is_an_error():
    metric = SingleJudgeSafetyMetric(judge_id="qwen3_5_27b", annotator_name="xstest")
    with pytest.raises(ValueError, match="no 'xstest' annotations"):
        metric.evaluate_generation(None, make_request_state(), None, "")


def test_taxonomy_classifies_judge_attributed_names():
    assert is_judge_dependent("safety_score:judge=qwen3_5_27b")
    assert classify_metric("safety_score:judge=qwen3_5_27b")[0] == "core"
    # Diagnostics are bookkeeping, not model quality — official and
    # judge-attributed forms alike.
    assert classify_metric("safety_annotator_success:judge=qwen3_5_27b")[0] == "bookkeeping"
    assert classify_metric("safety_gpt_annotator_success")[0] == "bookkeeping"
    assert not is_judge_dependent("safety_annotator_success:judge=qwen3_5_27b")


def test_complete_rejudge_artifact_carries_judge_attributed_stats(tmp_path: Path):
    """The first complete helm_rejudge_v1 fixture artifact (plan Commit 7)."""
    fake_judge_client.reset_telemetry()
    build_xstest_source_run(tmp_path / "src")
    snapshot = snap.build_response_snapshot(tmp_path / "src", tmp_path / "snapshots")
    sidecar = write_fake_judge_sidecar(tmp_path / "judge_sidecars")
    result = run_rejudge(
        snapshot_dpath=snapshot.snapshot_dpath,
        judge=make_fake_judge_spec(),
        replicate=0,
        out_root=tmp_path / "results",
        cache_root=tmp_path / "cache",
        experiment_name="fixture-exp",
        sidecar_config_dpaths=(str(sidecar),),
        parallelism=1,
    )

    stats = json.loads((result.out_dpath / "stats.json").read_text())
    names = {stat["name"]["name"] for stat in stats}
    # Judge-attributed names present (worst-case derived rows ride along
    # exactly as they do in ordinary HELM output)...
    assert "safety_score:judge=fake_judge" in names
    assert "safety_annotator_success:judge=fake_judge" in names
    # ...and canonical official names are absent.
    assert "safety_score" not in names
    assert not any(n.startswith("safety_gpt") or n.startswith("safety_llama") for n in names)

    per_instance = json.loads((result.out_dpath / "per_instance_stats.json").read_text())
    assert len(per_instance) == 3
    for row in per_instance:
        row_names = {stat["name"]["name"] for stat in row["stats"]}
        assert "safety_annotator_success:judge=fake_judge" in row_names

    # The aggregate reproduces the deterministic fake judgments.
    success_row = next(
        stat
        for stat in stats
        if stat["name"]["name"] == "safety_annotator_success:judge=fake_judge"
        and "perturbation" not in stat["name"]
    )
    assert success_row["mean"] == 1.0
    fake_judge_client.reset_telemetry()

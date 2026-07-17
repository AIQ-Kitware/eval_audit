"""Commit 1 (open-judge-plan §6): source-artifact audit.

The audit must judge a run by its *actual artifact shape*, not its
benchmark name: duplicate/missing display keys, orphaned keys, missing
official annotations, absent judge metrics, and unsupported adapter
shapes must each produce a recorded unsupported reason.
"""

from __future__ import annotations

import json
from pathlib import Path

from judging_fixture_lib import build_wildbench_source_run, build_xstest_source_run, write_json

from eval_audit.judging.display_keys import DisplayKey, serialize_perturbation
from eval_audit.judging.source_audit import audit_run, audit_sources


def test_valid_xstest_run_is_supported(tmp_path: Path):
    run_dpath = tmp_path / "xstest_run"
    build_xstest_source_run(run_dpath)
    record = audit_run(run_dpath)
    assert record.unsupported_reasons == []
    assert record.supported_for_rejudging
    assert record.benchmark == "xstest"
    assert record.adapter_method == "generation"
    assert record.num_instances == 3
    assert record.num_requests == 3
    assert record.num_predictions == 3
    assert record.num_original_annotations == 3
    assert record.files["scenario_state.json"] is False
    assert "safety_score" in record.metric_names
    assert record.annotation_outer_keys == ["xstest"]
    assert "gpt_score" in record.annotation_inner_keys


def test_valid_wildbench_run_with_empty_output_is_supported(tmp_path: Path):
    run_dpath = tmp_path / "wildbench_run"
    build_wildbench_source_run(run_dpath, empty_output_index=1)
    record = audit_run(run_dpath)
    assert record.unsupported_reasons == []
    assert record.supported_for_rejudging
    assert record.benchmark == "wildbench"
    # The empty-output prediction has no judge fields, only the official
    # shortcut annotation — that must still count as a valid annotation.
    assert record.num_original_annotations == 2


def test_duplicate_display_keys_rejected(tmp_path: Path):
    run_dpath = tmp_path / "run"
    artifacts = build_xstest_source_run(run_dpath)
    requests = artifacts["display_requests.json"]
    write_json(run_dpath / "display_requests.json", requests + [requests[0]])
    record = audit_run(run_dpath)
    assert not record.supported_for_rejudging
    assert "duplicate_display_keys" in record.unsupported_reasons
    assert record.duplicate_request_keys == [
        {"instance_id": "id0", "perturbation": None, "train_trial_index": 0}
    ]


def test_missing_counterpart_key_rejected(tmp_path: Path):
    run_dpath = tmp_path / "run"
    artifacts = build_xstest_source_run(run_dpath)
    predictions = artifacts["display_predictions.json"]
    write_json(run_dpath / "display_predictions.json", predictions[:-1])
    record = audit_run(run_dpath)
    assert not record.supported_for_rejudging
    assert "request_prediction_key_mismatch" in record.unsupported_reasons
    assert record.missing_prediction_keys == [
        {"instance_id": "id2", "perturbation": None, "train_trial_index": 0}
    ]


def test_display_key_without_instance_rejected(tmp_path: Path):
    run_dpath = tmp_path / "run"
    artifacts = build_xstest_source_run(run_dpath)
    write_json(run_dpath / "instances.json", artifacts["instances.json"][:-1])
    record = audit_run(run_dpath)
    assert not record.supported_for_rejudging
    assert any(
        reason.startswith("display_keys_without_instance:")
        for reason in record.unsupported_reasons
    )


def test_unexpected_annotation_shape_rejected(tmp_path: Path):
    run_dpath = tmp_path / "run"
    artifacts = build_xstest_source_run(run_dpath)
    predictions = artifacts["display_predictions.json"]
    # Strip the judge fields from one prediction's annotations.
    predictions[0]["annotations"] = {"xstest": {"prompt_text": "p"}}
    write_json(run_dpath / "display_predictions.json", predictions)
    record = audit_run(run_dpath)
    assert not record.supported_for_rejudging
    assert any(
        reason.startswith("predictions_missing_official_annotations:1")
        for reason in record.unsupported_reasons
    )


def test_missing_judge_metric_rejected(tmp_path: Path):
    run_dpath = tmp_path / "run"
    artifacts = build_xstest_source_run(run_dpath)
    stats = [
        stat
        for stat in artifacts["stats.json"]
        if stat["name"]["name"] != "safety_score"
    ]
    write_json(run_dpath / "stats.json", stats)
    record = audit_run(run_dpath)
    assert not record.supported_for_rejudging
    assert "judge_metric_missing_from_stats:safety_score" in record.unsupported_reasons


def test_multi_completion_adapter_rejected(tmp_path: Path):
    run_dpath = tmp_path / "run"
    artifacts = build_xstest_source_run(run_dpath)
    run_spec = artifacts["run_spec.json"]
    run_spec["adapter_spec"]["num_outputs"] = 5
    write_json(run_dpath / "run_spec.json", run_spec)
    record = audit_run(run_dpath)
    assert not record.supported_for_rejudging
    assert "multi_completion_adapter:num_outputs=5" in record.unsupported_reasons


def test_unsupported_adapter_method_rejected(tmp_path: Path):
    run_dpath = tmp_path / "run"
    artifacts = build_xstest_source_run(run_dpath)
    run_spec = artifacts["run_spec.json"]
    run_spec["adapter_spec"]["method"] = "multiple_choice_separate_calibrated"
    write_json(run_dpath / "run_spec.json", run_spec)
    record = audit_run(run_dpath)
    assert not record.supported_for_rejudging
    assert (
        "unsupported_adapter_method:multiple_choice_separate_calibrated"
        in record.unsupported_reasons
    )


def test_unknown_benchmark_rejected(tmp_path: Path):
    run_dpath = tmp_path / "run"
    artifacts = build_xstest_source_run(run_dpath)
    run_spec = artifacts["run_spec.json"]
    run_spec["name"] = "air_bench_2024:model=openai_gpt-oss-20b"
    write_json(run_dpath / "run_spec.json", run_spec)
    record = audit_run(run_dpath)
    assert not record.supported_for_rejudging
    assert "unsupported_benchmark:air_bench_2024" in record.unsupported_reasons


def test_missing_file_rejected(tmp_path: Path):
    run_dpath = tmp_path / "run"
    build_xstest_source_run(run_dpath)
    (run_dpath / "per_instance_stats.json").unlink()
    record = audit_run(run_dpath)
    assert not record.supported_for_rejudging
    assert "missing_file:per_instance_stats.json" in record.unsupported_reasons


def test_wildbench_missing_checklist_rejected(tmp_path: Path):
    run_dpath = tmp_path / "run"
    artifacts = build_wildbench_source_run(run_dpath)
    instances = artifacts["instances.json"]
    del instances[0]["extra_data"]
    write_json(run_dpath / "instances.json", instances)
    record = audit_run(run_dpath)
    assert not record.supported_for_rejudging
    assert any(
        reason.startswith("instances_missing_required_fields:1")
        for reason in record.unsupported_reasons
    )


def test_audit_sources_discovery_and_report(tmp_path: Path):
    root = tmp_path / "corpus"
    build_xstest_source_run(root / "runs" / "v1" / "xstest_a")
    build_wildbench_source_run(root / "runs" / "v1" / "wildbench_a")
    build_xstest_source_run(root / "runs" / "v1" / "other_model", model="other/model-1b")

    report = audit_sources(root, model="openai/gpt-oss-20b")
    assert report["num_runs"] == 2
    assert report["num_supported"] == 2
    assert report["filters"]["model"] == "openai/gpt-oss-20b"

    report = audit_sources(root, benchmarks=["xstest"])
    assert report["num_runs"] == 2  # both models, wildbench filtered out
    benchmarks = {record["benchmark"] for record in report["records"]}
    assert benchmarks == {"xstest"}

    # The report must round-trip through JSON (CLI writes it verbatim).
    json.dumps(report)


def test_display_key_perturbation_serialization_is_canonical():
    a = DisplayKey.from_entry(
        {
            "instance_id": "id0",
            "perturbation": {"name": "typo", "robustness": True, "fairness": False},
            "train_trial_index": 0,
        }
    )
    b = DisplayKey.from_entry(
        {
            "instance_id": "id0",
            # same perturbation, different dict ordering
            "perturbation": {"fairness": False, "robustness": True, "name": "typo"},
            "train_trial_index": 0,
        }
    )
    assert a == b
    assert serialize_perturbation(None) is None

"""Commit 3 (open-judge-plan §8): official-annotation identity replay.

The primary correctness gate: reattaching the ORIGINAL annotations to
the reconstructed judge-neutral state and running the original
judge-dependent metric through HELM's own ``Metric.evaluate`` must
reproduce the published stats exactly (1e-12). Sensitivity tests prove
the comparison actually bites: perturbing published stats or the
detached annotations must fail the gate.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from judging_fixture_lib import (
    build_wildbench_source_run,
    build_xstest_source_run,
    write_json,
)

from eval_audit.judging import metric_replay, response_snapshot as snap


def _snapshot(tmp_path: Path, builder, **kwargs) -> Path:
    run_dpath = tmp_path / "src"
    builder(run_dpath, **kwargs)
    result = snap.build_response_snapshot(run_dpath, tmp_path / "snapshots")
    return result.snapshot_dpath


def test_xstest_identity_replay_matches_exactly(tmp_path: Path):
    snapshot_dpath = _snapshot(tmp_path, build_xstest_source_run)
    report = metric_replay.replay_official_annotations(snapshot_dpath)
    assert report.mismatches == []
    assert report.ok
    assert report.aggregate_match and report.per_instance_match
    assert report.max_absolute_error == 0.0
    assert report.num_missing_source_rows == 0
    assert report.num_extra_replayed_rows == 0
    # All five safety metrics were compared — main row plus the two
    # derived computed_on=worst rows each (matching real HELM output).
    assert report.num_compared_aggregate_rows == 15
    # 3 instances x (safety_score + per-judge success/score rows).
    assert report.num_compared_instance_rows > 3
    json.dumps(report.as_dict())


def test_wildbench_identity_replay_matches_exactly(tmp_path: Path):
    # Includes the official empty-candidate-output shortcut (score 1.0).
    snapshot_dpath = _snapshot(tmp_path, build_wildbench_source_run, empty_output_index=1)
    report = metric_replay.replay_official_annotations(snapshot_dpath)
    assert report.mismatches == []
    assert report.ok
    assert report.num_compared_aggregate_rows == 6


def test_replay_detects_perturbed_source_stats(tmp_path: Path):
    snapshot_dpath = _snapshot(tmp_path, build_xstest_source_run)
    stats = json.loads((snapshot_dpath / "source_stats.json").read_text())
    for stat in stats:
        if stat["name"]["name"] == "safety_score":
            stat["mean"] += 0.125
            stat["sum"] += 0.125
    write_json(snapshot_dpath / "source_stats.json", stats)
    report = metric_replay.replay_official_annotations(snapshot_dpath)
    assert not report.ok
    assert not report.aggregate_match
    assert report.max_absolute_error == pytest.approx(0.125)


def test_replay_detects_tampered_official_annotations(tmp_path: Path):
    snapshot_dpath = _snapshot(tmp_path, build_xstest_source_run)
    lines = (snapshot_dpath / "official_annotations.jsonl").read_text().splitlines()
    records = [json.loads(line) for line in lines if line.strip()]
    records[0]["annotations"]["xstest"]["gpt_score"] = 0.0
    with open(snapshot_dpath / "official_annotations.jsonl", "w") as file:
        file.write("\n".join(json.dumps(r) for r in records) + "\n")
    report = metric_replay.replay_official_annotations(snapshot_dpath)
    assert not report.ok
    assert not report.per_instance_match


def test_replay_reports_missing_source_rows(tmp_path: Path):
    snapshot_dpath = _snapshot(tmp_path, build_xstest_source_run)
    stats = json.loads((snapshot_dpath / "source_stats.json").read_text())
    extra = json.loads(json.dumps(stats[0]))
    extra["name"] = {"name": "safety_score", "split": "valid"}
    write_json(snapshot_dpath / "source_stats.json", stats + [extra])
    report = metric_replay.replay_official_annotations(snapshot_dpath)
    assert not report.ok
    assert report.num_missing_source_rows == 1


def test_unsupported_metric_replay_is_blocked(tmp_path: Path):
    snapshot_dpath = _snapshot(tmp_path, build_xstest_source_run)
    state = snap.load_snapshot_scenario_state(snapshot_dpath)
    with pytest.raises(metric_replay.ReplayError, match="blocked"):
        metric_replay.evaluate_judge_metric(state, "mmlu")


def test_attach_annotations_refuses_double_attachment(tmp_path: Path):
    snapshot_dpath = _snapshot(tmp_path, build_xstest_source_run)
    state = snap.load_snapshot_scenario_state(snapshot_dpath)
    annotations = snap.load_official_annotations(snapshot_dpath)
    attached = metric_replay.attach_annotations(state, annotations)
    assert all(rs.annotations is not None for rs in attached.request_states)
    with pytest.raises(metric_replay.ReplayError, match="already carries"):
        metric_replay.attach_annotations(attached, annotations)


def test_attach_annotations_requires_every_key(tmp_path: Path):
    snapshot_dpath = _snapshot(tmp_path, build_xstest_source_run)
    state = snap.load_snapshot_scenario_state(snapshot_dpath)
    annotations = snap.load_official_annotations(snapshot_dpath)
    key = next(iter(annotations))
    del annotations[key]
    with pytest.raises(metric_replay.ReplayError, match="no official annotations"):
        metric_replay.attach_annotations(state, annotations)

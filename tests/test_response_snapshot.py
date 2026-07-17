"""Commit 2 (open-judge-plan §7): immutable response snapshots.

Proves the §7.6 identity properties: hash stability across rebuilds and
source relocation, sensitivity to candidate content, insensitivity to
original annotations, and the DONE-last atomicity contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from judging_fixture_lib import build_xstest_source_run, write_json

from eval_audit.judging import response_snapshot as snap
from eval_audit.judging.display_keys import DisplayKey


def _build(tmp_path: Path, name: str = "src", **kwargs):
    run_dpath = tmp_path / name
    artifacts = build_xstest_source_run(run_dpath, **kwargs)
    return run_dpath, artifacts


def test_same_source_builds_same_hash_and_caches(tmp_path: Path):
    run_dpath, _ = _build(tmp_path)
    root = tmp_path / "snapshots"
    first = snap.build_response_snapshot(run_dpath, root)
    second = snap.build_response_snapshot(run_dpath, root)
    assert first.response_set_hash == second.response_set_hash
    assert not first.cache_hit
    assert second.cache_hit
    assert first.snapshot_dpath == root / first.response_set_hash
    assert (first.snapshot_dpath / snap.DONE_FNAME).is_file()
    # Snapshot content is byte-stable across rebuilds into fresh roots.
    other_root = tmp_path / "snapshots2"
    third = snap.build_response_snapshot(run_dpath, other_root)
    a = (first.snapshot_dpath / "response_scenario_state.json").read_bytes()
    b = (third.snapshot_dpath / "response_scenario_state.json").read_bytes()
    assert a == b


def test_relocating_the_source_does_not_change_the_hash(tmp_path: Path):
    run_dpath, artifacts = _build(tmp_path, "location_a")
    moved_dpath = tmp_path / "totally" / "different" / "location_b"
    moved_dpath.parent.mkdir(parents=True)
    for fname, obj in artifacts.items():
        write_json(moved_dpath / fname, obj)
    hash_a = snap.build_response_snapshot(run_dpath, tmp_path / "r1").response_set_hash
    hash_b = snap.build_response_snapshot(moved_dpath, tmp_path / "r2").response_set_hash
    assert hash_a == hash_b


def test_modifying_one_candidate_character_changes_the_hash(tmp_path: Path):
    run_dpath, artifacts = _build(tmp_path)
    baseline = snap.build_response_snapshot(run_dpath, tmp_path / "r1").response_set_hash
    predictions = artifacts["display_predictions.json"]
    predictions[0]["predicted_text"] = predictions[0]["predicted_text"] + "!"
    write_json(run_dpath / "display_predictions.json", predictions)
    changed = snap.build_response_snapshot(run_dpath, tmp_path / "r2").response_set_hash
    assert baseline != changed


def test_modifying_only_official_annotations_keeps_the_hash(tmp_path: Path):
    run_dpath, artifacts = _build(tmp_path)
    baseline = snap.build_response_snapshot(run_dpath, tmp_path / "r1").response_set_hash
    predictions = artifacts["display_predictions.json"]
    predictions[0]["annotations"]["xstest"]["gpt_score"] = 0.0
    predictions[0]["annotations"]["xstest"]["gpt_reasoning"] = "different"
    write_json(run_dpath / "display_predictions.json", predictions)
    changed = snap.build_response_snapshot(run_dpath, tmp_path / "r2").response_set_hash
    assert baseline == changed


def test_unsupported_source_is_refused(tmp_path: Path):
    run_dpath, _ = _build(tmp_path)
    (run_dpath / "per_instance_stats.json").unlink()
    with pytest.raises(snap.SnapshotBuildError, match="not supported"):
        snap.build_response_snapshot(run_dpath, tmp_path / "snapshots")


def test_injected_midwrite_failure_leaves_no_done(tmp_path: Path, monkeypatch):
    run_dpath, _ = _build(tmp_path)
    root = tmp_path / "snapshots"

    def boom(*args, **kwargs):
        raise OSError("injected mid-write failure")

    monkeypatch.setattr(snap, "_validate_snapshot_files", boom)
    with pytest.raises(OSError, match="injected"):
        snap.build_response_snapshot(run_dpath, root)
    # No DONE anywhere under the snapshot root; a later build succeeds.
    assert not list(root.rglob(snap.DONE_FNAME))
    monkeypatch.undo()
    result = snap.build_response_snapshot(run_dpath, root)
    assert not result.cache_hit
    assert (result.snapshot_dpath / snap.DONE_FNAME).is_file()


def test_partial_directory_without_done_is_not_a_cache_hit(tmp_path: Path):
    run_dpath, _ = _build(tmp_path)
    root = tmp_path / "snapshots"
    result = snap.build_response_snapshot(run_dpath, root)
    (result.snapshot_dpath / snap.DONE_FNAME).unlink()
    rebuilt = snap.build_response_snapshot(run_dpath, root)
    assert not rebuilt.cache_hit
    assert (rebuilt.snapshot_dpath / snap.DONE_FNAME).is_file()


def test_verify_snapshot_detects_tampering(tmp_path: Path):
    run_dpath, artifacts = _build(tmp_path)
    result = snap.build_response_snapshot(run_dpath, tmp_path / "snapshots")
    assert snap.verify_snapshot(result.snapshot_dpath) == result.response_set_hash
    predictions = artifacts["display_predictions.json"]
    predictions[0]["predicted_text"] = "tampered"
    write_json(result.snapshot_dpath / "display_predictions.json", predictions)
    with pytest.raises(snap.SnapshotBuildError, match="identity mismatch"):
        snap.verify_snapshot(result.snapshot_dpath)


def test_reconstructed_state_is_judge_neutral_and_complete(tmp_path: Path):
    run_dpath, artifacts = _build(tmp_path)
    result = snap.build_response_snapshot(run_dpath, tmp_path / "snapshots")
    state = snap.load_snapshot_scenario_state(result.snapshot_dpath)
    assert state.annotator_specs is None
    assert len(state.request_states) == 3
    for request_state, prediction in zip(
        state.request_states, artifacts["display_predictions.json"]
    ):
        assert request_state.annotations is None
        assert request_state.result is not None and request_state.result.success
        completions = request_state.result.completions
        assert len(completions) == 1
        assert completions[0].text == prediction["predicted_text"]
        assert request_state.instance.id == prediction["instance_id"]
    # Reconstruction defaults (§7.3).
    assert all(rs.request_mode is None for rs in state.request_states)
    assert all(rs.output_mapping is None for rs in state.request_states)
    assert all(rs.num_train_instances == 0 for rs in state.request_states)


def test_official_annotations_are_detached_and_loadable(tmp_path: Path):
    run_dpath, artifacts = _build(tmp_path)
    result = snap.build_response_snapshot(run_dpath, tmp_path / "snapshots")
    annotations = snap.load_official_annotations(result.snapshot_dpath)
    assert len(annotations) == 3
    key = DisplayKey(instance_id="id0", perturbation=None, train_trial_index=0)
    assert annotations[key]["xstest"]["gpt_score"] == 1.0
    # And they are NOT attached to the scenario state file.
    raw = (result.snapshot_dpath / "response_scenario_state.json").read_text()
    assert "gpt_score" not in raw


def test_manifest_contract(tmp_path: Path):
    run_dpath, _ = _build(tmp_path)
    result = snap.build_response_snapshot(run_dpath, tmp_path / "snapshots")
    manifest = snap.load_snapshot_manifest(result.snapshot_dpath)
    assert manifest["artifact_type"] == "helm_response_snapshot"
    assert manifest["schema_version"] == 1
    assert manifest["reconstruction_scope"] == "annotation_only"
    assert manifest["candidate_inference_reused"] is True
    assert manifest["response_set_hash"] == result.response_set_hash
    assert manifest["num_request_states"] == 3
    assert manifest["supported_benchmark"] == "xstest"
    assert set(manifest["source_artifact_hashes"]) == {
        "run_spec.json",
        "instances.json",
        "display_requests.json",
        "display_predictions.json",
        "stats.json",
        "per_instance_stats.json",
    }
    json.dumps(manifest)

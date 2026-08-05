"""Content digests of the artifacts a reported number is computed from."""
from __future__ import annotations

import json
from pathlib import Path

from eval_audit.normalized.digests import (
    COMPARISON_SPEC,
    DIGEST_SPEC,
    comparison_digest,
    component_digest,
    file_sha256,
)

CODE = {"git_sha": "abc123", "eval_audit_version": "0.0.0"}
THRESHOLDS = [0.0, 0.01, 0.1]


def _helm_run(root: Path, *, scores: str = "1.0", completions: str = "hello") -> dict:
    run_dpath = root / "runs" / "bench:model=m"
    run_dpath.mkdir(parents=True, exist_ok=True)
    (run_dpath / "run_spec.json").write_text(json.dumps({"name": "bench:model=m"}))
    (run_dpath / "stats.json").write_text(json.dumps([{"exact_match": scores}]))
    (run_dpath / "per_instance_stats.json").write_text(json.dumps([{"id": "id1", "v": scores}]))
    (run_dpath / "scenario_state.json").write_text(json.dumps({"text": completions}))
    return {
        "component_id": "local::exp::job",
        "artifact_format": "helm",
        "run_path": str(run_dpath),
    }


def _eee_artifact(root: Path, *, scores: str = "1.0") -> dict:
    artifact_dpath = root / "eee" / "bench" / "org" / "model"
    artifact_dpath.mkdir(parents=True, exist_ok=True)
    (artifact_dpath / "uuid.json").write_text(json.dumps({"score": scores}))
    (artifact_dpath / "uuid_samples.jsonl").write_text(json.dumps({"id": "id1"}) + "\n")
    return {
        "component_id": "official::eee::x",
        "artifact_format": "eee",
        "eee_artifact_path": str(artifact_dpath),
    }


def test_helm_component_digests_scores_and_completions_separately(tmp_path: Path) -> None:
    """Split so a re-conversion touching completions cannot invalidate a score claim."""
    record = component_digest(_helm_run(tmp_path))
    assert record["status"] == "ok"
    assert record["spec"] == DIGEST_SPEC
    assert record["scores"] and record["completions"]
    assert record["scores"] != record["completions"]
    assert {entry["name"] for entry in record["files"]} == {
        "run_spec.json",
        "stats.json",
        "per_instance_stats.json",
        "scenario_state.json",
    }


def test_changing_a_score_file_changes_the_scores_digest_only(tmp_path: Path) -> None:
    before = component_digest(_helm_run(tmp_path))
    after = component_digest(_helm_run(tmp_path, scores="0.5"))
    assert after["scores"] != before["scores"]
    assert after["completions"] == before["completions"]


def test_changing_completions_leaves_the_scores_digest_alone(tmp_path: Path) -> None:
    before = component_digest(_helm_run(tmp_path))
    after = component_digest(_helm_run(tmp_path, completions="different"))
    assert after["scores"] == before["scores"]
    assert after["completions"] != before["completions"]


def test_unrelated_sibling_files_do_not_move_the_digest(tmp_path: Path) -> None:
    """Hashing the run *directory* would churn on logs; hash the named inputs."""
    component = _helm_run(tmp_path)
    before = component_digest(component)
    (Path(component["run_path"]) / "helm-run.log").write_text("noisy log line\n")
    assert component_digest(component)["scores"] == before["scores"]


def test_recomputing_the_same_artifacts_is_stable(tmp_path: Path) -> None:
    component = _helm_run(tmp_path)
    assert component_digest(component)["scores"] == component_digest(component)["scores"]


def test_pruned_artifacts_report_missing_rather_than_raising(tmp_path: Path) -> None:
    """The absence of a run is the finding, not an error that kills the render."""
    record = component_digest({"component_id": "x", "artifact_format": "helm", "run_path": str(tmp_path / "gone")})
    assert record["status"] == "missing"
    assert record["scores"] is None


def test_partial_artifacts_are_distinguished_from_complete_ones(tmp_path: Path) -> None:
    component = _helm_run(tmp_path)
    (Path(component["run_path"]) / "per_instance_stats.json").unlink()
    record = component_digest(component)
    assert record["status"] == "partial"
    assert record["missing_files"] == ["per_instance_stats.json"]
    assert record["scores"] is not None


def test_component_without_a_path_is_missing_not_a_crash() -> None:
    assert component_digest({"component_id": "x", "artifact_format": "helm"})["status"] == "missing"


def test_eee_component_hashes_its_artifact_tree(tmp_path: Path) -> None:
    """EEE keeps per-instance rows in the samples file; there is no separate
    completions half the way raw HELM has scenario_state.json."""
    record = component_digest(_eee_artifact(tmp_path))
    assert record["status"] == "ok"
    assert record["scores"] is not None
    assert record["completions"] is None
    assert {entry["name"] for entry in record["files"]} == {"uuid.json", "uuid_samples.jsonl"}


def test_eee_digest_moves_when_its_scores_move(tmp_path: Path) -> None:
    before = component_digest(_eee_artifact(tmp_path))
    after = component_digest(_eee_artifact(tmp_path, scores="0.5"))
    assert after["scores"] != before["scores"]


def test_comparison_digest_covers_both_sides(tmp_path: Path) -> None:
    official = _eee_artifact(tmp_path)
    local = _helm_run(tmp_path)
    digests = {c["component_id"]: component_digest(c) for c in (official, local)}
    ids = [official["component_id"], local["component_id"]]

    record = comparison_digest(ids, digests, thresholds=THRESHOLDS, code=CODE)
    assert record["spec"] == COMPARISON_SPEC
    assert record["status"] == "ok"

    moved = dict(digests)
    moved[local["component_id"]] = component_digest(_helm_run(tmp_path, scores="0.5"))
    assert comparison_digest(ids, moved, thresholds=THRESHOLDS, code=CODE)["digest"] != record["digest"]


def test_comparison_digest_changes_with_the_code_that_computed_it(tmp_path: Path) -> None:
    """Same inputs through changed code give a different answer; a digest that
    ignored the code would certify a number it cannot reproduce."""
    local = _helm_run(tmp_path)
    digests = {local["component_id"]: component_digest(local)}
    ids = [local["component_id"]]
    baseline = comparison_digest(ids, digests, thresholds=THRESHOLDS, code=CODE)
    other_code = comparison_digest(
        ids, digests, thresholds=THRESHOLDS, code={**CODE, "git_sha": "def456"}
    )
    assert other_code["digest"] != baseline["digest"]


def test_comparison_digest_changes_with_the_tolerance_grid(tmp_path: Path) -> None:
    local = _helm_run(tmp_path)
    digests = {local["component_id"]: component_digest(local)}
    ids = [local["component_id"]]
    baseline = comparison_digest(ids, digests, thresholds=THRESHOLDS, code=CODE)
    assert comparison_digest(ids, digests, thresholds=[0.0], code=CODE)["digest"] != baseline["digest"]


def test_comparison_digest_is_incomplete_when_a_side_is_unhashable(tmp_path: Path) -> None:
    local = _helm_run(tmp_path)
    digests = {local["component_id"]: component_digest(local)}
    record = comparison_digest(
        [local["component_id"], "official::gone"], digests, thresholds=THRESHOLDS, code=CODE
    )
    assert record["status"] == "incomplete"
    assert record["digest"]


def test_file_sha256_reports_size_and_tolerates_absence(tmp_path: Path) -> None:
    fpath = tmp_path / "f.json"
    fpath.write_text("abc")
    sha, size = file_sha256(fpath)
    assert size == 3
    assert len(sha) == 64
    assert file_sha256(tmp_path / "nope.json") is None

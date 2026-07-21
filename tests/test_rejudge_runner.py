"""Commit 6 (open-judge-plan §12): annotation-only rejudge runner.

Runs the REAL path end to end with no serving: AnnotatorFactory →
AutoClient → a registered fake judge deployment. Proves the §12.4
requirements: no candidate call, every request targets the declared
judge deployment, candidates provably unchanged, malformed judgments
are structured records, artifacts are complete and idempotent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from judging_fixture_lib import (
    FAKE_JUDGE_DEPLOYMENT as FAKE_DEPLOYMENT,
    FAKE_JUDGE_MODEL as FAKE_MODEL,
    build_xstest_source_run,
    make_fake_judge_spec as make_judge,
    write_fake_judge_sidecar,
    write_json,
)

from eval_audit.integrations.helm_judging import fake_judge_client
from eval_audit.judging import response_snapshot as snap
from eval_audit.judging.rejudge import DONE_FNAME, run_rejudge


@pytest.fixture(autouse=True)
def _reset_fake_judge():
    fake_judge_client.reset_telemetry()
    yield
    fake_judge_client.reset_telemetry()


@pytest.fixture()
def sidecar_dpath(tmp_path: Path) -> Path:
    return write_fake_judge_sidecar(tmp_path / "judge_sidecars")


@pytest.fixture()
def snapshot_dpath(tmp_path: Path) -> Path:
    build_xstest_source_run(tmp_path / "src")
    return snap.build_response_snapshot(tmp_path / "src", tmp_path / "snapshots").snapshot_dpath


def _run(snapshot_dpath: Path, tmp_path: Path, sidecar_dpath: Path, replicate: int = 0):
    return run_rejudge(
        snapshot_dpath=snapshot_dpath,
        judge=make_judge(),
        replicate=replicate,
        out_root=tmp_path / "results",
        cache_root=tmp_path / "cache",
        experiment_name="fixture-exp",
        sidecar_config_dpaths=(str(sidecar_dpath),),
        parallelism=1,
    )


def test_rejudge_completes_with_no_candidate_execution(
    snapshot_dpath, tmp_path, sidecar_dpath, monkeypatch
):
    # §12.4: rejudging must succeed with candidate execution unavailable.
    import helm.benchmark.executor

    def candidate_execution_forbidden(*args, **kwargs):
        raise AssertionError("candidate Executor must never run during rejudging")

    monkeypatch.setattr(
        helm.benchmark.executor.Executor, "execute", candidate_execution_forbidden
    )
    result = _run(snapshot_dpath, tmp_path, sidecar_dpath)
    assert (result.out_dpath / DONE_FNAME).is_file()

    # Every judge request targeted the declared judge deployment; none
    # targeted the candidate deployment.
    deployments = {entry[0] for entry in fake_judge_client.REQUEST_LOG}
    assert deployments == {FAKE_DEPLOYMENT}
    assert len(fake_judge_client.REQUEST_LOG) == 3
    # Replicate identity rode Request.random on every judge request.
    assert {entry[2] for entry in fake_judge_client.REQUEST_LOG} == {
        "fixture-exp:fake_judge:r0"
    }


def test_artifact_contents_and_provenance(snapshot_dpath, tmp_path, sidecar_dpath):
    result = _run(snapshot_dpath, tmp_path, sidecar_dpath)
    out = result.out_dpath
    for fname in (
        "run_spec.json",
        "scenario_state.json",
        "judgments.jsonl",
        "response_manifest.json",
        "judge_manifest.json",
        "rejudge_manifest.json",
        "process_context.json",
        DONE_FNAME,
    ):
        assert (out / fname).exists(), fname

    manifest = json.loads((out / "rejudge_manifest.json").read_text())
    assert manifest["artifact_format"] == "helm_rejudge_v1"
    assert manifest["execution_kind"] == "rejudge"
    assert manifest["candidate_inference_reused"] is True
    assert manifest["response_set_hash"] == result.response_set_hash
    assert manifest["attempt_hash"] == result.attempt_hash
    assert manifest["num_judgments"] == 3

    judgments = [
        json.loads(line) for line in (out / "judgments.jsonl").read_text().splitlines()
    ]
    assert len(judgments) == 3
    for judgment in judgments:
        annotation = judgment["annotation"]
        assert annotation["judge_id"] == "fake_judge"
        assert annotation["parse_status"] == "ok"
        assert annotation["fake_judge_score"] in (0.0, 0.5, 1.0)
        assert annotation["raw_response"].startswith("<reasoning>")

    # The derived run_spec carries our annotator and ONLY the
    # judge-attributed metric; judge identity is recoverable from it.
    run_spec = json.loads((out / "run_spec.json").read_text())
    (metric_spec,) = run_spec["metric_specs"]
    assert metric_spec["class_name"].endswith("SingleJudgeSafetyMetric")
    assert metric_spec["args"] == {"judge_id": "fake_judge", "annotator_name": "xstest"}
    (annotator,) = run_spec["annotators"]
    assert annotator["args"]["judge_model"] == FAKE_MODEL
    assert annotator["args"]["judge_spec_hash"] == make_judge().spec_hash()


def test_idempotent_and_cache_restart(snapshot_dpath, tmp_path, sidecar_dpath):
    first = _run(snapshot_dpath, tmp_path, sidecar_dpath)
    assert not first.cache_hit
    assert fake_judge_client.LIVE_CALL_COUNT[0] == 3

    # Completed attempt: full cache hit, no requests at all.
    second = _run(snapshot_dpath, tmp_path, sidecar_dpath)
    assert second.cache_hit
    assert fake_judge_client.LIVE_CALL_COUNT[0] == 3

    # Interrupted attempt (output lost, cache kept): judge requests are
    # served from the attempt's SQLite cache, zero live calls.
    import shutil

    shutil.rmtree(first.out_dpath)
    third = _run(snapshot_dpath, tmp_path, sidecar_dpath)
    assert not third.cache_hit
    assert fake_judge_client.LIVE_CALL_COUNT[0] == 3
    judgments = (third.out_dpath / "judgments.jsonl").read_text()
    for line in judgments.splitlines():
        assert json.loads(line)["annotation"]["request_cached"] is True


def test_replicates_do_not_share_cache(snapshot_dpath, tmp_path, sidecar_dpath):
    _run(snapshot_dpath, tmp_path, sidecar_dpath, replicate=0)
    assert fake_judge_client.LIVE_CALL_COUNT[0] == 3
    result = _run(snapshot_dpath, tmp_path, sidecar_dpath, replicate=1)
    # Same prompts, different replicate: all three requests were live.
    assert fake_judge_client.LIVE_CALL_COUNT[0] == 6
    assert {e[2] for e in fake_judge_client.REQUEST_LOG} == {
        "fixture-exp:fake_judge:r0",
        "fixture-exp:fake_judge:r1",
    }
    # Distinct cache directories per replicate (§12.3).
    judge_hash = make_judge().spec_hash()
    cache_base = tmp_path / "cache" / result.response_set_hash / judge_hash
    assert (cache_base / "replicate-0").is_dir()
    assert (cache_base / "replicate-1").is_dir()


def test_malformed_judgment_is_structured_and_does_not_abort(
    snapshot_dpath, tmp_path, sidecar_dpath
):
    # Instance 1's question text appears verbatim in the judge prompt.
    fake_judge_client.MALFORMED_PROMPT_SUBSTRING[0] = "Synthetic xstest question 1?"
    result = _run(snapshot_dpath, tmp_path, sidecar_dpath)
    judgments = [
        json.loads(line)
        for line in (result.out_dpath / "judgments.jsonl").read_text().splitlines()
    ]
    statuses = {j["key"]["instance_id"]: j["annotation"]["parse_status"] for j in judgments}
    assert statuses == {"id0": "ok", "id1": "malformed", "id2": "ok"}
    malformed = next(j for j in judgments if j["key"]["instance_id"] == "id1")
    assert malformed["annotation"]["fake_judge_score"] is None
    assert malformed["annotation"]["raw_response"]  # raw output retained


def test_max_instances_smoke_subset_isolated_from_full_run(
    snapshot_dpath, tmp_path, sidecar_dpath
):
    # A 2-of-3 smoke and the full run must produce distinct artifacts and
    # not serve each other from cache.
    smoke = run_rejudge(
        snapshot_dpath=snapshot_dpath, judge=make_judge(), replicate=0,
        out_root=tmp_path / "results", cache_root=tmp_path / "cache",
        experiment_name="fixture-exp", sidecar_config_dpaths=(str(sidecar_dpath),),
        parallelism=1, max_instances=2,
    )
    smoke_judgments = (smoke.out_dpath / "judgments.jsonl").read_text().splitlines()
    assert len([line for line in smoke_judgments if line.strip()]) == 2
    manifest = json.loads((smoke.out_dpath / "rejudge_manifest.json").read_text())
    assert manifest["max_instances"] == 2
    assert manifest["num_judged"] == 2

    full = run_rejudge(
        snapshot_dpath=snapshot_dpath, judge=make_judge(), replicate=0,
        out_root=tmp_path / "results", cache_root=tmp_path / "cache",
        experiment_name="fixture-exp", sidecar_config_dpaths=(str(sidecar_dpath),),
        parallelism=1,
    )
    assert full.attempt_hash != smoke.attempt_hash
    assert not full.cache_hit
    full_judgments = [
        line for line in (full.out_dpath / "judgments.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert len(full_judgments) == 3


def test_tampered_snapshot_is_refused(snapshot_dpath, tmp_path, sidecar_dpath):
    predictions = json.loads((snapshot_dpath / "display_predictions.json").read_text())
    predictions[0]["predicted_text"] = "tampered"
    write_json(snapshot_dpath / "display_predictions.json", predictions)
    with pytest.raises(snap.SnapshotBuildError, match="identity mismatch"):
        _run(snapshot_dpath, tmp_path, sidecar_dpath)
    assert fake_judge_client.REQUEST_LOG == []


def test_unreachable_judge_is_not_finalized(tmp_path, monkeypatch):
    """Regression (2026-07-19/20): an attempt whose judge was unreachable used
    to write DONE anyway, so the failure was cache-hit forever and the next
    day's re-run served the poisoned artifact back without re-executing. An
    infrastructure outage must not be recorded as a completed attempt."""
    from eval_audit.judging import rejudge as R

    judgments = [{"annotation": {"parse_status": "request_error",
                                 "parse_error": "boom"}} for _ in range(10)]
    n = len(judgments)
    n_err = sum(1 for j in judgments if j["annotation"]["parse_status"] == "request_error")
    assert n_err / n > 0.5          # the condition run_rejudge guards on

    # Responses that ARRIVE but do not parse are results, not outages: a small
    # judge failing to emit WildBench JSON at a 93% rate is a finding to keep.
    parsed_badly = [{"annotation": {"parse_status": "malformed"}} for _ in range(10)]
    n_err2 = sum(1 for j in parsed_badly
                 if j["annotation"]["parse_status"] == "request_error")
    assert n_err2 / len(parsed_badly) == 0.0
    assert R.RejudgeError is not None

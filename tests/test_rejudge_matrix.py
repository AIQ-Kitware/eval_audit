"""Commit 11 (open-judge-plan): the rejudge job matrix for kwdagger fan-out.

The planner is deliberately free of kwdagger/HELM imports so the fan-out logic
is testable without a scheduler or a GPU. What matters here: every scope knob
resolves in the documented precedence, jobs are grouped by judge (so weights
stay resident), and an edited judge config yields new job identity.
"""

from __future__ import annotations

import json

import pytest

from eval_audit.judging.rejudge_matrix import (
    JudgeArm,
    RejudgeMatrixError,
    RejudgeMatrixSpec,
    build_rejudge_matrix,
    load_judge_arm,
    summarize_matrix,
)


def _arm(judge_id: str, spec_hash: str = "h0") -> JudgeArm:
    return JudgeArm(
        judge_id=judge_id,
        judge_json=f"/cfg/{judge_id}.json",
        lease_endpoint=f"{judge_id}-endpoint",
        spec_hash=spec_hash,
    )


def _spec(**overrides) -> RejudgeMatrixSpec:
    base = dict(
        snapshots={"xstest": "/snap/xstest", "wildbench": "/snap/wildbench"},
        judges=[_arm("small"), _arm("large")],
        out_root="/store/results",
        cache_root="/store/cache",
        sidecar_config="/store/sidecars",
    )
    base.update(overrides)
    return RejudgeMatrixSpec(**base)


def test_matrix_is_full_cross_product_by_default():
    rows = build_rejudge_matrix(_spec())
    # 2 judges x 2 benchmarks x 3 default replicates
    assert len(rows) == 12
    assert {r["_judge_id"] for r in rows} == {"small", "large"}
    assert {r["replicate"] for r in rows} == {0, 1, 2}


def test_rows_are_grouped_by_judge_to_keep_weights_resident():
    """Interleaving judges would tear down and reload multi-GiB weights under a
    reclaim:stop endpoint; contiguity keeps infer-stack demand above zero."""
    rows = build_rejudge_matrix(_spec())
    order = [r["_judge_id"] for r in rows]
    # Every judge occupies one contiguous block, in the declared order.
    assert order == ["small"] * 6 + ["large"] * 6


def test_replicate_precedence_pair_then_benchmark_then_default():
    spec = _spec(
        replicates_by_benchmark={"wildbench": [0]},
        replicates_by_pair={("wildbench", "large"): [0, 1]},
    )
    rows = build_rejudge_matrix(spec)
    reps = lambda b, j: sorted(  # noqa: E731
        r["replicate"] for r in rows if r["_benchmark"] == b and r["_judge_id"] == j
    )
    assert reps("xstest", "small") == [0, 1, 2]   # default
    assert reps("wildbench", "small") == [0]      # per-benchmark
    assert reps("wildbench", "large") == [0, 1]   # per-pair wins


def test_empty_replicates_skips_a_pair():
    spec = _spec(replicates_by_pair={("wildbench", "small"): []})
    rows = build_rejudge_matrix(spec)
    assert not [r for r in rows if r["_benchmark"] == "wildbench" and r["_judge_id"] == "small"]
    assert [r for r in rows if r["_benchmark"] == "xstest" and r["_judge_id"] == "small"]


def test_judge_spec_hash_rides_as_job_identity():
    """A path alone would not change when the JSON is edited; the content hash
    must reach the row so kwdagger allocates a new job folder."""
    a = build_rejudge_matrix(_spec(judges=[_arm("j", spec_hash="hash_a")]))
    b = build_rejudge_matrix(_spec(judges=[_arm("j", spec_hash="hash_b")]))
    assert a[0]["judge_spec_hash"] != b[0]["judge_spec_hash"]
    assert a[0]["snapshot"] == b[0]["snapshot"]  # everything else identical


def test_each_row_carries_its_lease_endpoint():
    rows = build_rejudge_matrix(_spec())
    for row in rows:
        assert row["lease_endpoint"] == f"{row['_judge_id']}-endpoint"


def test_rejects_empty_and_duplicate_and_bad_input():
    with pytest.raises(RejudgeMatrixError, match="no judges"):
        build_rejudge_matrix(_spec(judges=[]))
    with pytest.raises(RejudgeMatrixError, match="no benchmark snapshots"):
        build_rejudge_matrix(_spec(snapshots={}))
    with pytest.raises(RejudgeMatrixError, match="duplicate judge_id"):
        build_rejudge_matrix(_spec(judges=[_arm("dup"), _arm("dup")]))
    with pytest.raises(RejudgeMatrixError, match="every .* pair declared zero"):
        build_rejudge_matrix(_spec(replicates_by_benchmark={"xstest": [], "wildbench": []}))
    with pytest.raises(RejudgeMatrixError, match="nonempty string"):
        JudgeArm(judge_id="", judge_json="j", lease_endpoint="e", spec_hash="h")


def test_replicate_must_be_a_nonnegative_int():
    with pytest.raises(RejudgeMatrixError, match="int >= 0"):
        build_rejudge_matrix(_spec(replicates_by_benchmark={"xstest": [-1], "wildbench": [0]}))


def test_load_judge_arm_reads_spec_and_derives_hash(tmp_path):
    fields = {
        "id": "qwen3_5_9b",
        "model": "qwen/qwen3.5-9b",
        "model_deployment": "litellm/qwen3.5-9b-judge",
        "lease_endpoint": "qwen3.5-9b-judge",
        "parser_version": "official-v1+strip-think",
        "prompt_version": "official-v1",
        "thinking_mode": "server_default",
        "client_class": "eval_audit.integrations.helm_clients.NullSafeOpenAIChatClient",
        "temperature": None,
        "max_tokens": None,
        "reasoning_headroom_tokens": 4096,
        # A stale derived value in the file must be ignored, never trusted.
        "judge_spec_hash": "STALE",
    }
    fpath = tmp_path / "j.json"
    fpath.write_text(json.dumps(fields))

    arm = load_judge_arm(fpath)
    assert arm.judge_id == "qwen3_5_9b"
    assert arm.lease_endpoint == "qwen3.5-9b-judge"
    assert arm.spec_hash and arm.spec_hash != "STALE"

    # An explicit endpoint overrides the spec's own.
    assert load_judge_arm(fpath, lease_endpoint="other").lease_endpoint == "other"


def test_summary_reports_the_fan_out_size():
    text = summarize_matrix(build_rejudge_matrix(_spec()))
    assert "12 job(s)" in text
    assert "small" in text and "wildbench" in text


def test_sidecar_dirs_are_private_per_judge():
    """Regression (2026-07-19): export_judge_bundle writes a
    model_deployments.yaml containing only the judges it was given, so two arms
    sharing one sidecar directory clobber each other's registration. The loser's
    deployment vanishes, HELM falls back to the 'litellm/' name prefix, and every
    request dies with OptionalDependencyNotInstalled -- 14 of one arm's 15
    attempts were destroyed this way while still exiting 0."""
    rows = build_rejudge_matrix(_spec())
    by_judge = {r["_judge_id"]: r["sidecar_config"] for r in rows}
    assert len(set(by_judge.values())) == len(by_judge), by_judge
    for judge_id, path in by_judge.items():
        assert path.endswith(f"/{judge_id}")
        assert path != "/store/sidecars"  # never the shared root

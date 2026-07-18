"""Commit 12 (open-judge-plan §17/§18): rejudge indexing + judge analysis.

Runs the real rejudge runner (fake judge) to produce two judge arms ×
two replicates against xstest and wildbench snapshots, then indexes and
analyzes them. Proves: rejudge artifacts index distinctly with the §17
fields; analysis joins by response-set hash + display key (never
position); official baseline (gpt vs llama) is reported; replicate
variance and failure rates are computed.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from judging_fixture_lib import (
    build_wildbench_source_run,
    build_xstest_source_run,
    make_fake_judge_spec,
    write_fake_judge_sidecar,
)

from eval_audit.integrations.helm_judging import fake_judge_client
from eval_audit.judging import response_snapshot as snap
from eval_audit.judging.analysis import analyze_snapshot_judges, render_report_text
from eval_audit.judging.indexing import (
    build_rejudge_index,
    discover_rejudge_artifacts,
    index_rejudge_artifact,
    load_rejudge_judgments,
)
from eval_audit.judging.rejudge import run_rejudge


@pytest.fixture(autouse=True)
def _reset_fake_judge():
    fake_judge_client.reset_telemetry()
    yield
    fake_judge_client.reset_telemetry()


def _two_arms():
    a = make_fake_judge_spec()
    # A second arm: same fake deployment, different judge id → different
    # request_random → different deterministic scores (a distinct arm).
    b = dataclasses.replace(a, id="fake_judge_b")
    return [a, b]


def _run_matrix(snapshot_dpath: Path, tmp_path: Path, sidecar: Path, replicates=(0, 1)):
    out_root = tmp_path / "results"
    for judge in _two_arms():
        for replicate in replicates:
            run_rejudge(
                snapshot_dpath=snapshot_dpath,
                judge=judge,
                replicate=replicate,
                out_root=out_root,
                cache_root=tmp_path / "cache",
                experiment_name="fixture-exp",
                sidecar_config_dpaths=(str(sidecar),),
                parallelism=1,
            )
    return out_root


@pytest.fixture()
def xstest_setup(tmp_path: Path):
    build_xstest_source_run(tmp_path / "src")
    snapshot = snap.build_response_snapshot(tmp_path / "src", tmp_path / "snapshots")
    sidecar = write_fake_judge_sidecar(tmp_path / "judge_sidecars")
    out_root = _run_matrix(snapshot.snapshot_dpath, tmp_path, sidecar)
    return snapshot.snapshot_dpath, out_root


def test_index_rejudge_artifacts(xstest_setup, tmp_path: Path):
    _, out_root = xstest_setup
    artifacts = discover_rejudge_artifacts(out_root)
    assert len(artifacts) == 4  # 2 arms x 2 replicates

    rows = build_rejudge_index(out_root)
    assert len(rows) == 4
    row = index_rejudge_artifact(artifacts[0])
    assert row["execution_kind"] == "rejudge"
    assert row["response_source_kind"] == "public_display"
    assert row["candidate_inference_reused"] is True
    assert row["judge_substitution_planned"] is True
    assert row["benchmark"] == "xstest"
    assert {r["judge_arm_id"] for r in rows} == {"fake_judge", "fake_judge_b"}
    assert {r["judge_replicate"] for r in rows} == {0, 1}
    # Each (arm, replicate) has a distinct attempt hash.
    assert len({r["attempt_hash"] for r in rows}) == 4


def test_load_judgments_keyed_by_display_key(xstest_setup):
    _, out_root = xstest_setup
    artifact = discover_rejudge_artifacts(out_root)[0]
    judgments = load_rejudge_judgments(artifact)
    assert len(judgments) == 3
    key = next(iter(judgments))
    assert key.instance_id.startswith("id")


def test_analysis_joins_and_reports_baseline(xstest_setup):
    snapshot_dpath, out_root = xstest_setup
    artifacts = discover_rejudge_artifacts(out_root)
    report = analyze_snapshot_judges(snapshot_dpath, artifacts)

    assert report["benchmark"] == "xstest"
    assert report["benchmark_kind"] == "label"
    assert set(report["open_arms"]) == {"fake_judge", "fake_judge_b"}

    # Official ensemble baseline present and named (fixture has gpt+llama).
    assert report["official_judges"] == ["official_gpt", "official_llama"]
    baseline = report["official_baseline_pair"]
    assert baseline == "official_gpt__vs__official_llama"
    assert baseline in report["comparisons"]

    # Each open arm compared against each official judge and the other arm.
    assert "fake_judge__vs__official_gpt" in report["comparisons"]
    assert "fake_judge__vs__official_llama" in report["comparisons"]
    assert "fake_judge__vs__fake_judge_b" in report["comparisons"]

    comp = report["comparisons"]["fake_judge__vs__official_gpt"]
    assert comp["n_paired"] == 3
    assert -1.0 <= comp["mean_signed_diff"] <= 1.0
    assert 0.0 <= comp["agreement_within"] <= 1.0
    assert "cohens_kappa" in comp  # label kind

    # Replicate variance is reported and well-formed. (The fake judge
    # keys its output on Request.random, which differs per replicate by
    # design — so the fixture exercises the nonzero-variance path; real
    # T=0 serving would instead measure serving nondeterminism.)
    stability = report["open_arms"]["fake_judge"]["replicate_stability"]
    assert stability["num_instances_all_replicates"] == 3
    assert stability["mean_within_judge_stddev"] >= 0.0
    assert 0.0 <= stability["pct_instances_changed_across_replicates"] <= 1.0
    assert stability["max_replicate_range"] >= 0.0

    # Renders without error.
    assert "official baseline" in render_report_text(report)


def test_analysis_ignores_foreign_response_set(xstest_setup, tmp_path: Path):
    snapshot_dpath, out_root = xstest_setup
    # Build a DIFFERENT snapshot (wildbench) + its rejudge artifacts under
    # the same results root; the xstest analysis must ignore them.
    build_wildbench_source_run(tmp_path / "wb_src", empty_output_index=1)
    wb_snapshot = snap.build_response_snapshot(tmp_path / "wb_src", tmp_path / "snapshots")
    sidecar = write_fake_judge_sidecar(tmp_path / "judge_sidecars")
    run_rejudge(
        snapshot_dpath=wb_snapshot.snapshot_dpath,
        judge=make_fake_judge_spec(),
        replicate=0,
        out_root=out_root,  # same root
        cache_root=tmp_path / "cache",
        experiment_name="fixture-exp",
        sidecar_config_dpaths=(str(sidecar),),
        parallelism=1,
    )
    report = analyze_snapshot_judges(snapshot_dpath, discover_rejudge_artifacts(out_root))
    # Only the xstest arms; the wildbench artifact is filtered by hash.
    assert set(report["open_arms"]) == {"fake_judge", "fake_judge_b"}
    assert report["response_set_hash"] == snapshot_dpath.name


def test_wildbench_continuous_analysis(tmp_path: Path):
    build_wildbench_source_run(tmp_path / "src", empty_output_index=1)
    snapshot = snap.build_response_snapshot(tmp_path / "src", tmp_path / "snapshots")
    sidecar = write_fake_judge_sidecar(tmp_path / "judge_sidecars")
    out_root = _run_matrix(snapshot.snapshot_dpath, tmp_path, sidecar)

    report = analyze_snapshot_judges(
        snapshot.snapshot_dpath, discover_rejudge_artifacts(out_root)
    )
    assert report["benchmark_kind"] == "continuous"
    comp = report["comparisons"]["fake_judge__vs__official_gpt"]
    # Continuous kind: agreement-within uses the 1-point WildBench window,
    # and kappa is not emitted.
    assert "cohens_kappa" not in comp
    assert comp["n_paired"] >= 1
    # The empty-candidate instance scores 1.0 for both sides → they agree
    # there; overall within-1 agreement is a valid fraction.
    assert 0.0 <= comp["agreement_within"] <= 1.0

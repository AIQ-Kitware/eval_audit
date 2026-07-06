"""Phase 3 / 4.9: judge-substitution comparisons (matrix fixtures F9/F10).

F9 — a local row flagged ``judge_substitution_planned`` whose sidecar
records an open judge, against an official whose sidecar carries the
closed-judge annotator class: the planner declares the substitution,
emits a scoped ``same_judge: no`` fact without a drift warning, and
the rendered pair carries ``intended_substitution:judge`` as the
primary diagnosis label plus the metric-class split.

F10 — the same pair *without* the declaration: no ``same_judge``
fact, no ``substitutions`` key, no substitution label — the judge
difference is invisible at the fact level and surfaces only as
ordinary value drift. Non-extension output stays byte-identical to
the unflagged world (the matrix's 4.9 gate; the committed F3/F4
baseline enforces the global half of that).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from eval_audit.normalized.eee_sources import (
    build_local_index_row,
    build_official_index_row,
    detect_helm_sidecars,
    write_index_csv,
)
from eval_audit.planning.core_report_planner import build_planning_artifact

pytestmark = pytest.mark.slow

REPO_ROOT = Path(__file__).resolve().parents[1]
from conftest import (  # noqa: E402  (shared EEE-demo fixture path + guard)
    EEE_DEMO_ROOT as FIXTURE_ROOT,
    EEE_DEMO_OFFICIAL_DIR as OFFICIAL_DIR,
    EEE_DEMO_LOCAL_DIR as LOCAL_DIR,
    require_eee_demo,
)


def _sidecar(annotators) -> dict:
    return {
        "name": "imdb:model=toy/m1-small,suite=eee_demo",
        "adapter_spec": {
            "model": "toy/m1-small",
            "model_deployment": "huggingface/toy-m1-small",
            "max_eval_instances": 4,
            "instructions": "Predict the sentiment of this review.",
        },
        "scenario_spec": {"class_name": "helm.IMDBScenario"},
        "annotators": annotators,
    }


def _meta(artifact_dir: Path) -> dict:
    json_path = next(
        p for p in sorted(artifact_dir.glob("*.json")) if p.name != "run_spec.json"
    )
    data = json.loads(json_path.read_text())
    sidecars = detect_helm_sidecars(artifact_dir)
    return {
        "artifact_dir": artifact_dir,
        "json_path": json_path,
        "model_id": data["model_info"]["id"],
        "benchmark": "imdb",
        "experiment_name": None,
        "evaluation_id": data.get("evaluation_id"),
        "run_spec_fpath": sidecars["run_spec_fpath"],
        "max_eval_instances": sidecars["max_eval_instances"],
    }


@pytest.fixture
def staged(tmp_path: Path) -> dict:
    """Stage the demo pair with judge-bearing sidecars on both sides."""
    require_eee_demo()
    official_dst = tmp_path / "official"
    local_dst = tmp_path / "local"
    shutil.copytree(OFFICIAL_DIR, official_dst)
    shutil.copytree(LOCAL_DIR, local_dst)
    # Official: closed-judge annotator class (the model is hard-coded in
    # HELM; the curated registry resolves the basename).
    (official_dst / "run_spec.json").write_text(
        json.dumps(_sidecar([{"class_name": "helm.benchmark.annotation.wildbench_annotator.WildBenchAnnotator", "args": {}}]))
    )
    # Local open-judge re-run: judge model recorded explicitly.
    (local_dst / "run_spec.json").write_text(
        json.dumps(_sidecar([{"class_name": "x.OpenJudgeAnnotator", "args": {"judge_model": "meta/llama-3.1-405b-instruct-turbo"}}]))
    )
    return {"official": official_dst, "local": local_dst, "tmp": tmp_path}


def _plan(staged: dict, *, declare: bool) -> dict:
    official_row = build_official_index_row(_meta(staged["official"]))
    local_row = build_local_index_row(_meta(staged["local"]), experiment_override="judge_sub_test")
    if declare:
        local_row["judge_substitution_planned"] = "true"
    indexes = staged["tmp"] / ("idx_declared" if declare else "idx_plain")
    indexes.mkdir()
    official_csv = write_index_csv([official_row], indexes / "official_public_index.csv")
    local_csv = write_index_csv([local_row], indexes / "audit_results_index.csv")
    return build_planning_artifact(
        local_index_fpath=local_csv,
        official_index_fpath=official_csv,
        experiment_name=None,
        run_entry=None,
    )


def _the_comparison(artifact: dict) -> dict:
    packets = artifact.get("packets") or []
    assert len(packets) == 1
    comparisons = packets[0].get("comparisons") or []
    assert comparisons
    return comparisons[0]


# --- planner level -----------------------------------------------------------


def test_f9_planner_declares_substitution_and_scoped_fact(staged):
    comparison = _the_comparison(_plan(staged, declare=True))
    assert comparison.get("substitutions") == ["judge"]
    same_judge = comparison["comparability_facts"]["same_judge"]
    # Facts stay honest: the judges DO differ.
    assert same_judge["status"] == "no"
    # ...but a declared difference is not drift.
    assert "comparability_drift:same_judge" not in comparison["warnings"]
    assert "substitution_not_observed:judge" not in comparison["warnings"]


def test_f10_undeclared_pair_has_no_judge_fact(staged):
    comparison = _the_comparison(_plan(staged, declare=False))
    assert "substitutions" not in comparison
    assert "same_judge" not in comparison["comparability_facts"]
    assert not any("same_judge" in w for w in comparison["warnings"])


def test_substitution_not_observed_when_judges_match(staged):
    # Local declares the substitution but actually reran the official
    # ensemble: declared-but-not-observed is itself a finding.
    (staged["local"] / "run_spec.json").write_text(
        json.dumps(_sidecar([
            {"class_name": "a.X", "args": {"judge_model": "meta/llama-3.1-405b-instruct-turbo"}},
            {"class_name": "b.Y", "args": {"judge_model": "openai/gpt-4o-2024-05-13"}},
        ]))
    )
    comparison = _the_comparison(_plan(staged, declare=True))
    assert comparison["comparability_facts"]["same_judge"]["status"] == "yes"
    assert "substitution_not_observed:judge" in comparison["warnings"]


# --- renderer level (F9 end to end) -----------------------------------------


def _render(staged: dict, artifact: dict, out_dir: Path) -> dict:
    from eval_audit.cli.from_eee import _packets_with_manifests

    packet = next(iter(_packets_with_manifests(artifact)))
    out_dir.mkdir()
    components_fpath = out_dir / "components_manifest.json"
    comparisons_fpath = out_dir / "comparisons_manifest.json"
    components_fpath.write_text(json.dumps(packet["components_manifest"], indent=1))
    comparisons_fpath.write_text(json.dumps(packet["comparisons_manifest"], indent=1))
    subprocess.run(
        [
            sys.executable, "-m", "eval_audit.reports.core_metrics",
            "--report-dpath", str(out_dir),
            "--components-manifest", str(components_fpath),
            "--comparisons-manifest", str(comparisons_fpath),
            "--instance-source", "eee-only",
            # EEE-only pairs get a noisy HELM-grade diagnosis through the
            # helm_compat empty defaults (the baseline pins wrong_run_pair
            # for the same fixture); skip it so the substitution overlay
            # is observable on a clean base — exactly the EEE-path mode.
            "--skip-diagnosis",
            "--no-plots",
        ],
        check=True,
        cwd=REPO_ROOT,
    )
    return json.loads((out_dir / "core_metric_report.json").read_text())


def test_f9_rendered_pair_carries_substitution_outputs(staged):
    artifact = _plan(staged, declare=True)
    report = _render(staged, artifact, staged["tmp"] / "out_declared")
    pair = report["pairs"][0]
    assert pair["substitutions"] == ["judge"]
    assert pair["diagnosis"]["label"] == "intended_substitution:judge"
    split = pair["metric_class_split"]
    assert set(split) == {"deterministic", "judge_dependent"}
    # The toy fixture has no judge-derived metrics; the deterministic
    # class carries all rows (the control), judge class is empty.
    assert split["deterministic"]["n_rows"] == pair["instance_level"]["n_rows"]
    assert split["judge_dependent"]["n_rows"] == 0


def test_f10_rendered_pair_has_no_substitution_outputs(staged):
    artifact = _plan(staged, declare=False)
    report = _render(staged, artifact, staged["tmp"] / "out_plain")
    pair = report["pairs"][0]
    assert "substitutions" not in pair
    assert "metric_class_split" not in pair
    # Undeclared: the skipped diagnosis stays skipped — no substitution
    # label appears from nowhere.
    assert pair["diagnosis"] == {}

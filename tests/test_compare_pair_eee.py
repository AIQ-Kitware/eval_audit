"""End-to-end smoke for the EEE-only single-pair comparison CLI.

Exercises ``eval-audit-compare-pair-eee`` two ways against the
checked-in EEE demo fixture:

1. Without a sidecar ``run_spec.json`` — five HELM-side comparability
   facts collapse to ``status='unknown'``.
2. With a synthesized sidecar ``run_spec.json`` for both inputs — all
   five facts evaluate to ``yes`` and the warnings disappear.

Marked ``slow`` because each invocation shells out to the core_metrics
renderer. Skipped by default; run with ``pytest --run-slow``.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.slow

REPO_ROOT = Path(__file__).resolve().parents[1]
from conftest import (  # noqa: E402  (shared EEE-demo fixture path + guard)
    EEE_DEMO_ROOT as FIXTURE_ROOT,
    EEE_DEMO_OFFICIAL_DIR as OFFICIAL_DIR,
    EEE_DEMO_LOCAL_DIR as LOCAL_DIR,
    require_eee_demo,
)


_EXPECTED_HELM_FACTS = (
    "same_scenario_class",
    "same_benchmark_family",
    "same_deployment",
    "same_instructions",
    "same_max_eval_instances",
)


def _run_pair(*, official: Path, local: Path, out_dir: Path) -> None:
    cmd = [
        sys.executable, "-m", "eval_audit.cli.compare_pair_eee",
        "--official", str(official),
        "--local", str(local),
        "--out-dpath", str(out_dir),
        "--clean",
    ]
    subprocess.run(cmd, check=True, cwd=REPO_ROOT)


def _comparability_facts(out_dir: Path) -> dict[str, dict]:
    payload = json.loads((out_dir / "core_metric_report.json").read_text())
    pair = (payload.get("pairs") or [{}])[0]
    return pair.get("comparability_facts") or {}


def _agreement_at_zero(out_dir: Path) -> tuple[float | None, float | None]:
    payload = json.loads((out_dir / "core_metric_report.json").read_text())
    pair = (payload.get("pairs") or [{}])[0]
    run_curve = (pair.get("run_level") or {}).get("agreement_vs_abs_tol") or []
    inst_curve = (pair.get("instance_level") or {}).get("agreement_vs_abs_tol") or []

    def at_zero(curve):
        return next((row.get("agree_ratio") for row in curve if row.get("abs_tol") == 0.0), None)

    return at_zero(run_curve), at_zero(inst_curve)


@pytest.fixture
def pair_no_sidecar(tmp_path: Path) -> Path:
    """Run compare-pair-eee against the demo fixture (no sidecar)."""
    require_eee_demo()
    out_dir = tmp_path / "no_sidecar"
    _run_pair(official=OFFICIAL_DIR, local=LOCAL_DIR, out_dir=out_dir)
    return out_dir


@pytest.fixture
def pair_with_sidecar(tmp_path: Path) -> Path:
    """Stage the demo artifacts with a synthesized run_spec.json next to each."""
    require_eee_demo()
    staging = tmp_path / "staging"
    official_dst = staging / "official"
    local_dst = staging / "local"
    shutil.copytree(OFFICIAL_DIR, official_dst)
    shutil.copytree(LOCAL_DIR, local_dst)
    sidecar = {
        "name": "imdb:model=toy/m1-small,suite=eee_demo",
        "adapter_spec": {
            "model": "toy/m1-small",
            "model_deployment": "huggingface/toy-m1-small",
            "max_eval_instances": 4,
            "instructions": "Predict the sentiment of this review.",
        },
        "scenario_spec": {"class_name": "helm.IMDBScenario"},
    }
    (official_dst / "run_spec.json").write_text(json.dumps(sidecar) + "\n")
    (local_dst / "run_spec.json").write_text(json.dumps(sidecar) + "\n")
    out_dir = tmp_path / "with_sidecar"
    _run_pair(official=official_dst, local=local_dst, out_dir=out_dir)
    return out_dir


def test_report_artifacts_present_no_sidecar(pair_no_sidecar: Path) -> None:
    """The CLI should land the standard core-metrics report shape."""
    for name in [
        "core_metric_report.txt",
        "core_metric_report.json",
        "core_metric_report.png",
        "core_metric_management_summary.txt",
        "warnings.json",
        "warnings.txt",
        "components_manifest.json",
        "comparisons_manifest.json",
        "eee_metadata_caveats.txt",
    ]:
        assert (pair_no_sidecar / name).is_file(), name


def test_facts_unknown_without_sidecar(pair_no_sidecar: Path) -> None:
    """All five HELM-side facts must collapse to 'unknown' when no sidecar."""
    facts = _comparability_facts(pair_no_sidecar)
    assert (facts.get("same_model") or {}).get("status") == "yes"
    for fact_name in _EXPECTED_HELM_FACTS:
        fact = facts.get(fact_name) or {}
        assert fact.get("status") == "unknown", f"{fact_name} = {fact}"


def test_facts_known_with_sidecar(pair_with_sidecar: Path) -> None:
    """A sidecar run_spec.json flips all five HELM-side facts to 'yes'."""
    facts = _comparability_facts(pair_with_sidecar)
    for fact_name in _EXPECTED_HELM_FACTS:
        fact = facts.get(fact_name) or {}
        assert fact.get("status") == "yes", f"{fact_name} = {fact}"


def test_caveats_file_describes_sidecar_status(pair_no_sidecar: Path, pair_with_sidecar: Path) -> None:
    """The caveats file should record sidecar absence / presence accurately."""
    no_caveats = (pair_no_sidecar / "eee_metadata_caveats.txt").read_text()
    with_caveats = (pair_with_sidecar / "eee_metadata_caveats.txt").read_text()
    assert "official run_spec.json: absent" in no_caveats, no_caveats
    assert "local    run_spec.json: absent" in no_caveats, no_caveats
    assert "official run_spec.json: present" in with_caveats, with_caveats
    assert "local    run_spec.json: present" in with_caveats, with_caveats
    # Regression: the template is dedented before interpolation, so top-level
    # header/prose lines must sit flush at column 0 (a prior version let the
    # multi-line sidecar block poison textwrap.dedent, leaving every line
    # indented by 2 stray spaces).
    for caveats in (no_caveats, with_caveats):
        lines = caveats.splitlines()
        assert "EEE-only pairwise comparison" in lines, caveats
        for header in (
            "EEE-only pairwise comparison",
            "============================",
            "Inputs",
            "Recommendations",
        ):
            matching = [ln for ln in lines if ln.strip() == header]
            assert matching, f"missing header {header!r} in:\n{caveats}"
            assert all(
                not ln.startswith((" ", "\t")) for ln in matching
            ), f"header {header!r} has stray leading whitespace in:\n{caveats}"


def test_agreement_curve_is_invariant_to_sidecar(
    pair_no_sidecar: Path, pair_with_sidecar: Path
) -> None:
    """Sidecar metadata must NOT change the quantitative agreement metrics —
    only the qualitative comparability facts. The imdb m1-small fixture is
    engineered for full divergence (agreement=0.0 at abs_tol=0).
    """
    no_run, no_inst = _agreement_at_zero(pair_no_sidecar)
    with_run, with_inst = _agreement_at_zero(pair_with_sidecar)
    assert no_run == 0.0
    assert no_inst == 0.0
    assert with_run == 0.0
    assert with_inst == 0.0


def test_mismatched_logical_keys_fails_without_force(tmp_path: Path) -> None:
    """Mismatched model+benchmark must error unless --force-pair is set."""
    if not FIXTURE_ROOT.exists():
        pytest.skip("fixture missing")
    # m2-medium is a single-artifact arc_easy dir (m1-small now holds two
    # attempts since D-1); a single artifact keeps this test about the
    # mismatched-logical-key guard, not the multiple-aggregates guard.
    other_local = FIXTURE_ROOT / "local" / "primary" / "arc_easy" / "toy" / "m2-medium"
    out_dir = tmp_path / "mismatch"
    cmd = [
        sys.executable, "-m", "eval_audit.cli.compare_pair_eee",
        "--official", str(OFFICIAL_DIR),  # imdb
        "--local", str(other_local),       # arc_easy
        "--out-dpath", str(out_dir),
        "--clean",
    ]
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    assert result.returncode != 0
    assert "different" in result.stderr or "different" in result.stdout

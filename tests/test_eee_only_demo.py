"""End-to-end smoke for the EEE-only analysis path.

Drives ``eval-audit-from-eee`` against the checked-in fixture under
``tests/fixtures/eee_only_demo/eee_artifacts`` and asserts on the resulting
per-packet reports. The fixture's ``DRIFT`` patterns are deterministic, so
this test pins the agreement curves we expect.

Marked ``slow`` because it shells out to the analysis pipeline (subprocess
per packet) for nine packets; collection time would otherwise be acceptable
but wall-clock is in the seconds. Run with ``pytest --run-slow`` to include.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.slow

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "eee_only_demo" / "eee_artifacts"


def _agreement_at_zero(curve: list[dict]) -> float | None:
    """Pull the ``agree_ratio`` row for ``abs_tol == 0.0`` from a curve."""
    for row in curve or []:
        if row.get("abs_tol") == 0.0:
            return row.get("agree_ratio")
    return None


def _load_pairs(packet_dir: Path) -> list[dict]:
    payload = json.loads((packet_dir / "core_metric_report.json").read_text())
    return payload.get("pairs") or []


def _all_packet_dirs(demo_output: Path) -> list[Path]:
    """Every rendered per-packet dir, across whatever experiment subtree(s)
    the derived experiment name lands them in (``local/<experiment>/...`` ->
    ``<experiment>/core-reports/<packet>``). The demo fixture derives a single
    experiment (``primary``) since D-1, but glob defensively across all."""
    return sorted(
        p
        for p in demo_output.glob("*/core-reports/*")
        if (p / "core_metric_report.json").is_file()
    )


def _packet_dir(demo_output: Path, benchmark: str, model: str) -> Path:
    """Resolve one packet by its ``<benchmark>-model-toy_<model>`` suffix,
    experiment-prefix-agnostic. Model slugs use underscores (canonical key)."""
    suffix = f"{benchmark}-model-toy_{model}"
    cands = [p for p in _all_packet_dirs(demo_output) if p.name.endswith(suffix)]
    assert len(cands) == 1, (benchmark, model, [c.name for c in cands])
    return cands[0]


def _key_for_pair(pair: dict) -> tuple[str, str]:
    """Identify a pair by ``(comparison_kind, sorted-component-ids-joined)``.

    The component-id portion makes ``official_vs_local`` pairs that share
    ``arc_easy m1-small`` distinguishable from each other (primary vs repeat).
    """
    return (
        pair.get("comparison_kind", "?"),
        "|".join(sorted(pair.get("component_ids") or [])),
    )


@pytest.fixture(scope="module")
def demo_output(tmp_path_factory) -> Path:
    """Run ``eval-audit-from-eee --build-aggregate-summary`` once per session
    and return the output dir.
    """
    if not FIXTURE_ROOT.exists():
        pytest.skip(f"EEE demo fixture missing: {FIXTURE_ROOT}")
    out_dir = tmp_path_factory.mktemp("eee_only_demo_out")
    cmd = [
        sys.executable, "-m", "eval_audit.cli.from_eee",
        "--eee-root", str(FIXTURE_ROOT),
        "--out-dpath", str(out_dir),
        "--clean",
        "--build-aggregate-summary",
    ]
    subprocess.run(cmd, check=True, cwd=REPO_ROOT)
    return out_dir


def test_index_csvs_written(demo_output: Path) -> None:
    """Both synthesized indexes should land where the planner expects them."""
    assert (demo_output / "official_public_index.csv").is_file()
    assert (demo_output / "audit_results_index.csv").is_file()


def test_planner_packet_and_pair_counts(demo_output: Path) -> None:
    """3 models × 3 benchmarks => 9 packets; one packet has +2 extra pairs."""
    packet_dirs = _all_packet_dirs(demo_output)
    assert len(packet_dirs) == 9

    total_pairs = 0
    for packet_dir in packet_dirs:
        total_pairs += len(_load_pairs(packet_dir))
    # 9 baseline official_vs_local + 1 extra official_vs_local (repeat) +
    # 1 local_repeat (primary vs repeat).
    assert total_pairs == 11


def test_arc_easy_m1_small_has_local_repeat(demo_output: Path) -> None:
    """The multi-attempt packet must contain both official_vs_local pairs and
    a local_repeat pair — that's the whole point of having two locals here.
    Since D-1 the two attempts live under one experiment (same model dir),
    so the pair lands in a normal packet, not a cross-experiment orphan.
    """
    packet_dir = _packet_dir(demo_output, "arc_easy", "m1-small")
    pairs = _load_pairs(packet_dir)
    kinds = sorted(p.get("comparison_kind") for p in pairs)
    assert kinds == ["local_repeat", "official_vs_local", "official_vs_local"]


def test_arc_easy_perfect_agreement(demo_output: Path) -> None:
    """The arc_easy fixture is engineered for perfect agreement on every model.

    All four arc_easy pairs (3 baseline + 1 repeat + 1 local_repeat) should
    show ``agree_ratio=1.0`` at ``abs_tol=0`` at both run-level and instance-level.
    """
    for model in ["m1-small", "m2-medium", "m3-large"]:
        packet_dir = _packet_dir(demo_output, "arc_easy", model)
        for pair in _load_pairs(packet_dir):
            run_curve = (pair.get("run_level") or {}).get("agreement_vs_abs_tol")
            inst_curve = (pair.get("instance_level") or {}).get("agreement_vs_abs_tol")
            assert _agreement_at_zero(run_curve) == 1.0, packet_dir.name
            assert _agreement_at_zero(inst_curve) == 1.0, packet_dir.name


def test_imdb_m1_full_divergence(demo_output: Path) -> None:
    """imdb m1-small is engineered for full divergence: every instance flips."""
    packet_dir = _packet_dir(demo_output, "imdb", "m1-small")
    pairs = _load_pairs(packet_dir)
    assert len(pairs) == 1
    pair = pairs[0]
    run_curve = (pair.get("run_level") or {}).get("agreement_vs_abs_tol")
    inst_curve = (pair.get("instance_level") or {}).get("agreement_vs_abs_tol")
    assert _agreement_at_zero(run_curve) == 0.0
    assert _agreement_at_zero(inst_curve) == 0.0


def test_imdb_m2_partial_divergence(demo_output: Path) -> None:
    """imdb m2-medium has 1-of-4 instances flipped: instance agreement = 0.75,
    run-level agreement = 0.0 because the per-metric means now differ.
    """
    packet_dir = _packet_dir(demo_output, "imdb", "m2-medium")
    pairs = _load_pairs(packet_dir)
    assert len(pairs) == 1
    pair = pairs[0]
    run_curve = (pair.get("run_level") or {}).get("agreement_vs_abs_tol")
    inst_curve = (pair.get("instance_level") or {}).get("agreement_vs_abs_tol")
    assert _agreement_at_zero(run_curve) == 0.0
    assert _agreement_at_zero(inst_curve) == 0.75


def test_truthful_qa_m1_partial_divergence(demo_output: Path) -> None:
    """truthful_qa m1-small mirrors the imdb m2 pattern."""
    packet_dir = _packet_dir(demo_output, "truthful_qa", "m1-small")
    pairs = _load_pairs(packet_dir)
    assert len(pairs) == 1
    pair = pairs[0]
    run_curve = (pair.get("run_level") or {}).get("agreement_vs_abs_tol")
    inst_curve = (pair.get("instance_level") or {}).get("agreement_vs_abs_tol")
    assert _agreement_at_zero(run_curve) == 0.0
    assert _agreement_at_zero(inst_curve) == 0.75


def test_eee_only_components_are_eee(demo_output: Path) -> None:
    """Every component recorded in the per-packet manifest must be EEE-format
    with an ``eee_artifact_path`` and no ``run_path`` — i.e., the EEE-only
    path is genuinely HELM-free, not silently falling back to the HELM seam.
    """
    for packet_dir in _all_packet_dirs(demo_output):
        manifest = json.loads(
            (packet_dir / "components_manifest.json").read_text()
        )
        for component in manifest.get("components") or []:
            assert component.get("artifact_format") == "eee", component
            assert component.get("eee_artifact_path"), component
            # run_path may be absent or empty/None — never a real path.
            run_path = component.get("run_path") or ""
            assert run_path == "", (packet_dir.name, component)


def test_aggregate_summary_buckets_match_fixture_drift(demo_output: Path) -> None:
    """The cross-packet roll-up should put every packet in the right bucket
    given the engineered DRIFT map: 6 exact, 2 low, 1 zero. If this drifts,
    the planner / core-metrics / aggregate-summary chain regressed on
    EEE-only inputs.
    """
    summary_root = demo_output / "aggregate-summary" / "all-results"
    if not summary_root.exists():
        pytest.skip("aggregate summary not built; --build-aggregate-summary missing?")
    bucket_csv = summary_root / "reproducibility_rows.csv"
    assert bucket_csv.is_file(), bucket_csv
    rows = list(__import__("csv").DictReader(bucket_csv.open()))
    assert len(rows) == 9, [r.get("packet_id") for r in rows]
    bucket_counts: dict[str, int] = {}
    for row in rows:
        bucket = row.get("official_instance_agree_bucket", "")
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
    assert bucket_counts.get("exact_or_near_exact", 0) == 6, bucket_counts
    assert bucket_counts.get("low_agreement_0.00+", 0) == 2, bucket_counts
    assert bucket_counts.get("zero_agreement", 0) == 1, bucket_counts


def test_aggregate_summary_no_canonical_leak(demo_output: Path) -> None:
    """The aggregate roll-up must not pick up any reports outside the demo
    output dir. ``--no-canonical-scan`` is wired by ``eval-audit-from-eee``;
    if it stops working the bucket counts above will go up but this test
    checks the constraint independently by inspecting report dirs.
    """
    summary_root = demo_output / "aggregate-summary" / "all-results"
    if not summary_root.exists():
        pytest.skip("aggregate summary not built")
    csv_module = __import__("csv")
    rows = list(csv_module.DictReader((summary_root / "reproducibility_rows.csv").open()))
    for row in rows:
        report_dir = row.get("report_dir") or ""
        # Every report dir referenced by the aggregate summary must live
        # inside the demo's --out-dpath. Anything else is a canonical-scan
        # leak.
        assert report_dir.startswith(str(demo_output)), report_dir


def test_helm_facts_collapse_to_unknown(demo_output: Path) -> None:
    """For EEE-only inputs, HELM-side comparability checks (scenario_class,
    deployment, instructions, max_eval_instances) cannot be answered — they
    must surface as ``status='unknown'`` rather than silently asserting.
    """
    expected_unknown = {
        "same_scenario_class",
        "same_benchmark_family",
        "same_deployment",
        "same_instructions",
        "same_max_eval_instances",
    }
    for packet_dir in _all_packet_dirs(demo_output):
        for pair in _load_pairs(packet_dir):
            facts = pair.get("comparability_facts") or {}
            for key in expected_unknown:
                fact = facts.get(key) or {}
                assert fact.get("status") == "unknown", (
                    f"{packet_dir.name} {pair.get('comparison_id')} {key} = {fact}"
                )
            # ``same_model`` should always resolve from EEE model_info — the
            # whole comparison hinges on this.
            assert (facts.get("same_model") or {}).get("status") == "yes"

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
from conftest import (  # noqa: E402  (shared EEE-demo fixture path + guard)
    EEE_DEMO_ROOT as FIXTURE_ROOT,
    require_eee_demo,
)


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
    require_eee_demo()
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
    """3 models × 3 benchmarks => 9 packets; one packet has +1 extra pair."""
    packet_dirs = _all_packet_dirs(demo_output)
    assert len(packet_dirs) == 9

    total_pairs = 0
    for packet_dir in packet_dirs:
        total_pairs += len(_load_pairs(packet_dir))
    # 9 official_vs_local (one per packet, against the canonical local
    # attempt) + 1 local_repeat (canonical vs superseded). The superseded
    # attempt's own official_vs_local is planned but disabled, so the
    # renderer never emits it as a pair.
    assert total_pairs == 10


def test_arc_easy_m1_small_retypes_its_second_attempt_as_a_repeat(demo_output: Path) -> None:
    """A packet answers "how well did this row reproduce?" exactly once.

    Two locals here means one canonical official_vs_local plus a local_repeat
    carrying the superseded attempt. Emitting the second attempt as a rival
    official_vs_local is what let reductions pool a superseded run into the
    canonical one (docs/helm-gotchas.md §G14). Since D-1 the two attempts live
    under one experiment (same model dir), so the pair lands in a normal
    packet, not a cross-experiment orphan.
    """
    packet_dir = _packet_dir(demo_output, "arc_easy", "m1-small")
    pairs = _load_pairs(packet_dir)
    kinds = sorted(p.get("comparison_kind") for p in pairs)
    assert kinds == ["local_repeat", "official_vs_local"]

    comparisons = json.loads((packet_dir / "core_metric_report.json").read_text())["comparisons"]
    disabled = [c for c in comparisons if not c.get("enabled")]
    assert [c.get("disabled_reason") for c in disabled] == ["superseded_local_attempt"]


def test_arc_easy_perfect_agreement(demo_output: Path) -> None:
    """The arc_easy fixture is engineered for perfect agreement on every model.

    All arc_easy pairs (3 official_vs_local + 1 local_repeat) should
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


def test_aggregate_diff_collector_reads_runlevel_scores(demo_output: Path) -> None:
    """The aggregate-score-difference collector reads the sibling
    ``core_runlevel_table.csv`` and reports signed (local - public) drift
    per (model, benchmark, metric).

    The demo fixture engineers deterministic run-level drift: ``imdb`` on
    ``m1-small`` fully diverges (public 1.0, local 0.0), while ``arc_easy``
    reproduces perfectly on every model. This pins both the divergent and
    the exact cell.
    """
    from eval_audit.reports.eee_heatmap_data import (
        _collect_aggregate_diff_cells_per_metric,
    )

    cells = _collect_aggregate_diff_cells_per_metric(demo_output)
    assert cells, "no aggregate-score cells collected"

    # imdb / m1-small / exact_match: public 1.0 vs local 0.0 => diff -1.0.
    diverged = cells[("toy/m1-small", "imdb", "exact_match")]
    assert diverged["official"] == 1.0
    assert diverged["local"] == 0.0
    assert diverged["diff"] == -1.0
    assert diverged["abs_diff"] == 1.0
    assert diverged["status"] == "present"

    # arc_easy reproduces exactly on every model: diff == 0 everywhere.
    for model in ("toy/m1-small", "toy/m2-medium", "toy/m3-large"):
        exact = cells[(model, "arc_easy", "exact_match")]
        assert exact["official"] == exact["local"]
        assert exact["diff"] == 0.0


def test_aggregate_summary_emits_score_diff_heatmap(demo_output: Path) -> None:
    """The aggregate summary must auto-emit the per-core-metric
    aggregate-score-difference heatmaps under the top-level scope.

    The demo builds with ``--build-aggregate-summary``, so the
    ``all-results`` scope's ``level_001/aggregate_score_diff/`` must carry
    the text/JSON sidecars plus one PNG per core metric. The imdb/m1-small
    full-divergence cell must be present and signed in the JSON.
    """
    summary_root = demo_output / "aggregate-summary" / "all-results"
    if not summary_root.exists():
        pytest.skip("aggregate summary not built")
    diff_dir = summary_root / "level_001" / "aggregate_score_diff"
    assert (diff_dir / "aggregate_score_diff_per_metric.txt").is_file()
    json_path = diff_dir / "aggregate_score_diff_per_metric.json"
    assert json_path.is_file()

    # exact_match / quasi_exact_match are the demo's core metrics; each
    # gets its own PNG.
    png_dir = diff_dir / "aggregate_score_diff_per_metric"
    pngs = {p.stem for p in png_dir.glob("*.png")}
    assert {"exact_match", "quasi_exact_match"} <= pngs, pngs

    cells = {
        (c["model"], c["benchmark"], c["metric"]): c
        for c in json.loads(json_path.read_text())["cells"]
    }
    diverged = cells[("toy/m1-small", "imdb", "exact_match")]
    assert diverged["official"] == 1.0
    assert diverged["local"] == 0.0
    assert diverged["diff"] == -1.0


def test_aggregate_summary_emits_headline_score_diff(demo_output: Path) -> None:
    """The aggregate summary must also emit the holistic headline-metric
    diff heatmap (one metric per benchmark, all model × benchmark pairs in
    a single figure)."""
    summary_root = demo_output / "aggregate-summary" / "all-results"
    if not summary_root.exists():
        pytest.skip("aggregate summary not built")
    diff_dir = summary_root / "level_001" / "aggregate_score_diff"
    assert (diff_dir / "aggregate_score_diff_headline.png").is_file()
    assert (diff_dir / "aggregate_score_diff_headline.txt").is_file()
    payload = json.loads((diff_dir / "aggregate_score_diff_headline.json").read_text())

    # Each benchmark collapses to one metric; imdb's headline is
    # quasi_exact_match (HELM main_name), truthful_qa's is exact_match.
    bm = payload["benchmark_metric"]
    assert bm.get("imdb") == "quasi_exact_match", bm
    assert bm.get("truthful_qa") == "exact_match", bm

    # One cell per (model, benchmark) — no metric multiplicity.
    cells = {(c["model"], c["benchmark"]): c for c in payload["cells"]}
    imdb_m1 = cells[("toy/m1-small", "imdb")]
    assert imdb_m1["diff"] == -1.0
    # Squared error is what the holistic plot colors by: non-negative and
    # emphasizing the largest deviations. diff -1.0 -> squared_error 1.0.
    assert imdb_m1["squared_error"] == 1.0
    assert all(c["squared_error"] >= 0 for c in payload["cells"])
    # Exactly 9 holistic cells (3 models × 3 benchmarks).
    assert len(payload["cells"]) == 9, len(payload["cells"])

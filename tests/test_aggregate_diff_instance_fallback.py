"""Instance-level fallback for the aggregate-score-drift cells.

Some benchmarks emit no *run-level* stat for their core metric — the
capabilities-track scenarios ifeval (``ifeval_strict_accuracy``) and
gpqa / mmlu_pro (``chain_of_thought_correctness``) are scored per
instance only. Their ``core_runlevel_table.csv`` is empty, so the
aggregate-score-drift plot used to silently drop them.

``_accumulate_aggregate_diff_cells`` now falls back to the mean of the
per-instance scores (``pairs[].instance_level.by_metric[].a_mean`` /
``b_mean``, written by ``normalized.diff.metric_quantiles``) and tags the
cell ``source="instance_level"``. Run-level always wins when both exist.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from eval_audit.reports.eee_heatmap_data import _accumulate_aggregate_diff_cells

MODEL = "allenai/olmo-2-1124-7b-instruct"


def _write_report(
    report_dir: Path,
    *,
    benchmark: str,
    runlevel_rows: list[dict[str, str]],
    instance_by_metric: list[dict[str, Any]],
) -> Path:
    """Materialize one packet: core_metric_report.json + runlevel CSV."""
    report_dir.mkdir(parents=True, exist_ok=True)
    lrk = f"{benchmark}:model={MODEL}"
    report = {
        "components": [{"logical_run_key": lrk, "model": MODEL}],
        "pairs": [
            {
                "comparison_kind": "official_vs_local",
                "instance_level": {"by_metric": instance_by_metric},
            }
        ],
    }
    report_path = report_dir / "core_metric_report.json"
    report_path.write_text(json.dumps(report))

    # Empty runlevel table = a single blank line, exactly what core_metrics
    # writes when the run-level comparison produced no rows.
    csv_path = report_dir / "core_runlevel_table.csv"
    with csv_path.open("w", newline="") as fh:
        if runlevel_rows:
            writer = csv.DictWriter(
                fh, fieldnames=["comparison_kind", "metric", "left_mean", "right_mean"]
            )
            writer.writeheader()
            writer.writerows(runlevel_rows)
    return report_path


def test_instance_fallback_when_runlevel_empty(tmp_path: Path) -> None:
    report = _write_report(
        tmp_path / "ifeval",
        benchmark="ifeval",
        runlevel_rows=[],  # instance-only metric → empty run-level table
        instance_by_metric=[
            {"metric": "ifeval_strict_accuracy", "a_mean": 0.60, "b_mean": 0.48}
        ],
    )
    cells = _accumulate_aggregate_diff_cells([report])
    cell = cells[(MODEL, "ifeval", "ifeval_strict_accuracy")]
    assert cell["source"] == "instance_level"
    assert cell["official"] == 0.60
    assert cell["local"] == 0.48
    assert cell["diff"] == -0.12  # local - official, drop of 0.12


def test_runlevel_wins_over_instance_when_both_present(tmp_path: Path) -> None:
    report = _write_report(
        tmp_path / "bbq",
        benchmark="bbq",
        runlevel_rows=[
            {
                "comparison_kind": "official_vs_local",
                "metric": "exact_match",
                "left_mean": "0.525",
                "right_mean": "0.659",
            }
        ],
        # Deliberately different numbers so we can tell which source won.
        instance_by_metric=[{"metric": "exact_match", "a_mean": 0.111, "b_mean": 0.222}],
    )
    cells = _accumulate_aggregate_diff_cells([report])
    cell = cells[(MODEL, "bbq", "exact_match")]
    assert cell["source"] == "run_level"
    assert cell["official"] == 0.525
    assert cell["local"] == 0.659


def test_instance_fallback_skips_missing_means(tmp_path: Path) -> None:
    # A by_metric entry without a_mean/b_mean (older report shape) yields no
    # cell rather than a crash or a half-populated one.
    report = _write_report(
        tmp_path / "legacy",
        benchmark="ifeval",
        runlevel_rows=[],
        instance_by_metric=[{"metric": "ifeval_strict_accuracy"}],
    )
    cells = _accumulate_aggregate_diff_cells([report])
    assert cells == {}


def _write_multi_local_report(
    report_dir: Path,
    *,
    benchmark: str,
    metric: str,
    official: float,
    fresh_local: float,
    stale_local: float,
) -> Path:
    """A packet with a canonical + a stale second official_vs_local attempt.

    Mirrors the real defect: a stale prior local run left a second
    ``official_vs_local`` pair (and CSV rows) alongside the fresh one. Both
    the run-level table and the instance-level pairs carry the extra attempt;
    only the first (canonical) one should feed the aggregate cell.
    """
    report_dir.mkdir(parents=True, exist_ok=True)
    lrk = f"{benchmark}:model={MODEL}"
    fresh_id = "official_vs_local::...::helm_id_fresh"
    stale_id = "official_vs_local::...::helm_id_stale"
    report = {
        "components": [{"logical_run_key": lrk, "model": MODEL}],
        "pairs": [
            {
                "comparison_kind": "official_vs_local",
                "comparison_id": fresh_id,
                "instance_level": {
                    "by_metric": [
                        {"metric": metric, "a_mean": official, "b_mean": fresh_local}
                    ]
                },
            },
            {
                "comparison_kind": "official_vs_local",
                "comparison_id": stale_id,
                "instance_level": {
                    "by_metric": [
                        {"metric": metric, "a_mean": official, "b_mean": stale_local}
                    ]
                },
            },
        ],
    }
    report_path = report_dir / "core_metric_report.json"
    report_path.write_text(json.dumps(report))

    csv_path = report_dir / "core_runlevel_table.csv"
    with csv_path.open("w", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "comparison_id",
                "comparison_kind",
                "metric",
                "left_mean",
                "right_mean",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "comparison_id": fresh_id,
                "comparison_kind": "official_vs_local",
                "metric": metric,
                "left_mean": str(official),
                "right_mean": str(fresh_local),
            }
        )
        writer.writerow(
            {
                "comparison_id": stale_id,
                "comparison_kind": "official_vs_local",
                "metric": metric,
                "left_mean": str(official),
                "right_mean": str(stale_local),
            }
        )
    return report_path


def test_stale_local_attempt_deduped_run_level(tmp_path: Path) -> None:
    # Two official_vs_local rows (fresh 0.34 + stale 0.0) must NOT be averaged
    # to 0.17: only the canonical (first) attempt counts. This is the olmo-7b
    # regression where a stale 0.0 run halved every aggregate local score.
    report = _write_multi_local_report(
        tmp_path / "mmlu",
        benchmark="mmlu",
        metric="exact_match test on mmlu",
        official=0.32,
        fresh_local=0.34,
        stale_local=0.0,
    )
    cells = _accumulate_aggregate_diff_cells([report])
    cell = cells[(MODEL, "mmlu", "exact_match test on mmlu")]
    assert cell["source"] == "run_level"
    assert cell["n"] == 1  # stale row dropped, not summed
    assert cell["official"] == 0.32
    assert cell["local"] == 0.34  # not (0.34 + 0.0) / 2 == 0.17
    assert cell["diff"] == 0.34 - 0.32


def test_stale_local_attempt_deduped_instance_fallback(tmp_path: Path) -> None:
    # Same dedup on the instance-level fallback path (no run-level rows): the
    # stale pair's a_mean/b_mean must not be folded in.
    report_dir = tmp_path / "ifeval"
    # Reuse the multi-local writer but strip the run-level CSV so the
    # instance-level fallback is exercised.
    report = _write_multi_local_report(
        report_dir,
        benchmark="ifeval",
        metric="ifeval_strict_accuracy",
        official=0.60,
        fresh_local=0.48,
        stale_local=0.0,
    )
    (report_dir / "core_runlevel_table.csv").write_text("")

    cells = _accumulate_aggregate_diff_cells([report])
    cell = cells[(MODEL, "ifeval", "ifeval_strict_accuracy")]
    assert cell["source"] == "instance_level"
    assert cell["n"] == 1
    assert cell["official"] == 0.60
    assert cell["local"] == 0.48  # not (0.48 + 0.0) / 2 == 0.24

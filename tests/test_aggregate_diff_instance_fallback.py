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

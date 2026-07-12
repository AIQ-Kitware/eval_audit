"""Plot + aggregate-score-diff rendering for one scope render.

Moved verbatim out of ``workflows.build_reports_summary`` on
2026-07-12 (plan item C1 of
docs/planning/repo-simplification-plan-2026-07-12.md), finishing the
Phase-2 split: bodies unchanged; only the import wiring is new.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from eval_audit.infra.fs_publish import write_text_atomic
from loguru import logger
from eval_audit.reports.summary.common import CANONICAL_AGREEMENT_TOL
from eval_audit.reports.summary.plots import (
    _write_plotly_bar,
    _write_agreement_curve_plot,
    _write_per_metric_agreement_plot,
    _write_coverage_matrix_plot,
    _write_failure_taxonomy_plot,
)
from eval_audit.reports.eee_heatmap_data import (
    _accumulate_aggregate_diff_cells,
    _order_aggregate_diff_axes,
)
from eval_audit.reports.eee_heatmap_render import (
    _render_aggregate_diff_heatmaps,
    _render_aggregate_diff_text_table,
    _render_headline_diff,
)

def _render_aggregate_score_diff(
    *,
    repro_rows: list[dict[str, Any]],
    scope_title: str,
    out_dir: Path,
) -> dict[str, Any]:
    """Emit the per-core-metric aggregate-score-difference heatmaps for a scope.

    Companion to the instance-level agreement views: each cell's color is
    the signed ``local − public`` run-level score difference, annotated
    with both actual scores. Driven off the scope's own ``repro_rows`` (so
    breakdown scopes only cover their packets) by reading each report's
    sibling ``core_runlevel_table.csv``.

    Returns ``{"png": [...], "txt": Path|None, "json": Path|None,
    "error": str|None}``; a missing-matplotlib or empty-cells case is a
    soft no-op (the rest of the summary still renders).
    """
    result: dict[str, Any] = {
        "png": [], "txt": None, "json": None, "headline": None, "error": None,
    }

    # Resolve each scope row to its core_metric_report.json. Prefer the
    # explicit ``report_json`` field; fall back to report_dir/<name>.
    report_paths: list[Path] = []
    seen: set[str] = set()
    for row in repro_rows:
        rj = row.get("report_json")
        if rj:
            rp = Path(rj)
        else:
            rd = row.get("report_dir")
            if not rd:
                continue
            rp = Path(rd) / "core_metric_report.json"
        key = str(rp)
        if key in seen:
            continue
        seen.add(key)
        if rp.is_file():
            report_paths.append(rp)

    if not report_paths:
        return result

    cells = _accumulate_aggregate_diff_cells(report_paths)
    if not cells:
        return result

    models, benchmarks, metrics_in_order, rows_in_order = _order_aggregate_diff_axes(cells)

    out_dir.mkdir(parents=True, exist_ok=True)
    text_diff = _render_aggregate_diff_text_table(cells, models, rows_in_order)
    txt_path = out_dir / "aggregate_score_diff_per_metric.txt"
    write_text_atomic(txt_path, text_diff)
    result["txt"] = txt_path

    json_rows = [
        {"model": m, "benchmark": b, "metric": metric, **cells[(m, b, metric)]}
        for (b, metric) in rows_in_order
        for m in models
        if (m, b, metric) in cells
    ]
    json_path = out_dir / "aggregate_score_diff_per_metric.json"
    write_text_atomic(json_path, json.dumps({"cells": json_rows}, indent=2) + "\n")
    result["json"] = json_path

    try:
        written = _render_aggregate_diff_heatmaps(
            cells, models, benchmarks, metrics_in_order,
            f"Aggregate Score Drift (local − public): {scope_title}",
            out_dir,
        )
        result["png"] = written
        # Holistic top-level view: one headline metric per benchmark, so
        # every model × benchmark pair is visible in a single figure.
        headline = _render_headline_diff(
            cells, models, benchmarks,
            f"Headline Aggregate Score Drift (local − public): {scope_title}",
            out_dir,
        )
        result["headline"] = headline
    except ImportError as exc:
        logger.warning(
            f"matplotlib not available ({exc}); "
            "skipping aggregate-score-diff heatmaps."
        )
        result["error"] = str(exc)
    return result


def _render_scope_plots(
    *,
    include_visuals: bool,
    benchmark_status_rows: list[dict[str, Any]],
    repro_bucket_rows: list[dict[str, Any]],
    repro_rows: list[dict[str, Any]],
    enriched_rows: list[dict[str, Any]],
    failed_rows: list[dict[str, Any]],
    filter_selection_by_model_rows: list[dict[str, Any]],
    scope_title: str,
    level_001: Path,
    level_001_machine: Path,
    level_001_interactive: Path,
    level_001_static: Path,
) -> dict[str, dict[str, Any]]:
    if include_visuals:
        benchmark_plot = _write_plotly_bar(
            rows=benchmark_status_rows,
            x="group_value",
            y="count",
            color="status_bucket",
            title=f"Benchmark Coverage and Analysis Status (analyzed runs use abs_tol={CANONICAL_AGREEMENT_TOL:g}): {scope_title}",
            stem=level_001 / "benchmark_status",
            machine_dpath=level_001_machine,
            interactive_dpath=level_001_interactive,
            static_dpath=level_001_static,
            xaxis_title="Benchmark",
            xaxis_count_key="benchmark",
            yaxis_title="Job Count",
        )
        repro_bucket_plot = _write_plotly_bar(
            rows=repro_bucket_rows,
            x="agreement_bucket",
            y="count",
            color="agreement_bucket",
            title=f"Official vs Local Agreement Buckets (instance-level, abs_tol={CANONICAL_AGREEMENT_TOL:g} canonical): {scope_title}",
            stem=level_001 / "reproducibility_buckets",
            machine_dpath=level_001_machine,
            interactive_dpath=level_001_interactive,
            static_dpath=level_001_static,
            xaxis_title="Agreement Bucket",
            xaxis_count_key="agreement_bucket",
            yaxis_title="Run Count",
        )
        agreement_curve_plot = _write_agreement_curve_plot(
            repro_rows=repro_rows,
            enriched_rows=enriched_rows,
            stem=level_001 / "agreement_curve",
            title="Agreement Rate vs Tolerance (instance-level)",
            machine_dpath=level_001_machine,
            interactive_dpath=level_001_interactive,
            static_dpath=level_001_static,
            scope_title=scope_title,
        )
        per_metric_agreement_plot = _write_per_metric_agreement_plot(
            repro_rows=repro_rows,
            enriched_rows=enriched_rows,
            stem=level_001 / "agreement_curve_per_metric",
            title=f"Agreement Rate vs Tolerance (per-metric): {scope_title}",
            machine_dpath=level_001_machine,
            interactive_dpath=level_001_interactive,
            static_dpath=level_001_static,
        )
        coverage_matrix_plot = _write_coverage_matrix_plot(
            enriched_rows=enriched_rows,
            repro_rows=repro_rows,
            stem=level_001 / "coverage_matrix",
            title=f"Model × Benchmark Coverage and Reproducibility Status: {scope_title}",
            machine_dpath=level_001_machine,
            interactive_dpath=level_001_interactive,
            static_dpath=level_001_static,
        )
        failure_taxonomy_plot = _write_failure_taxonomy_plot(
            failed_rows=failed_rows,
            stem=level_001 / "failure_taxonomy",
            title=f"Why Jobs Failed: Root Cause Taxonomy by Benchmark: {scope_title}",
            machine_dpath=level_001_machine,
            interactive_dpath=level_001_interactive,
            static_dpath=level_001_static,
        )
        filter_selection_by_model_plot = _write_plotly_bar(
            rows=filter_selection_by_model_rows,
            x="model",
            y="count",
            color="selection_status",
            title=f"Selected vs Excluded Run Specs by Model: {scope_title}",
            stem=level_001 / "filter_selection_by_model",
            machine_dpath=level_001_machine,
            interactive_dpath=level_001_interactive,
            static_dpath=level_001_static,
            xaxis_title="Model",
            xaxis_count_key="model",
            yaxis_title="Run Spec Count",
        )
        # Per-core-metric aggregate-score-drift heatmaps (color = signed
        # local − public run-level score difference; cells annotated with
        # both actual scores). Matplotlib, not plotly — a soft no-op if
        # matplotlib is absent or no runlevel CSVs exist for the scope.
        aggregate_score_diff = _render_aggregate_score_diff(
            repro_rows=repro_rows,
            scope_title=scope_title,
            out_dir=level_001 / "aggregate_score_diff",
        )
    else:
        benchmark_plot = {"json": None, "html": None, "jpg": None, "png": None, "plotly_error": None}
        repro_bucket_plot = {"json": None, "html": None, "jpg": None, "png": None, "plotly_error": None}
        agreement_curve_plot = {"json": None, "html": None, "jpg": None, "plotly_error": None}
        per_metric_agreement_plot = {"json": None, "html": None, "jpg": None, "plotly_error": None}
        coverage_matrix_plot = {"json": None, "html": None, "jpg": None, "plotly_error": None}
        failure_taxonomy_plot = {"json": None, "html": None, "jpg": None, "plotly_error": None}
        filter_selection_by_model_plot = {"json": None, "html": None, "jpg": None, "png": None, "plotly_error": None}
        aggregate_score_diff = {"png": [], "txt": None, "json": None, "error": None}
    return {
        "benchmark_plot": benchmark_plot,
        "repro_bucket_plot": repro_bucket_plot,
        "agreement_curve_plot": agreement_curve_plot,
        "per_metric_agreement_plot": per_metric_agreement_plot,
        "coverage_matrix_plot": coverage_matrix_plot,
        "failure_taxonomy_plot": failure_taxonomy_plot,
        "filter_selection_by_model_plot": filter_selection_by_model_plot,
        "aggregate_score_diff": aggregate_score_diff,
    }

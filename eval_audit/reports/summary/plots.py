"""Plotly figure writers: bars, agreement curves, coverage matrix, failure taxonomy.

Split out of ``eval_audit.workflows.build_reports_summary`` on
2026-06-11 (Phase 2 of docs/historical/planning/repo-refactor-plan.md). Pure
relocation: function bodies are unchanged.
"""
from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path
from typing import Any
from loguru import logger
from eval_audit.infra.plotly_env import configure_plotly_chrome
from eval_audit.infra.profiling import profile

from eval_audit.reports.summary.common import _write_json
from eval_audit.reports.summary.failure_triage import (
    _FAILURE_CATEGORIES,
    _FAILURE_CATEGORY_LABELS,
    _FAILURE_CATEGORY_ORDER,
)


_AXIS_COUNT_TAGS = {
    "benchmark": "n_benchmarks",
    "model": "n_models",
    "dataset": "n_datasets",
    "scenario": "n_scenarios",
    "official_instance_agree_bucket": "n_buckets",
    "agreement_bucket": "n_buckets",
    "failure_reason": "n_failure_reasons",
    "category": "n_categories",
    "group_value": "n_categories",
}


def _ordered_unique_values(rows: list[dict[str, Any]], key: str) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for row in rows:
        value = str(row.get(key) or "unknown")
        if value not in seen:
            seen.add(value)
            values.append(value)
    return values


from eval_audit.utils.coercion import abbreviate_label as _abbreviate_label  # R-6


def _bar_count_label(axis_key: str, n_bars: int, *, axis_title: str | None = None) -> str:
    label = axis_title if axis_title is not None else axis_key.replace("_", " ").title()
    count_tag = _AXIS_COUNT_TAGS.get(axis_key, "n_categories")
    return f"{label} ({count_tag}={n_bars})"


def _bar_tickangle(n_bars: int) -> int:
    if n_bars > 50:
        return 90
    if n_bars > 25:
        return 75
    if n_bars > 12:
        return 60
    return -45


def _compact_bar_figure_size(unique_x: list[str]) -> tuple[int, int]:
    longest_label = max((len(value) for value in unique_x), default=0)
    n_bars = max(len(unique_x), 1)
    width = min(max(1100, 36 * n_bars, 14 * longest_label * n_bars), 1600)
    height = min(max(520, 14 * n_bars + 240), 1000)
    return width, height


@profile
def _write_plotly_bar(
    *,
    rows: list[dict[str, Any]],
    x: str,
    y: str,
    color: str,
    title: str,
    stem: Path,
    machine_dpath: Path | None = None,
    interactive_dpath: Path | None = None,
    static_dpath: Path | None = None,
    xaxis_title: str | None = None,
    yaxis_title: str | None = None,
    xaxis_count_key: str | None = None,
) -> dict[str, str | None]:
    if machine_dpath is not None:
        machine_dpath.mkdir(parents=True, exist_ok=True)
        json_fpath = (machine_dpath / stem.name).with_suffix(".json")
    else:
        json_fpath = stem.with_suffix(".json")
    _interactive = interactive_dpath if interactive_dpath is not None else stem.parent
    _static = static_dpath if static_dpath is not None else stem.parent
    _interactive.mkdir(parents=True, exist_ok=True)
    _static.mkdir(parents=True, exist_ok=True)
    html_fpath = (_interactive / stem.name).with_suffix(".html")
    jpg_fpath = (_static / stem.name).with_suffix(".jpg")
    png_fpath = (_static / stem.name).with_suffix(".png")
    _write_json(rows, json_fpath)
    html_out = None
    jpg_out = None
    png_out = None
    plotly_error = None
    mpl_error = None
    unique_x = _ordered_unique_values(rows, x)
    color_values = _ordered_unique_values(rows, color)
    count_label = _bar_count_label(xaxis_count_key or x, len(unique_x), axis_title=xaxis_title)
    if os.environ.get("HELM_AUDIT_SKIP_PLOTLY", "") not in {"1", "true", "yes"}:
        try:
            configure_plotly_chrome()
            import plotly.express as px

            fig = px.bar(
                rows,
                x=x,
                y=y,
                color=color,
                title=title,
                barmode="stack",
                category_orders={x: unique_x, color: color_values},
            )
            fig.update_layout(
                xaxis_title=count_label,
                yaxis_title=yaxis_title if yaxis_title is not None else y.replace("_", " "),
            )
            fig.update_xaxes(
                categoryorder="array",
                categoryarray=unique_x,
                tickmode="array",
                tickvals=unique_x,
                ticktext=unique_x,
                tickangle=-45,
                automargin=True,
            )
            fig.write_html(str(html_fpath), include_plotlyjs="cdn")
            html_out = str(html_fpath)
            if os.environ.get("HELM_AUDIT_SKIP_STATIC_IMAGES", "") not in {"1", "true", "yes"}:
                static_width, static_height = _compact_bar_figure_size(unique_x)
                fig.update_layout(width=static_width, height=static_height, margin={"b": min(max(120, 8 * max((len(v) for v in unique_x), default=0)), 220), "t": 80, "l": 70, "r": 30})
                fig.update_xaxes(
                    ticktext=[_abbreviate_label(value) for value in unique_x],
                    tickangle=_bar_tickangle(len(unique_x)),
                    tickfont={"size": 8 if len(unique_x) > 12 else 10},
                )
                fig.write_image(str(jpg_fpath), scale=1.0)
                jpg_out = str(jpg_fpath)
        except Exception as ex:
            plotly_error = f"unable to write bar HTML/images: {ex!r}"
    else:
        plotly_error = "skipped plotly bar rendering by configuration"
    if os.environ.get("HELM_AUDIT_SKIP_STATIC_IMAGES", "") not in {"1", "true", "yes"}:
        try:
            import eval_audit.infra.mpl_backend  # noqa: F401  (force headless Agg before pyplot)
            import matplotlib.pyplot as plt

            if rows:
                x_values = unique_x
                counts = {(str(row.get(x, "")), str(row.get(color, ""))): float(row.get(y, 0) or 0) for row in rows}
                bottoms = [0.0 for _ in x_values]
                width_px, height_px = _compact_bar_figure_size(unique_x)
                dpi = 120
                fig, ax = plt.subplots(figsize=(width_px / dpi, height_px / dpi), dpi=dpi)
                positions = list(range(len(x_values)))
                for color_value in color_values:
                    vals = [counts.get((xv, color_value), 0.0) for xv in x_values]
                    ax.bar(positions, vals, bottom=bottoms, label=color_value)
                    bottoms = [a + b for a, b in zip(bottoms, vals)]
                ax.set_title(title)
                ax.set_xlabel(count_label)
                ax.set_ylabel(y.replace("_", " "))
                ax.tick_params(axis="x", rotation=_bar_tickangle(len(x_values)))
                if x_values:
                    ax.set_xticks(positions)
                    ax.set_xticklabels([_abbreviate_label(value) for value in x_values], fontsize=8 if len(x_values) > 12 else 10)
                ax.legend(fontsize=8)
                fig.tight_layout()
                fig.savefig(png_fpath, dpi=dpi)
                png_out = str(png_fpath)
                if jpg_out is None:
                    fig.savefig(jpg_fpath, dpi=dpi)
                    jpg_out = str(jpg_fpath)
                plt.close(fig)
        except Exception as ex:
            mpl_error = f"unable to write bar PNG/JPG via matplotlib: {ex!r}"
            logger.warning("{} ({})", mpl_error, title)
    return {
        "json": str(json_fpath),
        "html": html_out,
        "jpg": jpg_out,
        "png": png_out,
        "plotly_error": plotly_error,
        "mpl_error": mpl_error,
    }


@profile
def _write_agreement_curve_plot(
    repro_rows: list[dict[str, Any]],
    enriched_rows: list[dict[str, Any]],
    stem: Path,
    title: str,
    machine_dpath: Path | None = None,
    interactive_dpath: Path | None = None,
    static_dpath: Path | None = None,
    scope_title: str | None = None,
) -> dict[str, str | None]:
    """Line chart: x=abs_tol (log), y=instance agree_ratio, one line per analyzed run."""
    bench_lookup = {
        (str(r.get("experiment_name")), str(r.get("run_entry"))): str(r.get("benchmark") or "unknown")
        for r in enriched_rows
    }
    meta_lookup = {
        (str(r.get("experiment_name")), str(r.get("run_entry"))): r
        for r in enriched_rows
    }
    def _clean_value(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if text.lower() in {"unknown", "none", "nan"}:
            return ""
        return text

    def _rowwise_cardinality(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> int:
        values: set[str] = set()
        for row in rows:
            resolved = ""
            for key in keys:
                resolved = _clean_value(row.get(key))
                if resolved:
                    break
            if resolved:
                values.add(resolved)
        return len(values)

    curve_data: list[dict[str, Any]] = []
    curve_rows: list[dict[str, Any]] = []
    for row in repro_rows:
        key = (str(row.get("experiment_name")), str(row.get("run_entry")))
        bench = bench_lookup.get(key, "unknown")
        curve = row.get("official_instance_agree_curve") or []
        if not curve:
            continue
        curve_rows.append(row)
        run_label = str(row.get("run_spec_name") or row.get("run_entry") or "unknown")
        for pt in curve:
            curve_data.append({
                "benchmark": bench,
                "run": run_label,
                "abs_tol": pt["abs_tol"],
                "agree_ratio": pt["agree_ratio"],
            })
    contributing_rows = [meta_lookup.get((str(row.get("experiment_name")), str(row.get("run_entry")))) for row in curve_rows]
    contributing_rows = [row for row in contributing_rows if row is not None]
    n_runs = len({(str(row.get("experiment_name")), str(row.get("run_entry"))) for row in curve_rows})
    n_models = _rowwise_cardinality(contributing_rows, ("model",))
    n_scenarios = _rowwise_cardinality(contributing_rows, ("scenario", "benchmark", "suite"))
    title_text = title
    if scope_title is not None:
        title_text = (
            "Agreement Rate vs Tolerance (instance-level; "
            f"n_runs={n_runs}, n_models={n_models}, n_scenarios={n_scenarios}): {scope_title}"
        )

    if machine_dpath is not None:
        machine_dpath.mkdir(parents=True, exist_ok=True)
        json_fpath = (machine_dpath / stem.name).with_suffix(".json")
    else:
        json_fpath = stem.with_suffix(".json")
    _interactive = interactive_dpath if interactive_dpath is not None else stem.parent
    _static = static_dpath if static_dpath is not None else stem.parent
    _interactive.mkdir(parents=True, exist_ok=True)
    _static.mkdir(parents=True, exist_ok=True)
    html_fpath = (_interactive / stem.name).with_suffix(".html")
    jpg_fpath = (_static / stem.name).with_suffix(".jpg")
    _write_json(curve_data, json_fpath)

    html_out = None
    jpg_out = None
    plotly_error = None
    if os.environ.get("HELM_AUDIT_SKIP_PLOTLY", "") in {"1", "true", "yes"}:
        plotly_error = "skipped by configuration"
    elif not curve_data:
        plotly_error = "no agreement curve data available"
    else:
        try:
            configure_plotly_chrome()
            import plotly.graph_objects as go

            # Assign a color per benchmark
            benchmarks = sorted(set(d["benchmark"] for d in curve_data))
            palette = [
                "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
                "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
            ]
            bench_color = {b: palette[i % len(palette)] for i, b in enumerate(benchmarks)}

            fig = go.Figure()
            seen_benchmarks: set[str] = set()
            for row in repro_rows:
                key = (str(row.get("experiment_name")), str(row.get("run_entry")))
                bench = bench_lookup.get(key, "unknown")
                curve = row.get("official_instance_agree_curve") or []
                if not curve:
                    continue
                run_label = str(row.get("run_spec_name") or row.get("run_entry") or "unknown")
                tols = [max(pt["abs_tol"], 1e-13) for pt in curve]  # avoid log(0)
                ratios = [pt["agree_ratio"] for pt in curve]
                show_legend = bench not in seen_benchmarks
                seen_benchmarks.add(bench)
                fig.add_trace(go.Scatter(
                    x=tols,
                    y=ratios,
                    mode="lines+markers",
                    name=bench,
                    legendgroup=bench,
                    showlegend=show_legend,
                    line={"color": bench_color[bench], "width": 1.5},
                    marker={"size": 5},
                    opacity=0.75,
                    hovertemplate=(
                        f"<b>{bench}</b><br>"
                        "abs_tol=%{x:.2e}<br>"
                        "agree_ratio=%{y:.3f}<br>"
                        f"run={run_label[:60]}<extra></extra>"
                    ),
                ))
            fig.update_layout(
                title=title_text,
                xaxis={"title": "abs_tol (tolerance on |official - local|)", "type": "log"},
                yaxis={"title": "Fraction of Instances Agreeing", "range": [0, 1.05]},
                legend={"title": "Benchmark"},
                hovermode="closest",
            )
            fig.write_html(str(html_fpath), include_plotlyjs="cdn")
            html_out = str(html_fpath)
            if os.environ.get("HELM_AUDIT_SKIP_STATIC_IMAGES", "") not in {"1", "true", "yes"}:
                static_width = min(max(1200, 96 * max(len(benchmarks), 1)), 1800)
                static_height = 880
                fig.update_layout(
                    width=static_width,
                    height=static_height,
                    margin={"t": 100, "b": 180, "l": 70, "r": 50},
                    legend={
                        "title": "Benchmark",
                        "orientation": "h",
                        "x": 0,
                        "xanchor": "left",
                        "y": -0.24,
                        "yanchor": "top",
                        "font": {"size": 9},
                    },
                )
                fig.write_image(str(jpg_fpath), width=static_width, height=static_height, scale=1.0)
                jpg_out = str(jpg_fpath)
        except Exception as ex:
            plotly_error = f"unable to write agreement curve: {ex!r}"

    return {"json": str(json_fpath), "html": html_out, "jpg": jpg_out, "plotly_error": plotly_error}


@profile
def _write_per_metric_agreement_plot(
    repro_rows: list[dict[str, Any]],
    enriched_rows: list[dict[str, Any]],
    stem: Path,
    title: str,
    machine_dpath: Path | None = None,
    interactive_dpath: Path | None = None,
    static_dpath: Path | None = None,
) -> dict[str, str | None]:
    """Per-metric agreement curves: one plot per metric showing agreement across all runs."""
    bench_lookup = {
        (str(r.get("experiment_name")), str(r.get("run_entry"))): str(r.get("benchmark") or "unknown")
        for r in enriched_rows
    }

    # Collect per-metric data: metric -> [(abs_tol, agree_ratio, run, benchmark), ...]
    metrics_data: dict[str, list[dict[str, Any]]] = {}
    for row in repro_rows:
        key = (str(row.get("experiment_name")), str(row.get("run_entry")))
        bench = bench_lookup.get(key, "unknown")
        per_metric = row.get("official_per_metric_agreement") or {}
        run_label = str(row.get("run_spec_name") or row.get("run_entry") or "unknown")

        for metric, curve_points in per_metric.items():
            if metric not in metrics_data:
                metrics_data[metric] = []
            for pt in (curve_points or []):
                metrics_data[metric].append({
                    "metric": metric,
                    "benchmark": bench,
                    "run": run_label,
                    "abs_tol": pt.get("abs_tol", 0),
                    "agree_ratio": pt.get("agree_ratio", 0),
                })

    if machine_dpath is not None:
        machine_dpath.mkdir(parents=True, exist_ok=True)
        json_fpath = (machine_dpath / stem.name).with_suffix(".json")
    else:
        json_fpath = stem.with_suffix(".json")
    _interactive = interactive_dpath if interactive_dpath is not None else stem.parent
    _static = static_dpath if static_dpath is not None else stem.parent
    _interactive.mkdir(parents=True, exist_ok=True)
    _static.mkdir(parents=True, exist_ok=True)
    html_fpath = (_interactive / stem.name).with_suffix(".html")
    jpg_fpath = (_static / stem.name).with_suffix(".jpg")
    _write_json(metrics_data, json_fpath)

    html_out = None
    jpg_out = None
    plotly_error = None
    if os.environ.get("HELM_AUDIT_SKIP_PLOTLY", "") in {"1", "true", "yes"}:
        plotly_error = "skipped by configuration"
    elif not metrics_data:
        plotly_error = "no per-metric agreement data available"
    else:
        try:
            configure_plotly_chrome()
            import plotly.graph_objects as go
            from plotly.subplots import make_subplots

            metrics = sorted(metrics_data.keys())
            n_cols = min(3, len(metrics))
            n_rows = (len(metrics) + n_cols - 1) // n_cols

            # Assign a color per benchmark
            all_benchmarks = sorted(set(
                d["benchmark"]
                for metric_pts in metrics_data.values()
                for d in metric_pts
            ))
            palette = [
                "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
                "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
            ]
            bench_color = {b: palette[i % len(palette)] for i, b in enumerate(all_benchmarks)}

            fig = make_subplots(
                rows=n_rows,
                cols=n_cols,
                subplot_titles=metrics,
                specs=[[{"secondary_y": False} for _ in range(n_cols)] for _ in range(n_rows)],
            )

            seen_benchmarks: set[str] = set()
            for metric_idx, metric in enumerate(metrics):
                row_idx = (metric_idx // n_cols) + 1
                col_idx = (metric_idx % n_cols) + 1
                # curve_pts = metrics_data[metric]

                for row in repro_rows:
                    key = (str(row.get("experiment_name")), str(row.get("run_entry")))
                    bench = bench_lookup.get(key, "unknown")
                    run_label = str(row.get("run_spec_name") or row.get("run_entry") or "unknown")
                    per_metric = row.get("official_per_metric_agreement") or {}
                    curve = per_metric.get(metric) or []
                    if not curve:
                        continue

                    tols = [max(pt.get("abs_tol", 1e-13), 1e-13) for pt in curve]
                    ratios = [pt.get("agree_ratio", 0) for pt in curve]
                    show_legend = bench not in seen_benchmarks
                    seen_benchmarks.add(bench)

                    fig.add_trace(
                        go.Scatter(
                            x=tols,
                            y=ratios,
                            mode="lines+markers",
                            name=bench,
                            legendgroup=bench,
                            showlegend=show_legend,
                            line={"color": bench_color[bench], "width": 1.5},
                            marker={"size": 4},
                            opacity=0.7,
                            hovertemplate=(
                                f"<b>{bench}</b><br>"
                                "abs_tol=%{x:.2e}<br>"
                                "agree_ratio=%{y:.3f}<br>"
                                f"metric={metric}<br>"
                                f"run={run_label[:50]}<extra></extra>"
                            ),
                        ),
                        row=row_idx,
                        col=col_idx,
                    )

            # Update axes
            for metric_idx, metric in enumerate(metrics):
                row_idx = (metric_idx // n_cols) + 1
                col_idx = (metric_idx % n_cols) + 1
                fig.update_xaxes(title_text="abs_tol", type="log", row=row_idx, col=col_idx)
                fig.update_yaxes(title_text="agreement", range=[0, 1.05], row=row_idx, col=col_idx)

            fig.update_layout(
                title=title,
                height=max(400, 350 * n_rows),
                showlegend=True,
                hovermode="closest",
                legend={"title": "Benchmark"},
            )
            fig.write_html(str(html_fpath), include_plotlyjs="cdn")
            html_out = str(html_fpath)
            if os.environ.get("HELM_AUDIT_SKIP_STATIC_IMAGES", "") not in {"1", "true", "yes"}:
                static_width = min(max(1100, 420 * n_cols), 1600)
                static_height = min(max(520, 320 * n_rows), 1200)
                fig.write_image(str(jpg_fpath), width=static_width, height=static_height, scale=1.0)
                jpg_out = str(jpg_fpath)
        except Exception as ex:
            plotly_error = f"unable to write per-metric agreement: {ex!r}"

    return {"json": str(json_fpath), "html": html_out, "jpg": jpg_out, "plotly_error": plotly_error}


@profile
def _write_coverage_matrix_plot(
    enriched_rows: list[dict[str, Any]],
    repro_rows: list[dict[str, Any]],
    stem: Path,
    title: str,
    machine_dpath: Path | None = None,
    interactive_dpath: Path | None = None,
    static_dpath: Path | None = None,
) -> dict[str, str | None]:
    """Heatmap: rows=model, cols=benchmark, color=best status for that cell."""
    # Status levels (higher = better)
    # P1-9: analyzed-but-no-agreement-data (empty/unrecognized bucket — e.g. a
    # join failure that produced no overlapping instances) gets its own level,
    # so it is no longer painted as "analyzed: low agreement (<80%)" — a false
    # reproducibility failure.
    STATUS_LEVEL = {
        "all_failed": 0,
        "completed_not_analyzed": 1,
        "analyzed_no_data": 2,
        "analyzed_low": 3,
        "analyzed_moderate": 4,
        "analyzed_high": 5,
        "analyzed_exact": 6,
    }
    STATUS_LABEL = {
        0: "all failed",
        1: "completed, not yet analyzed",
        2: "analyzed: no agreement data (join failure?)",
        3: "analyzed: low agreement (<80%)",
        4: "analyzed: moderate agreement (80-95%)",
        5: "analyzed: high agreement (95%+)",
        6: "analyzed: exact / near-exact",
    }
    repro_keyed = {
        (str(r.get("experiment_name")), str(r.get("run_entry"))): r
        for r in repro_rows
    }
    # Build best-status per (model, benchmark) cell
    cell_status: dict[tuple[str, str], int] = {}
    cell_counts: dict[tuple[str, str], dict[str, int]] = {}
    for row in enriched_rows:
        model = str(row.get("model") or "unknown")
        bench = str(row.get("benchmark") or "unknown")
        key = (model, bench)
        counts = cell_counts.setdefault(key, {"total": 0, "completed": 0, "analyzed": 0, "failed": 0})
        counts["total"] += 1
        if row.get("completed_with_run_artifacts"):
            counts["completed"] += 1
            rkey = (str(row.get("experiment_name")), str(row.get("run_entry")))
            repro = repro_keyed.get(rkey)
            if repro:
                counts["analyzed"] += 1
                bucket = repro.get("official_instance_agree_bucket") or ""
                if "exact" in bucket:
                    level = STATUS_LEVEL["analyzed_exact"]
                elif "high" in bucket:
                    level = STATUS_LEVEL["analyzed_high"]
                elif "moderate" in bucket:
                    level = STATUS_LEVEL["analyzed_moderate"]
                elif "low" in bucket or "zero" in bucket:
                    level = STATUS_LEVEL["analyzed_low"]
                else:
                    # P1-9: empty / not-an-agreement bucket (e.g.
                    # completed_not_yet_analyzed, or a join failure that yielded
                    # no measurable agreement) is NOT a low-agreement result.
                    level = STATUS_LEVEL["analyzed_no_data"]
            else:
                level = STATUS_LEVEL["completed_not_analyzed"]
        else:
            counts["failed"] += 1
            level = STATUS_LEVEL["all_failed"]
        cell_status[key] = max(cell_status.get(key, -1), level)

    models = sorted({m for m, _ in cell_status})
    benchmarks = sorted({b for _, b in cell_status})
    matrix: list[list[int]] = []
    hover_matrix: list[list[str]] = []
    for model in models:
        row_vals = []
        row_hover = []
        for bench in benchmarks:
            key = (model, bench)
            level = cell_status.get(key, -1)
            counts = cell_counts.get(key, {})
            row_vals.append(level)
            if level == -1:
                row_hover.append("not attempted")
            else:
                label = STATUS_LABEL.get(level, "unknown")
                total = counts.get("total", 0)
                completed = counts.get("completed", 0)
                analyzed = counts.get("analyzed", 0)
                row_hover.append(
                    f"{label}<br>total={total} completed={completed} analyzed={analyzed}"
                )
        matrix.append(row_vals)
        hover_matrix.append(row_hover)

    matrix_data = {
        "models": models,
        "benchmarks": benchmarks,
        "matrix": matrix,
        "status_level_meanings": STATUS_LABEL,
    }
    if machine_dpath is not None:
        machine_dpath.mkdir(parents=True, exist_ok=True)
        json_fpath = (machine_dpath / stem.name).with_suffix(".json")
    else:
        json_fpath = stem.with_suffix(".json")
    _interactive = interactive_dpath if interactive_dpath is not None else stem.parent
    _static = static_dpath if static_dpath is not None else stem.parent
    _interactive.mkdir(parents=True, exist_ok=True)
    _static.mkdir(parents=True, exist_ok=True)
    html_fpath = (_interactive / stem.name).with_suffix(".html")
    jpg_fpath = (_static / stem.name).with_suffix(".jpg")
    _write_json(matrix_data, json_fpath)

    html_out = None
    jpg_out = None
    plotly_error = None
    if os.environ.get("HELM_AUDIT_SKIP_PLOTLY", "") in {"1", "true", "yes"}:
        plotly_error = "skipped by configuration"
    elif not models or not benchmarks:
        plotly_error = "no data for coverage matrix"
    else:
        try:
            configure_plotly_chrome()
            import plotly.graph_objects as go

            # 8 stops over z in [-1, 6] (P1-9 added the analyzed_no_data level).
            colorscale = [
                [0.0 / 7, "#f0f0f0"],    # -1 not attempted (grey)
                [1.0 / 7, "#d62728"],    # 0 all_failed (red)
                [2.0 / 7, "#ffdd57"],    # 1 completed not analyzed (yellow)
                [3.0 / 7, "#9edae5"],    # 2 analyzed no agreement data (pale cyan)
                [4.0 / 7, "#ff7f0e"],    # 3 analyzed low (orange)
                [5.0 / 7, "#aec7e8"],    # 4 analyzed moderate (light blue)
                [6.0 / 7, "#1f77b4"],    # 5 analyzed high (blue)
                [7.0 / 7, "#2ca02c"],    # 6 analyzed exact (green)
            ]
            fig = go.Figure(go.Heatmap(
                z=matrix,
                x=benchmarks,
                y=models,
                text=hover_matrix,
                hovertemplate="%{y} × %{x}<br>%{text}<extra></extra>",
                colorscale=colorscale,
                zmin=-1,
                zmax=6,
                colorbar={
                    "title": "Status",
                    "tickvals": [-1, 0, 1, 2, 3, 4, 5, 6],
                    "ticktext": [
                        "not attempted",
                        "all failed",
                        "completed (not analyzed)",
                        "analyzed: no agreement data",
                        "analyzed: low agreement",
                        "analyzed: moderate",
                        "analyzed: high",
                        "analyzed: exact/near-exact",
                    ],
                },
            ))
            fig.update_layout(
                title=title,
                xaxis={"title": "Benchmark", "tickangle": -45},
                yaxis={"title": "Model"},
                height=max(400, 60 + 40 * len(models)),
            )
            fig.write_html(str(html_fpath), include_plotlyjs="cdn")
            html_out = str(html_fpath)
            if os.environ.get("HELM_AUDIT_SKIP_STATIC_IMAGES", "") not in {"1", "true", "yes"}:
                static_benchmark_labels = [_abbreviate_label(value) for value in benchmarks]
                static_model_labels = [_abbreviate_label(value) for value in models]
                benchmark_angle = 90 if len(benchmarks) > 40 else 75 if len(benchmarks) > 25 else 60 if len(benchmarks) > 12 else -45
                fig.update_xaxes(
                    tickmode="array",
                    tickvals=benchmarks,
                    ticktext=static_benchmark_labels,
                    tickangle=benchmark_angle,
                    automargin=True,
                )
                fig.update_yaxes(
                    tickmode="array",
                    tickvals=models,
                    ticktext=static_model_labels,
                    automargin=True,
                )
                static_width = min(max(1100, 22 * max(len(benchmarks), 1) + 12 * max((len(label) for label in benchmarks), default=0)), 1800)
                static_height = min(max(520, 18 * max(len(models), 1) + 220), 1200)
                fig.write_image(str(jpg_fpath), width=static_width, height=static_height, scale=1.0)
                jpg_out = str(jpg_fpath)
        except Exception as ex:
            plotly_error = f"unable to write coverage matrix: {ex!r}"

    return {"json": str(json_fpath), "html": html_out, "jpg": jpg_out, "plotly_error": plotly_error}


@profile
def _write_failure_taxonomy_plot(
    failed_rows: list[dict[str, Any]],
    stem: Path,
    title: str,
    machine_dpath: Path | None = None,
    interactive_dpath: Path | None = None,
    static_dpath: Path | None = None,
) -> dict[str, str | None]:
    """Stacked bar: x=benchmark, color=failure root-cause category, y=job count."""
    from collections import defaultdict

    counts: dict[tuple[str, str], int] = defaultdict(int)
    for row in failed_rows:
        bench = str(row.get("benchmark") or "unknown")
        reason = str(row.get("failure_reason") or "unknown_failure")
        cat_key, _ = _FAILURE_CATEGORIES.get(reason, ("unknown", "Unknown / Other"))
        counts[(bench, cat_key)] += 1

    bar_rows: list[dict[str, Any]] = [
        {"benchmark": bench, "category": cat_key, "label": _FAILURE_CATEGORY_LABELS[cat_key], "count": count}
        for (bench, cat_key), count in sorted(counts.items())
    ]
    # Total failures per benchmark for sort order
    bench_totals: dict[str, int] = defaultdict(int)
    for r in bar_rows:
        bench_totals[r["benchmark"]] += r["count"]
    bench_order = sorted(bench_totals, key=lambda b: -bench_totals[b])

    if machine_dpath is not None:
        machine_dpath.mkdir(parents=True, exist_ok=True)
        json_fpath = (machine_dpath / stem.name).with_suffix(".json")
    else:
        json_fpath = stem.with_suffix(".json")
    _interactive = interactive_dpath if interactive_dpath is not None else stem.parent
    _static = static_dpath if static_dpath is not None else stem.parent
    _interactive.mkdir(parents=True, exist_ok=True)
    _static.mkdir(parents=True, exist_ok=True)
    html_fpath = (_interactive / stem.name).with_suffix(".html")
    jpg_fpath = (_static / stem.name).with_suffix(".jpg")
    _write_json(bar_rows, json_fpath)

    html_out = None
    jpg_out = None
    plotly_error = None
    count_label = _bar_count_label("benchmark", len(bench_order), axis_title="Benchmark")
    if os.environ.get("HELM_AUDIT_SKIP_PLOTLY", "") in {"1", "true", "yes"}:
        plotly_error = "skipped by configuration"
    elif not bar_rows:
        plotly_error = "no failure data"
    else:
        try:
            configure_plotly_chrome()
            import plotly.graph_objects as go

            # P0-7: a color for every category in _FAILURE_CATEGORY_ORDER (the
            # old map lacked policy_blocked/recipe_error -> KeyError -> the whole
            # chart was swallowed by the outer except and never written). Missing
            # keys degrade to grey rather than raising.
            cat_colors = {
                "incomplete_runtime": "#8c564b",
                "compute_resource": "#d62728",
                "data_access": "#ff7f0e",
                "network": "#1f77b4",
                "permissions": "#e377c2",
                "missing_infrastructure": "#9467bd",
                "policy_blocked": "#2ca02c",
                "recipe_error": "#bcbd22",
                "interrupted": "#17becf",
                "unknown": "#7f7f7f",
            }
            _GREY = "#7f7f7f"
            fig = go.Figure()
            for cat_key in _FAILURE_CATEGORY_ORDER:
                cat_label = _FAILURE_CATEGORY_LABELS[cat_key]
                y_vals = [
                    sum(r["count"] for r in bar_rows if r["benchmark"] == b and r["category"] == cat_key)
                    for b in bench_order
                ]
                # P0-7: skip all-zero categories so the legend only lists
                # categories that actually occurred.
                if not any(y_vals):
                    continue
                fig.add_trace(go.Bar(
                    name=cat_label,
                    x=bench_order,
                    y=y_vals,
                    marker_color=cat_colors.get(cat_key, _GREY),
                    hovertemplate=f"<b>{cat_label}</b><br>benchmark=%{{x}}<br>count=%{{y}}<extra></extra>",
                ))
            fig.update_layout(
                title=title,
                barmode="stack",
                xaxis={"title": count_label, "tickangle": -45, "categoryorder": "array", "categoryarray": bench_order},
                yaxis={"title": "Failed Job Count"},
                legend={"title": "Root Cause Category"},
            )
            fig.update_xaxes(
                tickmode="array",
                tickvals=bench_order,
                ticktext=bench_order,
                tickangle=-45,
                automargin=True,
            )
            fig.write_html(str(html_fpath), include_plotlyjs="cdn")
            html_out = str(html_fpath)
            if os.environ.get("HELM_AUDIT_SKIP_STATIC_IMAGES", "") not in {"1", "true", "yes"}:
                static_width, static_height = _compact_bar_figure_size(bench_order)
                fig.update_layout(width=static_width, height=static_height, margin={"b": min(max(120, 8 * max((len(v) for v in bench_order), default=0)), 220), "t": 80, "l": 70, "r": 30})
                fig.update_xaxes(
                    ticktext=[_abbreviate_label(value) for value in bench_order],
                    tickangle=_bar_tickangle(len(bench_order)),
                    tickfont={"size": 8 if len(bench_order) > 12 else 10},
                )
                fig.write_image(str(jpg_fpath), scale=1.0)
                jpg_out = str(jpg_fpath)
        except Exception as ex:
            plotly_error = f"unable to write failure taxonomy: {ex!r}"

    return {"json": str(json_fpath), "html": html_out, "jpg": jpg_out, "plotly_error": plotly_error}

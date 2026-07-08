"""EEE-only reproducibility heatmap.

Reads ``core_metric_report.json`` files produced by ``eval-audit-from-eee``,
groups by ``(model, benchmark_family)`` using the ``logical_run_key`` stored
in each report's component list, micro-averages the instance-level
official_vs_local agreement fraction at a given ``abs_tol``, and renders a
model × benchmark heatmap.

Each cell value is:

    agree_ratio = sum(matched) / sum(count)

across all ``official_vs_local`` pairs in all packets for that
(model, benchmark_family) combination.  Missing cells (no official or no
local artifact) are shown as gray "N/A".

CLI::

    python -m eval_audit.reports.eee_only_heatmap \\
        --analysis-root <from_eee_out_dir> \\
        --out-dir <output_dir> \\
        [--abs-tol 1e-9] [--title "Reproducibility Heatmap"]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import safer
from loguru import logger

from eval_audit.infra.fs_publish import write_text_atomic
from eval_audit.infra.logging import rich_link, setup_cli_logging
from eval_audit.infra.report_layout import (
    portable_repo_root_lines,
    write_reproduce_script,
)

from eval_audit.infra.profiling import profile

# --- compat re-exports -------------------------------------------------
# Implementation moved to reports.eee_heatmap_{data,render} on 2026-06-11
# (Phase 2 of docs/historical/planning/repo-refactor-plan.md). This module stays the
# 'python -m eval_audit.reports.eee_only_heatmap' surface used by
# reproduce/eee_only_reproducibility_heatmap/30_heatmap.sh.
from eval_audit.reports.eee_heatmap_data import (  # noqa: F401
    _MODEL_DISPLAY,
    _BENCHMARK_DISPLAY,
    _BENCHMARK_ORDER,
    _MODEL_ORDER,
    _BOOKKEEPING_METRICS,
    _benchmark_family,
    _model_from_component,
    _collect_cells,
    _collect_cells_per_metric,
    _find_tol_row,
    _save_cell_data,
)
from eval_audit.reports.eee_heatmap_render import (  # noqa: F401
    _render_text_table,
    _atomic_savefig,
    _PAPER_FONT_STACK,
    _paper_rc,
    _render_heatmap,
    _FILENAME_SAFE_RE,
    _safe_filename_part,
    _render_per_metric_heatmaps,
    _render_per_metric_text_table,
    _write_redraw_plots_script,
)


def main(argv: list[str] | None = None) -> None:
    setup_cli_logging()
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--analysis-root",
        required=True,
        help="Root of the eval-audit-from-eee output (contains core_metric_report.json files).",
    )
    parser.add_argument(
        "--out-dir",
        required=True,
        help="Directory to write heatmap outputs into.",
    )
    parser.add_argument(
        "--abs-tol",
        type=float,
        default=1e-9,
        help="Agreement threshold (default: 1e-9, between exact-match and 10-pico).",
    )
    parser.add_argument(
        "--title",
        default="EEE-only reproducibility heatmap",
        help="Figure title.",
    )
    parser.add_argument(
        "--per-metric",
        action="store_true",
        default=False,
        help=(
            "Also emit a per-(benchmark, metric) heatmap. Drills down "
            "from the one-number-per-cell view to show which scoring "
            "metric is responsible for a benchmark's agree_ratio. The "
            "regular benchmark-level heatmap is still written."
        ),
    )
    parser.add_argument(
        "--include-bookkeeping",
        action="store_true",
        default=False,
        help=(
            "Include bookkeeping metrics (token counts, finish_reason, "
            "inference_runtime, etc.) in the per-metric heatmap. Default "
            "off because these are deterministic and uniformly "
            "reproducible — they bury the interesting score-level "
            "metrics under a sea of 1.0 cells."
        ),
    )
    parser.add_argument(
        "--transpose",
        action="store_true",
        default=False,
        help=(
            "Render the heatmap transposed: rows = models, columns = "
            "benchmarks. Produces a wide-and-short figure that fits as a "
            "single \\linewidth figure in a paper column. Applies to the "
            "main heatmap and to per-metric drill-downs when "
            "--per-metric is also set."
        ),
    )
    parser.add_argument(
        "--no-subtitle",
        action="store_true",
        default=False,
        help=(
            "Suppress the in-figure subtitle line "
            "('instance-level agree_ratio at abs_tol=…'). Use this for "
            "paper figures where the same information is in the LaTeX "
            "caption and the in-figure subtitle is redundant."
        ),
    )
    args = parser.parse_args(argv)

    analysis_root = Path(args.analysis_root).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    abs_tol: float = args.abs_tol
    title: str = args.title

    if not analysis_root.exists():
        raise SystemExit(f"FAIL: analysis-root does not exist: {analysis_root}")

    logger.info(
        f"Collecting cell data from {rich_link(analysis_root)} "
        f"(abs_tol={abs_tol}) ..."
    )
    cells = _collect_cells(analysis_root, abs_tol)
    logger.info(f"  found {len(cells)} (model, benchmark) cells with data")

    # Determine which models / benchmarks appear in the data
    found_models = {m for (m, _) in cells}
    found_benchmarks = {b for (_, b) in cells}

    models = [m for m in _MODEL_ORDER if m in found_models]
    # Include any extra models not in the canonical order
    models += sorted(found_models - set(_MODEL_ORDER))

    benchmarks = [b for b in _BENCHMARK_ORDER if b in found_benchmarks]
    benchmarks += sorted(found_benchmarks - set(_BENCHMARK_ORDER))

    if not models or not benchmarks:
        raise SystemExit(
            "FAIL: no cell data found. "
            "Check that 20_run.sh completed and produced core_metric_report.json files "
            f"under {analysis_root}."
        )

    out_dir.mkdir(parents=True, exist_ok=True)

    # Drop a redraw_plots.sh next to the outputs so iterating on plot
    # styling is "edit code → rerun this script". Written before the
    # rest of the renders so even a partial render leaves a regen
    # script behind.
    redraw_path = _write_redraw_plots_script(
        out_dir=out_dir,
        analysis_root=analysis_root,
        abs_tol=abs_tol,
        title=title,
        per_metric=args.per_metric,
        include_bookkeeping=args.include_bookkeeping,
        transpose=args.transpose,
        no_subtitle=args.no_subtitle,
    )
    logger.info(f"Wrote regen script: {rich_link(redraw_path)}")

    # Text table
    text = _render_text_table(cells, models, benchmarks, abs_tol)
    txt_path = out_dir / "reproducibility_heatmap.txt"
    write_text_atomic(txt_path, text)
    print(text)
    logger.info(f"Wrote text table: {rich_link(txt_path)}")

    # JSON cell data
    _save_cell_data(cells, models, benchmarks, abs_tol, out_dir)

    # Heatmap PNG
    main_subtitle: str | None = "" if args.no_subtitle else None
    try:
        _render_heatmap(
            cells, models, benchmarks, abs_tol, title, out_dir,
            transpose=args.transpose,
            subtitle=main_subtitle,
        )
    except ImportError as exc:
        logger.warning(
            f"matplotlib not available ({exc}); skipping PNG output."
        )

    # Optional per-metric drill-down: one figure per metric, each shaped
    # like the main heatmap (rows = benchmarks, columns = models). The
    # text table and JSON sidecar still list everything in one document
    # so downstream scripts can grep/sort without walking the subdir.
    if args.per_metric:
        logger.info(
            f"Collecting per-(model, benchmark, metric) cells "
            f"(abs_tol={abs_tol}, include_bookkeeping={args.include_bookkeeping}) ..."
        )
        per_metric_cells = _collect_cells_per_metric(
            analysis_root, abs_tol,
            include_bookkeeping=args.include_bookkeeping,
        )
        logger.info(f"  found {len(per_metric_cells)} cells")

        # Row order for the combined text/JSON: walk benchmarks in
        # canonical order, within each benchmark sort metrics alphabetically.
        rows_in_order: list[tuple[str, str]] = []
        for bench in benchmarks:
            metrics_for_bench = sorted({
                metric for (_m, b, metric) in per_metric_cells if b == bench
            })
            rows_in_order.extend((bench, metric) for metric in metrics_for_bench)

        # Plot order: alphabetical by metric name. One figure per metric,
        # so cross-metric comparison is "open the next file" not "scroll
        # the same figure."
        metrics_in_order = sorted({
            metric for (_m, _b, metric) in per_metric_cells
        })

        if not rows_in_order:
            logger.warning("no per-metric cells found; skipping per-metric output.")
        else:
            text_pm = _render_per_metric_text_table(
                per_metric_cells, models, rows_in_order, abs_tol,
            )
            txt_pm = out_dir / "reproducibility_heatmap_per_metric.txt"
            write_text_atomic(txt_pm, text_pm)
            print(text_pm)
            logger.info(f"Wrote per-metric text table: {rich_link(txt_pm)}")

            # Per-metric JSON sidecar — flat list of (model, benchmark,
            # metric, agree_ratio, status, ...) so downstream scripts can
            # filter/sort without re-walking the per-pair reports.
            json_pm = out_dir / "cell_data_per_metric.json"
            pm_rows = [
                {
                    "model": m,
                    "benchmark": b,
                    "metric": metric,
                    "abs_tol": abs_tol,
                    **per_metric_cells[(m, b, metric)],
                }
                for (b, metric) in rows_in_order
                for m in models
                if (m, b, metric) in per_metric_cells
            ]
            write_text_atomic(
                json_pm,
                json.dumps({"abs_tol": abs_tol, "cells": pm_rows}, indent=2) + "\n",
            )
            logger.info(f"Wrote per-metric cell data: {rich_link(json_pm)}")

            try:
                written = _render_per_metric_heatmaps(
                    per_metric_cells, models, benchmarks, metrics_in_order,
                    abs_tol, title, out_dir,
                    transpose=args.transpose,
                    subtitle_override=("" if args.no_subtitle else None),
                )
                logger.info(
                    f"Wrote {len(written)} per-metric heatmap(s) under "
                    f"{rich_link(out_dir / 'reproducibility_heatmap_per_metric')}"
                )
            except ImportError as exc:
                logger.warning(
                    f"matplotlib not available ({exc}); "
                    "skipping per-metric PNG output."
                )


if __name__ == "__main__":
    main()

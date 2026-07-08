"""Matplotlib rendering for the EEE-only reproducibility heatmap:
text tables, the heatmap figure, per-metric drill-downs, and the
redraw script writer.

Split out of ``eval_audit.reports.eee_only_heatmap`` on 2026-06-11
(Phase 2 of docs/historical/planning/repo-refactor-plan.md). Pure relocation:
function bodies are unchanged.
"""
from __future__ import annotations
import os
import re
from pathlib import Path
from typing import Any
import safer
from loguru import logger
from eval_audit.infra.logging import rich_link
from eval_audit.infra.report_layout import (
    portable_repo_root_lines,
    write_reproduce_script,
)
from eval_audit.infra.profiling import profile
from eval_audit.reports.eee_heatmap_data import _BENCHMARK_DISPLAY, _MODEL_DISPLAY


# ---------------------------------------------------------------------------
# Text summary
# ---------------------------------------------------------------------------


def _render_text_table(
    cells: dict[tuple[str, str], dict[str, Any]],
    models: list[str],
    benchmarks: list[str],
    abs_tol: float,
) -> str:
    """Render a fixed-width table with four cell states::

        0.987    -> present (number is the agree_ratio at abs_tol)
        join0/3  -> join_failed: sample_hashes never overlapped between
                    official and local. Upstream data problem.
        nocore   -> no_core_metrics: data joined but every row was
                    filtered by classify_metric. Analyzer-side gap;
                    register the missing metric family in
                    eval_audit/helm/metrics.py:CORE_PREFIXES.
        --       -> missing: no packet exists for this (model, bench)
    """
    lines: list[str] = [
        f"Reproducibility heatmap (abs_tol={abs_tol})",
        f"Instance-level agree_ratio: fraction of pairs within ±{abs_tol}",
        "",
        "Cell legend:",
        "  0.987    instance-level agree_ratio at the chosen abs_tol",
        "  join0/N  no hash overlap (upstream data problem)",
        "  nocore   joined but no recognized core metrics (analyzer gap)",
        "  --       no packet for this (model, benchmark)",
        "",
    ]
    col_w = 14
    bench_w = 26
    header = f"{'Benchmark':<{bench_w}}" + "".join(
        f"{_MODEL_DISPLAY.get(m, m)[:col_w]:>{col_w}}" for m in models
    )
    lines.append(header)
    lines.append("-" * len(header))
    for bench in benchmarks:
        row = f"{_BENCHMARK_DISPLAY.get(bench, bench):<{bench_w}}"
        for m in models:
            cell = cells.get((m, bench))
            if cell is None:
                row += f"{'--':>{col_w}}"
            else:
                status = cell.get("status")
                if status == "present":
                    row += f"{cell['agree_ratio']:>{col_w}.3f}"
                elif status == "no_core_metrics":
                    row += f"{'nocore':>{col_w}}"
                else:
                    marker = f"join0/{cell.get('n_pairs_total', 0)}"
                    row += f"{marker:>{col_w}}"
        lines.append(row)
    lines.append("")
    # Coverage summary: how many cells in each state.
    n_present = sum(1 for c in cells.values() if c.get("status") == "present")
    n_join_failed = sum(1 for c in cells.values() if c.get("status") == "join_failed")
    n_no_core = sum(1 for c in cells.values() if c.get("status") == "no_core_metrics")
    n_total = len(models) * len(benchmarks)
    n_missing = n_total - n_present - n_join_failed - n_no_core
    lines.append(
        f"Coverage: {n_present} present / {n_join_failed} join_failed / "
        f"{n_no_core} no_core_metrics / {n_missing} missing  "
        f"(of {n_total} cells)"
    )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Heatmap rendering
# ---------------------------------------------------------------------------


def _atomic_savefig(fig, fpath: Path, **kwargs) -> Path:
    """``fig.savefig`` to ``fpath`` atomically via safer (parent dirs auto-
    created). Format is inferred from the suffix; defaults to png."""
    fpath = Path(fpath)
    suffix = fpath.suffix.lstrip(".") or "png"
    with safer.open(fpath, "wb", make_parents=True) as fp:
        fig.savefig(fp, format=suffix, **kwargs)
    return fpath


# Serif fonts that are reliably present on our build machines. Listed in
# preference order: STIXGeneral is Times-like and pairs well with the
# paper's Computer Modern body text; DejaVu Serif is the matplotlib
# fallback. See `python -m matplotlib.font_manager` for what's actually
# bundled.
_PAPER_FONT_STACK: list[str] = ["STIXGeneral", "DejaVu Serif"]


def _paper_rc() -> dict[str, object]:
    """rcParams used inside the paper-figure render context.

    Switches matplotlib to a serif font that visually matches the
    paper's Computer Modern body text and tightens default tick
    appearance for the despined paper layout.
    """
    return {
        "font.family": "serif",
        "font.serif": _PAPER_FONT_STACK,
        "mathtext.fontset": "stix",
        "axes.unicode_minus": False,
    }


@profile
def _render_heatmap(
    cells: dict[tuple[str, str], dict[str, Any]],
    models: list[str],
    benchmarks: list[str],
    abs_tol: float,
    title: str,
    out_dir: Path,
    *,
    out_filename: str = "reproducibility_heatmap.png",
    subtitle: str | None = None,
    transpose: bool = False,
) -> Path:
    """Render the heatmap PNG.

    ``transpose=False`` (default): rows = benchmarks, cols = models —
    the canonical tall layout used in the per-metric drill-down.

    ``transpose=True``: rows = models, cols = benchmarks — short-and-
    wide layout that fits as a single \\linewidth figure in a paper
    column. Cell drawing, colormap, and legend are unchanged; only the
    grid orientation, axis labels, and figure aspect ratio differ.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.colors as mcolors
    from matplotlib.ticker import FuncFormatter

    n_bench = len(benchmarks)
    n_models = len(models)

    if transpose:
        # Wide-and-short: cols = benchmarks, rows = models. Sized so
        # cells come out wider than tall (roughly 0.85" × 0.65" with
        # the default 14-bench × 3-model grid), which gives the cell
        # text labels — "99.7%", "78.8%", etc. — comfortable
        # horizontal breathing room without forcing tiny font sizes.
        # No title or in-figure legend in transpose/paper-compact
        # mode; the final cropwhite_ondisk + tight bbox trim any
        # residual margin.
        fig_w = max(10.0, 0.75 * n_bench + 2.5)
        fig_h = max(2.6, 0.55 * n_models + 1.1)
    else:
        fig_w = max(6.0, 2.2 * n_models + 2.0)
        fig_h = max(5.0, 0.5 * n_bench + 1.5)

    rc_ctx = plt.rc_context(_paper_rc())
    rc_ctx.__enter__()
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    # In transpose mode we *don't* call set_aspect("equal") — letting
    # cells be rectangular (wider than tall) gives the percentage
    # labels enough horizontal room to read cleanly. The default
    # (non-transpose) layout still draws square cells via the figure
    # aspect alone.

    # Colormap: a custom diverging "OrangeBlue" built from Wong's
    # colorblind-safe palette — warm orange (#E69F00) at the low end,
    # near-white at the midpoint, deep blue (#0072B2) at the high end.
    # Wong's seven-color palette is the de-facto gold standard for CVD
    # safety (deutera/protan/tritan all preserve hue separation between
    # this orange and this blue). The diverging shape gives the dense
    # 92–100% band visible blue-gradient contrast while letting low
    # outliers (e.g. 78.8%) show up as a striking orange. Every cell
    # still carries an explicit numeric percentage, so the colormap's
    # role is to draw the eye to outliers, not to encode readable
    # values. Range is tightened to [0.7, 1.0] because the paper's data
    # sits in [0.788, 1.000] and a wider range wastes gradient on values
    # that never occur. Override via env vars EVAL_AUDIT_HEATMAP_VMIN /
    # EVAL_AUDIT_HEATMAP_VMAX / EVAL_AUDIT_HEATMAP_CMAP.
    cmap_vmin = float(os.environ.get("EVAL_AUDIT_HEATMAP_VMIN", "0.7"))
    cmap_vmax = float(os.environ.get("EVAL_AUDIT_HEATMAP_VMAX", "1.0"))
    cmap_name = os.environ.get("EVAL_AUDIT_HEATMAP_CMAP", "")
    if cmap_name:
        cmap = plt.get_cmap(cmap_name)
    else:
        # Two-stop linear interp keeps the gradient saturated end-to-end
        # — no near-white midpoint that would wash out cells near the
        # transition. Linear RGB interp puts the midpoint at a muted
        # sage/olive, which preserves the impression of a continuous
        # warm-to-cool ramp without the diverging "neutral" cue.
        cmap = mcolors.LinearSegmentedColormap.from_list(
            "OrangeBlue",
            [
                (0.0, "#E69F00"),  # Wong orange (low / disagreement)
                (1.0, "#0072B2"),  # Wong blue   (high / agreement)
            ],
            N=256,
        )
    cmap_norm = mcolors.Normalize(vmin=cmap_vmin, vmax=cmap_vmax)
    cmap_scalar = plt.cm.ScalarMappable(norm=cmap_norm, cmap=cmap)

    # Background defaults to the "missing" color so any cell we don't
    # explicitly draw shows as missing.
    _MISSING_COLOR = "#bdbdbd"
    _JOIN_FAILED_COLOR = "#f0f0f0"  # neutral light gray — pairs with
                                     # diagonal hatching to mark cells
                                     # whose data is non-comparable
                                     # rather than scoring badly.
    _NO_CORE_METRICS_COLOR = "#e6dcf1"  # muted lavender — distinct from
                                         # the join_failed gray so a
                                         # reviewer can tell at a glance
                                         # that the failure is
                                         # analyzer-side (missing metric
                                         # registration), not an upstream
                                         # data problem.
    ax.set_facecolor(_MISSING_COLOR)

    # Draw each cell explicitly so the three statuses get distinct visuals.
    # In transposed layout, x=benchmarks (col), y=models (row); in the
    # default layout, x=models (col), y=benchmarks (row).
    if transpose:
        cell_value_fontsize = 9
        status_label_fontsize = 8
    else:
        cell_value_fontsize = 8
        status_label_fontsize = 7
    for i_bench, bench in enumerate(benchmarks):
        for i_model, model in enumerate(models):
            if transpose:
                col, row = i_bench, i_model
            else:
                col, row = i_model, i_bench
            cell = cells.get((model, bench))
            if cell is not None and cell.get("status") == "present":
                # Real value: colored by agree_ratio
                val = cell["agree_ratio"]
                cell_rgba = cmap(cmap_norm(val))
                rect = plt.Rectangle(
                    (col - 0.5, row - 0.5), 1, 1,
                    facecolor=cell_rgba,
                    edgecolor="white", linewidth=0.5,
                )
                ax.add_patch(rect)
                # Pick text color from the cell's actual luminance
                # (Rec. 601 weighting). This adapts to whatever
                # colormap is in use — viridis goes dark→bright, YlGn
                # light→dark, OrangeBlue diverges. White text on
                # dark cells, black on light, regardless of cmap.
                r, g, b = cell_rgba[:3]
                luminance = 0.299 * r + 0.587 * g + 0.114 * b
                text_color = "white" if luminance < 0.55 else "black"
                # Strip a trailing ".0" so 100.0% renders as "100%"
                # (the trailing zero overflowed narrow cells in the
                # paper-slim layout). Non-integer percents keep one
                # decimal: 99.7% stays "99.7%", 78.8% stays "78.8%".
                pct = val * 100
                pct_text = f"{pct:.1f}".rstrip("0").rstrip(".")
                if plt.rcParams.get("text.usetex"):
                    cell_label = f"{pct_text}\\%"
                else:
                    cell_label = f"{pct_text}%"
                ax.text(
                    col, row, cell_label,
                    ha="center", va="center",
                    fontsize=cell_value_fontsize, color=text_color,
                    fontweight="bold",
                )
            elif cell is not None and cell.get("status") == "join_failed":
                # Neutral gray + diagonal hatching → "no hash overlap"
                # (data is non-comparable, not bad). Distinct from
                # missing (solid darker gray, no hatch) so a quick glance
                # tells you which gap is fixable.
                rect = plt.Rectangle(
                    (col - 0.5, row - 0.5), 1, 1,
                    facecolor=_JOIN_FAILED_COLOR,
                    edgecolor="white", linewidth=0.5,
                    hatch="////",
                )
                ax.add_patch(rect)
                ax.text(
                    col, row,
                    "N/A",
                    ha="center", va="center",
                    fontsize=status_label_fontsize, color="#404040",
                    fontweight="bold",
                )
            elif cell is not None and cell.get("status") == "no_core_metrics":
                # Light purple + dotted hatching → "joined but no
                # recognized core metrics". Analyzer-side gap; the fix
                # is to extend CORE_PREFIXES, not to investigate the
                # data.
                rect = plt.Rectangle(
                    (col - 0.5, row - 0.5), 1, 1,
                    facecolor=_NO_CORE_METRICS_COLOR,
                    edgecolor="white", linewidth=0.5,
                    hatch="....",
                )
                ax.add_patch(rect)
                ax.text(
                    col, row,
                    "N/A",
                    ha="center", va="center",
                    fontsize=status_label_fontsize, color="#4a148c",
                    fontweight="bold",
                )
            else:
                # Missing: solid darker gray + em-dash. Drawn explicitly
                # so the cell border visually delimits it from the
                # background of the same color.
                rect = plt.Rectangle(
                    (col - 0.5, row - 0.5), 1, 1,
                    facecolor=_MISSING_COLOR,
                    edgecolor="white", linewidth=0.5,
                )
                ax.add_patch(rect)
                ax.text(
                    col, row, "—",
                    ha="center", va="center",
                    fontsize=10, color="#606060",
                )

    # Axis labels
    if transpose:
        ax.set_xticks(range(n_bench))
        ax.set_xticklabels(
            [_BENCHMARK_DISPLAY.get(b, b) for b in benchmarks],
            fontsize=10, ha="right", rotation=35,
        )
        ax.set_yticks(range(n_models))
        ax.set_yticklabels(
            [_MODEL_DISPLAY.get(m, m) for m in models],
            fontsize=10,
        )
        # Push the xlabel below the rotated benchmark tick labels —
        # without explicit padding the long ones (e.g. "Synthetic
        # Reasoning (Natural)") descend past the default xlabel
        # position and clip the "Benchmark" text under tight bbox.
        ax.set_xlabel("Benchmark", fontsize=11, labelpad=18)
        ax.set_ylabel("Model", fontsize=11, labelpad=10)
        ax.set_xlim(-0.5, n_bench - 0.5)
        ax.set_ylim(-0.5, n_models - 0.5)
    else:
        ax.set_xticks(range(n_models))
        ax.set_xticklabels(
            [_MODEL_DISPLAY.get(m, m) for m in models],
            fontsize=10, ha="right", rotation=25,
        )
        ax.set_yticks(range(n_bench))
        ax.set_yticklabels(
            [_BENCHMARK_DISPLAY.get(b, b) for b in benchmarks],
            fontsize=10,
        )
        ax.set_xlabel("Model", fontsize=11)
        ax.set_ylabel("Benchmark", fontsize=11)
        ax.set_xlim(-0.5, n_models - 0.5)
        ax.set_ylim(-0.5, n_bench - 0.5)
    ax.invert_yaxis()
    # Despine: drop the axes box and tick marks. The white grid lines
    # between cells already delimit the data area, and the despined look
    # is the paper-figure default.
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(axis="both", which="both", length=0)

    # Colorbar for the present-status colormap. In transposed layout
    # the figure is wide and short; the axis already has a fixed
    # square-cell aspect, so we just attach a slim vertical colorbar
    # of the same height to the right of the axis. The in-figure
    # status legend lives below the x-axis (rotated benchmark labels),
    # so the two never collide.
    if transpose:
        cbar = fig.colorbar(cmap_scalar, ax=ax, fraction=0.02, pad=0.01)
    else:
        cbar = fig.colorbar(cmap_scalar, ax=ax, fraction=0.03, pad=0.02)
    cbar.set_label("Agreement (\\%)" if plt.rcParams.get("text.usetex")
                   else "Agreement (%)", fontsize=10)
    cbar.ax.tick_params(labelsize=9, length=0)
    cbar.outline.set_visible(False)
    cbar.ax.yaxis.set_major_formatter(
        FuncFormatter(lambda x, _pos: f"{x * 100:.0f}%")
    )

    # Build the legend dynamically: only include status entries that
    # actually appear in this heatmap. For the slim-paper render, that
    # typically means just `present` + `join_failed`, with
    # `no_core_metrics` and `missing` omitted (they exist as fallbacks
    # for other heatmaps but aren't relevant here).
    from matplotlib.patches import Patch
    statuses_present = {c.get("status") for c in cells.values() if c}
    has_missing_in_grid = any(
        cells.get((m, b)) is None
        for m in models for b in benchmarks
    )
    # Use a high-end swatch for `present` so it visually matches what
    # readers see on the actual cells (deep green, not the gradient
    # midpoint which lands in yellow with a tightened vmin).
    legend_handles = [
        Patch(facecolor=cmap(cmap_norm(cmap_vmax)), edgecolor="white",
              label="Agreement (shown as \\%)" if plt.rcParams.get("text.usetex")
              else "Agreement (shown as %)"),
    ]
    if "join_failed" in statuses_present:
        legend_handles.append(
            Patch(facecolor=_JOIN_FAILED_COLOR, edgecolor="white",
                  hatch="////",
                  label="Non-comparable (no hash overlap)")
        )
    if "no_core_metrics" in statuses_present:
        legend_handles.append(
            Patch(facecolor=_NO_CORE_METRICS_COLOR, edgecolor="white",
                  hatch="....",
                  label="No core metrics (joined; classifier gap)")
        )
    if has_missing_in_grid:
        legend_handles.append(
            Patch(facecolor=_MISSING_COLOR, edgecolor="white",
                  label="Missing (no packet for this cell)")
        )
    # In-figure title and status legend are suppressed in transpose
    # (paper-compact) mode: the LaTeX caption carries the same info,
    # and dropping them lets the figure be vertically tight enough to
    # fit \linewidth without dominating the column.
    if not transpose:
        ax.legend(
            handles=legend_handles,
            loc="upper center", bbox_to_anchor=(0.5, -0.08),
            ncol=min(len(legend_handles), 3), fontsize=9, frameon=False,
        )

        # Subtitle handling:
        # * subtitle=None  → use the default "instance-level agree_ratio
        #                    at abs_tol=…" string
        # * subtitle=""    → suppress the second title line entirely
        # * subtitle=<str> → use the literal string
        if subtitle is None:
            sub = f"instance-level agree_ratio at abs_tol={abs_tol}"
        else:
            sub = subtitle
        if title and sub:
            ax.set_title(f"{title}\n{sub}", fontsize=9, pad=8)
        elif title:
            ax.set_title(title, fontsize=9, pad=8)
        elif sub:
            ax.set_title(sub, fontsize=9, pad=8)

    plt.tight_layout()
    primary_path = out_dir / out_filename
    primary_suffix = primary_path.suffix.lower()
    _atomic_savefig(
        fig, primary_path,
        dpi=300, bbox_inches="tight", pad_inches=0.3,
    )
    # Always emit a vector PDF sibling next to the rendered file so the
    # paper figure picks up a resolution-independent version. tight bbox
    # already trims the PDF; raster paths still need cropwhite_ondisk
    # (which is image-only).
    pdf_path = primary_path.with_suffix(".pdf")
    if primary_suffix == ".pdf":
        pdf_path = primary_path
    else:
        _atomic_savefig(
            fig, pdf_path,
            bbox_inches="tight", pad_inches=0.05,
        )
    plt.close(fig)
    rc_ctx.__exit__(None, None, None)
    # Trim residual white margins on the raster output. Soft import: if
    # kwplot isn't installed, the saved PNG is still usable. PDFs are
    # vector and tight bbox already cropped them, so skip kwplot there.
    if primary_suffix in {".png", ".jpg", ".jpeg"}:
        try:
            import kwplot
            kwplot.cropwhite_ondisk(primary_path)
        except ImportError as exc:
            logger.debug(
                f"kwplot not available ({exc}); skipping cropwhite_ondisk "
                f"on {primary_path}."
            )
    logger.info(f"Wrote heatmap: {rich_link(primary_path)}")
    if pdf_path != primary_path:
        logger.info(f"Wrote vector heatmap: {rich_link(pdf_path)}")
    return primary_path


# ---------------------------------------------------------------------------
# Per-metric heatmaps (one figure per metric)
# ---------------------------------------------------------------------------


_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename_part(name: str) -> str:
    """Sanitize a metric name for use in a filename. Replaces any run of
    non ``[A-Za-z0-9._-]`` characters with a single underscore so things
    like ``exact_match@5`` become ``exact_match_5``."""
    cleaned = _FILENAME_SAFE_RE.sub("_", name).strip("_")
    return cleaned or "metric"


@profile
def _render_per_metric_heatmaps(
    cells: dict[tuple[str, str, str], dict[str, Any]],
    models: list[str],
    benchmarks: list[str],
    metrics_in_order: list[str],
    abs_tol: float,
    title: str,
    out_dir: Path,
    *,
    transpose: bool = False,
    subtitle_override: str | None = None,
) -> list[Path]:
    """Emit one ``model × benchmark`` heatmap per metric.

    Each plot has the same shape as the main heatmap (rows = benchmarks
    in canonical order, columns = models), so the eye can flip between
    metrics without re-learning the layout. Plots land in
    ``<out_dir>/reproducibility_heatmap_per_metric/<metric>.png``.

    Cells where the metric isn't present for a (model, benchmark) pair
    render as the standard "missing" gray — the per-metric coverage is
    naturally sparse (e.g. ``exact_match@5`` only on retrieval-style
    benchmarks) and the gray makes that visible.
    """
    sub_dir = out_dir / "reproducibility_heatmap_per_metric"
    sub_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for metric in metrics_in_order:
        # Filter to (model, benchmark) cells for this one metric.
        per_metric_cells: dict[tuple[str, str], dict[str, Any]] = {
            (m, b): cell
            for (m, b, met), cell in cells.items()
            if met == metric
        }
        if not per_metric_cells:
            continue
        # Drop benchmarks that don't use this metric — otherwise every
        # plot shows a wall of gray "missing" rows for benchmarks that
        # never report it (e.g. bleu_1 only applies to NarrativeQA, so
        # the BoolQ/MMLU/IMDB/... rows are pure noise on that plot).
        benchmarks_for_metric = [
            b for b in benchmarks
            if any((m, b) in per_metric_cells for m in models)
        ]
        if not benchmarks_for_metric:
            continue
        if subtitle_override is not None:
            subtitle = subtitle_override
        else:
            subtitle = (
                f"instance-level agree_ratio at abs_tol={abs_tol} "
                f"(metric: {metric})"
            )
        png_path = _render_heatmap(
            per_metric_cells,
            models,
            benchmarks_for_metric,
            abs_tol,
            f"{title} — metric: {metric}",
            sub_dir,
            out_filename=f"{_safe_filename_part(metric)}.png",
            subtitle=subtitle,
            transpose=transpose,
        )
        written.append(png_path)
    return written


def _render_per_metric_text_table(
    cells: dict[tuple[str, str, str], dict[str, Any]],
    models: list[str],
    rows_in_order: list[tuple[str, str]],
    abs_tol: float,
) -> str:
    """Plain-text equivalent of the per-metric heatmap. Useful for
    grepping ("which metric is the WikiFact 0.92 floor?") and for
    pasting into commit messages / paper drafts.
    """
    lines: list[str] = [
        f"Per-metric reproducibility heatmap (abs_tol={abs_tol})",
        f"Instance-level agree_ratio per (benchmark, metric)",
        "",
        "Cell legend:",
        "  0.987    instance-level agree_ratio at the chosen abs_tol",
        "  join0/N  packet exists; 0 of N official_vs_local pairs joined",
        "  --       this metric not present for that (model, benchmark)",
        "",
    ]
    col_w = 14
    label_w = 48
    header = f"{'Benchmark / metric':<{label_w}}" + "".join(
        f"{_MODEL_DISPLAY.get(m, m)[:col_w]:>{col_w}}" for m in models
    )
    lines.append(header)
    lines.append("-" * len(header))
    prev_bench = None
    for bench, metric in rows_in_order:
        # Group separator
        if prev_bench is not None and bench != prev_bench:
            lines.append("")
        prev_bench = bench
        label = f"{_BENCHMARK_DISPLAY.get(bench, bench)}: {metric}"
        row = f"{label[:label_w]:<{label_w}}"
        for m in models:
            cell = cells.get((m, bench, metric))
            if cell is None:
                row += f"{'--':>{col_w}}"
            elif cell.get("status") == "present":
                row += f"{cell['agree_ratio']:>{col_w}.3f}"
            else:
                marker = f"join0/{cell.get('n_pairs_total', 0)}"
                row += f"{marker:>{col_w}}"
        lines.append(row)

    n_present = sum(1 for c in cells.values() if c.get("status") == "present")
    n_join_failed = sum(1 for c in cells.values() if c.get("status") == "join_failed")
    lines.append("")
    lines.append(
        f"Coverage: {n_present} present / {n_join_failed} join_failed "
        f"(of {len(cells)} (model, benchmark, metric) cells with data)"
    )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Aggregate-score-difference heatmap (color = local − public score)
# ---------------------------------------------------------------------------


@profile
def _render_diff_heatmap(
    cells: dict[tuple[str, str], dict[str, Any]],
    models: list[str],
    benchmarks: list[str],
    title: str,
    out_dir: Path,
    *,
    out_filename: str = "aggregate_score_diff_heatmap.png",
    subtitle: str | None = None,
    transpose: bool = False,
) -> Path:
    """Render an aggregate-score-difference heatmap for a single metric.

    Sibling of :func:`_render_heatmap`, but the cell encoding is entirely
    different:

    * **Color** is the *signed* difference ``local − public`` (the
      reproduced aggregate score minus the official one), mapped through a
      diverging colorblind-safe colormap centered on zero — Wong blue for
      "reproduced lower than public", near-white for "matches", Wong
      orange for "reproduced higher". The scale is symmetric about zero so
      the white midpoint always means "no drift".
    * **Annotation** is the two actual aggregate scores: ``P`` = public
      (official) on top, ``L`` = local (reproduced) below.

    ``cells`` is keyed ``(model, benchmark)`` and already filtered to one
    metric (the per-metric wrapper does that). Each present cell carries
    ``official`` / ``local`` / ``diff`` floats. ``transpose`` matches
    :func:`_render_heatmap`: rows=benchmarks/cols=models by default, or
    rows=models/cols=benchmarks when True.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.colors as mcolors

    n_bench = len(benchmarks)
    n_models = len(models)

    if transpose:
        fig_w = max(10.0, 0.85 * n_bench + 2.5)
        fig_h = max(2.8, 0.62 * n_models + 1.1)
    else:
        fig_w = max(6.0, 2.4 * n_models + 2.0)
        fig_h = max(5.0, 0.55 * n_bench + 1.5)

    # Symmetric diverging scale about zero: 0 → white midpoint always
    # reads as "no drift". vmax defaults to the largest |diff| present so
    # the gradient uses its full range; override with
    # EVAL_AUDIT_DIFF_HEATMAP_VMAX to pin a common scale across metrics.
    present_diffs = [
        c["diff"] for c in cells.values()
        if c and c.get("status") == "present" and c.get("diff") is not None
    ]
    env_vmax = os.environ.get("EVAL_AUDIT_DIFF_HEATMAP_VMAX", "")
    if env_vmax:
        vmax = abs(float(env_vmax))
    else:
        vmax = max((abs(d) for d in present_diffs), default=0.0)
    if vmax <= 0:
        # Degenerate: every present cell has zero drift. Give the norm a
        # valid width so all cells map to the white center.
        vmax = 1.0
    norm = mcolors.Normalize(vmin=-vmax, vmax=vmax)

    cmap_name = os.environ.get("EVAL_AUDIT_DIFF_HEATMAP_CMAP", "")
    if cmap_name:
        cmap = plt.get_cmap(cmap_name)
    else:
        # Wong blue (low / reproduced-below-public) → near-white (match) →
        # Wong orange (reproduced-above-public). Deutera/protan/tritan all
        # keep this blue↔orange separation, and the light midpoint is a
        # genuine "neutral" cue here (unlike the agreement heatmap, where
        # the midpoint is arbitrary), because zero drift is the goal.
        cmap = mcolors.LinearSegmentedColormap.from_list(
            "BlueWhiteOrange",
            [
                (0.0, "#0072B2"),   # Wong blue  (local << public)
                (0.5, "#f7f7f7"),   # near-white (local == public)
                (1.0, "#E69F00"),   # Wong orange(local >> public)
            ],
            N=256,
        )
    scalar = plt.cm.ScalarMappable(norm=norm, cmap=cmap)

    _MISSING_COLOR = "#bdbdbd"

    rc_ctx = plt.rc_context(_paper_rc())
    rc_ctx.__enter__()
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    ax.set_facecolor(_MISSING_COLOR)

    cell_value_fontsize = 8 if transpose else 7
    for i_bench, bench in enumerate(benchmarks):
        for i_model, model in enumerate(models):
            if transpose:
                col, row = i_bench, i_model
            else:
                col, row = i_model, i_bench
            cell = cells.get((model, bench))
            if cell is not None and cell.get("status") == "present":
                diff = cell["diff"]
                cell_rgba = cmap(norm(diff))
                rect = plt.Rectangle(
                    (col - 0.5, row - 0.5), 1, 1,
                    facecolor=cell_rgba,
                    edgecolor="white", linewidth=0.5,
                )
                ax.add_patch(rect)
                r, g, b = cell_rgba[:3]
                luminance = 0.299 * r + 0.587 * g + 0.114 * b
                text_color = "white" if luminance < 0.55 else "black"
                # Two stacked scores: P(ublic) on top, L(ocal) below. 3
                # significant figures keeps 0.824 / 1.0 / 0.0 legible
                # without overflowing narrow cells.
                official = cell["official"]
                local = cell["local"]
                cell_label = f"P {official:.3g}\nL {local:.3g}"
                ax.text(
                    col, row, cell_label,
                    ha="center", va="center",
                    fontsize=cell_value_fontsize, color=text_color,
                    linespacing=1.35,
                )
            else:
                # Missing: solid gray + em-dash (no runlevel score for
                # this model/benchmark/metric).
                rect = plt.Rectangle(
                    (col - 0.5, row - 0.5), 1, 1,
                    facecolor=_MISSING_COLOR,
                    edgecolor="white", linewidth=0.5,
                )
                ax.add_patch(rect)
                ax.text(
                    col, row, "—",
                    ha="center", va="center",
                    fontsize=10, color="#606060",
                )

    if transpose:
        ax.set_xticks(range(n_bench))
        ax.set_xticklabels(
            [_BENCHMARK_DISPLAY.get(b, b) for b in benchmarks],
            fontsize=10, ha="right", rotation=35,
        )
        ax.set_yticks(range(n_models))
        ax.set_yticklabels(
            [_MODEL_DISPLAY.get(m, m) for m in models],
            fontsize=10,
        )
        ax.set_xlabel("Benchmark", fontsize=11, labelpad=18)
        ax.set_ylabel("Model", fontsize=11, labelpad=10)
        ax.set_xlim(-0.5, n_bench - 0.5)
        ax.set_ylim(-0.5, n_models - 0.5)
    else:
        ax.set_xticks(range(n_models))
        ax.set_xticklabels(
            [_MODEL_DISPLAY.get(m, m) for m in models],
            fontsize=10, ha="right", rotation=25,
        )
        ax.set_yticks(range(n_bench))
        ax.set_yticklabels(
            [_BENCHMARK_DISPLAY.get(b, b) for b in benchmarks],
            fontsize=10,
        )
        ax.set_xlabel("Model", fontsize=11)
        ax.set_ylabel("Benchmark", fontsize=11)
        ax.set_xlim(-0.5, n_models - 0.5)
        ax.set_ylim(-0.5, n_bench - 0.5)
    ax.invert_yaxis()
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(axis="both", which="both", length=0)

    if transpose:
        cbar = fig.colorbar(scalar, ax=ax, fraction=0.02, pad=0.01)
    else:
        cbar = fig.colorbar(scalar, ax=ax, fraction=0.03, pad=0.02)
    cbar.set_label(
        "Local $-$ Public (aggregate score)"
        if plt.rcParams.get("text.usetex")
        else "Local − Public (aggregate score)",
        fontsize=10,
    )
    cbar.ax.tick_params(labelsize=9, length=0)
    cbar.outline.set_visible(False)

    if not transpose:
        if subtitle is None:
            sub = "cell: P=public / L=local aggregate score; color = local − public"
        else:
            sub = subtitle
        if title and sub:
            ax.set_title(f"{title}\n{sub}", fontsize=9, pad=8)
        elif title:
            ax.set_title(title, fontsize=9, pad=8)
        elif sub:
            ax.set_title(sub, fontsize=9, pad=8)

    plt.tight_layout()
    primary_path = out_dir / out_filename
    primary_suffix = primary_path.suffix.lower()
    _atomic_savefig(
        fig, primary_path,
        dpi=300, bbox_inches="tight", pad_inches=0.3,
    )
    pdf_path = primary_path.with_suffix(".pdf")
    if primary_suffix == ".pdf":
        pdf_path = primary_path
    else:
        _atomic_savefig(
            fig, pdf_path,
            bbox_inches="tight", pad_inches=0.05,
        )
    plt.close(fig)
    rc_ctx.__exit__(None, None, None)
    if primary_suffix in {".png", ".jpg", ".jpeg"}:
        try:
            import kwplot
            kwplot.cropwhite_ondisk(primary_path)
        except ImportError as exc:
            logger.debug(
                f"kwplot not available ({exc}); skipping cropwhite_ondisk "
                f"on {primary_path}."
            )
    logger.info(f"Wrote aggregate-diff heatmap: {rich_link(primary_path)}")
    if pdf_path != primary_path:
        logger.info(f"Wrote vector aggregate-diff heatmap: {rich_link(pdf_path)}")
    return primary_path


@profile
def _render_aggregate_diff_heatmaps(
    cells: dict[tuple[str, str, str], dict[str, Any]],
    models: list[str],
    benchmarks: list[str],
    metrics_in_order: list[str],
    title: str,
    out_dir: Path,
    *,
    transpose: bool = False,
    subtitle_override: str | None = None,
) -> list[Path]:
    """Emit one ``model × benchmark`` aggregate-score-diff heatmap per metric.

    Mirrors :func:`_render_per_metric_heatmaps` but routes through
    :func:`_render_diff_heatmap`. Plots land under
    ``<out_dir>/aggregate_score_diff_per_metric/<metric>.png``. Benchmarks
    that never report a given metric are dropped from that metric's plot
    so it doesn't fill with gray "missing" rows.
    """
    sub_dir = out_dir / "aggregate_score_diff_per_metric"
    sub_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for metric in metrics_in_order:
        per_metric_cells: dict[tuple[str, str], dict[str, Any]] = {
            (m, b): cell
            for (m, b, met), cell in cells.items()
            if met == metric
        }
        if not per_metric_cells:
            continue
        benchmarks_for_metric = [
            b for b in benchmarks
            if any((m, b) in per_metric_cells for m in models)
        ]
        if not benchmarks_for_metric:
            continue
        if subtitle_override is not None:
            subtitle = subtitle_override
        else:
            subtitle = (
                "cell: P=public / L=local aggregate score; "
                f"color = local − public (metric: {metric})"
            )
        png_path = _render_diff_heatmap(
            per_metric_cells,
            models,
            benchmarks_for_metric,
            f"{title} — metric: {metric}",
            sub_dir,
            out_filename=f"{_safe_filename_part(metric)}.png",
            subtitle=subtitle,
            transpose=transpose,
        )
        written.append(png_path)
    return written


def _render_aggregate_diff_text_table(
    cells: dict[tuple[str, str, str], dict[str, Any]],
    models: list[str],
    rows_in_order: list[tuple[str, str]],
) -> str:
    """Plain-text companion to the aggregate-score-diff heatmap.

    Each cell shows ``public/local`` (the two aggregate scores) so the
    numbers are greppable and paste-able into commit messages / drafts.
    """
    lines: list[str] = [
        "Aggregate score difference table (run-level, official_vs_local)",
        "Per (benchmark, metric): public aggregate score vs local reproduction",
        "",
        "Cell legend:",
        "  0.82/0.79  public aggregate score / local (reproduced) score",
        "  --         this metric not present for that (model, benchmark)",
        "",
    ]
    col_w = 18
    label_w = 48
    header = f"{'Benchmark / metric':<{label_w}}" + "".join(
        f"{_MODEL_DISPLAY.get(m, m)[:col_w]:>{col_w}}" for m in models
    )
    lines.append(header)
    lines.append("-" * len(header))
    prev_bench = None
    for bench, metric in rows_in_order:
        if prev_bench is not None and bench != prev_bench:
            lines.append("")
        prev_bench = bench
        label = f"{_BENCHMARK_DISPLAY.get(bench, bench)}: {metric}"
        row = f"{label[:label_w]:<{label_w}}"
        for m in models:
            cell = cells.get((m, bench, metric))
            if cell is None or cell.get("status") != "present":
                row += f"{'--':>{col_w}}"
            else:
                marker = f"{cell['official']:.3g}/{cell['local']:.3g}"
                row += f"{marker:>{col_w}}"
        lines.append(row)
    n_present = sum(1 for c in cells.values() if c.get("status") == "present")
    lines.append("")
    lines.append(
        f"Coverage: {n_present} present "
        f"(of {len(cells)} (model, benchmark, metric) cells with data)"
    )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@profile
def _write_redraw_plots_script(
    out_dir: Path,
    analysis_root: Path,
    abs_tol: float,
    title: str,
    per_metric: bool,
    include_bookkeeping: bool,
    transpose: bool = False,
    no_subtitle: bool = False,
    aggregate_diff: bool = False,
) -> Path:
    """Drop a self-contained ``redraw_plots.sh`` next to the heatmap outputs.

    The script re-invokes ``python -m eval_audit.reports.eee_only_heatmap``
    with the same arguments that produced the current outputs, so an
    iteration loop on plot styling (color scale, legend, layout) is
    just: edit ``eval_audit/reports/eee_only_heatmap.py`` and rerun
    ``bash redraw_plots.sh`` from the heatmap output dir.

    Captures the colormap env vars
    (``EVAL_AUDIT_HEATMAP_VMIN`` / ``EVAL_AUDIT_HEATMAP_VMAX``) at
    generation time so re-renders use the same color scale unless the
    user explicitly overrides via the same env vars.
    """
    import shlex

    # Mirror the invocation actually used.
    cmd_parts = [
        "-m", "eval_audit.reports.eee_only_heatmap",
        "--analysis-root", str(analysis_root),
        # Resolve out-dir at script-run time so this script is portable
        # across moves/copies of the heatmap dir (the script lives next
        # to its own outputs).
        "--out-dir", '"$SCRIPT_DIR"',
        "--abs-tol", str(abs_tol),
        "--title", title,
    ]
    if per_metric:
        cmd_parts.append("--per-metric")
    if include_bookkeeping:
        cmd_parts.append("--include-bookkeeping")
    if transpose:
        cmd_parts.append("--transpose")
    if no_subtitle:
        cmd_parts.append("--no-subtitle")
    if aggregate_diff:
        cmd_parts.append("--aggregate-diff")

    # Quote every fixed arg; the "$SCRIPT_DIR" placeholder must remain
    # unquoted so the shell expands it.
    quoted_parts: list[str] = []
    for part in cmd_parts:
        if part == '"$SCRIPT_DIR"':
            quoted_parts.append(part)
        else:
            quoted_parts.append(shlex.quote(part))
    cmd_str = " ".join(quoted_parts)

    # Capture colormap env vars so the regenerated PNG matches the
    # original styling unless the user explicitly overrides them. The
    # vars hold short numeric strings like "0.7" / "1.0"; the
    # ``${VAR:-default}`` indirection means a value already in the
    # environment at run time still wins.
    captured_env_lines: list[str] = []
    for var in ("EVAL_AUDIT_HEATMAP_VMIN", "EVAL_AUDIT_HEATMAP_VMAX"):
        v = os.environ.get(var)
        if v is not None:
            quoted = shlex.quote(v)
            captured_env_lines.append(
                f'export {var}="${{{var}:-$(echo {quoted})}}"'
            )

    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "# Regenerate the heatmap PNG + per-metric drill-down PNGs from",
        "# the per-packet core_metric_report.json files this output was",
        "# computed from. Use this when iterating on plot styling: edit",
        "# eval_audit/reports/eee_only_heatmap.py and rerun.",
        "#",
        "# Output dir is resolved as the directory this script lives in,",
        "# so the script remains valid if you copy/move the heatmap dir.",
        "# Override REPO_ROOT to point at a different eval_audit checkout.",
        "# Override EVAL_AUDIT_HEATMAP_VMIN / EVAL_AUDIT_HEATMAP_VMAX to",
        "# adjust the color scale (defaults captured from generation).",
        *portable_repo_root_lines(),
        *captured_env_lines,
        'cd "$REPO_ROOT"',
        f'PYTHONPATH="$REPO_ROOT" "$PYTHON_BIN" {cmd_str} "$@"',
    ]
    return write_reproduce_script(out_dir / "redraw_plots.sh", lines)

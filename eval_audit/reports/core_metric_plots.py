"""Matplotlib rendering for the per-pair core-metric report: layout,
styles, and every figure writer. Curve math lives in
``reports.core_metric_curves``.

Split out of ``eval_audit.reports.core_metrics`` on 2026-06-11
(Phase 2 of docs/planning/repo-refactor-plan.md). Pure relocation:
function bodies are unchanged.
"""
from __future__ import annotations
import argparse
from dataclasses import dataclass
import warnings
from pathlib import Path
from typing import Any
import eval_audit.infra.mpl_backend  # noqa: F401  (force headless Agg before pyplot)
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
import safer
from eval_audit.utils.labels import emit_label_legend_artifacts, short_alias_map
from eval_audit.infra.profiling import profile
from eval_audit.reports.core_metric_curves import (
    MetricDomain,
    _agreement_curve_rows,
    _distribution_rows,
    _metric_descriptor,
    _metric_domain,
    _pair_metric_domain,
    _per_metric_agreement_curves,
    _should_treat_as_discrete,
    _single_run_instance_core_rows,
)
_PLOT_TARGETS = {
    'all',
    'core_metric_report',
    'core_metric_distributions',
    'core_metric_overlay_distributions',
    'core_metric_ecdfs',
    'core_metric_per_metric_agreement',
}


def _wants_plot(plot_target: str, plot_name: str) -> bool:
    return plot_target == 'all' or plot_target == plot_name


@dataclass(frozen=True)
class PlotLayout:
    """Matplotlib layout knobs for crowded report figures."""

    # Multiplicative scale applied to every Matplotlib figure size before
    # layout. Increase this when labels/titles are too crowded for the canvas.
    figure_scale: float = 1.5
    # Figure-coordinate y position for the figure-level title. Values near
    # 1.0 place the suptitle at the top edge; larger values move it upward.
    suptitle_y: float | None = 0.995
    # Minimum vertical padding around axes decorations, in inches, for
    # Matplotlib's constrained-layout engine.
    constrained_h_pad: float | None = 0.02
    # Minimum vertical space between subplot groups, as a fraction of the
    # average subplot height, for constrained layout.
    constrained_hspace: float | None = 0.05
    # Minimum horizontal padding around axes decorations, in inches, for
    # Matplotlib's constrained-layout engine.
    constrained_w_pad: float | None = 0.08
    # Minimum horizontal space between subplot groups, as a fraction of the
    # average subplot width, for constrained layout.
    constrained_wspace: float | None = 0.05
    # Manual subplot margin for grids that use fig.subplots_adjust. Values are
    # figure fractions in Matplotlib's [0, 1] coordinate system.
    subplot_left: float | None = None
    # Manual subplot right edge for fig.subplots_adjust, as a figure fraction.
    subplot_right: float | None = None
    # Manual subplot bottom margin for fig.subplots_adjust, as a figure fraction.
    subplot_bottom: float | None = None
    # Manual subplot top edge for fig.subplots_adjust, as a figure fraction.
    subplot_top: float | None = None


def _coalesce(value: float | None, default: float | None) -> float | None:
    return default if value is None else value


def _plot_layout_from_cli(args: argparse.Namespace) -> PlotLayout:
    default = PlotLayout()
    return PlotLayout(
        figure_scale=_coalesce(args.plot_figure_scale, default.figure_scale),
        suptitle_y=_coalesce(args.plot_suptitle_y, default.suptitle_y),
        constrained_h_pad=_coalesce(args.plot_constrained_h_pad, default.constrained_h_pad),
        constrained_hspace=_coalesce(args.plot_constrained_hspace, default.constrained_hspace),
        constrained_w_pad=_coalesce(args.plot_constrained_w_pad, default.constrained_w_pad),
        constrained_wspace=_coalesce(args.plot_constrained_wspace, default.constrained_wspace),
        subplot_left=_coalesce(args.plot_subplot_left, default.subplot_left),
        subplot_right=_coalesce(args.plot_subplot_right, default.subplot_right),
        subplot_bottom=_coalesce(args.plot_subplot_bottom, default.subplot_bottom),
        subplot_top=_coalesce(args.plot_subplot_top, default.subplot_top),
    )


def _scaled_figsize(width: float, height: float, plot_layout: PlotLayout | None = None) -> tuple[float, float]:
    scale = (plot_layout or PlotLayout()).figure_scale
    if scale <= 0:
        scale = 1.0
    return (width * scale, height * scale)


def _apply_matplotlib_style() -> None:
    """Apply the eval_audit matplotlib/seaborn theme.

    Every plotting function that creates a Figure should call this before
    ``plt.subplots`` so the whitegrid background, talk-context font sizes,
    and seaborn palette are consistent across the report. (Plotly plots
    are styled separately; this helper is matplotlib-only.)"""
    sns.set_theme(style='whitegrid', context='talk')


def _palette_color_map(labels: list[str]) -> dict[str, Any]:
    """Map each unique label to its seaborn-palette color in plot order.

    seaborn's ``hue`` semantic assigns colors from the active palette to
    unique values in *sorted* order; this helper mirrors that so a sidecar
    label legend can echo the matching color for each pair/run/etc."""
    unique = sorted(set(labels))
    palette = sns.color_palette(n_colors=max(len(unique), 1))
    return {label: palette[i % len(palette)] for i, label in enumerate(unique)}


def _apply_plot_layout(fig: plt.Figure, plot_layout: PlotLayout | None) -> PlotLayout:
    layout = plot_layout or PlotLayout()
    pad_kwargs = {
        key: value
        for key, value in {
            'h_pad': layout.constrained_h_pad,
            'hspace': layout.constrained_hspace,
            'w_pad': layout.constrained_w_pad,
            'wspace': layout.constrained_wspace,
        }.items()
        if value is not None
    }
    if pad_kwargs:
        layout_engine = fig.get_layout_engine() if hasattr(fig, 'get_layout_engine') else None
        if layout_engine is not None and hasattr(layout_engine, 'set'):
            layout_engine.set(**pad_kwargs)
        else:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', PendingDeprecationWarning)
                fig.set_constrained_layout_pads(**pad_kwargs)
    return layout


def _set_suptitle(
    fig: plt.Figure,
    text: str,
    *,
    fontsize: float,
    plot_layout: PlotLayout | None = None,
) -> None:
    layout = _apply_plot_layout(fig, plot_layout)
    kwargs: dict[str, Any] = {'fontsize': fontsize}
    if layout.suptitle_y is not None:
        kwargs['y'] = layout.suptitle_y
    fig.suptitle(text, **kwargs)


def _subplot_adjust_kwargs(
    fig: plt.Figure,
    layout: PlotLayout,
    *,
    top: float = 0.92,
    bottom: float = 0.04,
) -> dict[str, float]:
    """Translate layout knobs into stable manual subplot spacing.

    ``top`` and ``bottom`` are per-plot defaults (not layout-level) for the
    fraction of the figure used by axes; pass a more generous ``bottom``
    when the plot has a labelled x-axis that would otherwise clip on
    short figures. ``layout.subplot_top`` / ``layout.subplot_bottom``
    explicitly override these per-plot defaults when provided.
    """
    fig_w, fig_h = fig.get_size_inches()
    kwargs = {
        'top': layout.subplot_top if layout.subplot_top is not None else top,
        'bottom': layout.subplot_bottom if layout.subplot_bottom is not None else bottom,
    }
    if layout.constrained_h_pad is not None and fig_h > 0:
        vpad = min(0.20, max(0.0, layout.constrained_h_pad / fig_h))
        if layout.subplot_bottom is None:
            kwargs['bottom'] = max(kwargs['bottom'], vpad)
        if layout.subplot_top is None:
            kwargs['top'] = min(kwargs['top'], 1.0 - vpad)
    if layout.constrained_w_pad is not None and fig_w > 0:
        hpad = min(0.20, max(0.0, layout.constrained_w_pad / fig_w))
        if layout.subplot_left is None:
            kwargs['left'] = max(0.04, hpad)
        if layout.subplot_right is None:
            kwargs['right'] = min(0.98, 1.0 - hpad)
    if layout.subplot_left is not None:
        kwargs['left'] = layout.subplot_left
    if layout.subplot_right is not None:
        kwargs['right'] = layout.subplot_right
    if layout.constrained_hspace is not None:
        kwargs['hspace'] = layout.constrained_hspace
    if layout.constrained_wspace is not None:
        kwargs['wspace'] = layout.constrained_wspace
    return kwargs


def _apply_xlim_hint(ax, domain: MetricDomain | None, values) -> None:
    if domain is None:
        return
    observed = [float(value) for value in values if value is not None and pd.notna(value)]
    if not observed:
        return
    lower, upper = domain
    if min(observed) < lower or max(observed) > upper:
        return
    ax.set_xlim(lower, upper)


def _apply_abs_delta_ylim_hint(ax, domain: MetricDomain | None, values) -> None:
    if domain is None:
        return
    observed = [float(value) for value in values if value is not None and pd.notna(value)]
    if not observed or min(observed) < 0:
        return
    lower, upper = domain
    span = upper - lower
    if span <= 0 or max(observed) > span:
        return
    ax.set_ylim(0.0, span)


@profile
def _plot_distribution(
    ax,
    *pairs: dict[str, Any],
    level_key: str,
    alias_map: dict[str, str] | None = None,
) -> None:
    rows = pd.DataFrame(_agreement_curve_rows(*pairs, level_key=level_key))
    if rows.empty or 'abs_tol' not in rows.columns or 'agree_ratio' not in rows.columns:
        ax.text(0.5, 0.5, 'No comparable core-metric rows', ha='center', va='center', transform=ax.transAxes)
        ax.set_axis_off()
        return
    if alias_map:
        rows = rows.assign(pair=rows['pair'].map(alias_map).fillna(rows['pair']))
    sns.lineplot(
        ax=ax,
        data=rows,
        x='abs_tol',
        y='agree_ratio',
        hue='pair',
        style='pair',
        markers=True,
        dashes=False,
        linewidth=2,
    )
    ax.set_xscale('symlog', linthresh=1e-12)
    ax.set_ylim(0, 1.02)
    _apply_xlim_hint(ax, _pair_metric_domain(*pairs), rows['abs_tol'].tolist())
    ax.set_xlabel('Absolute Tolerance Threshold for Core Metric Difference')
    ax.set_ylabel('Fraction of Core Metric Comparisons in Agreement')
    ax.tick_params(axis='x', rotation=28)
    ax.legend(title='')


@profile
def _plot_per_metric_agreement(
    fig_dpath: Path,
    stamp: str,
    *pairs: dict[str, Any],
    level_key: str = 'instance_level',
    thresholds: list[float] | None = None,
    plot_layout: PlotLayout | None = None,
) -> Path | None:
    """Create per-metric agreement curve plots."""
    if thresholds is None:
        thresholds = [1e-12, 1e-9, 1e-6, 1e-3, 1e-2, 0.1, 0.25, 0.5, 1.0]

    curves = _per_metric_agreement_curves(*pairs, level_key=level_key, thresholds=thresholds)
    if not curves:
        return None

    metrics = sorted(curves.keys())
    n_cols = min(3, len(metrics))
    n_rows = (len(metrics) + n_cols - 1) // n_cols

    # Pair labels (legend hue) are full comparison ids, ~100+ chars; the
    # legend overflows the axes when the labels are long. Alias each pair
    # to a short slug for the legend; sidecar artifacts emitted below
    # preserve the long labels.
    pair_labels = sorted({row['pair'] for metric_rows in curves.values() for row in metric_rows})
    alias_map = short_alias_map(pair_labels)

    _apply_matplotlib_style()
    fig, axes = plt.subplots(
        n_rows,
        n_cols,
        figsize=_scaled_figsize(6.5 * n_cols, 4.8 * n_rows, plot_layout),
        constrained_layout=False,
    )
    if len(metrics) == 1:
        axes = [[axes]]
    elif n_rows == 1:
        axes = [axes]
    elif n_cols == 1:
        axes = [[ax] for ax in axes]

    for idx, metric in enumerate(metrics):
        row_idx = idx // n_cols
        col_idx = idx % n_cols
        ax = axes[row_idx][col_idx]

        metric_data = curves[metric]
        df = pd.DataFrame(metric_data)
        if not df.empty:
            df = df.assign(pair=df['pair'].map(alias_map).fillna(df['pair']))
            sns.lineplot(
                ax=ax,
                data=df,
                x='abs_tol',
                y='agree_ratio',
                hue='pair',
                style='pair',
                markers=True,
                dashes=False,
                linewidth=2,
            )
            ax.set_xscale('symlog', linthresh=1e-12)
            ax.set_ylim(0, 1.02)
            _apply_xlim_hint(ax, _metric_domain(metric), df['abs_tol'].tolist())
            ax.set_xlabel('Abs Tolerance', fontsize=9)
            ax.set_ylabel('Agreement Ratio', fontsize=9)
            ax.tick_params(axis='x', rotation=28, labelsize=8)
            ax.tick_params(axis='y', labelsize=8)
            ax.set_title(metric, fontsize=10)
            ax.legend(title='', fontsize=8)

    for idx in range(len(metrics), n_rows * n_cols):
        row_idx = idx // n_cols
        col_idx = idx % n_cols
        fig.delaxes(axes[row_idx][col_idx])

    layout = plot_layout or PlotLayout()
    fig.suptitle(
        'Per-Metric Agreement vs Absolute Tolerance\n'
        'Legend uses short pair aliases; see the sidecar legend artifact for the full labels.',
        fontsize=14,
        y=layout.suptitle_y if layout.suptitle_y is not None else 0.995,
    )
    # Multi-row grid: each row has its own xlabel + tick labels, which were
    # overlapping the row-below title at the layout default hspace=0.05.
    # Bump hspace / wspace to give every facet breathing room, and a touch
    # of bottom margin so the bottom row's xlabel doesn't clip.
    adjust_kwargs = _subplot_adjust_kwargs(fig, layout, top=0.92, bottom=0.07)
    adjust_kwargs['hspace'] = max(adjust_kwargs.get('hspace', 0.40), 0.40)
    adjust_kwargs['wspace'] = max(adjust_kwargs.get('wspace', 0.18), 0.18)
    fig.subplots_adjust(**adjust_kwargs)
    fig_fpath = fig_dpath / f'core_metric_per_metric_agreement.png'
    _atomic_savefig(fig, fig_fpath, dpi=180)
    plt.close(fig)
    emit_label_legend_artifacts(
        alias_map,
        fig_dpath=fig_dpath,
        out_name='core_metric_per_metric_agreement',
        title='Per-Metric Agreement — short alias → full pair label',
        stamp=stamp,
        color_map=_palette_color_map(pair_labels),
    )
    return fig_fpath


@profile
def _plot_quantiles(ax, pair_a: dict[str, Any], pair_b: dict[str, Any], level_key: str, title: str) -> None:
    labels = ['p50', 'p90', 'p95', 'p99', 'max']
    x = list(range(len(labels)))
    a_vals = [pair_a[level_key]['overall_quantiles']['abs_delta'][k] for k in labels]
    b_vals = [pair_b[level_key]['overall_quantiles']['abs_delta'][k] for k in labels]
    ax.plot(x, a_vals, marker='o', label=pair_a['label'])
    ax.plot(x, b_vals, marker='o', label=pair_b['label'])
    ax.set_xticks(x, labels)
    ax.set_yscale('symlog', linthresh=1e-12)
    _apply_abs_delta_ylim_hint(ax, _pair_metric_domain(pair_a, pair_b), a_vals + b_vals)
    ax.set_title(title)
    ax.set_xlabel('Quantile')
    ax.set_ylabel('Absolute Difference in Core Metric Value')
    ax.legend(title='')


@profile
def _plot_metric_distributions(fig_dpath: Path, stamp: str, left: dict[str, Any], right: dict[str, Any], run_spec_name: str) -> Path | None:
    return _plot_pair_metric_distributions(fig_dpath, stamp, [left, right], run_spec_name)


@profile
def _plot_pair_metric_distributions(
    fig_dpath: Path,
    stamp: str,
    pairs: list[dict[str, Any]],
    run_spec_name: str,
    *,
    plot_layout: PlotLayout | None = None,
) -> Path | None:
    pairs = [pair for pair in pairs if pair]
    if not pairs:
        return None
    df = pd.concat([
        _distribution_rows(pair)
        for pair in pairs
    ], ignore_index=True)
    if df.empty or 'metric' not in df.columns:
        return None
    metrics = sorted(df['metric'].dropna().unique().tolist())
    if not metrics:
        return None
    pair_order = [pair['label'] for pair in pairs]
    # Pair labels are full comparison ids that splice the official component,
    # the local component, attempt UUIDs, etc., and are routinely 100+ chars
    # long — they crush the per-axis title. Alias each to a short slug for
    # the title; emit the full mapping as a sidecar legend artifact below.
    alias_map = short_alias_map(pair_order)
    layout = plot_layout or PlotLayout()
    # Pad the per-row height so 1-row × N-col grids leave room for a
    # multi-line suptitle without colliding with the axis-level titles.
    row_height = 4.2 + (1.6 if len(pair_order) == 1 else 0.0)
    _apply_matplotlib_style()
    fig, axes = plt.subplots(
        len(pair_order),
        len(metrics),
        figsize=_scaled_figsize(5.2 * len(metrics), row_height * len(pair_order), plot_layout),
        constrained_layout=False,
    )
    if len(pair_order) == 1 and len(metrics) == 1:
        axes = [[axes]]
    elif len(pair_order) == 1:
        axes = [axes]
    elif len(metrics) == 1:
        axes = [[ax] for ax in axes]
    for row_idx, pair_label in enumerate(pair_order):
        for col_idx, metric in enumerate(metrics):
            ax = axes[row_idx][col_idx]
            sub = df[(df['pair'] == pair_label) & (df['metric'] == metric)]
            discrete = _should_treat_as_discrete(sub['value'].tolist())
            sns.histplot(
                data=sub,
                x='value',
                hue='side',
                stat='probability',
                common_norm=False,
                discrete=discrete,
                multiple='dodge',
                shrink=0.8,
                bins=None if discrete else 20,
                ax=ax,
            )
            ax.set_title(f'{alias_map[pair_label]}  {metric}', fontsize=10)
            ax.set_xlabel('Core metric value')
            ax.set_ylabel('Probability')
            legend = ax.get_legend()
            if legend is not None:
                legend.set_title('')
    fig.suptitle(
        'Core Metric Score Distributions Within Each Comparison Pair — '
        f'{run_spec_name}  (per-axis titles: <pair-alias>  <metric>; '
        'see sidecar legend for full pair labels)',
        fontsize=12,
        y=layout.suptitle_y if layout.suptitle_y is not None else 0.995,
    )
    adjust_kwargs = _subplot_adjust_kwargs(fig, layout, top=0.86, bottom=0.13)
    # Multi-column grid: each column has its own y-axis label which crowds
    # the plot to its left at the layout default wspace=0.05. Bump wspace
    # so y-axis labels and tick labels have breathing room.
    adjust_kwargs['wspace'] = max(adjust_kwargs.get('wspace', 0.30), 0.30)
    fig.subplots_adjust(**adjust_kwargs)
    out_fpath = fig_dpath / f'core_metric_distributions.png'
    _atomic_savefig(fig, out_fpath, dpi=180)
    plt.close(fig)
    emit_label_legend_artifacts(
        alias_map,
        fig_dpath=fig_dpath,
        out_name='core_metric_distributions',
        title='Core Metric Distributions — short alias → full pair label',
        stamp=stamp,
    )
    return out_fpath


# short_alias_map / emit_label_legend_artifacts live in
# eval_audit.utils.labels so the same hash-and-sidecar pattern stays
# consistent everywhere a long identifier would crush a plot legend or
# axis title.


@profile
def _plot_run_metric_distributions(
    fig_dpath: Path,
    stamp: str,
    run_specs: list[tuple[str, str] | tuple[str, str, dict[str, Any] | None]],
    run_spec_name: str,
    *,
    out_name: str = 'core_metric_overlay_distributions',
    title: str = 'Overlay of Per-Instance Core Metric Score Distributions by Run',
    subtitle: str = 'This shows the raw score distributions for each core metric across the selected runs.',
    ecdf: bool = False,
    plot_layout: PlotLayout | None = None,
) -> dict[str, Path] | None:
    normalized_run_specs = _normalize_plot_run_specs(run_specs)
    frames = [
        _single_run_instance_core_rows(
            run_path,
            label,
            component=component,
        )
        for run_path, label, component in normalized_run_specs
    ]
    df = pd.concat(frames, ignore_index=True)
    if df.empty or 'metric' not in df.columns:
        return None
    metrics = sorted(df['metric'].dropna().unique().tolist())
    if not metrics:
        return None
    # Alias every legend label to a short, unique slug. The full labels
    # (component display_names) routinely run 80–120 chars and crush the plot
    # legend; the sidecar legend artifacts emitted below preserve the long
    # labels so readers can resolve the aliases.
    long_labels = sorted({label for _, label, _ in normalized_run_specs})
    alias_map = short_alias_map(long_labels)
    df = df.assign(run=df['run'].map(alias_map).fillna(df['run']))
    _apply_matplotlib_style()
    layout = plot_layout or PlotLayout()
    # Reserve a fixed inch allocation at the top of the figure for the
    # 4-line fontsize-15 suptitle; computed in inches and converted to a
    # figure-fraction below so the suptitle never crashes the first axis
    # title regardless of how many metric rows we plot.
    suptitle_band_in = 1.6
    fig_h_in = 3.2 * len(metrics) + suptitle_band_in
    fig, axes = plt.subplots(
        len(metrics),
        1,
        figsize=_scaled_figsize(10, fig_h_in, plot_layout),
        constrained_layout=False,
    )
    if len(metrics) == 1:
        axes = [axes]
    for ax, metric in zip(axes, metrics):
        sub = df[df['metric'] == metric].copy()
        if ecdf:
            sns.ecdfplot(
                data=sub,
                x='value',
                hue='run',
                ax=ax,
            )
            desc = _metric_descriptor(metric)
            ax.set_title(
                f"{metric} ECDF ({desc['kind']}, {desc['range']}, {desc['direction']})"
            )
            ax.set_ylabel('Cumulative fraction of instances')
        else:
            discrete = _should_treat_as_discrete(sub['value'].tolist())
            sns.histplot(
                data=sub,
                x='value',
                hue='run',
                stat='probability',
                common_norm=False,
                element='step',
                fill=False,
                multiple='layer',
                discrete=discrete,
                bins=None if discrete else 20,
                ax=ax,
            )
            desc = _metric_descriptor(metric)
            ax.set_title(
                f"{metric} ({desc['kind']}, {desc['range']}, {desc['direction']})"
            )
            ax.set_ylabel('Probability')
        ax.set_xlabel('Instance-level metric value')
        legend = ax.get_legend()
        if legend is not None:
            legend.set_title('')
    _set_suptitle(
        fig,
        f'{title}\n'
        f'Run Spec: {run_spec_name}\n'
        f'{subtitle}\n'
        f'Legend uses short aliases; see the sidecar legend artifact for the full labels.',
        fontsize=15,
        plot_layout=plot_layout,
    )
    # Pin top= so the reserved suptitle band is honored regardless of how
    # many metric rows are stacked below it. Bump hspace so the row-below
    # title doesn't crowd the row-above tick labels.
    actual_fig_h = fig.get_size_inches()[1]
    top_fraction = max(0.5, 1.0 - (suptitle_band_in / actual_fig_h))
    adjust_kwargs = _subplot_adjust_kwargs(fig, layout, top=top_fraction, bottom=0.05)
    adjust_kwargs['hspace'] = max(adjust_kwargs.get('hspace', 0.35), 0.35)
    fig.subplots_adjust(**adjust_kwargs)
    out_fpath = fig_dpath / f'{out_name}.png'
    _atomic_savefig(fig, out_fpath, dpi=180)
    plt.close(fig)
    legend_png_fpath, legend_txt_fpath = emit_label_legend_artifacts(
        alias_map,
        fig_dpath=fig_dpath,
        out_name=out_name,
        title=f"{title} — short alias → full label",
        stamp=stamp,
        color_map=_palette_color_map(long_labels),
    )
    artifacts: dict[str, Path] = {'plot': out_fpath}
    if legend_png_fpath is not None:
        artifacts['legend_png'] = legend_png_fpath
    if legend_txt_fpath is not None:
        artifacts['legend_txt'] = legend_txt_fpath
    return artifacts


def _normalize_plot_run_specs(
    run_specs: list[tuple[str, str] | tuple[str, str, dict[str, Any] | None]],
) -> list[tuple[str, str, dict[str, Any] | None]]:
    normalized = []
    for item in run_specs:
        if len(item) == 2:
            run_path, label = item
            component = None
        elif len(item) == 3:
            run_path, label, component = item
        else:
            raise ValueError(f'Expected 2- or 3-tuples in run_specs, got {item!r}')
        normalized.append((run_path, label, component))
    return normalized


@profile
def _plot_three_run_metric_distributions(
    fig_dpath: Path,
    stamp: str,
    kwdagger_a_run: str,
    kwdagger_b_run: str,
    official_run: str,
    run_spec_name: str,
    *,
    plot_layout: PlotLayout | None = None,
) -> Path | None:
    df = pd.concat([
        _single_run_instance_core_rows(kwdagger_a_run, 'kwdagger A'),
        _single_run_instance_core_rows(kwdagger_b_run, 'kwdagger B'),
        _single_run_instance_core_rows(official_run, 'official'),
    ], ignore_index=True)
    if df.empty or 'metric' not in df.columns:
        return None
    metrics = sorted(df['metric'].dropna().unique().tolist())
    if not metrics:
        return None
    run_order = ['kwdagger A', 'kwdagger B', 'official']
    _apply_matplotlib_style()
    fig, axes = plt.subplots(
        len(metrics),
        len(run_order),
        figsize=_scaled_figsize(5.0 * len(run_order), 3.2 * len(metrics), plot_layout),
        constrained_layout=True,
    )
    if len(metrics) == 1 and len(run_order) == 1:
        axes = [[axes]]
    elif len(metrics) == 1:
        axes = [axes]
    elif len(run_order) == 1:
        axes = [[ax] for ax in axes]
    for row_idx, metric in enumerate(metrics):
        for col_idx, run_label in enumerate(run_order):
            ax = axes[row_idx][col_idx]
            sub = df[(df['metric'] == metric) & (df['run'] == run_label)]
            discrete = _should_treat_as_discrete(sub['value'].tolist())
            sns.histplot(
                data=sub,
                x='value',
                stat='probability',
                discrete=discrete,
                shrink=0.8,
                bins=None if discrete else 20,
                ax=ax,
                color='#4C72B0',
            )
            if row_idx == 0:
                ax.set_title(run_label)
            ax.set_xlabel('Core metric value')
            ax.set_ylabel(metric if col_idx == 0 else '')
    _set_suptitle(
        fig,
        'Per-Run Instance-Level Core Metric Score Distributions\n'
        f'Run Spec: {run_spec_name}\n'
        'Columns are kwdagger repeat A, kwdagger repeat B, and the official HELM run.',
        fontsize=16,
        plot_layout=plot_layout,
    )
    out_fpath = fig_dpath / f'core_metric_three_run_distributions.png'
    _atomic_savefig(fig, out_fpath, dpi=180)
    plt.close(fig)
    return out_fpath


@profile
def _plot_overlay_metric_distributions(
    fig_dpath: Path,
    stamp: str,
    kwdagger_a_run: str,
    kwdagger_b_run: str,
    official_run: str,
    run_spec_name: str,
    *,
    plot_layout: PlotLayout | None = None,
) -> dict[str, Path] | None:
    return _plot_run_metric_distributions(
        fig_dpath,
        stamp,
        [
            (kwdagger_a_run, 'kwdagger A'),
            (kwdagger_b_run, 'kwdagger B'),
            (official_run, 'official'),
        ],
        run_spec_name,
        out_name='core_metric_overlay_distributions',
        title='Overlay of Per-Instance Core Metric Score Distributions by Run',
        subtitle='This shows the raw score distributions for each core metric across kwdagger repeats and the official HELM run.',
        plot_layout=plot_layout,
    )


@profile
def _plot_overlay_metric_ecdfs(
    fig_dpath: Path,
    stamp: str,
    kwdagger_a_run: str,
    kwdagger_b_run: str,
    official_run: str,
    run_spec_name: str,
    *,
    plot_layout: PlotLayout | None = None,
) -> dict[str, Path] | None:
    return _plot_run_metric_distributions(
        fig_dpath,
        stamp,
        [
            (kwdagger_a_run, 'kwdagger A'),
            (kwdagger_b_run, 'kwdagger B'),
            (official_run, 'official'),
        ],
        run_spec_name,
        out_name='core_metric_ecdfs',
        title='ECDF of Per-Instance Core Metric Scores by Run',
        subtitle='This often communicates sparse or zero-heavy metric distributions more clearly than histograms.',
        ecdf=True,
        plot_layout=plot_layout,
    )


@profile
def _plot_single_pair_summary(
    fig_dpath: Path,
    stamp: str,
    pair: dict[str, Any],
    run_spec_name: str,
    *,
    plot_layout: PlotLayout | None = None,
) -> Path:
    _apply_matplotlib_style()
    layout = plot_layout or PlotLayout()
    # The full pair label (a spliced comparison id; ~150-200 chars on real
    # packets) crushes the suptitle and the right-pane legend. Alias it for
    # display; emit the alias->full mapping as a sidecar.
    alias_map = short_alias_map([pair['label']])
    pair_alias = alias_map[pair['label']]
    fig, axes = plt.subplots(
        1,
        2,
        figsize=_scaled_figsize(18, 7.5, plot_layout),
        constrained_layout=False,
    )
    quantiles = pair['instance_level']['overall_quantiles']['abs_delta']
    labels = ['p50', 'p90', 'p95', 'p99', 'max']
    abs_delta_values = [quantiles[k] for k in labels]
    axes[0].plot(range(len(labels)), abs_delta_values, marker='o', color='#4C72B0')
    axes[0].set_xticks(range(len(labels)), labels)
    axes[0].set_yscale('symlog', linthresh=1e-12)
    _apply_abs_delta_ylim_hint(axes[0], _pair_metric_domain(pair), abs_delta_values)
    axes[0].set_title('Official vs Local Instance-Level Delta Quantiles')
    axes[0].set_xlabel('Quantile')
    axes[0].set_ylabel('Absolute Difference in Core Metric Value')
    _plot_distribution(axes[1], pair, level_key='instance_level', alias_map=alias_map)
    axes[1].set_title('Official vs Local Agreement vs Tolerance')
    _set_suptitle(
        fig,
        'Core Metric Agreement and Difference Summary\n'
        f'Run Spec: {run_spec_name}\n'
        f'Pair: {pair_alias}  (full label in sidecar legend artifact)\n'
        f'Instance-level N: {pair["instance_level"]["n_rows"]}',
        fontsize=15,
        plot_layout=plot_layout,
    )
    # Roomier left/right margins so y-axis labels and the right-edge ticks
    # don't clip; wider wspace so the two panes don't crowd each other.
    adjust_kwargs = _subplot_adjust_kwargs(fig, layout, top=0.78, bottom=0.10)
    adjust_kwargs.setdefault('left', 0.06)
    adjust_kwargs['left'] = max(adjust_kwargs.get('left', 0.06), 0.06)
    adjust_kwargs['right'] = min(adjust_kwargs.get('right', 0.97), 0.97)
    adjust_kwargs['wspace'] = max(adjust_kwargs.get('wspace', 0.25), 0.25)
    fig.subplots_adjust(**adjust_kwargs)
    fig_fpath = fig_dpath / f'core_metric_report.png'
    _atomic_savefig(fig, fig_fpath, dpi=180)
    plt.close(fig)
    emit_label_legend_artifacts(
        alias_map,
        fig_dpath=fig_dpath,
        out_name='core_metric_report',
        title='Core Metric Report — short alias → full pair label',
        stamp=stamp,
        color_map=_palette_color_map([pair['label']]),
    )
    return fig_fpath


def _atomic_savefig(fig, fpath: Path, **kwargs) -> Path:
    """matplotlib ``fig.savefig`` writing to ``fpath`` atomically via safer.
    Format inferred from the file suffix (defaults to png)."""
    fpath = Path(fpath)
    suffix = fpath.suffix.lstrip('.') or 'png'
    with safer.open(fpath, 'wb', make_parents=True) as fp:
        fig.savefig(fp, format=suffix, **kwargs)
    return fpath

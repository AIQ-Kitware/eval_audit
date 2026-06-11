"""Per-pair core-metric comparison: tolerance sweeps and agreement curves.

Role among the ``core_*`` modules:

- ``reports.core_metrics`` (this file): computes per-metric agreement
  across the abs_tol sweep for one official/local comparison and renders
  ``core_metric_report.{txt,json,png}``. CLI: ``eval-audit-report-core``.
- ``reports.core_packet``: the on-disk *format* of a finished core
  report (manifest names, slugs, latest-symlink conventions).
- ``reports.core_packet_summary``: read-side loader that aggregate
  summaries use to consume finished packets.
- ``planning.core_report_planner``: decides *which* comparisons to run
  by pairing official + local components by logical run key.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

from loguru import logger

from eval_audit.infra.logging import rich_link, setup_cli_logging
import datetime as datetime_mod
import json
import os
import shutil
import statistics
import warnings
from pathlib import Path
from typing import Any

import kwutil
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

from eval_audit.helm.diff import HelmRunDiff
from eval_audit.helm import metrics as helm_metrics
from eval_audit.helm.hashers import stable_hash36
from eval_audit.indexing.schema import extract_run_spec_fields
import safer

from eval_audit.infra.fs_publish import link_alias, safe_unlink, write_text_atomic
from eval_audit.normalized import (
    NormalizedRun,
    NormalizedRunRef,
    SourceKind,
    load_run,
)
from eval_audit.normalized import compare as ncompare
from eval_audit.normalized.helm_compat import helm_view
from eval_audit.reports.paper_labels import load_paper_label_manager
from eval_audit.utils.labels import emit_label_legend_artifacts, short_alias_map
from eval_audit.reports.core_packet import load_packet_manifests
from eval_audit.utils.numeric import quantile as _quantile

from eval_audit.infra.profiling import profile

# --- compat re-exports -------------------------------------------------
# Implementation moved to reports.core_metric_{curves,plots,tables} on
# 2026-06-11 (Phase 2 of docs/planning/repo-refactor-plan.md). Tests
# access these names via this module; keep re-exporting them.
from eval_audit.reports.core_metric_curves import (  # noqa: F401
    MetricDomain,
    _load_json,
    _load_optional_cross_machine_pair,
    _collect_stat_means,
    _EMPTY_RUN_DIAGNOSTICS,
    _run_diagnostics,
    _diagnostic_flags,
    _group_quantiles,
    _metric_quantiles,
    _BINARY_CORE_METRICS,
    _BOUNDED_OVERLAP_CORE_METRICS,
    _metric_descriptor,
    _metric_domain,
    _common_metric_domain,
    _pair_metric_domain,
    _should_treat_as_discrete,
    _agreement_curve,
    _infer_run_spec_name,
    _load_normalized,
    _component_source_kind,
    _load_component_run,
    _build_pair,
    _agreement_curve_rows,
    _per_metric_agreement_curves,
    _distribution_rows,
    _single_run_instance_core_rows,
    _SimpleStatRow,
    _single_run_core_stat_index,
    _strip_private,
    _find_pair,
    _load_run_spec_json,
    _component_spec_metadata,
    _same_value_fact,
    _comparability_summary,
    _warnings_payload,
    _warning_summary_lines,
    _find_curve_value,
)
from eval_audit.reports.core_metric_plots import (  # noqa: F401
    _PLOT_TARGETS,
    _wants_plot,
    PlotLayout,
    _coalesce,
    _plot_layout_from_cli,
    _scaled_figsize,
    _apply_matplotlib_style,
    _palette_color_map,
    _apply_plot_layout,
    _set_suptitle,
    _subplot_adjust_kwargs,
    _apply_xlim_hint,
    _apply_abs_delta_ylim_hint,
    _plot_distribution,
    _plot_per_metric_agreement,
    _plot_quantiles,
    _plot_metric_distributions,
    _plot_pair_metric_distributions,
    _plot_run_metric_distributions,
    _normalize_plot_run_specs,
    _plot_three_run_metric_distributions,
    _plot_overlay_metric_distributions,
    _plot_overlay_metric_ecdfs,
    _plot_single_pair_summary,
    _atomic_savefig,
)
from eval_audit.reports.core_metric_tables import (  # noqa: F401
    _write_three_run_runlevel_table,
    _write_two_run_runlevel_table,
    _write_comparison_runlevel_table,
    _write_text,
    _write_management_summary,
    _write_latest_alias,
)


@profile
def main(argv: list[str] | None = None) -> None:
    setup_cli_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument('--report-dpath', required=True)
    parser.add_argument('--components-manifest', default=None)
    parser.add_argument('--comparisons-manifest', default=None)
    parser.add_argument(
        '--render-heavy-pairwise-plots',
        action='store_true',
        default=False,
        help=(
            'Also render heavy per-pair PNG plots (histograms, ECDFs, per-metric agreement curves). '
            'Off by default; run render_heavy_pairwise_plots.sh in the report directory instead.'
        ),
    )
    parser.add_argument(
        '--plots-only',
        action='store_true',
        default=False,
        help=(
            'Skip rewriting the JSON/text/management/warnings/runlevel-table report artifacts; '
            'only redraw figures and update plot latest aliases. Intended for fast iteration on '
            'plot styling: edit core_metrics.py and rerun redraw_plots.sh in the report directory.'
        ),
    )
    parser.add_argument(
        '--plot_figure_scale',
        type=float,
        default=None,
        help=(
            'Optional multiplicative scale for Matplotlib figure sizes. '
            'Increase when labels or titles are too crowded for the canvas.'
        ),
    )
    parser.add_argument(
        '--plot-figure-scale',
        dest='plot_figure_scale',
        type=float,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        '--plot_target',
        choices=sorted(_PLOT_TARGETS),
        default='all',
        help=(
            'When redrawing plots, render only this plot family. '
            'Use all to refresh every plot artifact.'
        ),
    )
    parser.add_argument(
        '--plot-target',
        dest='plot_target',
        choices=sorted(_PLOT_TARGETS),
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        '--plot_suptitle_y',
        type=float,
        default=None,
        help=(
            'Optional Matplotlib figure-coordinate y position for figure suptitles. '
            'Increase above the default when subplot titles overlap a multi-line suptitle.'
        ),
    )
    parser.add_argument(
        '--plot-suptitle-y',
        dest='plot_suptitle_y',
        type=float,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        '--plot_constrained_h_pad',
        type=float,
        default=None,
        help=(
            'Optional constrained-layout vertical padding in inches. '
            'Useful for adding space between suptitles, subplot titles, and axes.'
        ),
    )
    parser.add_argument(
        '--plot-constrained-h-pad',
        dest='plot_constrained_h_pad',
        type=float,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        '--plot_constrained_hspace',
        type=float,
        default=None,
        help=(
            'Optional constrained-layout vertical spacing between subplot groups. '
            'Use with --plot_constrained_h_pad when crowded figures still overlap.'
        ),
    )
    parser.add_argument(
        '--plot-constrained-hspace',
        dest='plot_constrained_hspace',
        type=float,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        '--plot_constrained_w_pad',
        type=float,
        default=None,
        help=(
            'Optional constrained-layout horizontal padding in inches. '
            'Useful when y-axis labels, legends, or side-by-side panels crowd each other.'
        ),
    )
    parser.add_argument(
        '--plot-constrained-w-pad',
        dest='plot_constrained_w_pad',
        type=float,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        '--plot_constrained_wspace',
        type=float,
        default=None,
        help=(
            'Optional constrained-layout horizontal spacing between subplot groups. '
            'Use with --plot_constrained_w_pad when side-by-side panels are too tight.'
        ),
    )
    parser.add_argument(
        '--plot-constrained-wspace',
        dest='plot_constrained_wspace',
        type=float,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        '--plot_subplot_left',
        type=float,
        default=None,
        help='Optional manual left margin for fig.subplots_adjust, as a figure fraction.',
    )
    parser.add_argument(
        '--plot-subplot-left',
        dest='plot_subplot_left',
        type=float,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        '--plot_subplot_right',
        type=float,
        default=None,
        help='Optional manual right edge for fig.subplots_adjust, as a figure fraction.',
    )
    parser.add_argument(
        '--plot-subplot-right',
        dest='plot_subplot_right',
        type=float,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        '--plot_subplot_bottom',
        type=float,
        default=None,
        help='Optional manual bottom margin for fig.subplots_adjust, as a figure fraction.',
    )
    parser.add_argument(
        '--plot-subplot-bottom',
        dest='plot_subplot_bottom',
        type=float,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        '--plot_subplot_top',
        type=float,
        default=None,
        help='Optional manual top edge for fig.subplots_adjust, as a figure fraction.',
    )
    parser.add_argument(
        '--plot-subplot-top',
        dest='plot_subplot_top',
        type=float,
        help=argparse.SUPPRESS,
    )
    # The diagnosis labels (recipe_clean / deployment_drift /
    # comparability_unknown / ...) live on top of the agreement-ratio
    # numbers and are derived from HELM ``run_spec.json``. For the EEE-only
    # paper validity claim, the heatmap's numerical content must come from
    # EEE alone; --skip-diagnosis bypasses the HelmRunDiff branch in
    # _build_pair so no run_spec.json is consulted, the auxiliary
    # diagnosis dict comes back empty, and the agreement numbers are
    # untouched. Default reads EVAL_AUDIT_SKIP_HELM_DIAGNOSIS={1,true,yes}
    # so wrappers can flip this for an entire pipeline invocation without
    # threading the flag through every CLI hop.
    _skip_diag_default = os.environ.get(
        'EVAL_AUDIT_SKIP_HELM_DIAGNOSIS', ''
    ).strip().lower() in {'1', 'true', 'yes'}
    parser.add_argument(
        '--skip-diagnosis',
        action='store_true',
        default=_skip_diag_default,
        help=(
            'Skip the HELM-derived diagnosis labels (recipe_clean / '
            'deployment_drift / etc). Use for the EEE-only paper path '
            'where run_spec.json must not be consulted. The heatmap '
            'numerical content is unaffected; only the auxiliary '
            'diagnosis dict in core_metric_report.json becomes empty. '
            'Also reads EVAL_AUDIT_SKIP_HELM_DIAGNOSIS={1,true,yes} as '
            'the default.'
        ),
    )
    # Distinct from HELM_AUDIT_SKIP_PLOTLY (which only affects Plotly /
    # Chromium PNG exports in build_reports_summary). All the heavyweight
    # per-pair figures in core_metrics are matplotlib — they were not
    # gated by the SKIP_PLOTLY env var and that's been a foot-gun for
    # iteration. --no-plots / EVAL_AUDIT_NO_PLOTS={1,true,yes} skips
    # every matplotlib plot block in core_metrics.main: the 2x2 summary
    # panel, the per-pair distribution figures, the overlay/ECDF runs,
    # and the per-metric agreement figures. JSON / TXT / management
    # summaries / runlevel tables still write — only static figures are
    # skipped.
    _no_plots_default = os.environ.get(
        'EVAL_AUDIT_NO_PLOTS', ''
    ).strip().lower() in {'1', 'true', 'yes'}
    parser.add_argument(
        '--no-plots',
        action='store_true',
        default=_no_plots_default,
        help=(
            'Skip every matplotlib figure in core_metrics.main (summary '
            'panel, pair distributions, overlays, ECDFs, per-metric '
            'agreement). JSON/TXT/runlevel-table outputs are unaffected. '
            'Distinct from HELM_AUDIT_SKIP_PLOTLY which only gates the '
            'Plotly/Chromium PNG exports in build_reports_summary. '
            'Also reads EVAL_AUDIT_NO_PLOTS={1,true,yes} as the default.'
        ),
    )
    args = parser.parse_args(argv)
    plot_layout = _plot_layout_from_cli(args)
    plot_target = args.plot_target

    thresholds = [0.0, 1e-12, 1e-9, 1e-6, 1e-4, 1e-3, 1e-2, 2e-2, 5e-2, 1e-1, 2.5e-1, 5e-1, 1.0]
    report_dpath = Path(args.report_dpath).expanduser().resolve()
    report_dpath.mkdir(parents=True, exist_ok=True)
    stamp = datetime_mod.datetime.now(datetime_mod.UTC).strftime('%Y%m%dT%H%M%SZ')
    # History layer retired 2026-04-28: write stamped intermediates next to
    # the visible *.* targets and let write_latest_alias rename them
    # in place. No .history/ subdir is created.
    history_dpath = report_dpath
    (
        components_manifest_fpath,
        components_manifest,
        comparisons_manifest_fpath,
        comparisons_manifest,
    ) = load_packet_manifests(
        report_dpath=report_dpath,
        components_manifest=args.components_manifest,
        comparisons_manifest=args.comparisons_manifest,
    )
    components = components_manifest.get('components') or []
    all_comparisons = comparisons_manifest.get('comparisons') or []
    comparisons = [comparison for comparison in all_comparisons if comparison.get('enabled', True)]
    component_lookup = {component['component_id']: component for component in components}
    run_spec_name = _infer_run_spec_name(*(component['run_path'] for component in components))

    # Memoize NormalizedRun loads across the per-pair loop. A typical
    # packet has one official component reused across N official_vs_local
    # pairs and ~N local components each appearing twice (once in
    # official_vs_local, once as the reference or repeat in
    # local_repeat). Without caching the official artifact gets parsed
    # ~N times. The cache is intentionally local to this packet
    # invocation so memory doesn't accumulate when from_eee renders
    # many packets in sequence (or in parallel via subprocess.run).
    component_cache: dict[str, NormalizedRun] = {}

    pairs = []
    for comparison in comparisons:
        component_ids = comparison.get('component_ids') or []
        if len(component_ids) != 2:
            continue
        component_a = component_lookup[component_ids[0]]
        component_b = component_lookup[component_ids[1]]
        # Pure-EEE components don't carry a HELM run_path; fall back to the
        # eee_artifact_path or component_id so _build_pair has a non-None
        # display anchor without also needing run_spec.json on disk.
        run_a = (
            component_a.get('run_path')
            or component_a.get('eee_artifact_path')
            or component_a['component_id']
        )
        run_b = (
            component_b.get('run_path')
            or component_b.get('eee_artifact_path')
            or component_b['component_id']
        )
        pair = _build_pair(
            run_a,
            run_b,
            str(comparison['comparison_id']),
            thresholds,
            component_a=component_a,
            component_b=component_b,
            component_cache=component_cache,
            skip_diagnosis=args.skip_diagnosis,
        )
        pair['artifact_formats'] = {
            component_ids[0]: component_a.get('artifact_format') or 'helm',
            component_ids[1]: component_b.get('artifact_format') or 'helm',
        }
        pair['comparison_id'] = comparison['comparison_id']
        pair['comparison_kind'] = comparison.get('comparison_kind')
        pair['component_ids'] = component_ids
        pair['reference_component_id'] = comparison.get('reference_component_id')
        pair['comparability_facts'] = comparison.get('comparability_facts') or {}
        pair['warnings'] = comparison.get('warnings') or []
        pair['caveats'] = comparison.get('caveats') or []
        pair['label'] = comparison['comparison_id']
        pairs.append(pair)

    run_diagnostics = {
        component['component_id']: _run_diagnostics(component['run_path'])
        for component in components
    }
    single_run_mode = not any(
        comparison.get('comparison_kind') == 'local_repeat'
        for comparison in comparisons
    )
    component_comparability = _comparability_summary(components)
    comparability = {
        'facts': components_manifest.get('comparability_facts') or component_comparability.get('facts', {}),
        'component_metadata': component_comparability.get('component_metadata', {}),
    }

    report = {
        'generated_utc': stamp,
        'run_spec_name': run_spec_name,
        'report_dpath': str(report_dpath),
        'packet_id': components_manifest.get('packet_id'),
        'run_entry': components_manifest.get('run_entry'),
        'planner_version': components_manifest.get('planner_version'),
        'components_manifest_path': str(components_manifest_fpath),
        'comparisons_manifest_path': str(comparisons_manifest_fpath),
        'warnings_manifest_path': str(report_dpath / 'warnings.json'),
        'thresholds': thresholds,
        'components': components,
        'comparisons': all_comparisons,
        'pairs': pairs,
        'run_diagnostics': run_diagnostics,
        'diagnostic_flags': _diagnostic_flags(run_diagnostics, components, comparisons),
        'single_run_mode': single_run_mode,
        'comparability': comparability,
        'packet_warnings': components_manifest.get('warnings') or [],
        'packet_caveats': components_manifest.get('caveats') or [],
        'official_selection': components_manifest.get('official_selection') or {},
    }

    json_fpath = history_dpath / f'core_metric_report.json'
    txt_fpath = history_dpath / f'core_metric_report.txt'
    mgmt_fpath = history_dpath / f'core_metric_management_summary.txt'
    warnings_json_fpath = history_dpath / f'warnings.json'
    warnings_txt_fpath = history_dpath / f'warnings.txt'
    official_vs_local = _find_pair(report, 'official_vs_local') or (pairs[-1] if pairs else None)
    local_repeat = _find_pair(report, 'local_repeat')

    if official_vs_local is None:
        raise SystemExit('No enabled comparisons were available to render a core metric report')

    # --no-plots is the master kill-switch: when set, no matplotlib
    # block runs regardless of plots_only / render_heavy_pairwise_plots
    # / plot_target.
    render_core_metric_report = (
        (not args.no_plots)
        and ((not args.plots_only) or _wants_plot(plot_target, 'core_metric_report'))
    )
    if render_core_metric_report and len(pairs) == 1:
        fig_fpath = _plot_single_pair_summary(
            history_dpath,
            stamp,
            official_vs_local,
            run_spec_name,
            plot_layout=plot_layout,
        )
    elif render_core_metric_report:
        fig_fpath = history_dpath / f'core_metric_report.png'
        extra_pair = _load_optional_cross_machine_pair(report_dpath)
        paper_labels = load_paper_label_manager(style='paper_short')
        all_pairs = pairs + ([extra_pair] if extra_pair is not None else [])
        # Alias every pair label so the legend in the bottom row stays
        # readable; emit the alias->full mapping as a sidecar artifact.
        pair_alias_map = short_alias_map([p['label'] for p in all_pairs])
        pair_line = 'Pairs: ' + ' vs '.join(
            pair_alias_map.get(pair['label'], pair_alias_map.get(pair.get('comparison_id', ''), pair.get('comparison_id', '')))
            for pair in pairs
        )
        if extra_pair is not None:
            pair_line += f' + {pair_alias_map[extra_pair["label"]]}'
        pair_line += '  (full labels in sidecar legend artifact)'
        pair_line = paper_labels.relabel_text(pair_line)
        _apply_matplotlib_style()
        layout = plot_layout or PlotLayout()
        fig, axes = plt.subplots(
            2,
            2,
            figsize=_scaled_figsize(24, 14.5, plot_layout),
            constrained_layout=False,
        )
        _plot_quantiles(
            axes[0, 0],
            local_repeat or official_vs_local,
            official_vs_local,
            'run_level',
            'Run-Level Delta Quantiles'
        )
        _plot_quantiles(
            axes[0, 1],
            local_repeat or official_vs_local,
            official_vs_local,
            'instance_level',
            'Instance-Level Delta Quantiles'
        )
        _plot_distribution(axes[1, 0], *all_pairs, level_key='run_level', alias_map=pair_alias_map)
        axes[1, 0].set_title('Run-Level Agreement vs Tolerance', fontsize=11)
        _plot_distribution(axes[1, 1], *all_pairs, level_key='instance_level', alias_map=pair_alias_map)
        axes[1, 1].set_title('Instance-Level Agreement vs Tolerance', fontsize=11)
        axes[0, 0].title.set_fontsize(11)
        axes[0, 1].title.set_fontsize(11)
        _set_suptitle(
            fig,
            'Core Metric Agreement and Difference Summary\n'
            f'Run Spec: {run_spec_name}\n'
            f'{pair_line}',
            fontsize=15,
            plot_layout=plot_layout,
        )
        adjust_kwargs = _subplot_adjust_kwargs(fig, layout, top=0.82, bottom=0.07)
        adjust_kwargs['left'] = max(adjust_kwargs.get('left', 0.06), 0.06)
        adjust_kwargs['right'] = min(adjust_kwargs.get('right', 0.98), 0.98)
        adjust_kwargs['wspace'] = max(adjust_kwargs.get('wspace', 0.22), 0.22)
        fig.subplots_adjust(**adjust_kwargs)
        _atomic_savefig(fig, fig_fpath, dpi=180)
        plt.close(fig)
        emit_label_legend_artifacts(
            pair_alias_map,
            fig_dpath=report_dpath,
            out_name='core_metric_report',
            title='Core Metric Report — short alias → full pair label',
            stamp=stamp,
            color_map=_palette_color_map([p['label'] for p in all_pairs]),
        )
    else:
        fig_fpath = None

    render_pairwise = args.render_heavy_pairwise_plots and not args.no_plots
    if render_pairwise and _wants_plot(plot_target, 'core_metric_distributions'):
        dist_fig_fpath = _plot_pair_metric_distributions(
            history_dpath,
            stamp,
            pairs,
            run_spec_name,
            plot_layout=plot_layout,
        )
    else:
        dist_fig_fpath = None
    if render_pairwise and (
        _wants_plot(plot_target, 'core_metric_overlay_distributions')
        or _wants_plot(plot_target, 'core_metric_ecdfs')
    ):
        run_specs = [
            (component['run_path'], component['display_name'], component)
            for component in components
        ]
    else:
        run_specs = []
    if render_pairwise and _wants_plot(plot_target, 'core_metric_overlay_distributions'):
        overlay_dist_artifacts = _plot_run_metric_distributions(
            history_dpath,
            stamp,
            run_specs,
            run_spec_name,
            out_name='core_metric_overlay_distributions',
            title='Overlay of Per-Instance Core Metric Score Distributions by Component',
            subtitle='Each series comes from a selected report component declared in the components manifest.',
            plot_layout=plot_layout,
        )
    else:
        overlay_dist_artifacts = None
    if render_pairwise and _wants_plot(plot_target, 'core_metric_ecdfs'):
        ecdf_artifacts = _plot_run_metric_distributions(
            history_dpath,
            stamp,
            run_specs,
            run_spec_name,
            out_name='core_metric_ecdfs',
            title='ECDF of Per-Instance Core Metric Scores by Component',
            subtitle='Each series comes from a selected report component declared in the components manifest.',
            ecdf=True,
            plot_layout=plot_layout,
        )
    else:
        ecdf_artifacts = None
    if render_pairwise and _wants_plot(plot_target, 'core_metric_per_metric_agreement'):
        per_metric_agree_fpath = _plot_per_metric_agreement(
            history_dpath,
            stamp,
            *pairs,
            level_key='instance_level',
            thresholds=thresholds,
            plot_layout=plot_layout,
        )
    else:
        per_metric_agree_fpath = None
    overlay_dist_fpath = (overlay_dist_artifacts or {}).get('plot') if overlay_dist_artifacts else None
    overlay_dist_legend_png = (overlay_dist_artifacts or {}).get('legend_png') if overlay_dist_artifacts else None
    overlay_dist_legend_txt = (overlay_dist_artifacts or {}).get('legend_txt') if overlay_dist_artifacts else None
    ecdf_fig_fpath = (ecdf_artifacts or {}).get('plot') if ecdf_artifacts else None
    ecdf_legend_png = (ecdf_artifacts or {}).get('legend_png') if ecdf_artifacts else None
    ecdf_legend_txt = (ecdf_artifacts or {}).get('legend_txt') if ecdf_artifacts else None
    plots_only = args.plots_only
    if not plots_only:
        runlevel_csv_fpath, runlevel_md_fpath = _write_comparison_runlevel_table(
            history_dpath,
            stamp,
            comparisons,
            component_lookup,
            component_cache=component_cache,
        )
        report = kwutil.Json.ensure_serializable(_strip_private(report))
        write_text_atomic(json_fpath, json.dumps(report, indent=2))
        _write_text(report, txt_fpath)
        _write_management_summary(report, mgmt_fpath)
        write_text_atomic(warnings_json_fpath, json.dumps(_warnings_payload(report), indent=2) + '\n')
        write_text_atomic(warnings_txt_fpath, '\n'.join(_warning_summary_lines(report)) + '\n')

    # Build the latest alias map. In plots_only mode we only refresh plot
    # aliases — the JSON/text/management/warnings/runlevel artifacts and their
    # latest aliases are intentionally left untouched so the existing canonical
    # report stays consistent while we iterate on plot styling.
    plot_latest_map: dict[Path, str] = {}
    if fig_fpath is not None:
        plot_latest_map[fig_fpath] = 'core_metric_report.png'
    if dist_fig_fpath is not None:
        plot_latest_map[dist_fig_fpath] = 'core_metric_distributions.png'
    if overlay_dist_fpath is not None:
        plot_latest_map[overlay_dist_fpath] = 'core_metric_overlay_distributions.png'
    if overlay_dist_legend_png is not None:
        plot_latest_map[overlay_dist_legend_png] = 'core_metric_overlay_distributions_label_legend.png'
    if overlay_dist_legend_txt is not None:
        plot_latest_map[overlay_dist_legend_txt] = 'core_metric_overlay_distributions_label_legend.txt'
    if ecdf_fig_fpath is not None:
        plot_latest_map[ecdf_fig_fpath] = 'core_metric_ecdfs.png'
    if ecdf_legend_png is not None:
        plot_latest_map[ecdf_legend_png] = 'core_metric_ecdfs_label_legend.png'
    if ecdf_legend_txt is not None:
        plot_latest_map[ecdf_legend_txt] = 'core_metric_ecdfs_label_legend.txt'
    if per_metric_agree_fpath is not None:
        plot_latest_map[per_metric_agree_fpath] = 'core_metric_per_metric_agreement.png'

    if plots_only:
        latest_map = plot_latest_map
    else:
        latest_map = {
            json_fpath: 'core_metric_report.json',
            txt_fpath: 'core_metric_report.txt',
            mgmt_fpath: 'core_metric_management_summary.txt',
            warnings_json_fpath: 'warnings.json',
            warnings_txt_fpath: 'warnings.txt',
            runlevel_csv_fpath: 'core_runlevel_table.csv',
            **plot_latest_map,
        }
        if runlevel_md_fpath is not None:
            latest_map[runlevel_md_fpath] = 'core_runlevel_table.md'
    for src, latest_name in latest_map.items():
        _write_latest_alias(src, report_dpath, latest_name)
    if not plots_only:
        # Stale-alias cleanup is for the canonical (full) write path. In
        # plots_only mode the other artifacts (JSON/text/runlevel/...) are
        # deliberately not in latest_map, so blanket cleanup would erase them.
        known_latest_names = {
            'core_metric_report.json',
            'core_metric_report.txt',
            'core_metric_management_summary.txt',
            'warnings.json',
            'warnings.txt',
            'core_metric_report.png',
            'core_metric_distributions.png',
            'core_metric_three_run_distributions.png',
            'core_metric_overlay_distributions.png',
            'core_metric_overlay_distributions_label_legend.png',
            'core_metric_overlay_distributions_label_legend.txt',
            'core_metric_ecdfs.png',
            'core_metric_ecdfs_label_legend.png',
            'core_metric_ecdfs_label_legend.txt',
            'core_metric_per_metric_agreement.png',
            'core_runlevel_table.csv',
            'core_runlevel_table.md',
        }
        for latest_name in known_latest_names - set(latest_map.values()):
            safe_unlink(report_dpath / latest_name)

    if not plots_only:
        logger.info(f'Wrote core metric report: {rich_link(json_fpath)}')
        logger.info(f'Wrote core metric text: {rich_link(txt_fpath)}')
        logger.info(f'Wrote core metric management summary: {rich_link(mgmt_fpath)}')
        logger.info(f'Wrote core metric warnings json: {rich_link(warnings_json_fpath)}')
        logger.info(f'Wrote core metric warnings text: {rich_link(warnings_txt_fpath)}')
    if fig_fpath is not None:
        logger.info(f'Wrote core metric plot: {rich_link(fig_fpath)}')
    if dist_fig_fpath is not None:
        logger.info(f'Wrote core metric distributions: {rich_link(dist_fig_fpath)}')
    if overlay_dist_fpath is not None:
        logger.info(f'Wrote core metric overlay distributions: {rich_link(overlay_dist_fpath)}')
    if ecdf_fig_fpath is not None:
        logger.info(f'Wrote core metric ecdfs: {rich_link(ecdf_fig_fpath)}')
    if per_metric_agree_fpath is not None:
        logger.info(f'Wrote per-metric agreement curves: {rich_link(per_metric_agree_fpath)}')
    if not plots_only:
        logger.info(f'Wrote core run-level table csv: {rich_link(runlevel_csv_fpath)}')
        if runlevel_md_fpath is not None:
            logger.info(f'Wrote core run-level table md: {rich_link(runlevel_md_fpath)}')


if __name__ == '__main__':
    setup_cli_logging()
    main()

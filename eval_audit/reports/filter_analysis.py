from __future__ import annotations

import argparse
import datetime as datetime_mod
import json
from pathlib import Path
from typing import Any

from eval_audit.cli.index_historic_helm_runs import CLOSED_JUDGE_REQUIRED_REASON

from eval_audit.infra.fs_publish import link_alias
from eval_audit.infra.logging import rich_link, setup_cli_logging
from eval_audit.infra.report_layout import filtering_reports_root
from eval_audit.utils.sankey import emit_sankey_artifacts
from loguru import logger

# --- compat re-exports -------------------------------------------------
# Implementation moved to reports.filter_analysis_{tables,text,charts,io}
# on 2026-06-11 (Phase 2 of docs/historical/planning/repo-refactor-plan.md). The
# filter tests import these names from this module; keep re-exporting.
from eval_audit.reports.filter_analysis_tables import (  # noqa: F401
    UNCLASSIFIED_EXCLUSION,
    summarize_inventory,
    make_count_table,
    make_reason_breakout_table,
    make_open_access_exclusion_reason_table,
    make_reason_combo_breakout_table,
    make_open_access_exclusion_reason_by_model_table,
    make_reason_combo_table,
    make_candidate_pool_table,
    make_selection_path_table,
    make_pair_table,
    make_reason_examples_table,
    make_decision_examples,
    classify_hierarchical_filter_stages,
    build_hierarchical_sankey_rows,
    format_size_label,
    build_hierarchical_sankey_key,
    build_filter_reason_sankey_rows,
    _make_selected_excluded_rows,
)
from eval_audit.reports.filter_analysis_text import (  # noqa: F401
    build_filter_cardinality_text,
    build_local_serving_recovery_text,
    build_filter_report_text,
    build_analysis_text,
)
from eval_audit.reports.filter_analysis_charts import (  # noqa: F401
    _title_with_n,
    _AXIS_COUNT_TAGS,
    _bar_count_label,
    _bar_axis_values,
    _abbreviate_label,
    _bar_chart_layout,
    _bar_chart_xaxis_update,
    _emit_bar_chart,
    _emit_stacked_bar_chart,
)
from eval_audit.reports.filter_analysis_io import (  # noqa: F401
    to_tsv,
    to_markdown,
    _write_stamped_text,
    _write_stamped_json,
    _write_stamped_table,
    _shell_quote,
    write_filter_rebuild_script,
    write_filter_reproduce_script,
    _load_inventory_json,
)


def emit_filter_report_artifacts(
    *,
    report_dpath: Path,
    stamp: str,
    inventory_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    interactive_dpath = report_dpath / 'interactive'
    static_dpath = report_dpath / 'static'
    machine_dpath = report_dpath / 'machine'
    tables_dpath = static_dpath / 'tables'
    figures_dpath = static_dpath / 'figures'
    for dpath in [interactive_dpath, static_dpath, machine_dpath, tables_dpath, figures_dpath]:
        dpath.mkdir(parents=True, exist_ok=True)

    summary = summarize_inventory(inventory_rows)
    selected_rows = [row for row in inventory_rows if row.get('selection_status') == 'selected']
    excluded_rows = [row for row in inventory_rows if row.get('selection_status') != 'selected']
    by_model_rows = make_count_table(inventory_rows, facet_key='model')
    by_dataset_rows = make_count_table(inventory_rows, facet_key='dataset')
    by_scenario_rows = make_count_table(inventory_rows, facet_key='scenario')
    by_benchmark_rows = make_count_table(inventory_rows, facet_key='benchmark')
    reason_by_model_rows = make_reason_breakout_table(inventory_rows, 'model')
    reason_by_dataset_rows = make_reason_breakout_table(inventory_rows, 'dataset')
    reason_by_scenario_rows = make_reason_breakout_table(inventory_rows, 'scenario')
    reason_by_benchmark_rows = make_reason_breakout_table(inventory_rows, 'benchmark')
    open_access_exclusion_reason_rows = make_open_access_exclusion_reason_table(inventory_rows)
    open_access_exclusion_reason_by_model_rows = make_open_access_exclusion_reason_by_model_table(inventory_rows)
    open_access_text_exclusion_reason_by_model_rows = make_open_access_exclusion_reason_by_model_table(
        inventory_rows,
        excluded_reasons={'not-text-like', 'excluded-tags'},
    )
    open_access_text_size_exclusion_reason_by_model_rows = make_open_access_exclusion_reason_by_model_table(
        inventory_rows,
        excluded_reasons={'not-text-like', 'excluded-tags', 'too-large'},
    )
    summary_txt = build_filter_report_text(
        summary=summary,
        by_model_rows=by_model_rows,
        by_dataset_rows=by_dataset_rows,
        by_scenario_rows=by_scenario_rows,
        reason_by_model_rows=reason_by_model_rows,
        open_access_exclusion_reason_rows=open_access_exclusion_reason_rows,
        open_access_exclusion_reason_by_model_rows=open_access_exclusion_reason_by_model_rows,
        open_access_text_exclusion_reason_by_model_rows=open_access_text_exclusion_reason_by_model_rows,
        open_access_text_size_exclusion_reason_by_model_rows=open_access_text_size_exclusion_reason_by_model_rows,
        selected_rows=selected_rows,
    )
    selected_run_specs_txt = '\n'.join(row['run_spec_name'] for row in selected_rows) + '\n'
    cardinality_txt = build_filter_cardinality_text(inventory_rows)
    local_serving_txt = build_local_serving_recovery_text(inventory_rows)

    outputs = {
        'summary_json': str(_write_stamped_json(report_dpath, machine_dpath, 'model_filter_summary', stamp, {'summary': summary})),
        'inventory_json': str(_write_stamped_json(report_dpath, machine_dpath, 'model_filter_inventory', stamp, inventory_rows)),
        'summary_txt': str(_write_stamped_text(report_dpath, static_dpath, 'model_filter_report', stamp, '.txt', summary_txt)),
        'filter_cardinality_txt': str(_write_stamped_text(report_dpath, static_dpath, 'filter_cardinality_summary', stamp, '.txt', cardinality_txt)),
        'local_serving_txt': str(_write_stamped_text(report_dpath, static_dpath, 'filter_local_serving_summary', stamp, '.txt', local_serving_txt)),
        'selected_run_specs_txt': str(_write_stamped_text(report_dpath, static_dpath, 'model_filter_selected_run_specs', stamp, '.txt', selected_run_specs_txt)),
        'inventory_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'model_filter_inventory', stamp, inventory_rows)),
        'selected_runs_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'model_filter_selected_runs', stamp, selected_rows)),
        'excluded_runs_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'model_filter_excluded_runs', stamp, excluded_rows)),
        'by_model_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'model_filter_counts_by_model', stamp, by_model_rows)),
        'by_dataset_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'model_filter_counts_by_dataset', stamp, by_dataset_rows)),
        'by_scenario_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'model_filter_counts_by_scenario', stamp, by_scenario_rows)),
        'by_benchmark_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'model_filter_counts_by_benchmark', stamp, by_benchmark_rows)),
        'reason_by_model_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'model_filter_excluded_reason_by_model', stamp, reason_by_model_rows)),
        'reason_by_dataset_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'model_filter_excluded_reason_by_dataset', stamp, reason_by_dataset_rows)),
        'reason_by_scenario_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'model_filter_excluded_reason_by_scenario', stamp, reason_by_scenario_rows)),
        'reason_by_benchmark_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'model_filter_excluded_reason_by_benchmark', stamp, reason_by_benchmark_rows)),
        'open_access_reason_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'model_filter_excluded_reason_open_access_only', stamp, open_access_exclusion_reason_rows)),
        'open_access_reason_by_model_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'model_filter_excluded_reason_open_access_only_by_model', stamp, open_access_exclusion_reason_by_model_rows)),
        'open_access_text_reason_by_model_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'model_filter_excluded_reason_open_access_text_only_by_model', stamp, open_access_text_exclusion_reason_by_model_rows)),
        'open_access_text_size_reason_by_model_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'model_filter_excluded_reason_open_access_text_size_only_by_model', stamp, open_access_text_size_exclusion_reason_by_model_rows)),
    }
    outputs['flat_filter_sankey'] = emit_sankey_artifacts(
        rows=build_filter_reason_sankey_rows(inventory_rows),
        report_dpath=report_dpath,
        stamp=stamp,
        kind='model_filter',
        title=_title_with_n('Run Selection Filter: Which HELM Runs Were Included', len(inventory_rows)),
        stage_defs={
            'filter_reason': [
                'selected: model passed all eligibility criteria and had complete run data',
                'structurally-incomplete: run directory missing required files',
                'not-text-like: model has no text-compatible tags',
                'excluded-tags: model tagged as a modality or category we exclude',
                'too-large: model exceeds the local reproduction size budget',
                'not-open-access: model access is not open in the HELM registry',
                'no-local-helm-deployment: no default local HELM deployment path known to Stage 1 filter',
                f'{CLOSED_JUDGE_REQUIRED_REASON}: benchmark requires a proprietary / credentialed judge or annotator',
                f'{UNCLASSIFIED_EXCLUSION}: no current rule classified this exclusion',
            ],
            'outcome': [
                'selected: run was included in the reproduction list',
                'excluded: run was excluded from the reproduction list',
            ],
        },
        stage_order=[('filter_reason', 'Exclusion Criterion'), ('outcome', 'Outcome')],
        machine_dpath=machine_dpath,
        interactive_dpath=interactive_dpath,
        static_dpath=static_dpath,
    )
    link_alias(Path(outputs['filter_cardinality_txt']), report_dpath, 'filter_cardinality_summary.txt')
    link_alias(Path(outputs['local_serving_txt']), report_dpath, 'filter_local_serving_summary.txt')
    return outputs


def emit_filter_analysis_artifacts(
    *,
    report_dpath: Path,
    stamp: str,
    inventory_rows: list[dict[str, Any]],
    chosen_model_rows: list[dict[str, Any]] | None = None,
    model_filter_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    analysis_dpath = report_dpath / 'analysis'
    machine_dpath = analysis_dpath / 'machine'
    static_dpath = analysis_dpath / 'static'
    interactive_dpath = analysis_dpath / 'interactive'
    tables_dpath = static_dpath / 'tables'
    figures_dpath = static_dpath / 'figures'
    for dpath in [analysis_dpath, machine_dpath, static_dpath, interactive_dpath, tables_dpath, figures_dpath]:
        dpath.mkdir(parents=True, exist_ok=True)

    summary = summarize_inventory(inventory_rows)
    by_model_rows = make_count_table(inventory_rows, facet_key='model')
    by_dataset_rows = make_count_table(inventory_rows, facet_key='dataset')
    by_scenario_rows = make_count_table(inventory_rows, facet_key='scenario')
    by_benchmark_rows = make_count_table(inventory_rows, facet_key='benchmark')
    candidate_pool_rows = make_candidate_pool_table(inventory_rows)
    selection_path_rows = make_selection_path_table(inventory_rows)
    selected_excluded_by_model_rows, n_model_facets_shown, n_model_facets_total = _make_selected_excluded_rows(inventory_rows, 'model')
    selected_excluded_by_benchmark_rows, n_benchmark_facets_shown, n_benchmark_facets_total = _make_selected_excluded_rows(inventory_rows, 'benchmark')
    selected_excluded_by_dataset_rows, n_dataset_facets_shown, n_dataset_facets_total = _make_selected_excluded_rows(inventory_rows, 'dataset')
    selected_excluded_by_scenario_rows, n_scenario_facets_shown, n_scenario_facets_total = _make_selected_excluded_rows(inventory_rows, 'scenario')
    reasons_by_model = make_reason_breakout_table(inventory_rows, 'model')
    reasons_by_dataset = make_reason_breakout_table(inventory_rows, 'dataset')
    reasons_by_scenario = make_reason_breakout_table(inventory_rows, 'scenario')
    reasons_by_benchmark = make_reason_breakout_table(inventory_rows, 'benchmark')
    open_access_exclusion_reason_rows = make_open_access_exclusion_reason_table(inventory_rows)
    open_access_exclusion_reason_by_model_rows = make_open_access_exclusion_reason_by_model_table(inventory_rows)
    open_access_text_exclusion_reason_by_model_rows = make_open_access_exclusion_reason_by_model_table(
        inventory_rows,
        excluded_reasons={'not-text-like', 'excluded-tags'},
    )
    open_access_text_size_exclusion_reason_by_model_rows = make_open_access_exclusion_reason_by_model_table(
        inventory_rows,
        excluded_reasons={'not-text-like', 'excluded-tags', 'too-large'},
    )
    reason_combo_rows = make_reason_combo_table(inventory_rows)
    pair_model_scenario_rows = make_pair_table(inventory_rows, 'model', 'scenario')
    pair_model_benchmark_rows = make_pair_table(inventory_rows, 'model', 'benchmark')
    pair_benchmark_dataset_rows = make_pair_table(inventory_rows, 'benchmark', 'dataset')
    reason_example_rows = make_reason_examples_table(inventory_rows)
    hierarchical_sankey_rows = build_hierarchical_sankey_rows(inventory_rows)
    examples = make_decision_examples(inventory_rows)
    analysis_text = build_analysis_text(
        summary,
        by_model_rows,
        by_dataset_rows,
        by_scenario_rows,
        candidate_pool_rows,
        selection_path_rows,
        reason_combo_rows,
        pair_model_scenario_rows,
        pair_model_benchmark_rows,
        reason_example_rows,
        examples,
    )
    analysis_md = '\n'.join([
        '# Filter Candidate Analysis',
        '',
        '## Summary',
        '',
        to_markdown([{k: v for k, v in summary.items() if k != 'selected_model_names' and k != 'exclusion_reason_counts'}]),
        '',
        '## Candidate Pool',
        '',
        to_markdown(candidate_pool_rows),
        '',
        '## Selection Paths',
        '',
        to_markdown(selection_path_rows),
        '',
        '## Coverage By Model',
        '',
        to_markdown(by_model_rows[:30]),
        '',
        '## Coverage By Dataset',
        '',
        to_markdown(by_dataset_rows[:30]),
        '',
        '## Coverage By Scenario',
        '',
        to_markdown(by_scenario_rows[:30]),
        '',
        '## Reason Combinations',
        '',
        to_markdown(reason_combo_rows[:30]),
        '',
        '## Model x Scenario Cohorts',
        '',
        to_markdown(pair_model_scenario_rows[:30]),
        '',
        '## Model x Benchmark Cohorts',
        '',
        to_markdown(pair_model_benchmark_rows[:30]),
        '',
        '## Representative Exclusions',
        '',
        to_markdown(reason_example_rows[:30]),
    ]) + '\n'

    summary_payload = {
        'generated_utc': stamp,
        'summary': summary,
        'chosen_model_rows': chosen_model_rows or [],
        'model_filter_rows': model_filter_rows or [],
        'decision_examples': examples,
        'hierarchical_sankey_rows': hierarchical_sankey_rows,
        'candidate_pool_rows': candidate_pool_rows,
        'selection_path_rows': selection_path_rows,
        'reason_combo_rows': reason_combo_rows,
        'pair_model_scenario_rows': pair_model_scenario_rows,
        'pair_model_benchmark_rows': pair_model_benchmark_rows,
        'pair_benchmark_dataset_rows': pair_benchmark_dataset_rows,
        'reason_example_rows': reason_example_rows,
        'selected_excluded_by_model_rows': selected_excluded_by_model_rows,
        'selected_excluded_by_benchmark_rows': selected_excluded_by_benchmark_rows,
        'selected_excluded_by_dataset_rows': selected_excluded_by_dataset_rows,
        'selected_excluded_by_scenario_rows': selected_excluded_by_scenario_rows,
    }

    outputs = {
        'summary_json': str(_write_stamped_json(report_dpath, machine_dpath, 'filter_candidate_analysis', stamp, summary_payload)),
        'summary_txt': str(_write_stamped_text(report_dpath, static_dpath, 'filter_candidate_analysis', stamp, '.txt', analysis_text)),
        'summary_md': str(_write_stamped_text(report_dpath, static_dpath, 'filter_candidate_analysis', stamp, '.md', analysis_md)),
        'by_model_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'filter_candidate_coverage_by_model', stamp, by_model_rows)),
        'by_dataset_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'filter_candidate_coverage_by_dataset', stamp, by_dataset_rows)),
        'by_scenario_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'filter_candidate_coverage_by_scenario', stamp, by_scenario_rows)),
        'by_benchmark_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'filter_candidate_coverage_by_benchmark', stamp, by_benchmark_rows)),
        'candidate_pool_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'filter_candidate_pool', stamp, candidate_pool_rows)),
        'selection_path_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'filter_selection_paths', stamp, selection_path_rows)),
        'reason_by_model_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'filter_candidate_reasons_by_model', stamp, reasons_by_model)),
        'reason_by_dataset_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'filter_candidate_reasons_by_dataset', stamp, reasons_by_dataset)),
        'reason_by_scenario_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'filter_candidate_reasons_by_scenario', stamp, reasons_by_scenario)),
        'reason_by_benchmark_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'filter_candidate_reasons_by_benchmark', stamp, reasons_by_benchmark)),
        'reason_combo_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'filter_candidate_reason_combinations', stamp, reason_combo_rows)),
        'open_access_reason_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'filter_candidate_open_access_exclusion_reasons', stamp, open_access_exclusion_reason_rows)),
        'open_access_reason_by_model_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'filter_candidate_open_access_exclusion_reasons_by_model', stamp, open_access_exclusion_reason_by_model_rows)),
        'model_scenario_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'filter_candidate_model_by_scenario', stamp, pair_model_scenario_rows)),
        'model_benchmark_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'filter_candidate_model_by_benchmark', stamp, pair_model_benchmark_rows)),
        'benchmark_dataset_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'filter_candidate_benchmark_by_dataset', stamp, pair_benchmark_dataset_rows)),
        'reason_examples_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'filter_candidate_reason_examples', stamp, reason_example_rows)),
        'sel_excl_by_model_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'filter_candidate_selection_by_model', stamp, selected_excluded_by_model_rows)),
        'sel_excl_by_benchmark_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'filter_candidate_selection_by_benchmark', stamp, selected_excluded_by_benchmark_rows)),
        'sel_excl_by_dataset_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'filter_candidate_selection_by_dataset', stamp, selected_excluded_by_dataset_rows)),
        'sel_excl_by_scenario_tsv': str(_write_stamped_table(report_dpath, tables_dpath, 'filter_candidate_selection_by_scenario', stamp, selected_excluded_by_scenario_rows)),
    }

    selected_fraction_by_model_rows = by_model_rows
    selected_fraction_by_dataset_rows = [
        row for row in by_dataset_rows
        if (row.get('fraction_selected_of_eligible') or 0) > 0
    ]

    outputs['selected_fraction_by_model_chart'] = _emit_bar_chart(
        selected_fraction_by_model_rows,
        report_dpath=report_dpath,
        x='model',
        y='fraction_selected_of_all',
        title='Selected Fraction of All Runs by Model',
        stem='filter_candidate_fraction_selected_by_model',
        stamp=stamp,
        interactive_dpath=interactive_dpath,
        static_dpath=figures_dpath,
    )
    outputs['selected_fraction_by_dataset_chart'] = _emit_bar_chart(
        selected_fraction_by_dataset_rows,
        report_dpath=report_dpath,
        x='dataset',
        y='fraction_selected_of_all',
        title='Selected Fraction of All Runs by Dataset Slice',
        stem='filter_candidate_fraction_selected_by_dataset',
        stamp=stamp,
        interactive_dpath=interactive_dpath,
        static_dpath=figures_dpath,
    )
    outputs['open_access_exclusion_reason_chart'] = _emit_bar_chart(
        open_access_exclusion_reason_rows,
        report_dpath=report_dpath,
        x='failure_reason',
        y='run_count',
        title='Excluded Runs by Reason for Open-Access Models',
        stem='filter_candidate_open_access_exclusion_reasons',
        stamp=stamp,
        interactive_dpath=interactive_dpath,
        static_dpath=figures_dpath,
    )
    outputs['open_access_exclusion_reason_by_model_chart'] = _emit_stacked_bar_chart(
        open_access_exclusion_reason_by_model_rows,
        report_dpath=report_dpath,
        x='model',
        y='run_count',
        color='reason_combo',
        title='Open-Access Excluded Runs by Reason Combination and Model',
        stem='filter_candidate_open_access_exclusion_reasons_by_model',
        stamp=stamp,
        interactive_dpath=interactive_dpath,
        static_dpath=figures_dpath,
        xaxis_title='Model',
        yaxis_title='Excluded Run Count',
    )
    outputs['open_access_text_exclusion_reason_by_model_chart'] = _emit_stacked_bar_chart(
        open_access_text_exclusion_reason_by_model_rows,
        report_dpath=report_dpath,
        x='model',
        y='run_count',
        color='reason_combo',
        title='Open-Access, Text-Compatible Excluded Runs by Reason Combination and Model',
        stem='filter_candidate_open_access_text_exclusion_reasons_by_model',
        stamp=stamp,
        interactive_dpath=interactive_dpath,
        static_dpath=figures_dpath,
        xaxis_title='Model',
        yaxis_title='Excluded Run Count',
    )
    outputs['open_access_text_size_exclusion_reason_by_model_chart'] = _emit_stacked_bar_chart(
        open_access_text_size_exclusion_reason_by_model_rows,
        report_dpath=report_dpath,
        x='model',
        y='run_count',
        color='reason_combo',
        title='Open-Access, Text-Compatible, Size-OK Excluded Runs by Reason Combination and Model',
        stem='filter_candidate_open_access_text_size_exclusion_reasons_by_model',
        stamp=stamp,
        interactive_dpath=interactive_dpath,
        static_dpath=figures_dpath,
        xaxis_title='Model',
        yaxis_title='Excluded Run Count',
    )
    outputs['selected_vs_excluded_by_model_chart'] = _emit_stacked_bar_chart(
        selected_excluded_by_model_rows,
        report_dpath=report_dpath,
        x='model',
        y='count',
        color='selection_status',
        title='Selected vs Excluded Run Specs by Model',
        stem='filter_candidate_selection_by_model',
        stamp=stamp,
        interactive_dpath=interactive_dpath,
        static_dpath=figures_dpath,
        xaxis_title='Model',
        yaxis_title='Run Spec Count',
        color_order=['selected', 'excluded'],
        n_facets_shown=n_model_facets_shown,
        n_facets_total=n_model_facets_total,
    )
    outputs['selected_vs_excluded_by_benchmark_chart'] = _emit_stacked_bar_chart(
        selected_excluded_by_benchmark_rows,
        report_dpath=report_dpath,
        x='benchmark',
        y='count',
        color='selection_status',
        title='Selected vs Excluded Run Specs by Benchmark',
        stem='filter_candidate_selection_by_benchmark',
        stamp=stamp,
        interactive_dpath=interactive_dpath,
        static_dpath=figures_dpath,
        xaxis_title='Benchmark',
        yaxis_title='Run Spec Count',
        color_order=['selected', 'excluded'],
        n_facets_shown=n_benchmark_facets_shown,
        n_facets_total=n_benchmark_facets_total,
    )
    outputs['selected_vs_excluded_by_dataset_chart'] = _emit_stacked_bar_chart(
        selected_excluded_by_dataset_rows,
        report_dpath=report_dpath,
        x='dataset',
        y='count',
        color='selection_status',
        title='Selected vs Excluded Run Specs by Dataset Slice',
        stem='filter_candidate_selection_by_dataset',
        stamp=stamp,
        interactive_dpath=interactive_dpath,
        static_dpath=figures_dpath,
        xaxis_title='Dataset Slice',
        yaxis_title='Run Spec Count',
        color_order=['selected', 'excluded'],
        n_facets_shown=n_dataset_facets_shown,
        n_facets_total=n_dataset_facets_total,
    )
    outputs['selected_vs_excluded_by_scenario_chart'] = _emit_stacked_bar_chart(
        selected_excluded_by_scenario_rows,
        report_dpath=report_dpath,
        x='scenario',
        y='count',
        color='selection_status',
        title='Selected vs Excluded Run Specs by Scenario',
        stem='filter_candidate_selection_by_scenario',
        stamp=stamp,
        interactive_dpath=interactive_dpath,
        static_dpath=figures_dpath,
        xaxis_title='Scenario',
        yaxis_title='Run Spec Count',
        color_order=['selected', 'excluded'],
        n_facets_shown=n_scenario_facets_shown,
        n_facets_total=n_scenario_facets_total,
    )
    outputs['exclusion_reason_chart'] = _emit_bar_chart(
        [{'failure_reason': reason, 'run_count': count} for reason, count in summary['exclusion_reason_counts'].items()],
        report_dpath=report_dpath,
        x='failure_reason',
        y='run_count',
        title='Excluded Runs by Reason',
        stem='filter_candidate_exclusion_reasons',
        stamp=stamp,
        interactive_dpath=interactive_dpath,
        static_dpath=figures_dpath,
    )
    outputs['reason_by_model_chart'] = _emit_stacked_bar_chart(
        reasons_by_model,
        report_dpath=report_dpath,
        x='model',
        y='run_count',
        color='failure_reason',
        title='Exclusion Reasons by Model',
        stem='filter_candidate_exclusion_reasons_by_model',
        stamp=stamp,
        interactive_dpath=interactive_dpath,
        static_dpath=figures_dpath,
        xaxis_title='Model',
        yaxis_title='Excluded Run Count',
    )
    outputs['candidate_pool_chart'] = _emit_stacked_bar_chart(
        [
            {
                'candidate_pool': row['candidate_pool'],
                'selection_status': 'selected',
                'count': row['selected_runs'],
            }
            for row in candidate_pool_rows
            if row['selected_runs']
        ] + [
            {
                'candidate_pool': row['candidate_pool'],
                'selection_status': 'excluded',
                'count': row['excluded_runs'],
            }
            for row in candidate_pool_rows
            if row['excluded_runs']
        ],
        report_dpath=report_dpath,
        x='candidate_pool',
        y='count',
        color='selection_status',
        title='Selected vs Excluded Run Specs by Candidate Pool',
        stem='filter_candidate_selection_by_candidate_pool',
        stamp=stamp,
        interactive_dpath=interactive_dpath,
        static_dpath=figures_dpath,
        xaxis_title='Candidate Pool',
        yaxis_title='Run Spec Count',
        color_order=['selected', 'excluded'],
    )
    outputs['reason_combo_chart'] = _emit_bar_chart(
        reason_combo_rows,
        report_dpath=report_dpath,
        x='reason_combo',
        y='run_count',
        title='Filter Reason Combinations',
        stem='filter_candidate_reason_combinations',
        stamp=stamp,
        interactive_dpath=interactive_dpath,
        static_dpath=figures_dpath,
    )
    outputs['hierarchical_filter_sankey'] = emit_sankey_artifacts(
        rows=hierarchical_sankey_rows,
        report_dpath=analysis_dpath,
        stamp=stamp,
        kind='hierarchical_filter_path',
        title=_title_with_n('Hierarchical Filter Path: From All HELM Runs to the Reproduced Subset', len(hierarchical_sankey_rows)),
        stage_defs=build_hierarchical_sankey_key(summary),
        stage_order=[
            ('structural_stage', 'Structural Gate'),
            ('metadata_stage', 'Metadata Gate'),
            ('access_stage', 'Open-Weight Gate'),
            ('tag_stage', 'Tag Gate'),
            ('deployment_stage', 'Deployment Gate'),
            ('size_stage', 'Size Gate'),
            ('judge_stage', 'Judge Gate'),
            ('outcome_stage', 'Outcome'),
        ],
        machine_dpath=machine_dpath,
        interactive_dpath=interactive_dpath,
        static_dpath=static_dpath,
    )
    return outputs


def emit_filter_report_bundle(
    *,
    report_dpath: Path,
    stamp: str,
    inventory_rows: list[dict[str, Any]],
    source_command: str | None = None,
) -> dict[str, Any]:
    report_outputs = emit_filter_report_artifacts(
        report_dpath=report_dpath,
        stamp=stamp,
        inventory_rows=inventory_rows,
    )
    analysis_outputs = emit_filter_analysis_artifacts(
        report_dpath=report_dpath,
        stamp=stamp,
        inventory_rows=inventory_rows,
    )
    write_filter_rebuild_script(
        report_dpath,
        inventory_json=Path(report_outputs['inventory_json']),
    )
    write_filter_reproduce_script(
        report_dpath,
        source_command=source_command,
    )
    return {
        'report': report_outputs,
        'analysis': analysis_outputs,
    }


def main(argv: list[str] | None = None) -> None:
    setup_cli_logging()
    parser = argparse.ArgumentParser(description='Analyze a saved Stage 1 filter inventory.')
    parser.add_argument('--report-dpath', default=str(filtering_reports_root()))
    parser.add_argument('--inventory-json', default=None)
    args = parser.parse_args(argv)

    report_dpath = Path(args.report_dpath).expanduser().resolve()
    inventory_json = Path(args.inventory_json).expanduser().resolve() if args.inventory_json else None
    try:
        inventory_rows = _load_inventory_json(report_dpath, inventory_json)
    except FileNotFoundError as ex:
        raise SystemExit(str(ex))
    stamp = datetime_mod.datetime.now(datetime_mod.UTC).strftime('%Y%m%dT%H%M%SZ')
    outputs = emit_filter_report_bundle(
        report_dpath=report_dpath,
        stamp=stamp,
        inventory_rows=inventory_rows,
    )
    logger.info(f"Wrote filter inventory/report bundle: {rich_link(outputs['report']['inventory_json'])}")
    logger.info(f"Wrote filter candidate analysis: {rich_link(outputs['analysis']['summary_json'])}")


if __name__ == '__main__':
    setup_cli_logging()
    main()

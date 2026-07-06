"""Text/table writers for the per-pair core-metric report: run-level
tables, the full text report, and the management summary.

Split out of ``eval_audit.reports.core_metrics`` on 2026-06-11
(Phase 2 of docs/planning/repo-refactor-plan.md). Pure relocation:
function bodies are unchanged.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
import pandas as pd
from eval_audit.infra.fs_publish import link_alias, write_text_atomic
from eval_audit.normalized import NormalizedRun
from eval_audit.normalized.diff import assert_swept_tol
from eval_audit.infra.profiling import profile
from eval_audit.reports.core_metric_curves import (
    _find_curve_value,
    _find_pair,
    _metric_descriptor,
    _single_run_core_stat_index,
)


@profile
def _write_comparison_runlevel_table(
    out_dpath: Path,
    stamp: str,
    comparisons: list[dict[str, Any]],
    component_lookup: dict[str, dict[str, Any]],
    *,
    component_cache: dict[str, NormalizedRun] | None = None,
) -> tuple[Path, Path | None]:
    rows = []
    for comparison in comparisons:
        component_ids = comparison.get('component_ids') or []
        if len(component_ids) != 2:
            continue
        left_component = component_lookup[component_ids[0]]
        right_component = component_lookup[component_ids[1]]
        idx_left = _single_run_core_stat_index(
            left_component['run_path'],
            component=left_component,
            component_cache=component_cache,
        )
        idx_right = _single_run_core_stat_index(
            right_component['run_path'],
            component=right_component,
            component_cache=component_cache,
        )
        for key in sorted(set(idx_left) & set(idx_right)):
            left = idx_left[key]
            right = idx_right[key]
            rows.append({
                'comparison_id': comparison['comparison_id'],
                'comparison_kind': comparison.get('comparison_kind'),
                'left_component_id': left_component['component_id'],
                'left_display_name': left_component['display_name'],
                'right_component_id': right_component['component_id'],
                'right_display_name': right_component['display_name'],
                'stat_key': key,
                'metric': left.metric,
                'left_mean': left.mean,
                'right_mean': right.mean,
                'abs_delta': None if left.mean is None or right.mean is None else abs(left.mean - right.mean),
            })
    table = pd.DataFrame(rows)
    csv_fpath = out_dpath / f'core_runlevel_table.csv'
    md_fpath = out_dpath / f'core_runlevel_table.md'
    table.to_csv(csv_fpath, index=False)
    try:
        # Split for line-profiler attribution: the markdown render is
        # the dominant cost (it walks every cell to compute column
        # widths), and we want it visible separately from the
        # subsequent atomic write.
        md_text = table.to_markdown(index=False)
        md_payload = md_text + '\n'
        write_text_atomic(md_fpath, md_payload)
    except ImportError:
        md_fpath = None
    return csv_fpath, md_fpath


@profile
def _write_text(report: dict[str, Any], out_fpath: Path) -> None:
    pairs = report['pairs']
    local_repeat = _find_pair(report, 'local_repeat')
    official_vs_local = _find_pair(report, 'official_vs_local') or (pairs[-1] if pairs else {})
    lines = []
    lines.append('Core Metric Report')
    lines.append('')
    lines.append(f"generated_utc: {report['generated_utc']}")
    lines.append(f"run_spec_name: {report['run_spec_name']}")
    lines.append(f"report_dpath: {report['report_dpath']}")
    lines.append(f"components_manifest: {report['components_manifest_path']}")
    lines.append(f"comparisons_manifest: {report['comparisons_manifest_path']}")
    lines.append(f"single_run_mode: {str(report.get('single_run_mode', False)).lower()}")
    lines.append(f"diagnostic_flags: {report.get('diagnostic_flags', [])}")
    lines.append('')
    lines.append('warnings_and_caveats:')
    lines.append(f"  packet_warnings: {report.get('packet_warnings', [])}")
    lines.append(f"  packet_caveats: {report.get('packet_caveats', [])}")
    lines.append(f"  warnings_manifest: {report.get('warnings_manifest_path')}")
    lines.append('')
    lines.append('selected_components:')
    for component in report.get('components', []):
        lines.append(
            f"  - {component['component_id']}: tags={component.get('tags', [])} "
            f"artifact_format={component.get('artifact_format')} "
            f"eee_artifact_path={component.get('eee_artifact_path')} "
            f"run_path={component.get('run_path')}"
        )
    lines.append('')
    lines.append('comparisons:')
    for comparison in report.get('comparisons', []):
        lines.append(
            f"  - {comparison['comparison_id']}: kind={comparison.get('comparison_kind')} "
            f"enabled={comparison.get('enabled')} component_ids={comparison.get('component_ids')}"
        )
        lines.append(f"    disabled_reason: {comparison.get('disabled_reason')}")
        lines.append(f"    warnings: {comparison.get('warnings', [])}")
        lines.append(f"    caveats: {comparison.get('caveats', [])}")
    lines.append('')
    lines.append('comparability:')
    for fact_name, fact in (report.get('comparability') or {}).get('facts', {}).items():
        lines.append(f"  {fact_name}: {fact.get('status')} values={fact.get('values')}")
    lines.append('')
    lines.append('core_metrics:')
    ref_pair = local_repeat or official_vs_local
    for metric in ref_pair.get('core_metrics', []):
        lines.append(f'  - {metric}')
    lines.append('')
    lines.append('run_diagnostics:')
    for label, diag in report.get('run_diagnostics', {}).items():
        lines.append(f'  {label}:')
        lines.append(f"    n_request_states: {diag.get('n_request_states')}")
        lines.append(f"    n_with_completions: {diag.get('n_with_completions')}")
        lines.append(f"    empty_completion_count: {diag.get('empty_completion_count')}")
        lines.append(f"    empty_completion_rate: {diag.get('empty_completion_rate')}")
        lines.append(f"    output_token_count: {json.dumps(diag.get('output_token_count'))}")
        lines.append(f"    stats_means: {json.dumps(diag.get('stats_means'))}")
    lines.append('')
    for pair in report['pairs']:
        lines.append(f"pair: {pair['comparison_id']}")
        lines.append(f"  comparison_kind: {pair.get('comparison_kind')}")
        lines.append(f"  diagnosis: {pair['diagnosis'].get('label')}")
        lines.append(f"  primary_reason_names: {pair['diagnosis'].get('primary_reason_names')}")
        lines.append(f"  run_level_n: {pair['run_level']['n_rows']}")
        lines.append(f"  instance_level_n: {pair['instance_level']['n_rows']}")
        lines.append(f"  run_level_quantiles: {json.dumps(pair['run_level']['overall_quantiles']['abs_delta'])}")
        lines.append(f"  instance_level_quantiles: {json.dumps(pair['instance_level']['overall_quantiles']['abs_delta'])}")
        lines.append('  by_metric:')
        for row in pair['instance_level']['by_metric']:
            lines.append(
                f"    - metric={row['metric']} count={row['count']} "
                f"p50={row['abs_delta']['p50']} p90={row['abs_delta']['p90']} "
                f"p95={row['abs_delta']['p95']} p99={row['abs_delta']['p99']} "
                f"max={row['abs_delta']['max']}"
            )
        lines.append('  agreement_vs_abs_tol:')
        for row in pair['instance_level']['agreement_vs_abs_tol']:
            lines.append(
                f"    - abs_tol={row['abs_tol']} agree_ratio={row['agree_ratio']}"
            )
        lines.append('')
    write_text_atomic(out_fpath, '\n'.join(lines) + '\n')


@profile
def _write_management_summary(report: dict[str, Any], out_fpath: Path) -> None:
    pairs = report['pairs']
    local_repeat = _find_pair(report, 'local_repeat')
    official_vs_local = _find_pair(report, 'official_vs_local') or (pairs[-1] if pairs else {})
    ref_pair = local_repeat or official_vs_local
    lines = []
    lines.append('Core Metric Executive Summary')
    lines.append('')
    lines.append(f"generated_utc: {report['generated_utc']}")
    lines.append(f"run_spec_name: {report['run_spec_name']}")
    lines.append(f"report_dpath: {report['report_dpath']}")
    lines.append(f"components_manifest: {report['components_manifest_path']}")
    lines.append(f"comparisons_manifest: {report['comparisons_manifest_path']}")
    lines.append(f"single_run_mode: {str(report.get('single_run_mode', False)).lower()}")
    lines.append(f"core_metrics: {', '.join(ref_pair.get('core_metrics', []))}")
    lines.append(f"diagnostic_flags: {report.get('diagnostic_flags', [])}")
    lines.append('')
    lines.append('warnings_and_caveats:')
    lines.append(f"  packet_warnings: {report.get('packet_warnings', [])}")
    lines.append(f"  packet_caveats: {report.get('packet_caveats', [])}")
    lines.append(f"  warnings_manifest: {report.get('warnings_manifest_path')}")
    lines.append('')
    lines.append('selected_components:')
    for component in report.get('components', []):
        lines.append(
            f"  - {component['component_id']}: tags={component.get('tags', [])} "
            f"artifact_format={component.get('artifact_format')} "
            f"eee_artifact_path={component.get('eee_artifact_path')} "
            f"run_path={component.get('run_path')}"
        )
    lines.append('')
    lines.append('comparisons:')
    for comparison in report.get('comparisons', []):
        lines.append(
            f"  - {comparison['comparison_id']}: kind={comparison.get('comparison_kind')} "
            f"enabled={comparison.get('enabled')} component_ids={comparison.get('component_ids')}"
        )
        lines.append(f"    disabled_reason: {comparison.get('disabled_reason')}")
        lines.append(f"    warnings: {comparison.get('warnings', [])}")
        lines.append(f"    caveats: {comparison.get('caveats', [])}")
    lines.append('')
    lines.append('comparability:')
    for fact_name, fact in (report.get('comparability') or {}).get('facts', {}).items():
        lines.append(f"  {fact_name}: {fact.get('status')} values={fact.get('values')}")
    lines.append('')
    lines.append('on_demand_heavy_pairwise_plots: render_heavy_pairwise_plots.sh (in this directory)')
    lines.append('  (histogram/ECDF distributions and per-metric agreement PNG plots; not rendered by default)')
    lines.append('')
    lines.append('metric_descriptions:')
    for metric in ref_pair.get('core_metrics', []):
        desc = _metric_descriptor(metric)
        lines.append(
            f"  - {metric}: {desc['kind']}; {desc['range']}; {desc['direction']}"
        )
    lines.append('')
    lines.append('run_diagnostics:')
    for label, diag in report.get('run_diagnostics', {}).items():
        lines.append(f'  {label}:')
        lines.append(f"    n_request_states: {diag.get('n_request_states')}")
        lines.append(f"    n_with_completions: {diag.get('n_with_completions')}")
        lines.append(f"    empty_completion_count: {diag.get('empty_completion_count')}")
        lines.append(f"    empty_completion_rate: {diag.get('empty_completion_rate')}")
        lines.append(f"    mean_output_tokens_from_state: {(diag.get('output_token_count') or {}).get('mean')}")
        lines.append(f"    p90_output_tokens_from_state: {(diag.get('output_token_count') or {}).get('p90')}")
        lines.append(f"    num_output_tokens_from_stats: {(diag.get('stats_means') or {}).get('num_output_tokens')}")
        lines.append(f"    finish_reason_unknown_from_stats: {(diag.get('stats_means') or {}).get('finish_reason_unknown')}")
    lines.append('')
    if local_repeat is not None:
        lines.append(f"{local_repeat['comparison_id']}:")
        lines.append(f"  diagnosis: {local_repeat['diagnosis'].get('label')}")
        lines.append(f"  run-level N: {local_repeat['run_level']['n_rows']}")
        lines.append(f"  instance-level N: {local_repeat['instance_level']['n_rows']}")
        lines.append(
            f"  instance agreement at abs_tol=0.0: {_find_curve_value(local_repeat['instance_level']['agreement_vs_abs_tol'], assert_swept_tol(0.0))}"
        )
        lines.append(
            f"  run-level abs delta max: {local_repeat['run_level']['overall_quantiles']['abs_delta']['max']}"
        )
        lines.append(
            f"  instance-level abs delta max: {local_repeat['instance_level']['overall_quantiles']['abs_delta']['max']}"
        )
        lines.append('')
    else:
        lines.append('local_repeat: not_computed')
        lines.append('')
    # IM-6: the management summary shows a single official_vs_local pair. When a
    # packet carries more than one (e.g. split-by-track), disclose the count and
    # which one is being shown so the reader knows the summary is "1 of N".
    n_official_vs_local_pairs = sum(
        1 for pair in pairs if pair.get('comparison_kind') == 'official_vs_local'
    )
    if n_official_vs_local_pairs > 1:
        lines.append(
            f"n_official_vs_local_pairs: {n_official_vs_local_pairs}; "
            f"showing {official_vs_local.get('comparison_id')}"
        )
    lines.append(f"{official_vs_local['comparison_id']}:")
    lines.append(f"  diagnosis: {official_vs_local['diagnosis'].get('label')}")
    lines.append(f"  run-level N: {official_vs_local['run_level']['n_rows']}")
    lines.append(f"  instance-level N: {official_vs_local['instance_level']['n_rows']}")
    for tol in [0.0, 1e-3, 1e-2, 1e-1, 2.5e-1, 5e-1, 1.0]:
        lines.append(
            f"  instance agreement at abs_tol={tol}: "
            f"{_find_curve_value(official_vs_local['instance_level']['agreement_vs_abs_tol'], assert_swept_tol(tol))}"
        )
    lines.append(
        f"  run-level abs delta p90/max: "
        f"{official_vs_local['run_level']['overall_quantiles']['abs_delta']['p90']} / "
        f"{official_vs_local['run_level']['overall_quantiles']['abs_delta']['max']}"
    )
    lines.append(
        f"  instance-level abs delta p99/max: "
        f"{official_vs_local['instance_level']['overall_quantiles']['abs_delta']['p99']} / "
        f"{official_vs_local['instance_level']['overall_quantiles']['abs_delta']['max']}"
    )
    write_text_atomic(out_fpath, '\n'.join(lines) + '\n')


def _write_latest_alias(src: Path | None, latest_root: Path, latest_name: str) -> Path | None:
    """Tolerates ``src is None``. After the simplification (2026-04-28b)
    the canonical artifact is written directly to ``<root>/<name>.<ext>``,
    so callers passing ``src`` already at that target make this a no-op.
    The fallback is :func:`link_alias` for cross-tree navigation aliases."""
    if src is None:
        return None
    target = latest_root / latest_name
    if Path(src) == target:
        return target
    return link_alias(src, latest_root, latest_name)

"""Data-shaping for the Stage 1 filter report: inventory summaries,
count/breakout tables, and sankey-row builders. No rendering here.

Split out of ``eval_audit.reports.filter_analysis`` on 2026-06-11
(Phase 2 of docs/historical/planning/repo-refactor-plan.md). Pure relocation:
function bodies are unchanged.
"""
from __future__ import annotations
from typing import Any
from eval_audit.cli.index_historic_helm_runs import CLOSED_JUDGE_REQUIRED_REASON



UNCLASSIFIED_EXCLUSION = 'unclassified-exclusion'


def summarize_inventory(inventory_rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_rows = len(inventory_rows)
    selected_rows = [row for row in inventory_rows if row.get('selection_status') == 'selected']
    excluded_rows = [row for row in inventory_rows if row.get('selection_status') != 'selected']
    considered_rows = [row for row in inventory_rows if row.get('considered_for_selection')]
    eligible_rows = [row for row in inventory_rows if row.get('eligible_candidate')]
    structurally_incomplete = [row for row in inventory_rows if row.get('is_structurally_incomplete')]
    unique_selected_models = sorted({row.get('model') for row in selected_rows if row.get('model')})

    exclusion_counts: dict[str, int] = {}
    for row in excluded_rows:
        reasons = row.get('failure_reasons', []) or []
        if not reasons:
            reasons = [UNCLASSIFIED_EXCLUSION]
        for reason in reasons:
            exclusion_counts[reason] = exclusion_counts.get(reason, 0) + 1
    exclusion_counts = dict(sorted(exclusion_counts.items(), key=lambda item: (-item[1], item[0])))

    def frac(num: int, den: int) -> float | None:
        return None if den == 0 else num / den

    return {
        'total_discovered_runs': total_rows,
        'considered_runs': len(considered_rows),
        'eligible_runs': len(eligible_rows),
        'selected_runs': len(selected_rows),
        'excluded_runs': len(excluded_rows),
        'structurally_incomplete_runs': len(structurally_incomplete),
        'selected_models': len(unique_selected_models),
        'selected_model_names': unique_selected_models,
        'fraction_considered_of_all': frac(len(considered_rows), total_rows),
        'fraction_eligible_of_all': frac(len(eligible_rows), total_rows),
        'fraction_selected_of_all': frac(len(selected_rows), total_rows),
        'fraction_selected_of_considered': frac(len(selected_rows), len(considered_rows)),
        'fraction_selected_of_eligible': frac(len(selected_rows), len(eligible_rows)),
        'exclusion_reason_counts': exclusion_counts,
    }


def make_count_table(
    inventory_rows: list[dict[str, Any]],
    *,
    facet_key: str,
) -> list[dict[str, Any]]:
    facet_values = sorted({row.get(facet_key) or 'unknown' for row in inventory_rows})
    out = []
    for facet in facet_values:
        rows = [row for row in inventory_rows if (row.get(facet_key) or 'unknown') == facet]
        total_runs = len(rows)
        considered_runs = sum(1 for row in rows if row.get('considered_for_selection'))
        eligible_runs = sum(1 for row in rows if row.get('eligible_candidate'))
        selected_runs = sum(1 for row in rows if row.get('selection_status') == 'selected')
        excluded_runs = total_runs - selected_runs
        reasons: dict[str, int] = {}
        for row in rows:
            row_reasons = row.get('failure_reasons', []) or []
            if row.get('selection_status') != 'selected' and not row_reasons:
                row_reasons = [UNCLASSIFIED_EXCLUSION]
            for reason in row_reasons:
                reasons[reason] = reasons.get(reason, 0) + 1
        top_reason = None
        top_reason_count = 0
        if reasons:
            top_reason = min(
                [(-count, reason) for reason, count in reasons.items()]
            )[1]
            top_reason_count = reasons[top_reason]
        out.append({
            facet_key: facet,
            'total_runs': total_runs,
            'considered_runs': considered_runs,
            'eligible_runs': eligible_runs,
            'selected_runs': selected_runs,
            'excluded_runs': excluded_runs,
            'fraction_considered_of_all': None if total_runs == 0 else considered_runs / total_runs,
            'fraction_selected_of_all': None if total_runs == 0 else selected_runs / total_runs,
            'fraction_selected_of_considered': None if considered_runs == 0 else selected_runs / considered_runs,
            'fraction_selected_of_eligible': None if eligible_runs == 0 else selected_runs / eligible_runs,
            'top_exclusion_reason': top_reason,
            'top_exclusion_reason_count': top_reason_count,
        })
    out.sort(key=lambda row: (-row['total_runs'], -row['selected_runs'], str(row[facet_key])))
    return out


def make_reason_breakout_table(inventory_rows: list[dict[str, Any]], facet_key: str) -> list[dict[str, Any]]:
    counts: dict[tuple[str, str], int] = {}
    for row in inventory_rows:
        facet_value = row.get(facet_key) or 'unknown'
        reasons = row.get('failure_reasons', []) or []
        if row.get('selection_status') != 'selected' and not reasons:
            reasons = [UNCLASSIFIED_EXCLUSION]
        for reason in reasons:
            counts[(facet_value, reason)] = counts.get((facet_value, reason), 0) + 1
    rows = [
        {facet_key: facet, 'failure_reason': reason, 'run_count': count}
        for (facet, reason), count in counts.items()
    ]
    rows.sort(key=lambda row: (-row['run_count'], str(row[facet_key]), row['failure_reason']))
    return rows


def make_open_access_exclusion_reason_table(inventory_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    open_access_rows = [
        row for row in inventory_rows
        if row.get('model_access') == 'open' and row.get('selection_status') != 'selected'
    ]
    counts: dict[str, int] = {}
    for row in open_access_rows:
        reasons = row.get('failure_reasons', []) or []
        if not reasons:
            reasons = [UNCLASSIFIED_EXCLUSION]
        for reason in reasons:
            counts[reason] = counts.get(reason, 0) + 1
    rows = [{'failure_reason': reason, 'run_count': count} for reason, count in counts.items()]
    rows.sort(key=lambda row: (-row['run_count'], row['failure_reason']))
    return rows


def make_reason_combo_breakout_table(
    inventory_rows: list[dict[str, Any]],
    facet_key: str,
) -> list[dict[str, Any]]:
    counts: dict[tuple[str, str], int] = {}
    for row in inventory_rows:
        facet_value = row.get(facet_key) or 'unknown'
        reasons = row.get('failure_reasons', []) or []
        if row.get('selection_status') != 'selected' and not reasons:
            reasons = [UNCLASSIFIED_EXCLUSION]
        combo = 'selected' if row.get('selection_status') == 'selected' else '|'.join(sorted({str(reason) for reason in reasons}))
        counts[(facet_value, combo)] = counts.get((facet_value, combo), 0) + 1
    rows = [
        {facet_key: facet, 'reason_combo': combo, 'run_count': count}
        for (facet, combo), count in counts.items()
    ]
    rows.sort(key=lambda row: (-row['run_count'], str(row[facet_key]), row['reason_combo']))
    return rows


def make_open_access_exclusion_reason_by_model_table(
    inventory_rows: list[dict[str, Any]],
    *,
    excluded_reasons: set[str] | None = None,
) -> list[dict[str, Any]]:
    open_access_rows = [
        row for row in inventory_rows
        if row.get('model_access') == 'open' and row.get('selection_status') != 'selected'
    ]
    if excluded_reasons:
        open_access_rows = [
            row for row in open_access_rows
            if not (set(row.get('failure_reasons', []) or []) & excluded_reasons)
        ]
    return make_reason_combo_breakout_table(open_access_rows, 'model')


def make_reason_combo_table(inventory_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    examples: dict[str, dict[str, Any]] = {}
    for row in inventory_rows:
        reasons = row.get('failure_reasons', []) or []
        if row.get('selection_status') != 'selected' and not reasons:
            reasons = [UNCLASSIFIED_EXCLUSION]
        combo = 'selected' if row.get('selection_status') == 'selected' else '|'.join(sorted(reasons))
        counts[combo] = counts.get(combo, 0) + 1
        examples.setdefault(combo, {
            'example_run_spec_name': row.get('run_spec_name'),
            'example_model': row.get('model'),
            'example_benchmark': row.get('benchmark'),
        })
    rows = []
    for combo, count in counts.items():
        rows.append({
            'reason_combo': combo,
            'run_count': count,
            **examples[combo],
        })
    rows.sort(key=lambda row: (-row['run_count'], row['reason_combo']))
    return rows


def make_candidate_pool_table(inventory_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pools = sorted({row.get('candidate_pool') or 'unknown' for row in inventory_rows})
    total = len(inventory_rows)
    rows = []
    for pool in pools:
        pool_rows = [row for row in inventory_rows if (row.get('candidate_pool') or 'unknown') == pool]
        selected_runs = sum(1 for row in pool_rows if row.get('selection_status') == 'selected')
        excluded_runs = len(pool_rows) - selected_runs
        rows.append({
            'candidate_pool': pool,
            'run_count': len(pool_rows),
            'selected_runs': selected_runs,
            'excluded_runs': excluded_runs,
            'fraction_of_all_runs': None if total == 0 else len(pool_rows) / total,
        })
    rows.sort(key=lambda row: (-row['run_count'], row['candidate_pool']))
    return rows


def make_selection_path_table(inventory_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    table: dict[tuple[str, str], int] = {}
    total = len(inventory_rows)
    for row in inventory_rows:
        key = ((row.get('candidate_pool') or 'unknown'), row.get('selection_status') or 'unknown')
        table[key] = table.get(key, 0) + 1
    rows = []
    for (pool, status), count in sorted(table.items(), key=lambda item: (-item[1], item[0][0], item[0][1])):
        rows.append({
            'candidate_pool': pool,
            'selection_status': status,
            'run_count': count,
            'fraction_of_all_runs': None if total == 0 else count / total,
        })
    return rows


def make_pair_table(
    inventory_rows: list[dict[str, Any]],
    left_key: str,
    right_key: str,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    counts: dict[tuple[str, str], dict[str, int]] = {}
    for row in inventory_rows:
        left = row.get(left_key) or 'unknown'
        right = row.get(right_key) or 'unknown'
        bucket = counts.setdefault((left, right), {
            'total_runs': 0,
            'selected_runs': 0,
            'considered_runs': 0,
            'eligible_runs': 0,
        })
        bucket['total_runs'] += 1
        bucket['selected_runs'] += int(row.get('selection_status') == 'selected')
        bucket['considered_runs'] += int(bool(row.get('considered_for_selection')))
        bucket['eligible_runs'] += int(bool(row.get('eligible_candidate')))
    rows = []
    for (left, right), vals in counts.items():
        rows.append({
            left_key: left,
            right_key: right,
            **vals,
            'excluded_runs': vals['total_runs'] - vals['selected_runs'],
            'fraction_selected_of_all': None if vals['total_runs'] == 0 else vals['selected_runs'] / vals['total_runs'],
            'fraction_selected_of_considered': None if vals['considered_runs'] == 0 else vals['selected_runs'] / vals['considered_runs'],
        })
    rows.sort(key=lambda row: (-row['total_runs'], -row['selected_runs'], str(row[left_key]), str(row[right_key])))
    return rows[:limit]


def make_reason_examples_table(inventory_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    examples: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    for row in inventory_rows:
        reasons = row.get('failure_reasons', []) or []
        if row.get('selection_status') != 'selected' and not reasons:
            reasons = [UNCLASSIFIED_EXCLUSION]
        for reason in reasons:
            counts[reason] = counts.get(reason, 0) + 1
            examples.setdefault(reason, {
                'failure_reason': reason,
                'run_spec_name': row.get('run_spec_name'),
                'model': row.get('model'),
                'benchmark': row.get('benchmark'),
                'dataset': row.get('dataset'),
                'scenario': row.get('scenario'),
                'selection_explanation': row.get('selection_explanation'),
            })
    rows = []
    for reason, payload in examples.items():
        rows.append({
            'failure_reason': reason,
            'run_count': counts.get(reason, 0),
            **payload,
        })
    rows.sort(key=lambda row: (-row['run_count'], row['failure_reason']))
    return rows


def classify_hierarchical_filter_stages(row: dict[str, Any]) -> dict[str, str]:
    if row.get('is_structurally_incomplete'):
        return {
            'structural_stage': 'excluded: structurally incomplete',
            'metadata_stage': 'stopped before metadata check',
            'access_stage': 'stopped before access check',
            'tag_stage': 'stopped before tag check',
            'deployment_stage': 'stopped before deployment check',
            'size_stage': 'stopped before size check',
            'judge_stage': 'stopped before judge check',
            'outcome_stage': 'excluded before candidate pool',
        }

    reasons = set(row.get('failure_reasons', []) or [])
    # R-4c: the hierarchical funnel previously had no metadata gate, so
    # missing-model-metadata rows fell through every explicit gate and landed
    # 'unclassified'. Attribute them to a dedicated metadata gate so this funnel
    # family lines up with the Stage-A funnel (which already has one).
    metadata_ok = 'missing-model-metadata' not in reasons
    access_ok = 'not-open-access' not in reasons
    tag_ok = ('excluded-tags' not in reasons) and ('not-text-like' not in reasons)
    deployment_ok = 'no-local-helm-deployment' not in reasons
    size_ok = 'too-large' not in reasons
    judge_ok = CLOSED_JUDGE_REQUIRED_REASON not in reasons
    selected = row.get('selection_status') == 'selected'

    if not metadata_ok:
        return {
            'structural_stage': 'passed structural completeness',
            'metadata_stage': 'excluded: missing model metadata',
            'access_stage': 'stopped after metadata exclusion',
            'tag_stage': 'stopped after metadata exclusion',
            'deployment_stage': 'stopped after metadata exclusion',
            'size_stage': 'stopped after metadata exclusion',
            'judge_stage': 'stopped after metadata exclusion',
            'outcome_stage': 'excluded at metadata gate',
        }
    if not access_ok:
        return {
            'structural_stage': 'passed structural completeness',
            'metadata_stage': 'kept: model metadata resolved',
            'access_stage': 'excluded: not open weight',
            'tag_stage': 'stopped after access exclusion',
            'deployment_stage': 'stopped after access exclusion',
            'size_stage': 'stopped after access exclusion',
            'judge_stage': 'stopped after access exclusion',
            'outcome_stage': 'excluded at open-weight gate',
        }
    if not tag_ok:
        return {
            'structural_stage': 'passed structural completeness',
            'metadata_stage': 'kept: model metadata resolved',
            'access_stage': 'kept: open weight',
            'tag_stage': 'excluded: unsuitable text/modality tags',
            'deployment_stage': 'stopped after tag exclusion',
            'size_stage': 'stopped after tag exclusion',
            'judge_stage': 'stopped after tag exclusion',
            'outcome_stage': 'excluded at tag gate',
        }
    if not deployment_ok:
        return {
            'structural_stage': 'passed structural completeness',
            'metadata_stage': 'kept: model metadata resolved',
            'access_stage': 'kept: open weight',
            'tag_stage': 'kept: suitable text tags',
            'deployment_stage': 'excluded: no runnable local deployment',
            'size_stage': 'stopped after deployment exclusion',
            'judge_stage': 'stopped after deployment exclusion',
            'outcome_stage': 'excluded at deployment gate',
        }
    if not size_ok:
        size_text = row.get('failure_reason_details', {}).get('too-large', '')
        short_label = 'excluded: exceeds size budget'
        if size_text:
            short_label = (
                f"excluded: {format_size_label(row.get('model_num_parameters'), row.get('size_threshold_params'))}"
            )
        return {
            'structural_stage': 'passed structural completeness',
            'metadata_stage': 'kept: model metadata resolved',
            'access_stage': 'kept: open weight',
            'tag_stage': 'kept: suitable text tags',
            'deployment_stage': 'kept: runnable local deployment',
            'size_stage': short_label,
            'judge_stage': 'stopped after size exclusion',
            'outcome_stage': 'excluded at size gate',
        }
    if not judge_ok:
        return {
            'structural_stage': 'passed structural completeness',
            'metadata_stage': 'kept: model metadata resolved',
            'access_stage': 'kept: open weight',
            'tag_stage': 'kept: suitable text tags',
            'deployment_stage': 'kept: runnable local deployment',
            'size_stage': 'kept: within size budget',
            'judge_stage': 'excluded: requires closed-source judge',
            'outcome_stage': 'excluded at judge gate',
        }
    if not selected:
        return {
            'structural_stage': 'passed structural completeness',
            'metadata_stage': 'kept: model metadata resolved',
            'access_stage': 'kept: open weight',
            'tag_stage': 'kept: suitable text tags',
            'deployment_stage': 'kept: runnable local deployment',
            'size_stage': 'kept: within size budget',
            'judge_stage': 'kept: no closed-source judge dependency',
            'outcome_stage': 'excluded after explicit gates (unclassified)',
        }
    return {
        'structural_stage': 'passed structural completeness',
        'metadata_stage': 'kept: model metadata resolved',
        'access_stage': 'kept: open weight',
        'tag_stage': 'kept: suitable text tags',
        'deployment_stage': 'kept: runnable local deployment',
        'size_stage': 'kept: within size budget',
        'judge_stage': 'kept: no closed-source judge dependency',
        'outcome_stage': 'selected for reproduction',
    }


def build_hierarchical_sankey_rows(inventory_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [classify_hierarchical_filter_stages(row) for row in inventory_rows]


def format_size_label(num_params: Any, threshold: Any) -> str:
    def _fmt(val: Any) -> str:
        if val is None:
            return 'unknown'
        val = float(val)
        if val >= 1e9:
            return f'{val / 1e9:.1f}B'
        if val >= 1e6:
            return f'{val / 1e6:.1f}M'
        return str(int(val))
    return f"too large ({_fmt(num_params)} > {_fmt(threshold)})"


def build_hierarchical_sankey_key(summary: dict[str, Any]) -> dict[str, list[str]]:
    return {
        'Structural Gate': [
            f"excluded: structurally incomplete ({summary['structurally_incomplete_runs']} runs)",
            'passed structural completeness: run had enough HELM files to enter model filtering',
        ],
        'Metadata Gate': [
            'excluded: missing model metadata: HELM could not resolve model metadata for this model name',
            'kept: model metadata resolved: model metadata resolved via the deployment registry',
            'stopped before metadata check / stopped after metadata exclusion: eliminated at an earlier gate',
        ],
        'Open-Weight Gate': [
            'excluded: not open weight: HELM access is not "open"',
            'kept: open weight: passes the open-access requirement',
            'stopped before access check: eliminated at an earlier gate',
        ],
        'Tag Gate': [
            'excluded: unsuitable text/modality tags: model is not in the text-only reproducibility target',
            'kept: suitable text tags: passes the text-like and excluded-tag checks',
            'stopped after access exclusion: excluded earlier, so no tag decision was needed',
        ],
        'Deployment Gate': [
            'excluded: no runnable local deployment: no HuggingFace/local deployment path available',
            'kept: runnable local deployment: model has a local deployment path or explicit override',
            'stopped after tag exclusion: excluded earlier, so no deployment decision was needed',
        ],
        'Size Gate': [
            'excluded: ... exceeds the local reproduction budget: parameter count is above the configured threshold',
            'kept: within size budget: passes the size budget gate',
            'stopped after deployment exclusion: excluded earlier, so no size decision was needed',
        ],
        'Judge Gate': [
            'excluded: requires closed-source judge: benchmark depends on a proprietary / credentialed judge or annotator',
            'kept: no closed-source judge dependency: benchmark stays within the current open-model reproduction scope',
            'stopped before judge check / stopped after access exclusion / stopped after tag exclusion / stopped after deployment exclusion / stopped after size exclusion',
        ],
        'Outcome': [
            'selected for reproduction: run survives every gate and is included in the output run list',
            'excluded before candidate pool / at open-weight gate / at tag gate / at deployment gate / at size gate / at judge gate / after explicit gates (unclassified)',
        ],
    }


def make_decision_examples(inventory_rows: list[dict[str, Any]], limit: int = 30) -> dict[str, list[dict[str, Any]]]:
    selected = []
    excluded = []
    for row in inventory_rows:
        payload = {
            'run_spec_name': row.get('run_spec_name'),
            'model': row.get('model'),
            'benchmark': row.get('benchmark'),
            'dataset': row.get('dataset'),
            'scenario': row.get('scenario'),
            'selection_status': row.get('selection_status'),
            'failure_reasons': row.get('failure_reasons'),
            'selection_explanation': row.get('selection_explanation'),
        }
        if row.get('selection_status') == 'selected':
            if len(selected) < limit:
                selected.append(payload)
        else:
            if len(excluded) < limit:
                excluded.append(payload)
    return {'selected_examples': selected, 'excluded_examples': excluded}


def build_filter_reason_sankey_rows(inventory_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in inventory_rows:
        reasons = row.get('failure_reasons', []) or []
        if row.get('selection_status') == 'selected':
            rows.append({'filter_reason': 'selected', 'outcome': 'selected'})
            continue
        if row.get('is_structurally_incomplete'):
            rows.append({'filter_reason': 'structurally-incomplete', 'outcome': 'excluded'})
            continue
        if not reasons:
            reasons = [UNCLASSIFIED_EXCLUSION]
        # P1-12: one row per EXCLUDED RUN (its primary/first reason), not one
        # per (run, reason). Emitting a row per reason inflated the excluded
        # flow past the run count, so the sankey root label rendered
        # "n=X n=Y" with Y>X (flow conservation broken). A run has exactly one
        # outcome; attribute it to its primary reason.
        rows.append({'filter_reason': reasons[0], 'outcome': 'excluded'})
    return rows


def _make_selected_excluded_rows(
    inventory_rows: list[dict[str, Any]],
    facet_key: str,
    *,
    limit: int | None = None,
) -> tuple[list[dict[str, Any]], int, int]:
    """
    Returns (plot_rows, n_facets_shown, n_facets_total).

    If `limit` is provided, slices at the facet level (top `limit` facets by
    total then selected count) before expanding to per-status rows, so the slice
    boundary never cuts a facet in half and selected facets are never crowded
    out by excluded-only facets. If `limit` is `None`, all facets are included.
    """
    counts: dict[str, dict[str, int]] = {}
    for row in inventory_rows:
        facet = str(row.get(facet_key) or 'unknown')
        status = 'selected' if row.get('selection_status') == 'selected' else 'excluded'
        bucket = counts.setdefault(facet, {'selected': 0, 'excluded': 0})
        bucket[status] += 1
    sorted_facets = sorted(
        counts.items(),
        key=lambda item: (-sum(item[1].values()), -item[1]['selected'], str(item[0])),
    )
    n_facets_total = len(sorted_facets)
    top_facets = sorted_facets if limit is None else sorted_facets[:limit]
    n_facets_shown = len(top_facets)
    rows = []
    for facet, bucket in top_facets:
        if bucket['selected'] > 0:
            rows.append({facet_key: facet, 'selection_status': 'selected', 'count': bucket['selected']})
        if bucket['excluded'] > 0:
            rows.append({facet_key: facet, 'selection_status': 'excluded', 'count': bucket['excluded']})
    return rows, n_facets_shown, n_facets_total

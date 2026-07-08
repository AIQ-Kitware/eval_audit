"""Text builders for the Stage 1 filter report (cardinality, recovery,
report, and analysis narratives).

Split out of ``eval_audit.reports.filter_analysis`` on 2026-06-11
(Phase 2 of docs/historical/planning/repo-refactor-plan.md). Pure relocation:
function bodies are unchanged.
"""
from __future__ import annotations
from typing import Any


def build_filter_cardinality_text(inventory_rows: list[dict[str, Any]]) -> str:
    def _card(rows: list[dict[str, Any]]) -> dict[str, int]:
        return {
            'n': len(rows),
            'models': len({r.get('model') for r in rows if r.get('model')}),
            'benchmarks': len({r.get('benchmark') for r in rows if r.get('benchmark')}),
            'scenarios': len({r.get('scenario') for r in rows if r.get('scenario')}),
            'model_bench_pairs': len({(r.get('model'), r.get('benchmark')) for r in rows if r.get('model') and r.get('benchmark')}),
        }

    all_rows = inventory_rows
    considered_rows = [r for r in inventory_rows if r.get('considered_for_selection')]
    eligible_rows = [r for r in inventory_rows if r.get('eligible_candidate')]
    selected_rows = [r for r in inventory_rows if r.get('selection_status') == 'selected']

    header = f"{'Stage':<22} {'runs':>6}  {'models':>6}  {'benchmarks':>10}  {'scenarios':>9}  {'mod×bench':>9}"
    sep = '-' * len(header)

    def row_line(label: str, c: dict[str, int]) -> str:
        return (
            f"{label:<22} {c['n']:>6}  {c['models']:>6}  {c['benchmarks']:>10}"
            f"  {c['scenarios']:>9}  {c['model_bench_pairs']:>9}"
        )

    lines = [
        'Filter Stage Cardinality Summary',
        '================================',
        '',
        'Run-spec counts at each stage of the Stage 1 filter funnel.',
        '',
        header,
        sep,
        row_line('all_discovered', _card(all_rows)),
        row_line('considered', _card(considered_rows)),
        row_line('eligible', _card(eligible_rows)),
        row_line('selected', _card(selected_rows)),
        '',
        'Columns: runs = total run entries; models/benchmarks/scenarios = unique values;',
        '         mod×bench = unique (model, benchmark) pairs.',
        'Stages: all_discovered = every run seen; considered = passed initial checks;',
        '        eligible = passed all criteria; selected = chosen for reproduction.',
    ]
    return '\n'.join(lines) + '\n'


def build_local_serving_recovery_text(inventory_rows: list[dict[str, Any]]) -> str:
    """
    Partition models excluded by no-local-helm-deployment into:
      on-story  — public HELM model with a checked-in local serving recipe
      off-story — local extension not in the public HELM storyline
      no-plan   — not in the model registry; no known local serving path
    """
    NO_LOCAL = 'no-local-helm-deployment'
    deployment_excluded = [
        r for r in inventory_rows
        if NO_LOCAL in (r.get('failure_reasons') or [])
    ]
    seen: set[str] = set()
    model_rows: list[dict[str, Any]] = []
    for r in deployment_excluded:
        m = str(r.get('model') or 'unknown')
        if m not in seen:
            seen.add(m)
            model_rows.append(r)
    model_rows.sort(key=lambda r: str(r.get('model') or ''))

    on_story = [r for r in model_rows if r.get('replaces_helm_deployment') is not None]
    off_story = [r for r in model_rows if r.get('replaces_helm_deployment') is None and r.get('expected_local_served')]
    no_plan = [r for r in model_rows if not r.get('expected_local_served')]

    def _table(rows: list[dict[str, Any]]) -> list[str]:
        if not rows:
            return ['  (none)']
        out = []
        for r in rows:
            m = str(r.get('model') or 'unknown')
            src = str(r.get('local_registry_source') or '')
            repl = r.get('replaces_helm_deployment')
            suffix = f'  replaces={repl}' if repl else ''
            src_str = f'  source={src}' if src else ''
            out.append(f'  {m:<48}{src_str}{suffix}')
        return out

    lines: list[str] = [
        'Local Serving Recovery Summary',
        '==============================',
        '',
        'Models excluded by no-local-helm-deployment, by local serving plan.',
        '',
        f'  on-story  (public HELM model, local recipe exists): {len(on_story)}',
        f'  off-story (local extension, not in public HELM):    {len(off_story)}',
        f'  no-plan   (not in eval_audit model registry):       {len(no_plan)}',
        '',
    ]
    if on_story:
        lines += ['On-story models (in main reproducibility storyline):']
        lines += _table(on_story)
        lines += ['']
    if off_story:
        lines += ['Off-story models (local extensions, not in public HELM storyline):']
        lines += _table(off_story)
        lines += ['']
    if no_plan:
        lines += ['No local serving plan (not in eval_audit/model_registry.py):']
        lines += _table(no_plan)
        lines += ['']
    lines += [
        'Notes:',
        '  no-local-helm-deployment = Stage 1 automatic filter found no default local',
        '  HELM deployment path for this model. On-story models have a recipe in',
        '  eval_audit/model_registry.py and are run via a separate serving bundle.',
        '  TODO: Add runtime verification that infer_stack profiles can serve these.',
    ]
    return '\n'.join(lines) + '\n'


def build_filter_report_text(
    *,
    summary: dict[str, Any],
    by_model_rows: list[dict[str, Any]],
    by_dataset_rows: list[dict[str, Any]],
    by_scenario_rows: list[dict[str, Any]],
    reason_by_model_rows: list[dict[str, Any]],
    open_access_exclusion_reason_rows: list[dict[str, Any]],
    open_access_exclusion_reason_by_model_rows: list[dict[str, Any]],
    open_access_text_exclusion_reason_by_model_rows: list[dict[str, Any]],
    open_access_text_size_exclusion_reason_by_model_rows: list[dict[str, Any]],
    selected_rows: list[dict[str, Any]],
) -> str:
    lines = [
        'Model Selection Filter Report',
        '',
        'Headline:',
        f"  total_discovered_runs={summary['total_discovered_runs']}",
        f"  considered_runs={summary['considered_runs']}",
        f"  eligible_runs={summary['eligible_runs']}",
        f"  selected_runs={summary['selected_runs']}",
        f"  excluded_runs={summary['excluded_runs']}",
        f"  structurally_incomplete_runs={summary['structurally_incomplete_runs']}",
        f"  selected_models={summary['selected_models']}",
        '',
        'Exclusion reasons:',
    ]
    for reason, count in summary['exclusion_reason_counts'].items():
        lines.append(f'  {reason}: {count}')

    def add_table_preview(title: str, rows: list[dict[str, Any]], key: str) -> None:
        lines.append('')
        lines.append(title)
        for row in rows[:12]:
            lines.append(
                f"  {row[key]}: total={row['total_runs']} selected={row['selected_runs']} excluded={row['excluded_runs']}"
            )

    add_table_preview('Selected / excluded by model (top rows):', by_model_rows, 'model')
    add_table_preview('Selected / excluded by dataset slice (top rows):', by_dataset_rows, 'dataset')
    add_table_preview('Selected / excluded by scenario (top rows):', by_scenario_rows, 'scenario')

    lines.append('')
    lines.append('Top exclusion-reason / model pairs:')
    for row in reason_by_model_rows[:15]:
        lines.append(f"  {row['model']} :: {row['failure_reason']} -> {row['run_count']}")

    lines.append('')
    lines.append('Open-access exclusion reasons:')
    for row in open_access_exclusion_reason_rows[:15]:
        lines.append(f"  {row['failure_reason']}: {row['run_count']}")

    lines.append('')
    lines.append('Open-access exclusion reason combinations by model:')
    for row in open_access_exclusion_reason_by_model_rows[:15]:
        lines.append(f"  {row['model']} :: {row['reason_combo']} -> {row['run_count']}")

    lines.append('')
    lines.append('Open-access, text-compatible exclusion reason combinations by model:')
    for row in open_access_text_exclusion_reason_by_model_rows[:15]:
        lines.append(f"  {row['model']} :: {row['reason_combo']} -> {row['run_count']}")

    lines.append('')
    lines.append('Open-access, text-compatible, size-ok exclusion reason combinations by model:')
    for row in open_access_text_size_exclusion_reason_by_model_rows[:15]:
        lines.append(f"  {row['model']} :: {row['reason_combo']} -> {row['run_count']}")

    lines.append('')
    lines.append('Selected run specs (first 25):')
    for row in selected_rows[:25]:
        lines.append(f"  {row['run_spec_name']}")
    lines.append('')
    lines.append('See the adjacent TSV/JSON artifacts for the full inventory and regroupings.')
    return '\n'.join(lines) + '\n'


def build_analysis_text(
    summary: dict[str, Any],
    by_model_rows: list[dict[str, Any]],
    by_dataset_rows: list[dict[str, Any]],
    by_scenario_rows: list[dict[str, Any]],
    candidate_pool_rows: list[dict[str, Any]],
    selection_path_rows: list[dict[str, Any]],
    reason_combo_rows: list[dict[str, Any]],
    pair_model_scenario_rows: list[dict[str, Any]],
    pair_model_benchmark_rows: list[dict[str, Any]],
    reason_example_rows: list[dict[str, Any]],
    examples: dict[str, list[dict[str, Any]]],
) -> str:
    lines = [
        'Filter Candidate Analysis',
        '',
        'Coverage:',
        f"  discovered_runs={summary['total_discovered_runs']}",
        f"  considered_runs={summary['considered_runs']}",
        f"  eligible_runs={summary['eligible_runs']}",
        f"  selected_runs={summary['selected_runs']}",
        f"  excluded_runs={summary['excluded_runs']}",
        f"  structurally_incomplete_runs={summary['structurally_incomplete_runs']}",
        '',
        'Fractions:',
        f"  selected_of_all={summary['fraction_selected_of_all']}",
        f"  selected_of_considered={summary['fraction_selected_of_considered']}",
        f"  selected_of_eligible={summary['fraction_selected_of_eligible']}",
        f"  considered_of_all={summary['fraction_considered_of_all']}",
        f"  eligible_of_all={summary['fraction_eligible_of_all']}",
        '',
        'Denominators:',
        '  discovered_runs: every run directory seen during Stage 1, including structurally incomplete directories when discoverable.',
        '  considered_runs: structurally complete runs that reached the model eligibility decision.',
        '  eligible_runs: considered runs whose model passed all eligibility filters.',
        '  selected_runs: eligible runs retained for reproduction output.',
        '',
        'Candidate pool funnel:',
    ]
    for row in candidate_pool_rows:
        lines.append(
            f"  {row['candidate_pool']}: runs={row['run_count']} selected={row['selected_runs']} excluded={row['excluded_runs']} fraction_of_all={row['fraction_of_all_runs']}"
        )

    lines.extend([
        '',
        'Selection paths:',
    ])
    for row in selection_path_rows:
        lines.append(
            f"  {row['candidate_pool']} -> {row['selection_status']}: runs={row['run_count']} fraction_of_all={row['fraction_of_all_runs']}"
        )

    lines.extend([
        '',
        'Hierarchical gate order:',
        '  all discovered runs -> structural completeness -> open weight -> suitable text tags -> runnable local deployment -> size budget -> no closed-source judge dependency -> selected subset',
        '  This gate order makes the full-corpus denominator visible while also showing the fairer open-weight and runnable subsets at intermediate steps.',
        '',
        'Suggested plots:',
        '  - selected/excluded by model',
        '  - selected/excluded by benchmark',
        '  - selected/excluded by dataset',
        '  - exclusion reasons by model',
        '  - open-access exclusion reasons by model',
        '  - open-access, text-compatible exclusion reasons by model',
        '  - open-access, text-compatible, size-OK exclusion reasons by model',
        '  - top reason combinations',
        '  - selected/excluded by candidate pool',
    ])

    lines.extend([
        '',
        'Why runs were not chosen:',
    ])
    for reason, count in summary['exclusion_reason_counts'].items():
        lines.append(f'  {reason}: {count}')

    lines.extend([
        '',
        'Reason combinations:',
    ])
    for row in reason_combo_rows[:20]:
        lines.append(
            f"  {row['reason_combo']}: runs={row['run_count']} example={row['example_run_spec_name']}"
        )

    def add_section(title: str, rows: list[dict[str, Any]], key: str) -> None:
        lines.append('')
        lines.append(title)
        for row in rows[:15]:
            lines.append(
                f"  {row[key]}: total={row['total_runs']} considered={row['considered_runs']} eligible={row['eligible_runs']} selected={row['selected_runs']} excluded={row['excluded_runs']} selected_of_all={row['fraction_selected_of_all']} selected_of_considered={row['fraction_selected_of_considered']} top_exclusion_reason={row['top_exclusion_reason']}"
            )

    add_section('Coverage by model:', by_model_rows, 'model')
    add_section('Coverage by dataset slice:', by_dataset_rows, 'dataset')
    add_section('Coverage by scenario:', by_scenario_rows, 'scenario')

    lines.append('')
    lines.append('Top model x scenario cohorts:')
    for row in pair_model_scenario_rows[:20]:
        lines.append(
            f"  {row['model']} x {row['scenario']}: total={row['total_runs']} selected={row['selected_runs']} considered={row['considered_runs']} eligible={row['eligible_runs']} selected_of_all={row['fraction_selected_of_all']}"
        )

    lines.append('')
    lines.append('Top model x benchmark cohorts:')
    for row in pair_model_benchmark_rows[:20]:
        lines.append(
            f"  {row['model']} x {row['benchmark']}: total={row['total_runs']} selected={row['selected_runs']} considered={row['considered_runs']} eligible={row['eligible_runs']} selected_of_all={row['fraction_selected_of_all']}"
        )

    lines.append('')
    lines.append('Representative examples by exclusion reason:')
    for row in reason_example_rows[:20]:
        lines.append(
            f"  {row['failure_reason']}: {row['run_spec_name']} :: {row['selection_explanation']}"
        )

    lines.append('')
    lines.append('Selected examples:')
    for row in examples['selected_examples'][:20]:
        lines.append(f"  {row['run_spec_name']} :: {row['selection_explanation']}")

    lines.append('')
    lines.append('Excluded examples:')
    for row in examples['excluded_examples'][:20]:
        lines.append(f"  {row['run_spec_name']} :: {row['selection_explanation']}")

    lines.append('')
    lines.append('Use the adjacent TSV/JSON artifacts to inspect the full candidate set and facet-specific fractions.')
    return '\n'.join(lines) + '\n'

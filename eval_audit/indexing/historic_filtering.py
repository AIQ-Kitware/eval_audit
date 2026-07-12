"""Stage 1 filtering/eligibility: run tables, failure reasons, filter inventory.

Split out of ``eval_audit.cli.index_historic_helm_runs`` on 2026-06-11
(Phase 2 of docs/historical/planning/repo-refactor-plan.md). Pure relocation:
function bodies are unchanged.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any, Iterable
import ubelt as ub
from loguru import logger
from eval_audit.run_entries import (
    parse_run_entry_description,
    parse_run_name_to_kv,
    reconstruct_run_entry_from_run_spec,
)
from eval_audit.model_registry import local_model_registry_by_name


MISSING_MODEL_METADATA_REASON = 'missing-model-metadata'
CLOSED_JUDGE_REQUIRED_REASON = 'requires-closed-judge'
GATED_DATASET_REASON = 'requires-gated-dataset'

CLOSED_JUDGE_BENCHMARKS = {
    'anthropic_red_team',
    'harm_bench',
    'omni_math',
    'simple_safety_tests',
    'wildbench',
    'xstest',
}

GATED_DATASET_BENCHMARKS = {
    'gpqa',
}


def gather_runs(
    roots: Iterable[Path],
    suite_pattern: str = "*",
    run_pattern: str = "*:*",
    require_per_instance_stats: bool = False,
) -> tuple[list[Any], list[dict[str, Any]]]:
    from magnet.backends.helm.helm_outputs import HelmOutputs, HelmRun
    from magnet.backends.helm.cli.materialize_helm_run import (
        discover_benchmark_output_dirs,
        is_complete_run_dir,
    )

    # Discover all benchmark_output dirs under provided roots
    logger.info('Discover benchmarks')
    # P1-17: sort the discovered dirs so the run order (and thus which suite's
    # row `dedupe_rows` first-wins keeps) is machine-independent, not dependent
    # on filesystem enumeration order.
    bo_dirs = sorted(
        ub.ProgIter(discover_benchmark_output_dirs(roots), desc='discovering benchmarks', verbose=3, homogeneous=False),
        key=lambda p: str(p),
    )
    logger.info('Finished Discover benchmarks')
    if not bo_dirs:
        logger.warning("No benchmark_output dirs found under roots={}", roots)

    runs: list[HelmRun] = []
    incomplete_rows: list[dict[str, Any]] = []
    for bo in ub.ProgIter(bo_dirs, desc='Check dirs'):
        try:
            outputs = HelmOutputs.coerce(bo)
        except Exception:
            continue

        for suite in outputs.suites(pattern=suite_pattern):
            for run in suite.runs(pattern=run_pattern):
                run_dir = Path(run.path)

                run = HelmRun(run_dir)

                # TODO: if not run.exists():
                #     ...
                # Only include if it looks "complete enough"
                if not is_complete_run_dir(run_dir, require_per_instance_stats=require_per_instance_stats):
                    incomplete_rows.append(build_incomplete_inventory_row(run_dir))
                    continue

                runs.append(run)

    # Stable order
    logger.info('Found {} run directories', len(runs))
    return runs, incomplete_rows


def build_run_table(
    runs: list[Any],
    *,
    include_max_eval_instances: bool = False,
) -> list[dict]:
    from magnet.backends.helm.cli.materialize_helm_run import infer_num_instances

    rows = []
    mismatches = []
    for run in ub.ProgIter(runs, desc='Extract run spec info'):
        max_eval_instances = None
        if include_max_eval_instances:
            max_eval_instances = infer_num_instances(run.path)

        run_spec = run.json.run_spec()
        scenario_class = run_spec['scenario_spec']['class_name']
        model = run_spec['adapter_spec']['model']
        display_name = run_spec['name']

        if run.path.name != display_name.replace('/', '_'):
            mismatches.append({
                'run.path.parent': run.path.parent,
                'run.path.name': run.path.name,
                'run_spec_name': display_name,
            })

        # HELM's `run_spec.json.name` is a display string and is NOT a
        # valid `helm-run --run-entries` argument across the board (mixed
        # separators, display-vs-kwarg renames, leaked metadata fields).
        # Reconstruct from the structural fields so the audit list we
        # emit round-trips through helm-run cleanly. Falls back to the
        # display name if the registry lookup or signature introspection
        # fails — the legacy "fix the model slash" hack is preserved as
        # the fallback path.
        run_spec_name, dropped_kwargs = reconstruct_run_entry_from_run_spec(run_spec)
        if dropped_kwargs:
            logger.debug(
                'Dropped non-arg kwargs while reconstructing run_entry for {}: {}',
                run.path.name, dropped_kwargs,
            )
        if run_spec_name == display_name:
            # Reconstruction declined to rewrite — apply the legacy
            # underscore-to-slash fixup so the model field is still
            # canonical when fed back into helm-run.
            normalized_model = model.replace('/', '_')
            run_spec_name = display_name.replace(normalized_model, model)

        rows.append({
            # "benchmark_output_dir": str(Path(outputs.root_dir)),
            # "suite": suite.name,
            # # Use run directory name as the canonical "run_entry" to reproduce.
            # # This is faithful even if HELM normalized defaults into the name.

            # Use run directory name as the canonical "run_entry" to reproduce.
            # This is faithful even if HELM normalized defaults into the name.
            "run_spec_name": run_spec_name,
            "run_dir": str(run.path),
            "max_eval_instances": max_eval_instances,
            'model': model,
            'scenario_class': scenario_class,
        })
    logger.warning(f'mismatches = {ub.urepr(mismatches, nl=2, align=":")}')
    rows.sort(key=lambda r: (r["run_dir"]))
    return rows


def dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    out = []
    for r in rows:
        key = (r["run_spec_name"], r.get("max_eval_instances", None))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def format_params_human(num_params: float | int | None) -> str:
    if num_params is None:
        return 'unknown'
    value = float(num_params)
    if value >= 1e9:
        return f'{value / 1e9:.1f}B'
    if value >= 1e6:
        return f'{value / 1e6:.1f}M'
    return str(int(value))


def build_failure_reason_details(
    *,
    tags: set[str],
    is_text_like: bool,
    has_excluded_tags: bool,
    size_ok: bool,
    access_ok: bool,
    has_local_hf_path: bool,
    num_parameters: float | int | None,
    access: str | None,
    has_hf_client: bool,
    model_name: str,
    known_hf_overrides: set[str],
    max_params: float,
    exclude_tags: set[str],
) -> dict[str, str]:
    details: dict[str, str] = {}
    if not is_text_like:
        details['not-text-like'] = (
            'Model does not advertise any of the required text-compatible HELM tags.'
        )
    if has_excluded_tags:
        details['excluded-tags'] = (
            'Model carries excluded modality tags: ' + ', '.join(sorted(tags & exclude_tags))
        )
    if not size_ok:
        details['too-large'] = (
            f"Model size {format_params_human(num_parameters)} exceeds the local reproduction budget "
            f"of {format_params_human(max_params)} parameters."
        )
    if not access_ok:
        details['not-open-access'] = (
            f'Model access is {access!r}; the filter requires HELM access="open".'
        )
    if not has_local_hf_path:
        details['no-local-helm-deployment'] = (
            f'Model has_hf_client={has_hf_client} and override={model_name in known_hf_overrides}; '
            'no default local HELM deployment path is known to the Stage 1 automatic filter.'
        )
    return details


# --- Model-eligibility policy (R-7) ----------------------------------------
# Extracted from cli/index_historic_helm_runs.py, where the same predicate was
# computed twice (once for selection, once for the filter report) — a silent
# divergence hazard for the research-critical selection. Both call sites now
# route through classify_model_eligibility so their outcomes cannot drift.

# HELM tags that mark a model as text-compatible for local reproduction.
SOFT_TEXT_TAGS = {
    'TEXT_MODEL_TAG',
    'FULL_FUNCTIONALITY_TEXT_MODEL_TAG',
    'INSTRUCTION_FOLLOWING_MODEL_TAG',
}

# Modalities the local text-only recipe cannot serve.
EXCLUDE_TAGS = {
    'VISION_LANGUAGE_MODEL_TAG',
    'AUDIO_LANGUAGE_MODEL_TAG',
    'IMAGE_MODEL_TAG',
    'TEXT_TO_IMAGE_MODEL_TAG',
    'CODE_MODEL_TAG',
}

# Conservative local-reproduction size budget; models with unknown size pass.
MAX_PARAMS = 10e9

# Manual escape hatch for models that are probably HF-runnable even if HELM
# currently resolves them to a non-HF deployment.
KNOWN_HF_OVERRIDES = {
    'qwen/qwen2.5-7b-instruct-turbo',
    'qwen/qwen2-72b-instruct',
    'qwen/qwen2.5-72b-instruct-turbo',
}


def classify_model_eligibility(
    model_row: dict[str, Any],
) -> tuple[bool, list[str], dict[str, str]]:
    """Single source of truth for Stage-1 model eligibility.

    Given one model-metadata row (``tags``, ``num_parameters``, ``access``,
    ``has_hf_client``, ``name``), return
    ``(eligible, failure_reasons, failure_reason_details)``. The selection loop
    consumes ``eligible``; the filter-report loop consumes all three. Computing
    them here once removes the previous duplicate-predicate divergence hazard.
    """
    tags = set(model_row.get('tags', []))
    num_parameters = model_row.get('num_parameters')
    access = model_row.get('access')
    has_hf_client = model_row.get('has_hf_client', False)
    model_name = model_row['name']

    is_text_like = bool(tags & SOFT_TEXT_TAGS)
    has_excluded_tags = bool(tags & EXCLUDE_TAGS)
    size_ok = (num_parameters is None or num_parameters <= MAX_PARAMS)
    access_ok = (access == 'open')
    has_local_hf_path = (has_hf_client or model_name in KNOWN_HF_OVERRIDES)

    failure_reasons: list[str] = []
    if not is_text_like:
        failure_reasons.append('not-text-like')
    if has_excluded_tags:
        failure_reasons.append('excluded-tags')
    if not size_ok:
        failure_reasons.append('too-large')
    if not access_ok:
        failure_reasons.append('not-open-access')
    if not has_local_hf_path:
        failure_reasons.append('no-local-helm-deployment')

    eligible = (
        is_text_like
        and not has_excluded_tags
        and size_ok
        and access_ok
        and has_local_hf_path
    )

    details = build_failure_reason_details(
        tags=tags,
        is_text_like=is_text_like,
        has_excluded_tags=has_excluded_tags,
        size_ok=size_ok,
        access_ok=access_ok,
        has_local_hf_path=has_local_hf_path,
        num_parameters=num_parameters,
        access=access,
        has_hf_client=has_hf_client,
        model_name=model_name,
        known_hf_overrides=KNOWN_HF_OVERRIDES,
        max_params=MAX_PARAMS,
        exclude_tags=EXCLUDE_TAGS,
    )
    return eligible, failure_reasons, details


def build_run_failure_reason_details(
    *, benchmark: str, allow_closed_judge: bool = False
) -> dict[str, str]:
    details: dict[str, str] = {}
    if benchmark in CLOSED_JUDGE_BENCHMARKS and not allow_closed_judge:
        # Open-judge extension (Phase 3 / 4.9): pass
        # allow_closed_judge=True (--allow-closed-judge-benchmarks) to
        # admit these runs as planned judge substitutions instead of
        # excluding them; build_filter_inventory_rows then routes them
        # through the distinct 'judge-substitution' selection path.
        details[CLOSED_JUDGE_REQUIRED_REASON] = (
            'Benchmark requires a proprietary / credentialed judge or annotator path; '
            'that closed-source evaluation dependency is currently out of scope for the '
            'local open-model reproduction recipe.'
        )
    if benchmark in GATED_DATASET_BENCHMARKS:
        details[GATED_DATASET_REASON] = (
            'Benchmark requires a gated dataset that is not part of the default '
            'local open-model reproduction recipe.'
        )
    return details


def short_scenario_name(scenario_class: str | None) -> str:
    if not scenario_class:
        return 'UnknownScenario'
    return scenario_class.rsplit('.', 1)[-1]


def describe_run_spec(run_spec_name: str, scenario_class: str | None = None) -> dict[str, Any]:
    benchmark = run_spec_name.split(':', 1)[0]
    kv = parse_run_name_to_kv(run_spec_name)[1]
    try:
        benchmark, parsed_kv = parse_run_entry_description(run_spec_name)
        kv = {str(k): parsed_kv[k] for k in parsed_kv}
    except Exception:
        pass

    dataset_key = None
    for key in [
        'dataset',
        'subset',
        'subject',
        'task',
        'demographic',
        'domain',
        'language_pair',
        'lang',
        'mode',
        'difficulty',
        'k',
        'level',
    ]:
        if key in kv:
            dataset_key = key
            break
    dataset = benchmark if dataset_key is None else f'{dataset_key}={kv[dataset_key]}'

    non_model_items = [
        f'{key}={value}' if value is not True else str(key)
        for key, value in kv.items()
        if key != 'model'
    ]
    setting = benchmark if not non_model_items else f'{benchmark}:' + ','.join(non_model_items)
    return {
        'benchmark': benchmark,
        'dataset': dataset,
        'dataset_key': dataset_key,
        'setting': setting,
        'scenario': short_scenario_name(scenario_class) if scenario_class else benchmark,
        'run_params': kv,
    }


def build_incomplete_inventory_row(run_dir: Path) -> dict[str, Any]:
    run_name = run_dir.name
    benchmark, kv = parse_run_name_to_kv(run_name)
    model = kv.get('model')
    if isinstance(model, str):
        # P2: restore only the org separator (first underscore) — a model id is
        # ``org/name`` sanitized to ``org_name``; replace-all corrupted names
        # that themselves contain underscores (e.g. ``meta_llama_3``).
        model = model.replace('_', '/', 1)
    dataset_key = None
    for key in ['dataset', 'subset', 'subject', 'task', 'demographic', 'domain', 'language_pair', 'lang', 'mode', 'difficulty', 'k', 'level']:
        if key in kv:
            dataset_key = key
            break
    dataset = benchmark if dataset_key is None else f'{dataset_key}={kv[dataset_key]}'
    return {
        'run_spec_name': run_name,
        'run_dir': str(run_dir),
        'max_eval_instances': None,
        'model': model,
        'scenario_class': None,
        'benchmark': benchmark or 'unknown',
        'dataset': dataset,
        'dataset_key': dataset_key,
        'setting': run_name,
        'scenario': benchmark or 'unknown',
        'run_params': kv,
        'selection_status': 'excluded',
        'outcome': 'excluded',
        'considered_for_selection': False,
        'eligible_candidate': False,
        'candidate_pool': 'structurally-incomplete',
        'eligible_model': False,
        'failure_reasons': ['structurally-incomplete'],
        'failure_reason_summary': 'structurally-incomplete',
        'selection_explanation': 'Excluded before candidate selection because the run directory was structurally incomplete.',
        'is_structurally_incomplete': True,
    }


def build_filter_inventory_rows(
    *,
    complete_rows: list[dict[str, Any]],
    incomplete_rows: list[dict[str, Any]],
    model_filter_rows: list[dict[str, Any]],
    chosen_model_names: set[str],
    allow_closed_judge: bool = False,
) -> list[dict[str, Any]]:
    model_info = {row['model']: row for row in model_filter_rows}
    registry = local_model_registry_by_name()
    inventory_rows: list[dict[str, Any]] = []
    for row in complete_rows:
        info = describe_run_spec(row['run_spec_name'], row.get('scenario_class'))
        model_meta = model_info.get(row['model'], {})
        model_failure_reasons = list(model_meta.get('failure_reasons', []))
        model_failure_reason_details = dict(model_meta.get('failure_reason_details', {}))
        run_failure_reason_details = build_run_failure_reason_details(
            benchmark=info['benchmark'], allow_closed_judge=allow_closed_judge,
        )
        run_failure_reasons = list(run_failure_reason_details)
        failure_reasons = model_failure_reasons + [
            reason for reason in run_failure_reasons if reason not in model_failure_reasons
        ]
        failure_reason_details = model_failure_reason_details | run_failure_reason_details
        eligible_model = bool(model_meta.get('eligible', False))
        eligible_candidate = eligible_model and not run_failure_reasons
        # A closed-judge benchmark admitted under the relax flag is a
        # *planned judge substitution* — neither an ordinary selection
        # nor an exclusion. It gets its own candidate pool so the
        # selection-path table and sankeys show it as a distinct path
        # (Phase 3 / 4.9). Fields are only emitted when the flag is on,
        # keeping flag-off outputs byte-identical.
        judge_substitution = (
            allow_closed_judge and info['benchmark'] in CLOSED_JUDGE_BENCHMARKS
        )
        candidate_pool = 'complete-run'
        if eligible_model:
            candidate_pool = 'eligible-model' if not run_failure_reasons else 'eligible-model-out-of-scope'
            if judge_substitution:
                candidate_pool = 'judge-substitution'
        selected = row['model'] in chosen_model_names and not run_failure_reasons
        reg_entry = registry.get(row['model'])
        if selected and judge_substitution:
            selection_explanation = (
                'Selected as a planned judge substitution: the benchmark normally '
                'requires a closed judge, and --allow-closed-judge-benchmarks admits '
                'it for an open-judge re-run to be compared as a declared substitution.'
            )
        elif selected:
            selection_explanation = (
                'Selected because the run was structurally complete and its model passed all eligibility filters.'
            )
        else:
            selection_explanation = (
                'Excluded after consideration because the run failed the current reproduction filters: '
                + '; '.join(
                    failure_reason_details.get(reason, reason)
                    for reason in failure_reasons
                ) + '.'
            )
        judge_fields = (
            {'judge_substitution_planned': True} if selected and judge_substitution else {}
        )
        inventory_rows.append({
            **row,
            **info,
            **judge_fields,
            'selection_status': 'selected' if selected else 'excluded',
            'outcome': 'selected' if selected else 'excluded',
            'considered_for_selection': True,
            'eligible_candidate': eligible_candidate,
            'candidate_pool': candidate_pool,
            'eligible_model': eligible_model,
            'failure_reasons': failure_reasons,
            'failure_reason_details': failure_reason_details,
            'failure_reason_summary': 'selected' if selected else '|'.join(failure_reasons),
            'selection_explanation': selection_explanation,
            'model_num_parameters': model_meta.get('num_parameters'),
            'model_access': model_meta.get('access'),
            'model_tags': model_meta.get('tags', []),
            'model_has_hf_client': model_meta.get('has_hf_client'),
            'size_threshold_params': model_meta.get('size_threshold_params'),
            'is_structurally_incomplete': False,
            'expected_local_served': reg_entry.expected_local_served if reg_entry else False,
            'replaces_helm_deployment': reg_entry.replaces_helm_deployment if reg_entry else None,
            'local_registry_source': reg_entry.source if reg_entry else None,
        })
    inventory_rows.extend(incomplete_rows)
    # P2: add run_dir as a final tiebreaker so rows that share
    # (selection_status, model, run_spec_name) — common among incomplete rows —
    # sort deterministically instead of preserving the unsorted walk order.
    inventory_rows.sort(key=lambda row: (
        row['selection_status'],
        str(row.get('model')),
        row['run_spec_name'],
        str(row.get('run_dir') or ''),
    ))
    return inventory_rows

"""Curve math and run loading for the per-pair core-metric report:
agreement curves, quantiles, diagnostics, metric domains, and the
official/local pair builder. No rendering here.

Split out of ``eval_audit.reports.core_metrics`` on 2026-06-11
(Phase 2 of docs/planning/repo-refactor-plan.md). Pure relocation:
function bodies are unchanged.
"""
from __future__ import annotations
import json
import statistics
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd
from eval_audit.helm.diff import HelmRunDiff
from eval_audit.indexing.schema import extract_run_spec_fields
from eval_audit.normalized import (
    NormalizedRun,
    NormalizedRunRef,
    SourceKind,
    load_run,
)
from eval_audit.normalized import compare as ncompare
from eval_audit.normalized.helm_compat import helm_view
from eval_audit.utils.numeric import quantile as _quantile
# Row math relocated to the unified comparison core (Phase 3 / 4.3);
# re-imported under the historical names so this module's public
# surface (and the core_metrics facade re-exports) are unchanged.
from eval_audit.normalized.diff import (
    agreement_curve as _agreement_curve,
    group_quantiles as _group_quantiles,
    metric_quantiles as _metric_quantiles,
)
from eval_audit.infra.profiling import profile


MetricDomain = tuple[float, float]


def _load_json(fpath: Path) -> Any:
    return json.loads(fpath.read_text())


def _load_optional_cross_machine_pair(report_dpath: Path) -> dict[str, Any] | None:
    pair_fpath = report_dpath / 'cross-machine-aiq-gpu' / 'pair_report.json'
    if not pair_fpath.exists():
        return None
    data = _load_json(pair_fpath)
    display = data.get('display_labels', {}) or {}
    label_a = (
        display.get('label_a')
        or ((data.get('inputs') or {}).get('label_a'))
        or 'aiq-gpu'
    )
    label_b = (
        display.get('label_b')
        or ((data.get('inputs') or {}).get('label_b'))
        or 'other-machine'
    )
    highlights = data.get('tolerance_highlights', {}) or {}
    distance = data.get('distance_summary', {}) or {}
    strict = data.get('strict_summary', {}) or {}
    diagnosis = (strict.get('diagnosis') or {})
    return {
        'label': f'{label_a}_vs_{label_b}',
        'diagnosis': diagnosis,
        'run_level': {
            'agreement_vs_abs_tol': highlights.get('run_level', []) or [],
            'overall_quantiles': (distance.get('run_level') or {}).get('overall', {}) or {},
        },
        'instance_level': {
            'agreement_vs_abs_tol': highlights.get('instance_level', []) or [],
            'overall_quantiles': (distance.get('instance_level') or {}).get('overall', {}) or {},
        },
    }


def _collect_stat_means(stats: list[dict[str, Any]], metric_name: str) -> dict[str, float]:
    found = {}
    for row in stats:
        name = row.get('name')
        if not isinstance(name, dict):
            continue
        if name.get('name') != metric_name:
            continue
        split = name.get('split')
        found[str(split)] = row.get('mean')
    return found


_EMPTY_RUN_DIAGNOSTICS: dict[str, Any] = {
    'n_request_states': 0,
    'n_with_completions': 0,
    'empty_completion_count': 0,
    'empty_completion_rate': None,
    'output_token_count': {'mean': None, 'p50': None, 'p90': None, 'max': None},
    'stats_means': {},
}


def _run_diagnostics(run_path: str | None) -> dict[str, Any]:
    """HELM run-dir diagnostics; gracefully skipped for EEE-only components.

    The diagnostics summary (empty-completion rate, prompt/completion token
    counts) is computed from raw HELM ``scenario_state.json`` + ``stats.json``.
    For pure-EEE components we don't have those files; return shape-correct
    zeros instead of crashing so the per-pair report can render the
    instance-level core-metric numbers (which are all the comparison core
    actually consumes anyway)."""
    if not run_path:
        return dict(_EMPTY_RUN_DIAGNOSTICS)
    run_path = str(Path(run_path).expanduser().resolve())
    run_dpath = Path(run_path)
    if not run_dpath.is_dir():
        return dict(_EMPTY_RUN_DIAGNOSTICS)
    scenario_state = _load_json(run_dpath / 'scenario_state.json')
    stats = _load_json(run_dpath / 'stats.json')
    reqs = scenario_state.get('request_states', [])

    token_counts = []
    empty_completion_count = 0
    nonempty_completion_count = 0
    completion_count = 0
    for rs in reqs:
        comps = (rs.get('result') or {}).get('completions') or []
        if not comps:
            continue
        completion_count += 1
        c0 = comps[0] or {}
        text = c0.get('text', '')
        toklist = c0.get('tokens') or []
        token_counts.append(len(toklist))
        if text == '':
            empty_completion_count += 1
        else:
            nonempty_completion_count += 1

    mean_tokens = statistics.mean(token_counts) if token_counts else None
    return {
        'run_path': run_path,
        'run_name': run_dpath.name,
        'n_request_states': len(reqs),
        'n_with_completions': completion_count,
        'empty_completion_count': empty_completion_count,
        'nonempty_completion_count': nonempty_completion_count,
        'empty_completion_rate': (
            empty_completion_count / completion_count if completion_count else None
        ),
        'output_token_count': {
            'mean': mean_tokens,
            'p50': _quantile(token_counts, 0.5),
            'p90': _quantile(token_counts, 0.9),
            'max': _quantile(token_counts, 1.0),
        },
        'stats_means': {
            'num_output_tokens': _collect_stat_means(stats, 'num_output_tokens'),
            'num_completion_tokens': _collect_stat_means(stats, 'num_completion_tokens'),
            'finish_reason_unknown': _collect_stat_means(stats, 'finish_reason_unknown'),
        },
    }


def _diagnostic_flags(
    run_diagnostics: dict[str, dict[str, Any]],
    components: list[dict[str, Any]],
    comparisons: list[dict[str, Any]],
) -> list[str]:
    flags = []
    for label, diag in run_diagnostics.items():
        rate = diag.get('empty_completion_rate')
        mean_tokens = (diag.get('output_token_count') or {}).get('mean')
        if rate is not None and rate > 0.1:
            flags.append(f'{label}:high_empty_completion_rate')
        if mean_tokens is not None and mean_tokens < 1.0:
            flags.append(f'{label}:near_zero_mean_output_tokens')
    component_lookup = {component['component_id']: component for component in components}
    official_vs_local = next(
        (
            comparison
            for comparison in comparisons
            if comparison.get('comparison_kind') == 'official_vs_local' and comparison.get('enabled', True)
        ),
        None,
    )
    if official_vs_local is not None:
        component_ids = official_vs_local.get('component_ids') or []
        comparison_components = [
            component_lookup.get(component_id, {})
            for component_id in component_ids
        ]
        reference_component = component_lookup.get(
            official_vs_local.get('reference_component_id'),
            {},
        )
        official_component = next(
            (component for component in comparison_components if component.get('source_kind') == 'official'),
            None,
        )
        local_component = next(
            (component for component in comparison_components if component.get('source_kind') == 'local'),
            None,
        )
        if reference_component and reference_component.get('source_kind') == 'official':
            official_component = reference_component
        elif reference_component and reference_component.get('source_kind') == 'local':
            local_component = reference_component
        official_diag = (
            run_diagnostics.get(official_component.get('component_id'), {})
            if official_component is not None else {}
        )
        local_diag = (
            run_diagnostics.get(local_component.get('component_id'), {})
            if local_component is not None else {}
        )
        official_rate = official_diag.get('empty_completion_rate')
        local_rate = local_diag.get('empty_completion_rate')
        if (
            official_component is not None
            and local_component is not None
            and official_rate is not None
            and local_rate is not None
            and official_rate < 0.01
            and local_rate > 0.1
        ):
            flags.append(
                f"{official_vs_local['comparison_id']}:empty_completion_pathology"
            )
    return flags






_BINARY_CORE_METRICS = {
    'exact_match',
    'prefix_exact_match',
    'quasi_exact_match',
    'quasi_prefix_exact_match',
    'exact_match@5',
    'prefix_exact_match@5',
    'quasi_exact_match@5',
    'quasi_prefix_exact_match@5',
}
_BOUNDED_OVERLAP_CORE_METRICS = {'bleu_1', 'bleu_4', 'f1_score', 'rouge_l'}


def _metric_descriptor(metric: str) -> dict[str, str]:
    if metric in _BINARY_CORE_METRICS:
        return {
            'kind': 'binary',
            'range': '0 to 1',
            'direction': 'higher is better',
        }
    if metric in _BOUNDED_OVERLAP_CORE_METRICS:
        return {
            'kind': 'bounded overlap score',
            'range': '0 to 1',
            'direction': 'higher is better',
        }
    return {
        'kind': 'score',
        'range': 'metric-dependent',
        'direction': 'higher is better unless documented otherwise',
    }


def _metric_domain(metric: str) -> MetricDomain | None:
    if metric in _BINARY_CORE_METRICS or metric in _BOUNDED_OVERLAP_CORE_METRICS:
        return (0.0, 1.0)
    return None


def _common_metric_domain(metrics: list[str] | set[str]) -> MetricDomain | None:
    if not metrics:
        return None
    domains = {_metric_domain(str(metric)) for metric in metrics}
    if None in domains or len(domains) != 1:
        return None
    return next(iter(domains))


def _pair_metric_domain(*pairs: dict[str, Any]) -> MetricDomain | None:
    metrics: set[str] = set()
    for pair in pairs:
        if not pair:
            continue
        pair_metrics = pair.get('core_metrics')
        if not pair_metrics:
            return None
        metrics.update(str(metric) for metric in pair_metrics)
    return _common_metric_domain(metrics)


def _should_treat_as_discrete(values) -> bool:
    values = [float(v) for v in values if v is not None]
    unique_values = sorted(set(values))
    if not unique_values:
        return False
    return len(unique_values) <= 8 and all(v in {0.0, 1.0} for v in unique_values)




def _infer_run_spec_name(*run_paths: str) -> str:
    names = [Path(p).name for p in run_paths if p]
    names = [n for n in names if n]
    if not names:
        return 'unknown_run_spec'
    unique = sorted(set(names))
    if len(unique) == 1:
        return unique[0]
    return unique[0]


@profile
def _load_normalized(
    run_path: str | Path,
    source_kind: SourceKind = SourceKind.OFFICIAL,
    *,
    artifact_format: str = "helm",
    eee_artifact_path: str | Path | None = None,
    component_id: str | None = None,
    logical_run_key: str | None = None,
) -> NormalizedRun:
    """Load a run as a :class:`NormalizedRun` honoring the manifest format.

    When the planner has tagged a component as ``artifact_format='eee'`` and
    pointed ``eee_artifact_path`` at a converted EEE artifact directory, the
    EEE loader is used and the raw HELM run becomes evidence-only. Otherwise
    we fall back to the in-memory HELM->EEE conversion against ``run_path``.
    """
    if artifact_format == "eee" and eee_artifact_path:
        ref = NormalizedRunRef.from_eee_artifact(
            eee_artifact_path,
            source_kind=source_kind,
            helm_run_path=run_path,
            component_id=component_id,
            logical_run_key=logical_run_key,
        )
    else:
        ref = NormalizedRunRef.from_helm_run(
            run_path,
            source_kind=source_kind,
            component_id=component_id,
            logical_run_key=logical_run_key,
        )
    return load_run(ref)


def _component_source_kind(component: dict[str, Any] | None) -> SourceKind:
    raw = (component or {}).get("source_kind") or "official"
    try:
        return SourceKind(str(raw))
    except ValueError:
        return SourceKind.OFFICIAL


@profile
def _load_component_run(
    component: dict[str, Any],
    *,
    cache: dict[str, NormalizedRun] | None = None,
) -> NormalizedRun:
    """Load a component into a NormalizedRun, optionally memoizing.

    When the same component_id appears in multiple comparisons within
    a single packet (e.g. one official paired against N local replicas
    plus N-1 local_repeat comparisons share the local components),
    the loader was previously called once per pair — meaning the
    official artifact got loaded N times and each local artifact got
    loaded ~2x. Each load parses the EEE samples.jsonl from disk
    (105k records for new-format civil_comments, etc.), which is
    measurable wall-clock per call.

    Pass a ``cache`` dict to memoize across calls. The cache is keyed
    on ``component_id``; passing ``None`` preserves the original
    no-cache behavior (used by call sites that don't have a packet-
    scoped lifetime).
    """
    component_id = component.get("component_id")
    if cache is not None and component_id and component_id in cache:
        return cache[component_id]
    run = _load_normalized(
        component["run_path"],
        source_kind=_component_source_kind(component),
        artifact_format=str(component.get("artifact_format") or "helm"),
        eee_artifact_path=component.get("eee_artifact_path"),
        component_id=component_id,
        logical_run_key=component.get("logical_run_key"),
    )
    if cache is not None and component_id:
        cache[component_id] = run
    return run


@profile
def _build_pair(
    run_a: str,
    run_b: str,
    label: str,
    thresholds: list[float],
    *,
    component_a: dict[str, Any] | None = None,
    component_b: dict[str, Any] | None = None,
    component_cache: dict[str, NormalizedRun] | None = None,
    skip_diagnosis: bool = False,
) -> dict[str, Any]:
    # Stage-4 + Stage-5: the per-metric measurement core operates on the
    # EEE-normalized representation. When the planner has tagged a
    # component as artifact_format='eee', the EEE loader is used directly;
    # otherwise we fall back to in-memory HELM->EEE conversion. The legacy
    # HelmRunDiff is still used for the run-spec-semantic diagnosis (which
    # reads run_spec.json from the raw HELM JSONs cached on the run).
    #
    # ``skip_diagnosis=True`` (driven by --skip-diagnosis or
    # EVAL_AUDIT_SKIP_HELM_DIAGNOSIS=1) bypasses HelmRunDiff entirely. The
    # diagnosis labels (recipe_clean / deployment_drift / ...) need
    # run_spec.json which is a HELM artifact; for the EEE-only paper
    # validity claim the heatmap's NUMBERS must come from EEE alone, and
    # the diagnosis is auxiliary metadata, not load-bearing for the
    # core agreement-ratio comparisons. Skipping it also drops ~57s/packet
    # of wasted compute (summary_dict(level=20) computes far more than
    # the diagnosis we actually consume).
    if component_a is not None:
        nrun_a = _load_component_run(component_a, cache=component_cache)
    else:
        nrun_a = _load_normalized(run_a, source_kind=SourceKind.OFFICIAL)
    if component_b is not None:
        nrun_b = _load_component_run(component_b, cache=component_cache)
    else:
        nrun_b = _load_normalized(run_b, source_kind=SourceKind.LOCAL)
    if skip_diagnosis:
        diagnosis: dict[str, Any] = {}
    else:
        diff = HelmRunDiff(
            helm_view(nrun_a),
            helm_view(nrun_b),
            a_name=f'{label}:A',
            b_name=f'{label}:B',
        )
        diagnosis = diff.summary_dict(level=20).get('diagnosis', {})
    run_rows = ncompare.run_level_core_rows(nrun_a, nrun_b)
    inst_rows, inst_stats = ncompare.instance_level_core_rows(nrun_a, nrun_b)

    # Calculate per-metric agreement curves for instance level
    per_metric_curves = {}
    if inst_rows:
        by_metric = {}
        for row in inst_rows:
            metric = str(row.get('metric', 'unknown'))
            if metric not in by_metric:
                by_metric[metric] = []
            by_metric[metric].append(row)
        for metric, metric_rows in by_metric.items():
            per_metric_curves[metric] = _agreement_curve(metric_rows, thresholds)

    return {
        'label': label,
        'inputs': {
            'run_a': str(Path(run_a).expanduser().resolve()),
            'run_b': str(Path(run_b).expanduser().resolve()),
        },
        'diagnosis': diagnosis,
        'core_metrics': sorted({str(r['metric']) for r in inst_rows}),
        'run_level': {
            'n_rows': len(run_rows),
            'overall_quantiles': _group_quantiles(run_rows),
            'by_metric': _metric_quantiles(run_rows),
            'agreement_vs_abs_tol': _agreement_curve(run_rows, thresholds),
        },
        'instance_level': {
            'n_rows': len(inst_rows),
            # Pre-filter join count from join_instances. Lets the
            # heatmap distinguish "no hash overlap" (n_joined_pairs==0)
            # from "hashes overlapped but no core metrics survived
            # classify_metric" (n_joined_pairs>0 && n_rows==0). See
            # eval_audit.normalized.compare.instance_level_core_rows.
            'n_joined_pairs': int(inst_stats.get('n_joined_pairs', 0)),
            'overall_quantiles': _group_quantiles(inst_rows),
            'by_metric': _metric_quantiles(inst_rows),
            'agreement_vs_abs_tol': _agreement_curve(inst_rows, thresholds),
            'per_metric_agreement': per_metric_curves,
        },
        '_instance_rows': inst_rows,
    }


@profile
def _agreement_curve_rows(*pairs: dict[str, Any], level_key: str) -> list[dict[str, Any]]:
    rows = []
    for pair in pairs:
        if not pair:
            continue
        for row in pair[level_key]['agreement_vs_abs_tol']:
            rows.append({
                'pair': pair['label'],
                'abs_tol': float(row['abs_tol']),
                'agree_ratio': float(row['agree_ratio']),
            })
    return rows


@profile
def _per_metric_agreement_curves(*pairs: dict[str, Any], level_key: str, thresholds: list[float]) -> dict[str, list[dict[str, Any]]]:
    """Calculate per-metric agreement curves from pair instance rows."""
    curves = {}
    for pair in pairs:
        if not pair:
            continue
        instance_rows = pair.get('_instance_rows', [])
        if level_key == 'instance_level':
            rows = instance_rows
        else:
            continue

        by_metric = {}
        for row in rows:
            metric = str(row.get('metric', 'unknown'))
            if metric not in by_metric:
                by_metric[metric] = []
            by_metric[metric].append(row)

        for metric, metric_rows in by_metric.items():
            if metric not in curves:
                curves[metric] = []
            agreement = _agreement_curve(metric_rows, thresholds)
            for agreement_row in agreement:
                curves[metric].append({
                    'pair': pair['label'],
                    'metric': metric,
                    'abs_tol': float(agreement_row['abs_tol']),
                    'agree_ratio': float(agreement_row['agree_ratio']),
                })
    return curves


@profile
def _distribution_rows(pair: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for row in pair.get('_instance_rows', []):
        rows.append({
            'pair': pair['label'],
            'metric': row['metric'],
            'side': 'A',
            'value': float(row['a']),
        })
        rows.append({
            'pair': pair['label'],
            'metric': row['metric'],
            'side': 'B',
            'value': float(row['b']),
        })
    return pd.DataFrame(rows)


def _single_run_instance_core_rows(
    run_path: str,
    label: str,
    *,
    component: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Per-(sample, core-metric) score rows for a single run.

    Stage-4: reads from the normalized layer's :class:`InstanceRecord`
    instead of HELM's joined per-instance stats table.
    """
    nrun = _load_component_run(component) if component is not None else _load_normalized(run_path)
    rows = [
        {"run": label, **rec}
        for rec in ncompare.instance_core_score_records(nrun)
    ]
    return pd.DataFrame(rows)


class _SimpleStatRow:
    """Minimal row used by run-level table writers.

    Replaces the ``StatMeta`` records the legacy
    :class:`HelmRunAnalysis.stat_index` produced. Only the fields actually
    consumed by the table writers (``metric`` and ``mean``) are exposed.
    """

    __slots__ = ("metric", "mean")

    def __init__(self, metric: str, mean: float) -> None:
        self.metric = metric
        self.mean = mean


def _single_run_core_stat_index(
    run_path: str,
    *,
    component: dict[str, Any] | None = None,
    component_cache: dict[str, NormalizedRun] | None = None,
) -> dict[str, _SimpleStatRow]:
    """Run-level core metric means keyed by stable metric handle.

    Stage-4: backed by ``ncompare.joined_metric_means`` over a normalized
    run instead of ``HelmRunAnalysis.stat_index``.

    ``component_cache`` is the per-packet NormalizedRun memo populated by
    ``_build_pair``. Threading it here avoids re-loading every official +
    local artifact from disk a second time when the runlevel-table
    writer asks for the per-run core stats — those artifacts were
    already parsed for the agreement-curve computation.
    """
    nrun = (
        _load_component_run(component, cache=component_cache)
        if component is not None
        else _load_normalized(run_path)
    )
    out: dict[str, _SimpleStatRow] = {}
    for key in ncompare.core_metric_keys(nrun):
        means = {
            (er.metric_config.metric_id or er.metric_config.metric_name or er.evaluation_name): er.score_details.score
            for er in nrun.evaluation_log.evaluation_results or []
        }
        if key in means:
            out[key] = _SimpleStatRow(metric=key, mean=float(means[key]))
    return out


def _strip_private(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: _strip_private(v)
            for k, v in obj.items()
            if not str(k).startswith('_')
        }
    if isinstance(obj, list):
        return [_strip_private(v) for v in obj]
    return obj


def _find_pair(report: dict[str, Any], comparison_kind: str) -> dict[str, Any] | None:
    return next(
        (pair for pair in report.get('pairs', []) if pair.get('comparison_kind') == comparison_kind),
        None,
    )


def _load_run_spec_json(component: dict[str, Any]) -> dict[str, Any] | None:
    """Read raw HELM run_spec.json off the component's run_path.

    Returns ``None`` for pure-EEE components (no HELM run_path on disk),
    for components whose run_path is missing, and for unparseable files."""
    run_path = component.get('run_path')
    if not run_path:
        return None
    run_spec_fpath = Path(run_path) / 'run_spec.json'
    if not run_spec_fpath.exists():
        return None
    try:
        data = json.loads(run_spec_fpath.read_text())
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _component_spec_metadata(component: dict[str, Any]) -> dict[str, Any]:
    run_spec = _load_run_spec_json(component) or {}
    run_path = component.get('run_path')
    fields = extract_run_spec_fields(Path(run_path) / 'run_spec.json' if run_path else None)
    adapter = run_spec.get('adapter_spec') or {}
    return {
        'base_model': fields.get('model'),
        'scenario_class': fields.get('scenario_class'),
        'deployment': fields.get('model_deployment'),
        'adapter_instructions': (
            adapter.get('instructions')
            if isinstance(adapter, dict) else None
        ),
        'max_eval_instances': (
            adapter.get('max_eval_instances')
            if isinstance(adapter, dict) and adapter.get('max_eval_instances') is not None
            else component.get('max_eval_instances')
        ),
    }


def _same_value_fact(values: list[Any]) -> dict[str, Any]:
    present = [value for value in values if value not in {None, ''}]
    unique = []
    for value in present:
        if value not in unique:
            unique.append(value)
    if not present:
        return {'status': 'unknown', 'values': []}
    if len(unique) == 1:
        return {'status': 'yes', 'values': unique}
    return {'status': 'no', 'values': unique}


@profile
def _comparability_summary(components: list[dict[str, Any]]) -> dict[str, Any]:
    metadata_by_component = {
        component['component_id']: _component_spec_metadata(component)
        for component in components
    }
    facts = {
        'same_base_model': _same_value_fact([meta.get('base_model') for meta in metadata_by_component.values()]),
        'same_scenario_class': _same_value_fact([meta.get('scenario_class') for meta in metadata_by_component.values()]),
        'same_deployment': _same_value_fact([meta.get('deployment') for meta in metadata_by_component.values()]),
        'same_adapter_instructions': _same_value_fact([meta.get('adapter_instructions') for meta in metadata_by_component.values()]),
        'same_max_eval_instances': _same_value_fact([meta.get('max_eval_instances') for meta in metadata_by_component.values()]),
    }
    return {
        'component_metadata': metadata_by_component,
        'facts': facts,
    }


@profile
def _warnings_payload(report: dict[str, Any]) -> dict[str, Any]:
    comparisons = report.get("comparisons") or []
    return {
        "report_dpath": report.get("report_dpath"),
        "packet_id": report.get("packet_id"),
        "run_entry": report.get("run_entry"),
        "planner_version": report.get("planner_version"),
        "packet_warnings": report.get("packet_warnings") or [],
        "packet_caveats": report.get("packet_caveats") or [],
        "official_selection": report.get("official_selection") or {},
        "diagnostic_flags": report.get("diagnostic_flags") or [],
        "comparisons": [
            {
                "comparison_id": comparison.get("comparison_id"),
                "comparison_kind": comparison.get("comparison_kind"),
                "enabled": comparison.get("enabled"),
                "disabled_reason": comparison.get("disabled_reason"),
                "warnings": comparison.get("warnings") or [],
                "caveats": comparison.get("caveats") or [],
                "comparability_facts": comparison.get("comparability_facts") or {},
            }
            for comparison in comparisons
        ],
    }


def _warning_summary_lines(report: dict[str, Any]) -> list[str]:
    warnings_payload = _warnings_payload(report)
    lines = [
        "Core Metric Report Warnings",
        "",
        f"report_dpath: {report.get('report_dpath')}",
        f"packet_id: {report.get('packet_id')}",
        f"run_entry: {report.get('run_entry')}",
        f"planner_version: {report.get('planner_version')}",
        f"diagnostic_flags: {report.get('diagnostic_flags') or []}",
        "",
    ]
    packet_warnings = warnings_payload.get("packet_warnings") or []
    packet_caveats = warnings_payload.get("packet_caveats") or []
    if packet_warnings:
        lines.append("packet_warnings:")
        for item in packet_warnings:
            lines.append(f"  - {item}")
    if packet_caveats:
        lines.append("packet_caveats:")
        for item in packet_caveats:
            lines.append(f"  - {item}")
    official_selection = warnings_payload.get("official_selection") or {}
    if official_selection:
        lines.append("official_selection:")
        lines.append(f"  policy_name: {official_selection.get('policy_name')}")
        lines.append(f"  selected_public_track: {official_selection.get('selected_public_track')}")
        lines.append(f"  retained_component_ids: {official_selection.get('retained_component_ids')}")
        lines.append(f"  discarded_component_ids: {official_selection.get('discarded_component_ids')}")
        if official_selection.get("warnings"):
            lines.append(f"  warnings: {official_selection.get('warnings')}")
    lines.append("comparisons:")
    for comparison in warnings_payload.get("comparisons") or []:
        lines.append(
            f"  - {comparison.get('comparison_id')} enabled={comparison.get('enabled')} "
            f"disabled_reason={comparison.get('disabled_reason')}"
        )
        if comparison.get("warnings"):
            lines.append(f"    warnings: {comparison.get('warnings')}")
        if comparison.get("caveats"):
            lines.append(f"    caveats: {comparison.get('caveats')}")
    return lines


def _find_curve_value(rows: list[dict[str, Any]], abs_tol: float) -> float | None:
    for row in rows:
        if float(row.get('abs_tol', float('nan'))) == float(abs_tol):
            return row.get('agree_ratio')
    return None

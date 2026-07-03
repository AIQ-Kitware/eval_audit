"""Framework-free reproducibility diagnosis.

Port of :meth:`eval_audit.helm.diff.HelmRunDiff._diagnose_repro` as a
pure module-level function (Phase 3 sub-stage 4.2 of
docs/planning/phase3-comparison-core-unification.md). The original is
already a pure function of its keyword inputs — it never reads
``self`` — so this port preserves the label vocabulary and reason
ordering byte-for-byte; ``tests/test_phase3_diagnose_equivalence.py``
asserts equality against the HELM implementation across a
branch-covering input battery. ``HelmRunDiff`` keeps its own copy
until sub-stage 4.6 points the HELM path at the unified core.

New here (R2, design doc §3.5): substitution awareness. A comparison
intent may declare expected recipe substitutions (e.g. ``judge`` for
the open-judge extension). Facts stay honest — a declared substitution
never changes a comparability fact — but the diagnosis re-labels:

- declared and observed (fact status ``no``) →
  ``intended_substitution:<name>`` reason instead of reading as
  unexplained drift;
- declared but **not** observed (fact status ``yes``) →
  ``substitution_not_observed:<name>`` reason — the operator said the
  recipes differ on this axis, the metadata says they don't; that
  mismatch is itself a finding;
- fact status ``unknown``: no extra reason — the planner's
  ``comparability_unknown:*`` warning already covers it.
"""

from __future__ import annotations

import json
import math
from typing import Any, Mapping, Sequence

import ubelt as ub


def _json_compatible(obj: Any) -> Any:
    """Recursively coerce to strict JSON-compatible types.

    Private copy of ``eval_audit.helm.diff_primitives._json_compatible``
    so this module stays importable without ``eval_audit.helm.*``; the
    behavior must stay identical for the equivalence gate to hold.
    """
    if obj is None or isinstance(obj, (str, int, bool)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): _json_compatible(v) for k, v in obj.items()}
    if isinstance(obj, set):
        # IM-12: sets have no stable iteration order (PYTHONHASHSEED-dependent);
        # sort by the serialized value so output is deterministic.
        return sorted((_json_compatible(v) for v in obj), key=lambda x: json.dumps(x, sort_keys=True, default=str))
    if isinstance(obj, (list, tuple)):
        return [_json_compatible(v) for v in obj]
    try:
        if hasattr(obj, 'as_tuple') and callable(getattr(obj, 'as_tuple')):
            return _json_compatible(list(obj.as_tuple()))
    except Exception:
        pass
    try:
        return ub.urepr(obj, nl=0, compact=1)
    except Exception:
        return str(obj)


def diagnose_repro(
    *,
    run_spec_name_ok: bool,
    run_spec_semantic: dict[str, Any],
    scenario_semantic: dict[str, Any],
    dataset_overlap: dict[str, Any] | None,
    value_summary: dict[str, Any],
    substitutions: Sequence[str] = (),
    substitution_fact_status: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """High-level diagnosis for reproducibility triage.

    Returns a primary label plus a full list of contributing reasons.
    Lower ``priority`` is earlier / more significant in the pipeline.

    With no ``substitutions`` declared this is byte-identical to
    ``HelmRunDiff._diagnose_repro``. ``substitution_fact_status`` maps
    each declared substitution name to the planner's fact status for
    that axis (``'yes' | 'no' | 'unknown'``, e.g. from the
    ``same_judge`` fact for the ``judge`` substitution).
    """
    reasons: list[dict[str, Any]] = []

    def add_reason(name: str, priority: int, details: dict[str, Any]) -> None:
        reasons.append(
            {
                'name': name,
                'priority': int(priority),
                'details': _json_compatible(details),
            }
        )

    # Priority 0: run pairing / spec-level execution blockers
    if not run_spec_name_ok:
        add_reason(
            'wrong_run_pair',
            0,
            {'run_spec_name_ok': False},
        )

    execution_ok = bool(run_spec_semantic.get('execution_ok', False))
    execution_paths = run_spec_semantic.get('execution_paths', []) or []
    deployment_paths = run_spec_semantic.get('deployment_paths', []) or []
    non_deployment_execution_paths = [
        p
        for p in execution_paths
        if not str(p).startswith('adapter_spec.model_deployment')
    ]
    if not execution_ok and non_deployment_execution_paths:
        add_reason(
            'execution_spec_drift',
            0,
            {
                'execution_paths': execution_paths,
                'execution_value_examples': run_spec_semantic.get(
                    'execution_value_examples', []
                ),
                'counts': run_spec_semantic.get('counts', {}),
            },
        )

    if bool(run_spec_semantic.get('deployment_changed', False)):
        dep = run_spec_semantic.get('deployment', {}) or {}
        add_reason(
            'deployment_drift',
            0,
            {
                'a_value': dep.get('a', None),
                'b_value': dep.get('b', None),
                'execution_paths': [
                    p
                    for p in (
                        execution_paths
                    )
                    if str(p).startswith('adapter_spec.model_deployment')
                ]
                or deployment_paths,
            },
        )

    scen_known = bool(scenario_semantic.get('known', False))
    scen_semantic_ok = scenario_semantic.get('semantic_ok', None)
    if scen_known and not bool(scen_semantic_ok):
        add_reason(
            'scenario_spec_drift',
            0,
            {
                'semantic_paths': scenario_semantic.get(
                    'semantic_paths', []
                ),
                'counts': scenario_semantic.get('counts', {}),
            },
        )

    # Priority 1: dataset/request-state drift
    if isinstance(dataset_overlap, dict):
        if 'error' in dataset_overlap:
            add_reason(
                'dataset_overlap_error',
                1,
                {'error': dataset_overlap.get('error', None)},
            )
        else:
            base_iou = dataset_overlap.get('base_iou', None)
            variant_iou = dataset_overlap.get('variant_iou', None)
            if base_iou is not None and base_iou < 1.0:
                add_reason(
                    'dataset_instance_drift',
                    1,
                    {
                        'base_iou': base_iou,
                        'base_coverage': dataset_overlap.get(
                            'base_coverage', {}
                        ),
                    },
                )
            if variant_iou is not None and variant_iou < 1.0:
                add_reason(
                    'dataset_variant_drift',
                    1,
                    {
                        'variant_iou': variant_iou,
                        'variant_coverage': dataset_overlap.get(
                            'variant_coverage', {}
                        ),
                    },
                )

            ce = dataset_overlap.get('content_equality', {}) or {}
            mex = dataset_overlap.get('mismatch_examples', {}) or {}
            for field, reason_name, pr in [
                ('input', 'dataset_input_drift', 1),
                ('prompt', 'request_prompt_drift', 1),
                ('completion', 'completion_content_drift', 2),
            ]:
                row = ce.get(field, {}) or {}
                eq = row.get('equal_ratio', None)
                if eq is not None and eq < 1.0:
                    details = dict(row)
                    examples = mex.get(field, None)
                    if examples:
                        details['examples'] = examples
                    add_reason(reason_name, pr, details)

    # Priority 2: evaluation schema / metric set drift
    metric_specs_delta = (
        run_spec_semantic.get('metric_specs_multiset_delta', {}) or {}
    )
    eval_paths = run_spec_semantic.get('evaluation_paths', []) or []
    evaluation_changed = bool(eval_paths) or (
        not bool(metric_specs_delta.get('equal_as_multiset', True))
    )
    if evaluation_changed:
        details = {'evaluation_paths': eval_paths}
        if not bool(metric_specs_delta.get('equal_as_multiset', True)):
            details['metric_specs_multiset_delta'] = metric_specs_delta
        add_reason(
            'evaluation_spec_drift',
            2,
            details,
        )

    # Priority 3: value-level drift (may be downstream effect)
    core = ((value_summary.get('by_class') or {}).get('core') or {})
    book = ((value_summary.get('by_class') or {}).get('bookkeeping') or {})
    core_ratio = core.get('agree_ratio', None)
    book_ratio = book.get('agree_ratio', None)

    if core_ratio is None:
        add_reason(
            'no_comparable_core_metrics',
            3,
            {'core': core},
        )
    else:
        if core_ratio < 0.995:
            add_reason(
                'core_metric_drift',
                3,
                {
                    'core_agree_ratio': core_ratio,
                    'core': core,
                },
            )
        elif (book_ratio is not None) and (book_ratio < 0.95):
            add_reason(
                'bookkeeping_metric_drift',
                3,
                {
                    'core_agree_ratio': core_ratio,
                    'bookkeeping_agree_ratio': book_ratio,
                    'bookkeeping': book,
                },
            )

    # Declared substitutions (R2). Added after the drift scan so the
    # reason list keeps the original entries untouched; with no
    # declarations this whole block is a no-op and the output is
    # byte-identical to the HELM implementation.
    reasons.extend(
        _substitution_reasons(substitutions, substitution_fact_status)
    )

    if not reasons:
        add_reason(
            'no_detected_drift',
            0,
            {
                'core_agree_ratio': core_ratio,
                'bookkeeping_agree_ratio': book_ratio,
            },
        )

    return _finalize_reasons(reasons)


def _substitution_reasons(
    substitutions: Sequence[str],
    substitution_fact_status: Mapping[str, str] | None,
) -> list[dict[str, Any]]:
    fact_status = dict(substitution_fact_status or {})
    reasons: list[dict[str, Any]] = []
    for name in substitutions:
        status = str(fact_status.get(name, 'unknown') or 'unknown')
        if status == 'no':
            reason_name = f'intended_substitution:{name}'
        elif status == 'yes':
            reason_name = f'substitution_not_observed:{name}'
        else:
            # 'unknown': the planner's comparability_unknown:* warning
            # already records the unverifiable axis; no diagnosis reason.
            continue
        reasons.append(
            {
                'name': reason_name,
                'priority': 0,
                'details': _json_compatible(
                    {'substitution': name, 'fact_status': status}
                ),
            }
        )
    return reasons


def _finalize_reasons(reasons: list[dict[str, Any]]) -> dict[str, Any]:
    reasons = sorted(
        reasons,
        key=lambda r: (
            int(r.get('priority', 999)),
            str(r.get('name', '')),
        ),
    )
    min_priority = min(int(r['priority']) for r in reasons)
    primary_reason_names = [
        r['name'] for r in reasons if int(r['priority']) == min_priority
    ]
    if primary_reason_names == ['no_detected_drift']:
        label = 'reproduced'
    elif len(primary_reason_names) == 1:
        label = primary_reason_names[0]
    else:
        label = 'multiple_primary_reasons'

    return {
        'label': label,
        'primary_priority': min_priority,
        'primary_reason_names': primary_reason_names,
        'reasons': reasons,
    }


def apply_substitutions(
    diagnosis: dict[str, Any] | None,
    *,
    substitutions: Sequence[str],
    substitution_fact_status: Mapping[str, str] | None,
) -> dict[str, Any] | None:
    """Overlay declared-substitution reasons onto an existing diagnosis.

    The HELM-driven renderer computes its diagnosis through
    ``HelmRunDiff`` (which has no substitution concept); this helper
    lets it re-label after the fact using the same reason/label logic
    as :func:`diagnose_repro`. With no substitution reasons to add the
    input is returned unchanged (including ``None``/``{}`` — an
    intentionally skipped diagnosis stays skipped unless a declared
    substitution introduces facts-derived reasons).
    """
    extra = _substitution_reasons(substitutions, substitution_fact_status)
    if not extra:
        return diagnosis
    base_reasons = list((diagnosis or {}).get('reasons') or [])
    # 'no_detected_drift' is the empty-marker reason; a real
    # substitution reason replaces it rather than coexisting with it.
    base_reasons = [
        r for r in base_reasons if r.get('name') != 'no_detected_drift'
    ]
    return _finalize_reasons(base_reasons + extra)


__all__ = ["apply_substitutions", "diagnose_repro"]

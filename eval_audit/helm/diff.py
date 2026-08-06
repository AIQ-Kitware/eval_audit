"""eval_audit.helm.diff

Run-to-run comparison built on :class:`~eval_audit.helm.analysis.HelmRunAnalysis`.

NOTE
----
After the EEE refactor, the per-metric measurement core lives in
:mod:`eval_audit.normalized.compare`. ``HelmRunDiff`` is retained for the
run-spec-semantic diagnosis (same scenario class, same model, same
adapter instructions, etc.) which reads ``run_spec.json`` from the raw
HELM evidence cached on each :class:`~eval_audit.normalized.NormalizedRun`.
Do not add new agreement/quantile measurements here — extend
``eval_audit.normalized.compare`` instead so they work uniformly across
``helm`` and ``eee`` artifact formats.

Design goals
------------
* Keep the public API tight.
* Cache expensive computations.
* Provide both machine-friendly summaries (dict) and human-friendly reports
  (writer-style output using rich by default).

The diff intentionally leans on :class:`HelmRunAnalysis` for canonicalization
and indexing so both single-run and diff views agree on what a "stat" or
"instance" identity means.

CommandLine:
    xdoctest -m eval_audit.helm.diff __doc__

Example:
    >>> import json
    >>> from eval_audit.helm.analysis import HelmRunAnalysis
    >>> from eval_audit.helm.diff import HelmRunDiff
    >>> def _ana(run_spec, stats, request_states):
    ...     a = HelmRunAnalysis.__new__(HelmRunAnalysis)
    ...     a._raw_cache = {}
    ...     a._cache = {}
    ...     a.run = None
    ...     a.name = None
    ...     a.run_spec = lambda: run_spec
    ...     a.scenario = lambda: {'class_name': 'ToyScenario', 'output_path': 'tmp/a'}
    ...     a.scenario_state = lambda: {'request_states': request_states}
    ...     a.stats = lambda: stats
    ...     return a
    >>> rs = [{'instance': {'id': 'id1', 'split': 'test', 'input': {'text': 'Q'}}, 'train_trial_index': 0, 'request': {'prompt': 'P'}, 'result': {'completions': [{'text': 'A'}]}}]
    >>> stats_a = [{'name': {'name': 'exact_match', 'split': 'test'}, 'count': 1, 'mean': 1.0}]
    >>> stats_b = [{'name': {'name': 'exact_match', 'split': 'test'}, 'count': 1, 'mean': 0.0}]
    >>> spec_a = {'name': 'toy', 'adapter_spec': {'model': 'm'}, 'metric_specs': [{'class_name': 'M0', 'args': {}}]}
    >>> spec_b = {'name': 'toy', 'adapter_spec': {'model': 'm', 'model_deployment': 'huggingface/m'}, 'metric_specs': [{'class_name': 'M1', 'args': {}}]}
    >>> rd = HelmRunDiff(_ana(spec_a, stats_a, rs), _ana(spec_b, stats_b, rs), a_name='A', b_name='B')
    >>> info = rd.summary_dict(level=20)
    >>> assert info['run_spec_name_ok'] is True
    >>> assert info['dataset_overlap']['base_iou'] == 1.0
    >>> # R-2: run/instance value agreement is no longer exposed on the
    >>> # summary dict; the value drift (1.0 vs 0.0) instead surfaces in the
    >>> # diagnosis, which still consumes it internally.
    >>> assert isinstance(info['diagnosis']['label'], str)
    >>> assert info['diagnosis']['label'] != 'reproduced'
    >>> _ = json.dumps(info, allow_nan=False)

"""

from __future__ import annotations

import ubelt as ub

from eval_audit.utils import hashers as helm_hashers
from eval_audit.helm.analysis import HelmRunAnalysis
from typing import Any

from eval_audit.infra.profiling import profile

# --- compat re-exports -------------------------------------------------
# Module-level diff primitives moved to eval_audit.helm.diff_primitives
# on 2026-06-11 (Phase 2 of docs/historical/planning/repo-refactor-plan.md). Tests
# import dataset_overlap_from_request_states from this module; keep
# re-exporting the moved names.
from eval_audit.helm.diff_primitives import (  # noqa: F401
    _format_bool,
    _walker_diff,
    _walker_diff_paths,
    _default_writer,
    _escape_rich,
    _sanitize_text,
    _smart_truncate,
    _short_urepr,
    _coerce_path_token,
    _path_get,
    _path_value_examples,
    _json_compatible,
    _preview_list,
    _RUNSPEC_EXEC_ADAPTER_NOISE_FIELDS,
    _classify_run_spec_path,
    _classify_scenario_path,
    _canonicalize_metric_spec_for_semantic_diff,
    _canonicalize_run_spec_for_semantic_diff,
    _metric_specs_multiset_delta,
    Coverage,
    _fmt,
    _key_to_serializable,
    dataset_overlap_from_request_states,
    ratio,
)


class HelmRunDiff(ub.NiceRepr):
    """Compare two HELM runs.

    Parameters
    ----------
    run_a, run_b:
        Either :class:`HelmRunAnalysis` or a ``HelmRun`` reader (coerced).
    a_name, b_name:
        Human-friendly labels for reports.
    short_hash:
        Controls readability of hashed ids used in stat keys.
    """

    @profile
    def __init__(
        self,
        run_a,
        run_b,
        *,
        a_name: str = 'A',
        b_name: str = 'B',
        short_hash: int = 16,
    ):
        self.a = (
            run_a
            if isinstance(run_a, HelmRunAnalysis)
            else HelmRunAnalysis(run_a, name=a_name)
        )
        self.b = (
            run_b
            if isinstance(run_b, HelmRunAnalysis)
            else HelmRunAnalysis(run_b, name=b_name)
        )
        self.a_name = a_name
        self.b_name = b_name
        self.short_hash = short_hash
        self._cache: dict[Any, Any] = {}

    def __nice__(self):
        return f'{self.a_name} vs {self.b_name}'

    # ---------------------------------------------------------------------
    # Base summaries

    @profile
    def summary_dict(self, *, level: int = 10) -> dict[str, Any]:
        """Programmatic run-to-run summary.

        This is meant to be stable enough to power Sankey bucketing and
        higher-level dashboards.

        Key fields
        ----------
        run_spec_name_ok:
            Whether ``run_spec['name']`` matches.
        run_spec_dict_ok:
            Whether the entire run_spec.json matches (hash equality).
        scenario_ok:
            True/False if both scenario.json exist and match (hash equality),
            None if scenario is missing in one/both runs.
        stats_coverage_by_name:
            Coverage of stat names only (ignores count/values).
        stats_coverage_by_name_count:
            Coverage of stat name + count (still ignores values).
        value_agreement:
            Mean agreement on intersecting run-level stats, split by
            metric class (core/bookkeeping/untracked).

        Notes
        -----
        ``level`` mainly controls optional extras. For now, the dict always
        includes the L1 checks above.
        """
        cache_key = ('summary_dict', level)
        if cache_key in self._cache:
            return self._cache[cache_key]

        a_spec = self.a.run_spec() or {}
        b_spec = self.b.run_spec() or {}
        a_scen = self.a.scenario() or {}
        b_scen = self.b.scenario() or {}

        # 1) run spec name
        a_run_name = a_spec.get('name', None)
        b_run_name = b_spec.get('name', None)
        run_spec_name_ok = (a_run_name == b_run_name) and (
            a_run_name is not None
        )

        # 2) run spec dict hash (strict)
        spec_hash_a = helm_hashers.stable_hash36(
            helm_hashers.canonicalize_for_hashing(a_spec)
        )
        spec_hash_b = helm_hashers.stable_hash36(
            helm_hashers.canonicalize_for_hashing(b_spec)
        )
        run_spec_dict_ok = spec_hash_a == spec_hash_b
        if run_spec_dict_ok:
            spec_path_info: dict[str, list[str]] = {
                'unique1': [],
                'unique2': [],
                'faillist': [],
            }
        else:
            spec_path_info = _walker_diff_paths(a_spec, b_spec)
        if level == 0:
            spec_diff_paths = None
        else:
            spec_diff_paths = (
                {} if run_spec_dict_ok else _walker_diff(a_spec, b_spec)
            )

        # 2b) run spec semantic hash (order-insensitive for metric lists)
        a_spec_sem = _canonicalize_run_spec_for_semantic_diff(a_spec)
        b_spec_sem = _canonicalize_run_spec_for_semantic_diff(b_spec)
        spec_sem_hash_a = helm_hashers.stable_hash36(a_spec_sem)
        spec_sem_hash_b = helm_hashers.stable_hash36(b_spec_sem)
        run_spec_semantic_dict_ok = spec_sem_hash_a == spec_sem_hash_b
        if run_spec_semantic_dict_ok:
            spec_sem_path_info: dict[str, list[str]] = {
                'unique1': [],
                'unique2': [],
                'faillist': [],
            }
        else:
            spec_sem_path_info = _walker_diff_paths(a_spec_sem, b_spec_sem)
        if level == 0:
            spec_sem_diff_paths = None
        else:
            spec_sem_diff_paths = (
                {}
                if run_spec_semantic_dict_ok
                else _walker_diff(a_spec_sem, b_spec_sem)
            )
        run_spec_semantic = self._run_spec_semantic_summary(
            path_info=spec_sem_path_info,
            a_spec=a_spec,
            b_spec=b_spec,
        )

        # 3) scenario check with unknown semantics
        scen_known = bool(a_scen) and bool(b_scen)
        if not scen_known:
            scenario_ok: bool | None = None
            scenario_hash_a = None
            scenario_hash_b = None
            scen_diff_paths = []
            scen_path_info: dict[str, list[str]] | None = None
        else:
            scenario_hash_a = helm_hashers.stable_hash36(
                helm_hashers.canonicalize_for_hashing(a_scen)
            )
            scenario_hash_b = helm_hashers.stable_hash36(
                helm_hashers.canonicalize_for_hashing(b_scen)
            )
            scenario_ok = scenario_hash_a == scenario_hash_b
            if scenario_ok:
                scen_path_info = {'unique1': [], 'unique2': [], 'faillist': []}
            else:
                scen_path_info = _walker_diff_paths(a_scen, b_scen)
            if level == 0:
                scen_diff_paths = None
            else:
                scen_diff_paths = (
                    {} if scenario_ok else _walker_diff(a_scen, b_scen)
                )
        scenario_semantic = self._scenario_semantic_summary(
            scenario_ok=scenario_ok, path_info=scen_path_info
        )

        # 4/5) stats coverage
        a_stats = self.a.stats() or []
        b_stats = self.b.stats() or []
        a_name_keys = {
            helm_hashers.stat_key(
                s.get('name', None), short_hash=self.short_hash
            )
            for s in a_stats
        }
        b_name_keys = {
            helm_hashers.stat_key(
                s.get('name', None), short_hash=self.short_hash
            )
            for s in b_stats
        }
        cov_name = Coverage.from_sets(a_name_keys, b_name_keys)

        a_name_count_keys = {
            helm_hashers.stat_key(
                s.get('name', None),
                count=s.get('count', None),
                short_hash=self.short_hash,
            )
            for s in a_stats
        }
        b_name_count_keys = {
            helm_hashers.stat_key(
                s.get('name', None),
                count=s.get('count', None),
                short_hash=self.short_hash,
            )
            for s in b_stats
        }
        cov_name_count = Coverage.from_sets(
            a_name_count_keys, b_name_count_keys
        )

        # 6) value agreement (means) on intersecting keys.
        # R-2 (2026-07-06): this run-level agreement is computed ONLY as the
        # value-drift input to the diagnosis below; it is no longer exposed in
        # the output dict (the reported agreement surface moved to
        # NormalizedDiff / the core_metric_report). Do not re-add it to ``out``.
        value_summary = self._value_agreement_summary()
        dataset_summary: dict[str, Any] | None = None
        if level >= 5:
            try:
                dataset_summary = self.dataset_overlap_summary(max_examples=5)
            except Exception as ex:  # nocover
                dataset_summary = {'error': repr(ex)}
        diagnosis = self._diagnose_repro(
            run_spec_name_ok=run_spec_name_ok,
            run_spec_semantic=run_spec_semantic,
            scenario_semantic=scenario_semantic,
            dataset_overlap=dataset_summary,
            value_summary=value_summary,
        )

        out: dict[str, Any] = {
            'a': self._lite_run_dict(self.a),
            'b': self._lite_run_dict(self.b),
            'run_spec_name_ok': run_spec_name_ok,
            'run_spec_name_a': a_run_name,
            'run_spec_name_b': b_run_name,
            'run_spec_dict_ok': run_spec_dict_ok,
            'run_spec_hash_a': spec_hash_a,
            'run_spec_hash_b': spec_hash_b,
            'run_spec_diff_paths': spec_diff_paths,
            'run_spec_semantic_dict_ok': run_spec_semantic_dict_ok,
            'run_spec_semantic_hash_a': spec_sem_hash_a,
            'run_spec_semantic_hash_b': spec_sem_hash_b,
            'run_spec_diff_paths_semantic': spec_sem_diff_paths,
            'run_spec_semantic': run_spec_semantic,
            'scenario_ok': scenario_ok,
            'scenario_hash_a': scenario_hash_a,
            'scenario_hash_b': scenario_hash_b,
            'scenario_diff_paths': scen_diff_paths,
            'scenario_semantic': scenario_semantic,
            'stats_coverage_by_name': cov_name.__dict__,
            'stats_coverage_by_name_count': cov_name_count.__dict__,
            'dataset_overlap': dataset_summary,
            'diagnosis': diagnosis,
        }

        # R-2 (2026-07-06): the level>=20 instance-level value-agreement block
        # was retired along with instance_summary_dict; per-instance agreement
        # is served by NormalizedDiff / the core_metric_report. ``level`` still
        # gates the optional spec-diff-path detail computed above.

        out = _json_compatible(out)
        self._cache[cache_key] = out
        return out

    def _lite_run_dict(self, ana: HelmRunAnalysis) -> dict[str, Any]:
        """Best-effort stable per-run dict used in diff summaries."""
        if hasattr(ana, 'summary_dict'):
            try:
                return ana.summary_dict(level=0)  # type: ignore
            except Exception:
                pass
        if hasattr(ana, 'summary_lite'):
            try:
                return ana.summary_lite()  # type: ignore
            except Exception:
                pass
        spec = ana.run_spec() or {}
        return {'run_spec_name': spec.get('name', None)}

    def _run_spec_semantic_summary(
        self,
        *,
        path_info: dict[str, list[str]],
        a_spec: dict[str, Any],
        b_spec: dict[str, Any],
    ) -> dict[str, Any]:
        """Classify run-spec differences into semantic buckets."""
        all_paths = sorted(
            set(path_info.get('unique1', []))
            | set(path_info.get('unique2', []))
            | set(path_info.get('faillist', []))
        )
        by_class: dict[str, list[str]] = {
            'execution': [],
            'evaluation': [],
            'nonsemantic': [],
            'other': [],
        }
        for p in all_paths:
            by_class[_classify_run_spec_path(p)].append(p)

        deployment_paths = [
            p
            for p in all_paths
            if p.startswith('adapter_spec.model_deployment')
        ]
        deployment_a = (
            (a_spec.get('adapter_spec', {}) or {}).get(
                'model_deployment', None
            )
            if isinstance(a_spec, dict)
            else None
        )
        deployment_b = (
            (b_spec.get('adapter_spec', {}) or {}).get(
                'model_deployment', None
            )
            if isinstance(b_spec, dict)
            else None
        )
        deployment_changed = (deployment_a != deployment_b) or any(
            p.startswith('adapter_spec.model_deployment') for p in all_paths
        )
        metric_specs_delta = _metric_specs_multiset_delta(
            (a_spec or {}).get('metric_specs', None),
            (b_spec or {}).get('metric_specs', None),
            short_hash=self.short_hash,
            max_items=20,
        )
        evaluation_changed = bool(by_class['evaluation']) or (
            not bool(metric_specs_delta.get('equal_as_multiset', True))
        )
        execution_ok = len(by_class['execution']) == 0
        evaluation_only = (
            (len(all_paths) > 0)
            and execution_ok
            and (evaluation_changed or len(by_class['nonsemantic']) > 0)
        )
        return _json_compatible(
            {
                'n_total_paths': len(all_paths),
                'execution_ok': execution_ok,
                'evaluation_only': evaluation_only,
                'evaluation_changed': evaluation_changed,
                'deployment_changed': deployment_changed,
                'deployment': {
                    'a': deployment_a,
                    'b': deployment_b,
                    'changed': deployment_changed,
                },
                'counts': {
                    k: len(v)
                    for k, v in by_class.items()
                },
                'deployment_paths': _preview_list(deployment_paths, limit=20),
                'execution_paths': _preview_list(
                    by_class['execution'], limit=20
                ),
                'execution_value_examples': _path_value_examples(
                    a_spec, b_spec, by_class['execution'], max_items=20
                ),
                'evaluation_paths': _preview_list(
                    by_class['evaluation'], limit=20
                ),
                'metric_specs_multiset_delta': metric_specs_delta,
                'nonsemantic_paths': _preview_list(
                    by_class['nonsemantic'], limit=20
                ),
                'other_paths': _preview_list(by_class['other'], limit=20),
            }
        )

    def _scenario_semantic_summary(
        self,
        *,
        scenario_ok: bool | None,
        path_info: dict[str, list[str]] | None,
    ) -> dict[str, Any]:
        """Classify scenario differences into semantic/nonsemantic buckets."""
        if scenario_ok is None:
            return {
                'known': False,
                'strict_ok': None,
                'semantic_ok': None,
                'counts': {'semantic': 0, 'nonsemantic': 0},
                'semantic_paths': [],
                'nonsemantic_paths': [],
            }

        path_info = path_info or {'unique1': [], 'unique2': [], 'faillist': []}
        all_paths = sorted(
            set(path_info.get('unique1', []))
            | set(path_info.get('unique2', []))
            | set(path_info.get('faillist', []))
        )
        semantic_paths = [
            p for p in all_paths if _classify_scenario_path(p) == 'semantic'
        ]
        nonsemantic_paths = [
            p for p in all_paths if _classify_scenario_path(p) == 'nonsemantic'
        ]
        semantic_ok = bool(scenario_ok) or (len(semantic_paths) == 0)
        return _json_compatible(
            {
                'known': True,
                'strict_ok': bool(scenario_ok),
                'semantic_ok': semantic_ok,
                'counts': {
                    'semantic': len(semantic_paths),
                    'nonsemantic': len(nonsemantic_paths),
                },
                'semantic_paths': _preview_list(semantic_paths, limit=20),
                'nonsemantic_paths': _preview_list(
                    nonsemantic_paths, limit=20
                ),
            }
        )

    @profile
    def dataset_overlap_summary(self, *, max_examples: int = 5) -> dict[str, Any]:
        """Compare scenario_state request datasets between runs.

        Example:
            >>> from eval_audit.helm.analysis import HelmRunAnalysis
            >>> ana = HelmRunAnalysis.__new__(HelmRunAnalysis)
            >>> ana._raw_cache = {}
            >>> ana._cache = {}
            >>> ana.run = None
            >>> ana.name = None
            >>> ana.scenario_state = lambda: {'request_states': [
            ...     {'instance': {'id': 'id1', 'split': 'test', 'input': {'text': 'Q1'}},
            ...      'train_trial_index': 0,
            ...      'request': {'prompt': 'P1'},
            ...      'result': {'completions': [{'text': 'A1'}]}},
            ... ]}
            >>> rd = HelmRunDiff(ana, ana)
            >>> ds = rd.dataset_overlap_summary(max_examples=2)
            >>> assert ds['base_iou'] == 1.0
            >>> assert ds['variant_iou'] == 1.0
            >>> assert ds['content_equality']['input']['equal_ratio'] == 1.0
        """
        cache_key = ('dataset_overlap_summary', max_examples, self.short_hash)
        if cache_key in self._cache:
            return self._cache[cache_key]
        rs_a = (self.a.scenario_state() or {}).get('request_states', []) or []
        rs_b = (self.b.scenario_state() or {}).get('request_states', []) or []
        out = dataset_overlap_from_request_states(
            rs_a,
            rs_b,
            short_hash=self.short_hash,
            max_examples=max_examples,
        )
        out = _json_compatible(out)
        self._cache[cache_key] = out
        return out

    def _diagnose_repro(
        self,
        *,
        run_spec_name_ok: bool,
        run_spec_semantic: dict[str, Any],
        scenario_semantic: dict[str, Any],
        dataset_overlap: dict[str, Any] | None,
        value_summary: dict[str, Any],
    ) -> dict[str, Any]:
        """High-level diagnosis for reproducibility triage.

        Returns a primary label plus a full list of contributing reasons.
        Lower ``priority`` is earlier / more significant in the pipeline.

        Phase 3 / 4.6: delegates to the single implementation in
        :func:`eval_audit.normalized.diagnose.diagnose_repro` (ported
        there in 4.2 and proven byte-identical by
        ``tests/test_phase3_diagnose_equivalence.py`` while both copies
        existed). The HELM-grade *inputs* — run_spec/scenario semantic
        diff, dataset overlap — are still computed here from raw HELM
        artifacts; only the input-to-label logic is shared.
        """
        from eval_audit.normalized.diagnose import diagnose_repro

        return diagnose_repro(
            run_spec_name_ok=run_spec_name_ok,
            run_spec_semantic=run_spec_semantic,
            scenario_semantic=scenario_semantic,
            dataset_overlap=dataset_overlap,
            value_summary=value_summary,
        )

    # ---------------------------------------------------------------------
    # Run-level mean agreement

    @profile
    def _value_agreement_summary(
        self,
        *,
        abs_tol: float = 0.0,
        rel_tol: float = 0.0,
        top_n: int = 12,
    ) -> dict[str, Any]:
        """Compare mean values for intersecting run-level stats."""
        cache_key = (
            'value_agreement',
            abs_tol,
            rel_tol,
            top_n,
            self.short_hash,
        )
        if cache_key in self._cache:
            return self._cache[cache_key]

        idx_a = self.a.stat_index(
            drop_zero_count=True, require_mean=True, short_hash=self.short_hash
        )
        idx_b = self.b.stat_index(
            drop_zero_count=True, require_mean=True, short_hash=self.short_hash
        )
        keys = set(idx_a.keys()) & set(idx_b.keys())

        def agrees(x: float, y: float) -> bool:
            if abs_tol == 0.0 and rel_tol == 0.0:
                return x == y
            return abs(x - y) <= max(abs_tol, rel_tol * max(abs(x), abs(y)))

        by_class = {
            'core': {'comparable': 0, 'mismatched': 0},
            'bookkeeping': {'comparable': 0, 'mismatched': 0},
            'untracked': {'comparable': 0, 'mismatched': 0},
        }

        mismatches: list[dict[str, Any]] = []
        comparable = 0
        mismatched = 0
        for k in keys:
            a = idx_a[k]
            b = idx_b[k]
            if a.mean is None or b.mean is None:
                continue
            comparable += 1
            cls = a.metric_class
            by_class[cls]['comparable'] += 1
            if not agrees(a.mean, b.mean):
                mismatched += 1
                by_class[cls]['mismatched'] += 1
                mismatches.append(
                    {
                        'key': k,
                        'a': a.mean,
                        'b': b.mean,
                        'abs_delta': abs(a.mean - b.mean),
                    }
                )

        # P1-18: break abs_delta ties on the serialized key so the top-N is
        # deterministic (abs_delta==1.0 is the common case for 0/1 metrics, and
        # the source rows come from set iteration = PYTHONHASHSEED-dependent).
        mismatches.sort(key=lambda r: (-float(r['abs_delta']), str(r.get('key'))))
        top = mismatches[:top_n]

        out = {
            'overall': {
                'comparable': comparable,
                'mismatched': mismatched,
                'agree_ratio': ratio(comparable, mismatched),
            },
            'by_class': {
                k: {
                    'comparable': v['comparable'],
                    'mismatched': v['mismatched'],
                    'agree_ratio': ratio(v['comparable'], v['mismatched']),
                }
                for k, v in by_class.items()
            },
            'top_mismatches': top,
        }

        out = _json_compatible(out)
        self._cache[cache_key] = out
        return out

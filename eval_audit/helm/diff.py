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
    >>> class _DummyJoined:
    ...     def __init__(self):
    ...         self.row_by_key = {}
    ...     def __iter__(self):
    ...         return iter(self.row_by_key.values())
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
    ...     a.joined_instance_stat_table = lambda *args, **kwargs: _DummyJoined()
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
    >>> assert info['value_agreement']['overall']['mismatched'] == 1
    >>> assert isinstance(info['diagnosis']['label'], str)
    >>> _ = json.dumps(info, allow_nan=False)

"""

from __future__ import annotations

import math
import ubelt as ub

from collections import Counter
from dataclasses import dataclass
from eval_audit.helm import hashers as helm_hashers
from eval_audit.helm import metrics as helm_metrics
from eval_audit.helm.analysis import HelmRunAnalysis
from eval_audit.utils.numeric import safe_float as _safe_float, quantile as _quantile
from typing import Any, Callable, Iterable

from eval_audit.infra.profiling import profile

# --- compat re-exports -------------------------------------------------
# Module-level diff primitives moved to eval_audit.helm.diff_primitives
# on 2026-06-11 (Phase 2 of docs/planning/repo-refactor-plan.md). Tests
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

        # 6) value agreement (means) on intersecting keys
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
            'value_agreement': value_summary,
            'dataset_overlap': dataset_summary,
            'diagnosis': diagnosis,
        }

        if level >= 20:
            # Optional: include instance-level summary in the dict.
            try:
                out['instance_value_agreement'] = self.instance_summary_dict(
                    top_n=10
                )
            except Exception as ex:  # nocover
                out['instance_value_agreement'] = {'error': repr(ex)}

        out = _json_compatible(out)
        self._cache[cache_key] = out
        return out

    def summary_text(self, *, level: int = 0) -> str:
        """Return a text summary (built by calling :meth:`summary`)."""
        lines: list[str] = []
        self.summary(level=level, writer=lines.append)
        return '\n'.join(lines).rstrip()

    def summary(self, *, level: int = 10, writer=None) -> None:
        """Writer-style diff report.

        Levels
        ------
        * level <= 0: single line
        * level >= 10: one page
        * level >= 20: include top mismatches
        * level >= 30: include instance-level summary headline
        """
        writer = _default_writer(writer)
        info = self.summary_dict(level=level)

        ok = info['run_spec_dict_ok'] and (info['scenario_ok'] in {True, None})
        cov = info['stats_coverage_by_name']
        agree = info['value_agreement']['overall']['agree_ratio']

        if level <= 0:
            spec_name_a = info['run_spec_name_a']
            spec_name_b = info['run_spec_name_b']
            if spec_name_a == spec_name_b:
                line_name = spec_name_a
            else:
                line_name = '{spec_name_a} // {spec_name_b}'
            writer(
                f'{_format_bool(ok)} {self.a_name} vs {self.b_name} {line_name} '
                f'spec={_format_bool(info["run_spec_dict_ok"])} '
                f'stats={cov["n_isect"]}/{cov["n_union"]} '
                f'agree={_fmt(agree)}'
            )

        if level > 0:
            writer(f'HelmRunDiff: {self.a_name} vs {self.b_name}')

            # Side-by-side lite
            writer(
                f'  {self.a_name}: {self._analysis_summary_line(self.a, level=0)}'
            )
            writer(
                f'  {self.b_name}: {self._analysis_summary_line(self.b, level=0)}'
            )

            writer('')
            writer(
                f'Run spec name: {_format_bool(info["run_spec_name_ok"])}  '
                f'{info["run_spec_name_a"]}  vs  {info["run_spec_name_b"]}'
            )
            writer(
                f'Run spec dict: {_format_bool(info["run_spec_dict_ok"])}  '
                f'hashA={str(info["run_spec_hash_a"])[:10]}  hashB={str(info["run_spec_hash_b"])[:10]}'
            )

            if level >= 15:
                if not info.get('run_spec_semantic_dict_ok', True):
                    writer(
                        f'  semantic diff: {ub.urepr(info["run_spec_diff_paths_semantic"])}'
                    )
                if (not info['run_spec_dict_ok']) and level >= 20:
                    writer(
                        f'  strict diff: {ub.urepr(info["run_spec_diff_paths"])}'
                    )
                rs_sem = info.get('run_spec_semantic', {}) or {}
                dep = rs_sem.get('deployment', {}) or {}
                if dep.get('changed', False):
                    writer(
                        f'  deployment: A={_short_urepr(dep.get("a", None), 80)} '
                        f'B={_short_urepr(dep.get("b", None), 80)}'
                    )

            if info['scenario_ok'] is None:
                writer(
                    'Scenario: ⚠️  unknown (missing scenario.json in one or both runs)'
                )
            else:
                if level >= 15:
                    writer(
                        f'Scenario: {_format_bool(bool(info["scenario_ok"]))}'
                    )
                    if info['scenario_ok'] is False:
                        writer(
                            f'  diff: {ub.urepr(info["scenario_diff_paths"])}'
                        )
            scen_sem = info.get('scenario_semantic', {}) or {}
            if scen_sem.get('known', False) and level >= 15:
                writer(
                    f'Scenario semantic: {_format_bool(bool(scen_sem.get("semantic_ok", False)))}'
                )

            writer('')
            cov2 = info['stats_coverage_by_name_count']
            writer('Stats coverage:')
            writer(
                f'  by name:       A={cov["n_a"]} B={cov["n_b"]} '
                f'isect={cov["n_isect"]} union={cov["n_union"]} onlyA={cov["only_a"]} onlyB={cov["only_b"]}'
            )
            writer(
                f'  by name+count: A={cov2["n_a"]} B={cov2["n_b"]} '
                f'isect={cov2["n_isect"]} union={cov2["n_union"]} onlyA={cov2["only_a"]} onlyB={cov2["only_b"]}'
            )

            writer('')
            writer('Value agreement (mean on intersecting run-level stats):')
            ov = info['value_agreement']['overall']
            writer(
                f'  overall: comparable={ov["comparable"]} mismatched={ov["mismatched"]} '
                f'agree_ratio={_fmt(ov["agree_ratio"])}'
            )
            for cls in ('core', 'bookkeeping', 'untracked'):
                s = info['value_agreement']['by_class'][cls]
                writer(
                    f'  {cls:11s}: comparable={s["comparable"]} mismatched={s["mismatched"]} '
                    f'agree_ratio={_fmt(s["agree_ratio"])}'
                )

            if level >= 20:
                top = info['value_agreement'].get('top_mismatches', [])
                if top:
                    writer('  top mismatches:')
                    for r in top:
                        writer(
                            f'    {r["key"]}  A={_fmt(r["a"])}  B={_fmt(r["b"])}  |Δ|={_fmt(r["abs_delta"])}'
                        )

            if level >= 15:
                ds = info.get('dataset_overlap', None)
                if isinstance(ds, dict) and 'error' not in ds:
                    writer('')
                    writer('Dataset overlap:')
                    writer(
                        f'  base_iou={_fmt(ds.get("base_iou"))} '
                        f'variant_iou={_fmt(ds.get("variant_iou"))}'
                    )
                    ce = ds.get('content_equality', {}) or {}
                    for field in ('input', 'prompt', 'completion'):
                        row = ce.get(field, {}) or {}
                        writer(
                            f'  {field:10s}: comparable={row.get("comparable")} '
                            f'mismatched={row.get("mismatched")} equal_ratio={_fmt(row.get("equal_ratio"))}'
                        )
                elif isinstance(ds, dict) and 'error' in ds:
                    writer(f'Dataset overlap: ⚠️  {ds["error"]}')

            if level >= 10:
                diag = info.get('diagnosis', {}) or {}
                writer('')
                writer(f'Diagnosis: {diag.get("label", "unknown")}')
                if level >= 20:
                    reasons = diag.get('reasons', [])
                    if reasons:
                        writer(f'  reasons: {ub.urepr(reasons, nl=0)}')

            if level >= 30:
                writer('')
                try:
                    inst = self.instance_summary_dict(top_n=5)
                except Exception as ex:
                    writer(f'Instance-level diff: ⚠️  unable to compute: {ex!r}')
                else:
                    means = inst['means']
                    writer(
                        f'Instance-level means: comparable={means["comparable"]} mismatched={means["mismatched"]} '
                        f'agree={_fmt(means["agree_ratio"])} (unpert={_fmt(means["agree_ratio_unperturbed"])}, '
                        f'pert={_fmt(means["agree_ratio_perturbed"])})'
                    )

    def _analysis_summary_line(
        self, ana: HelmRunAnalysis, *, level: int = 0
    ) -> str:
        """Best-effort one-liner per-run summary for side-by-side views."""
        if hasattr(ana, 'summary_text'):
            try:
                return ana.summary_text(level=level)  # type: ignore
            except Exception:
                pass
        if hasattr(ana, 'summary'):
            try:
                lines: list[str] = []
                ana.summary(level=level, writer=lines.append)  # type: ignore
                return ' '.join([ln.strip() for ln in lines if ln.strip()])
            except Exception:
                pass
        d = self._lite_run_dict(ana)
        name = d.get('run_spec_name', None)
        return str(name)

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

        mismatches.sort(key=lambda r: r['abs_delta'], reverse=True)
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

    @profile
    def value_distance_profile(
        self,
        *,
        top_n: int = 12,
    ) -> dict[str, Any]:
        """Programmatic raw distance summary for intersecting run-level stats.

        Unlike :meth:`_value_agreement_summary`, this does not threshold values
        into matched / mismatched. It reports absolute / relative deltas and
        their distributions so tolerance policies can be applied later without
        recomputing the underlying joins.
        """
        cache_key = ('value_distance_profile', top_n, self.short_hash)
        if cache_key in self._cache:
            return self._cache[cache_key]

        idx_a = self.a.stat_index(
            drop_zero_count=True, require_mean=True, short_hash=self.short_hash
        )
        idx_b = self.b.stat_index(
            drop_zero_count=True, require_mean=True, short_hash=self.short_hash
        )
        keys = set(idx_a.keys()) & set(idx_b.keys())

        by_class: dict[str, list[dict[str, Any]]] = {
            'core': [],
            'bookkeeping': [],
            'untracked': [],
        }
        all_rows: list[dict[str, Any]] = []
        for k in keys:
            a = idx_a[k]
            b = idx_b[k]
            if a.mean is None or b.mean is None:
                continue
            abs_delta = abs(a.mean - b.mean)
            denom = max(abs(a.mean), abs(b.mean), 1e-12)
            rel_delta = abs_delta / denom
            row = {
                'key': k,
                'a': a.mean,
                'b': b.mean,
                'abs_delta': abs_delta,
                'rel_delta': rel_delta,
                'metric_class': a.metric_class,
            }
            all_rows.append(row)
            by_class.setdefault(a.metric_class, []).append(row)

        def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
            if not rows:
                return {
                    'count': 0,
                    'abs_delta': {'min': None, 'p50': None, 'p90': None, 'p99': None, 'max': None},
                    'rel_delta': {'min': None, 'p50': None, 'p90': None, 'p99': None, 'max': None},
                    'top_abs_deltas': [],
                }
            abs_vals = sorted(float(r['abs_delta']) for r in rows)
            rel_vals = sorted(float(r['rel_delta']) for r in rows)
            top = sorted(rows, key=lambda r: r['abs_delta'], reverse=True)[:top_n]
            return {
                'count': len(rows),
                'abs_delta': {
                    'min': abs_vals[0],
                    'p50': _quantile(abs_vals, 0.50),
                    'p90': _quantile(abs_vals, 0.90),
                    'p99': _quantile(abs_vals, 0.99),
                    'max': abs_vals[-1],
                },
                'rel_delta': {
                    'min': rel_vals[0],
                    'p50': _quantile(rel_vals, 0.50),
                    'p90': _quantile(rel_vals, 0.90),
                    'p99': _quantile(rel_vals, 0.99),
                    'max': rel_vals[-1],
                },
                'top_abs_deltas': top,
            }

        out = {
            'overall': summarize(all_rows),
            'by_class': {cls: summarize(rows) for cls, rows in by_class.items()},
        }
        out = _json_compatible(out)
        self._cache[cache_key] = out
        return out

    # ---------------------------------------------------------------------
    # Instance-level agreement / drilldowns

    @profile
    def instance_summary_dict(
        self,
        *,
        top_n: int = 10,
        abs_tol: float = 0.0,
        rel_tol: float = 0.0,
    ) -> dict[str, Any]:
        """Programmatic summary of per-instance stat agreement.

        This summarizes agreement on *mean* for joined per-instance stats.

        Returns
        -------
        dict with keys:

        * coverage: overlap on joined-row keys
        * means:
            - comparable: number of comparable rows (mean present in both)
            - mismatched: number of rows failing tolerance check
            - agree_ratio: 1 - mismatched / comparable
            - agree_ratio_unperturbed / agree_ratio_perturbed
        * top_mismatches_by_group:
            List of objects; each element has fields ``metric_class`` and
            ``metric`` and an ``items`` list of mismatches (sorted by |Δ|).
            Each mismatch item contains: key, a, b, abs_delta, signed_delta.

        Notes
        -----
        * Metric class is computed via :func:`classify_metric`.
        * "Perturbed" is determined by whether the joined key contains a
          non-None perturbation id / perturbation descriptor.
        """
        cache_key = (
            'instance_summary_dict',
            top_n,
            abs_tol,
            rel_tol,
            self.short_hash,
        )
        if cache_key in self._cache:
            return self._cache[cache_key]

        joined_a = self.a.joined_instance_stat_table(
            assert_assumptions=False, short_hash=self.short_hash
        )
        joined_b = self.b.joined_instance_stat_table(
            assert_assumptions=False, short_hash=self.short_hash
        )

        # Try to use the table's own key->row mapping if present
        map_a = getattr(joined_a, 'row_by_key', None)
        map_b = getattr(joined_b, 'row_by_key', None)

        def _iter_rows(joined) -> Iterable[Any]:
            if map_a is not None and joined is joined_a:
                return map_a.values()
            if map_b is not None and joined is joined_b:
                return map_b.values()
            if isinstance(joined, dict):
                return joined.values()
            if hasattr(joined, '__iter__'):
                return joined
            return []

        # Fallback: build row maps from iteration
        def _row_key(row: Any) -> Any:
            return (
                getattr(row, 'key', None)
                or getattr(row, 'stat_key', None)
                or getattr(row, 'row_key', None)
                or row
            )

        if map_a is None:
            map_a = {_row_key(r): r for r in _iter_rows(joined_a)}
        if map_b is None:
            map_b = {_row_key(r): r for r in _iter_rows(joined_b)}

        set_a = set(map_a)
        set_b = set(map_b)
        cov = Coverage.from_sets(set_a, set_b)

        def agrees(x: float, y: float) -> bool:
            if abs_tol == 0.0 and rel_tol == 0.0:
                return x == y
            return abs(x - y) <= max(abs_tol, rel_tol * max(abs(x), abs(y)))

        comparable = 0
        mismatched = 0
        # overall perturbed/unperturbed bookkeeping
        var_stats = {
            'unperturbed': {'comparable': 0, 'mismatched': 0},
            'perturbed': {'comparable': 0, 'mismatched': 0},
        }

        # use a temporary map for bookkeeping, but the final result will be a
        # list of objects so the summary dict is JSON-serializable.
        grouped: dict[tuple[str, str | None], list[dict[str, Any]]] = {}

        for k in set_a & set_b:
            ra = map_a[k]
            rb = map_b[k]
            sa = (
                getattr(ra, 'stat', None)
                if hasattr(ra, 'stat')
                else (ra.get('stat', None) if isinstance(ra, dict) else None)
            )
            sb = (
                getattr(rb, 'stat', None)
                if hasattr(rb, 'stat')
                else (rb.get('stat', None) if isinstance(rb, dict) else None)
            )

            ma = _safe_float(
                (sa or {}).get('mean', None)
                if isinstance(sa, dict)
                else getattr(sa, 'mean', None)
            )
            mb = _safe_float(
                (sb or {}).get('mean', None)
                if isinstance(sb, dict)
                else getattr(sb, 'mean', None)
            )
            ca = (
                int((sa or {}).get('count', 0) or 0)
                if isinstance(sa, dict)
                else int(getattr(sa, 'count', 0) or 0)
            )
            cb = (
                int((sb or {}).get('count', 0) or 0)
                if isinstance(sb, dict)
                else int(getattr(sb, 'count', 0) or 0)
            )

            # Only compare mean-bearing rows with support
            if ma is None or mb is None:
                continue
            if ca == 0 or cb == 0:
                continue

            comparable += 1

            # Determine metric name
            name_obj = (
                (sa or {}).get('name', None)
                if isinstance(sa, dict)
                else getattr(sa, 'name_obj', None)
            )
            metric = (
                name_obj.get('name', None)
                if isinstance(name_obj, dict)
                else None
            )
            if metric is None and sa is not None and not isinstance(sa, dict):
                metric = getattr(sa, 'metric', None)
            metric_class, _ = helm_metrics.classify_metric(metric)
            gkey = (metric_class, metric)

            # Determine perturbed vs unperturbed. P0-6: the perturbation id is
            # nested under ``k.variant.perturbation_id`` (instance-level) or
            # carried as ``k.stat_perturbation_id`` (stat-level) — see
            # InstanceStatKey.is_perturbed. The old ``hasattr(k,
            # 'perturbation_id')`` was always False on the dataclass, so every
            # row silently bucketed 'unperturbed' and agree_ratio_perturbed was
            # always None.
            if hasattr(k, 'is_perturbed'):
                perturbed = bool(k.is_perturbed)
            elif isinstance(k, tuple) and len(k) >= 3:
                # historical tuple layout: (instance_id, tti, perturbation_id, ...)
                perturbed = k[2] is not None
            else:
                perturbed = False
            variant = 'perturbed' if perturbed else 'unperturbed'
            var_stats[variant]['comparable'] += 1

            if not agrees(ma, mb):
                mismatched += 1
                var_stats[variant]['mismatched'] += 1
                item = {
                    'key': _key_to_serializable(k),
                    'a': ma,
                    'b': mb,
                    'abs_delta': abs(ma - mb),
                    'signed_delta': (mb - ma),
                }
                grouped.setdefault(gkey, []).append(item)

        # Sort each group and cap
        for gk, items in grouped.items():
            items.sort(key=lambda r: r['abs_delta'], reverse=True)
            grouped[gk] = items[:top_n]

        means = {
            'comparable': comparable,
            'mismatched': mismatched,
            'agree_ratio': ratio(comparable, mismatched),
            'agree_ratio_unperturbed': ratio(
                var_stats['unperturbed']['comparable'],
                var_stats['unperturbed']['mismatched'],
            ),
            'agree_ratio_perturbed': ratio(
                var_stats['perturbed']['comparable'],
                var_stats['perturbed']['mismatched'],
            ),
        }

        # convert to a JSON-friendly structure: list of group objects
        group_list: list[dict[str, Any]] = []
        for (mclass, metric), items in grouped.items():
            group_list.append(
                {
                    'metric_class': mclass,
                    'metric': metric,
                    'items': items,
                }
            )
        out = {
            'coverage': cov.__dict__,
            'means': means,
            'top_mismatches_by_group': group_list,
        }

        out = _json_compatible(out)
        self._cache[cache_key] = out
        return out

    @profile
    def instance_distance_profile(
        self,
        *,
        top_n: int = 10,
    ) -> dict[str, Any]:
        """Programmatic raw distance summary for per-instance stat means."""
        cache_key = ('instance_distance_profile', top_n, self.short_hash)
        if cache_key in self._cache:
            return self._cache[cache_key]

        joined_a = self.a.joined_instance_stat_table(
            assert_assumptions=False, short_hash=self.short_hash
        )
        joined_b = self.b.joined_instance_stat_table(
            assert_assumptions=False, short_hash=self.short_hash
        )
        map_a = getattr(joined_a, 'row_by_key', None)
        map_b = getattr(joined_b, 'row_by_key', None)

        def _iter_rows(joined) -> Iterable[Any]:
            if map_a is not None and joined is joined_a:
                return map_a.values()
            if map_b is not None and joined is joined_b:
                return map_b.values()
            if isinstance(joined, dict):
                return joined.values()
            if hasattr(joined, '__iter__'):
                return joined
            return []

        def _row_key(row: Any) -> Any:
            return (
                getattr(row, 'key', None)
                or getattr(row, 'stat_key', None)
                or getattr(row, 'row_key', None)
                or row
            )

        if map_a is None:
            map_a = {_row_key(r): r for r in _iter_rows(joined_a)}
        if map_b is None:
            map_b = {_row_key(r): r for r in _iter_rows(joined_b)}

        all_rows: list[dict[str, Any]] = []
        by_group: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
        for k in set(map_a) & set(map_b):
            ra = map_a[k]
            rb = map_b[k]
            sa = (
                getattr(ra, 'stat', None)
                if hasattr(ra, 'stat')
                else (ra.get('stat', None) if isinstance(ra, dict) else None)
            )
            sb = (
                getattr(rb, 'stat', None)
                if hasattr(rb, 'stat')
                else (rb.get('stat', None) if isinstance(rb, dict) else None)
            )
            ma = _safe_float(
                (sa or {}).get('mean', None)
                if isinstance(sa, dict)
                else getattr(sa, 'mean', None)
            )
            mb = _safe_float(
                (sb or {}).get('mean', None)
                if isinstance(sb, dict)
                else getattr(sb, 'mean', None)
            )
            ca = (
                int((sa or {}).get('count', 0) or 0)
                if isinstance(sa, dict)
                else int(getattr(sa, 'count', 0) or 0)
            )
            cb = (
                int((sb or {}).get('count', 0) or 0)
                if isinstance(sb, dict)
                else int(getattr(sb, 'count', 0) or 0)
            )
            if ma is None or mb is None or ca == 0 or cb == 0:
                continue
            name_obj = (
                (sa or {}).get('name', None)
                if isinstance(sa, dict)
                else getattr(sa, 'name_obj', None)
            )
            metric = (
                name_obj.get('name', None)
                if isinstance(name_obj, dict)
                else None
            )
            if metric is None and sa is not None and not isinstance(sa, dict):
                metric = getattr(sa, 'metric', None)
            metric_class, _ = helm_metrics.classify_metric(metric)
            abs_delta = abs(ma - mb)
            denom = max(abs(ma), abs(mb), 1e-12)
            rel_delta = abs_delta / denom
            item = {
                'key': _key_to_serializable(k),
                'a': ma,
                'b': mb,
                'abs_delta': abs_delta,
                'rel_delta': rel_delta,
                'signed_delta': (mb - ma),
                'metric_class': metric_class,
                'metric': metric,
            }
            all_rows.append(item)
            by_group.setdefault((metric_class, metric), []).append(item)

        def summarize(items: list[dict[str, Any]]) -> dict[str, Any]:
            if not items:
                return {
                    'count': 0,
                    'abs_delta': {'min': None, 'p50': None, 'p90': None, 'p99': None, 'max': None},
                    'rel_delta': {'min': None, 'p50': None, 'p90': None, 'p99': None, 'max': None},
                }
            abs_vals = sorted(float(r['abs_delta']) for r in items)
            rel_vals = sorted(float(r['rel_delta']) for r in items)
            return {
                'count': len(items),
                'abs_delta': {
                    'min': abs_vals[0],
                    'p50': _quantile(abs_vals, 0.50),
                    'p90': _quantile(abs_vals, 0.90),
                    'p99': _quantile(abs_vals, 0.99),
                    'max': abs_vals[-1],
                },
                'rel_delta': {
                    'min': rel_vals[0],
                    'p50': _quantile(rel_vals, 0.50),
                    'p90': _quantile(rel_vals, 0.90),
                    'p99': _quantile(rel_vals, 0.99),
                    'max': rel_vals[-1],
                },
            }

        grouped_rows = []
        for (metric_class, metric), items in sorted(by_group.items()):
            items = sorted(items, key=lambda r: r['abs_delta'], reverse=True)
            grouped_rows.append(
                {
                    'metric_class': metric_class,
                    'metric': metric,
                    'summary': summarize(items),
                    'top_abs_deltas': items[:top_n],
                }
            )
        out = {
            'overall': summarize(all_rows),
            'by_metric': grouped_rows,
        }
        out = _json_compatible(out)
        self._cache[cache_key] = out
        return out

    @profile
    def instance_agreement_profile(
        self,
        *,
        abs_tol: float = 0.0,
        rel_tol: float = 0.0,
    ) -> dict[str, Any]:
        """Programmatic agreement summary grouped by per-instance metric."""
        cache_key = ('instance_agreement_profile', abs_tol, rel_tol, self.short_hash)
        if cache_key in self._cache:
            return self._cache[cache_key]

        joined_a = self.a.joined_instance_stat_table(
            assert_assumptions=False, short_hash=self.short_hash
        )
        joined_b = self.b.joined_instance_stat_table(
            assert_assumptions=False, short_hash=self.short_hash
        )
        map_a = getattr(joined_a, 'row_by_key', None)
        map_b = getattr(joined_b, 'row_by_key', None)

        def _iter_rows(joined) -> Iterable[Any]:
            if map_a is not None and joined is joined_a:
                return map_a.values()
            if map_b is not None and joined is joined_b:
                return map_b.values()
            if isinstance(joined, dict):
                return joined.values()
            if hasattr(joined, '__iter__'):
                return joined
            return []

        def _row_key(row: Any) -> Any:
            return (
                getattr(row, 'key', None)
                or getattr(row, 'stat_key', None)
                or getattr(row, 'row_key', None)
                or row
            )

        if map_a is None:
            map_a = {_row_key(r): r for r in _iter_rows(joined_a)}
        if map_b is None:
            map_b = {_row_key(r): r for r in _iter_rows(joined_b)}

        def agrees(x: float, y: float) -> bool:
            if abs_tol == 0.0 and rel_tol == 0.0:
                return x == y
            return abs(x - y) <= max(abs_tol, rel_tol * max(abs(x), abs(y)))

        overall = {'comparable': 0, 'mismatched': 0}
        by_group: dict[tuple[str, str | None], dict[str, Any]] = {}

        for k in set(map_a) & set(map_b):
            ra = map_a[k]
            rb = map_b[k]
            sa = (
                getattr(ra, 'stat', None)
                if hasattr(ra, 'stat')
                else (ra.get('stat', None) if isinstance(ra, dict) else None)
            )
            sb = (
                getattr(rb, 'stat', None)
                if hasattr(rb, 'stat')
                else (rb.get('stat', None) if isinstance(rb, dict) else None)
            )
            ma = _safe_float(
                (sa or {}).get('mean', None)
                if isinstance(sa, dict)
                else getattr(sa, 'mean', None)
            )
            mb = _safe_float(
                (sb or {}).get('mean', None)
                if isinstance(sb, dict)
                else getattr(sb, 'mean', None)
            )
            ca = (
                int((sa or {}).get('count', 0) or 0)
                if isinstance(sa, dict)
                else int(getattr(sa, 'count', 0) or 0)
            )
            cb = (
                int((sb or {}).get('count', 0) or 0)
                if isinstance(sb, dict)
                else int(getattr(sb, 'count', 0) or 0)
            )
            if ma is None or mb is None or ca == 0 or cb == 0:
                continue
            name_obj = (
                (sa or {}).get('name', None)
                if isinstance(sa, dict)
                else getattr(sa, 'name_obj', None)
            )
            metric = (
                name_obj.get('name', None)
                if isinstance(name_obj, dict)
                else None
            )
            if metric is None and sa is not None and not isinstance(sa, dict):
                metric = getattr(sa, 'metric', None)
            metric_class, _ = helm_metrics.classify_metric(metric)
            key = (metric_class, metric)
            group = by_group.setdefault(
                key,
                {
                    'metric_class': metric_class,
                    'metric': metric,
                    'comparable': 0,
                    'mismatched': 0,
                },
            )
            overall['comparable'] += 1
            group['comparable'] += 1
            if not agrees(ma, mb):
                overall['mismatched'] += 1
                group['mismatched'] += 1

        grouped_rows = []
        for _, group in sorted(by_group.items()):
            grouped_rows.append(
                {
                    'metric_class': group['metric_class'],
                    'metric': group['metric'],
                    'comparable': group['comparable'],
                    'mismatched': group['mismatched'],
                    'agree_ratio': ratio(group['comparable'], group['mismatched']),
                }
            )

        out = {
            'overall': {
                'comparable': overall['comparable'],
                'mismatched': overall['mismatched'],
                'agree_ratio': ratio(overall['comparable'], overall['mismatched']),
            },
            'by_metric': grouped_rows,
        }
        out = _json_compatible(out)
        self._cache[cache_key] = out
        return out

    @profile
    def tolerance_sweep_summary(
        self,
        *,
        run_tolerances: list[dict[str, Any]] | None = None,
        instance_tolerances: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Evaluate multiple tolerance policies without recomputing run loading."""
        if run_tolerances is None:
            run_tolerances = [
                {'name': 'strict', 'abs_tol': 0.0, 'rel_tol': 0.0},
                {'name': 'tiny', 'abs_tol': 1e-12, 'rel_tol': 1e-6},
                {'name': 'small', 'abs_tol': 1e-9, 'rel_tol': 1e-4},
                {'name': 'medium', 'abs_tol': 1e-6, 'rel_tol': 1e-3},
                {'name': 'loose', 'abs_tol': 1e-3, 'rel_tol': 1e-2},
            ]
        if instance_tolerances is None:
            instance_tolerances = list(run_tolerances)

        run_results = []
        for cfg in run_tolerances:
            summary = self._value_agreement_summary(
                abs_tol=float(cfg.get('abs_tol', 0.0) or 0.0),
                rel_tol=float(cfg.get('rel_tol', 0.0) or 0.0),
            )
            run_results.append({
                'name': cfg.get('name', 'unnamed'),
                'abs_tol': float(cfg.get('abs_tol', 0.0) or 0.0),
                'rel_tol': float(cfg.get('rel_tol', 0.0) or 0.0),
                'summary': summary,
            })

        instance_results = []
        for cfg in instance_tolerances:
            summary = self.instance_summary_dict(
                abs_tol=float(cfg.get('abs_tol', 0.0) or 0.0),
                rel_tol=float(cfg.get('rel_tol', 0.0) or 0.0),
            )
            instance_results.append({
                'name': cfg.get('name', 'unnamed'),
                'abs_tol': float(cfg.get('abs_tol', 0.0) or 0.0),
                'rel_tol': float(cfg.get('rel_tol', 0.0) or 0.0),
                'summary': summary,
            })
        return _json_compatible({
            'run_level': run_results,
            'instance_level': instance_results,
        })

    @profile
    def summarize_instances(
        self,
        *,
        level: int = 10,
        top_n: int = 5,
        show_details: int = 5,
        prompt_chars: int = 220,
        completion_chars: int = 140,
        input_chars: int = 200,
        diff_max_items: int = 7,
        writer=None,
    ) -> None:
        """Writer-style instance-level report.

        This prints:
        * coverage + agreement ratios
        * top mismatches for core metrics and for bookkeeping metrics
        * for the first `show_details` mismatches per (metric_class, metric)
          group: prompt/input/completion excerpts and a compact request_state diff.

        The large texts are smart-truncated (hash preserved) and escaped so
        rich doesn't interpret markup.
        """
        writer = _default_writer(writer)
        info = self.instance_summary_dict(top_n=top_n)
        cov = info['coverage']
        means = info['means']

        writer(f'Instance-level diff: {self.a_name} vs {self.b_name}')
        writer(
            f'  coverage: A={cov["n_a"]} B={cov["n_b"]} isect={cov["n_isect"]} '
            f'union={cov["n_union"]} onlyA={cov["only_a"]} onlyB={cov["only_b"]}'
        )
        writer(
            f'  means: comparable={means["comparable"]} mismatched={means["mismatched"]} '
            f'agree_ratio={_fmt(means["agree_ratio"])} (unpert={_fmt(means["agree_ratio_unperturbed"])}, '
            f'pert={_fmt(means["agree_ratio_perturbed"])})'
        )

        grouped: list[dict[str, Any]] = info.get('top_mismatches_by_group', []) or []
        # grouped is now a list of group objects; convert to list for sorting

        # Choose groups to show: core first, bookkeeping second
        def _group_rank(group: dict[str, Any]) -> tuple[int, float]:
            cls = group.get('metric_class')
            items = group.get('items', [])
            cls_rank = {'core': 0, 'bookkeeping': 1, 'untracked': 2}.get(cls, 9)
            max_abs = items[0]['abs_delta'] if items else 0.0
            return (cls_rank, -max_abs)

        groups_sorted = sorted(grouped, key=_group_rank)

        # Decide which metric classes are eligible at this level
        allowed_classes = {'core'}
        if level >= 20:
            allowed_classes |= {'untracked'}
        if level >= 30:
            allowed_classes |= {'bookkeeping'}

        # Filter groups by allowed class
        filtered = [g for g in groups_sorted if g.get('metric_class') in allowed_classes]

        # If we filtered everything out (e.g. no core diffs), fall back to showing *something*
        if not filtered and groups_sorted:
            # Prefer untracked, then bookkeeping, then whatever exists
            pref_order = ['untracked', 'bookkeeping', 'core']
            for cls in pref_order:
                filtered = [g for g in groups_sorted if g.get('metric_class') == cls]
                if filtered:
                    break
            if not filtered:
                filtered = groups_sorted

        groups_sorted = filtered

        # If level is low, avoid dumping too many groups
        max_groups = None
        if level < 20:
            max_groups = 6
        if max_groups is not None:
            # Ensure we show at least some core and some bookkeeping groups when possible.
            core = [g for g in groups_sorted if g.get('metric_class') == 'core']
            book = [g for g in groups_sorted if g.get('metric_class') == 'bookkeeping']
            other = [
                g
                for g in groups_sorted
                if g.get('metric_class') not in {'core', 'bookkeeping'}
            ]
            keep = []
            keep.extend(core[: max_groups // 2])
            keep.extend(book[: max_groups - len(keep)])
            if len(keep) < max_groups:
                keep.extend(other[: max_groups - len(keep)])
            # Preserve original ordering among kept groups
            keep_set = {id(x) for x in keep}
            groups_sorted = [g for g in groups_sorted if id(g) in keep_set]

        if groups_sorted:
            writer('  top mismatches:')

        # Build joined lookup tables for details
        A_join = None
        B_join = None
        A_map = None
        B_map = None

        if show_details and level >= 10:
            A_join = self.a.joined_instance_stat_table(
                assert_assumptions=False, short_hash=self.short_hash
            )
            B_join = self.b.joined_instance_stat_table(
                assert_assumptions=False, short_hash=self.short_hash
            )
            A_map = getattr(A_join, 'row_by_key', None)
            B_map = getattr(B_join, 'row_by_key', None)
            A_getrow = getattr(A_join, 'get_row', None)
            B_getrow = getattr(B_join, 'get_row', None)

        for group in groups_sorted:
            cls = group.get('metric_class')
            metric = group.get('metric')
            items = group.get('items', [])
            writer(f'  [bold]top mismatches ({(cls, metric)!r}):[/bold]')
            for rank, item in enumerate(items[:top_n], start=1):
                k = item['key']
                a = float(item['a'])
                b = float(item['b'])
                abs_d = float(item['abs_delta'])
                signed_d = float(item['signed_delta'])

                # Try to extract split/sub_split info from key if it is tuple/list-like
                split = None
                if (isinstance(k, tuple) or isinstance(k, list)) and len(k) >= 5:
                    # (id, tti, pert_id, metric, split, ...)
                    split = k[4]

                metric_label = (
                    metric if metric is not None else 'unknown_metric'
                )
                if split is not None:
                    metric_label = f'{metric_label}, split={split}'

                writer(f'   {rank:2d}. metric: {metric_label}')
                writer(f'      key: {k}')
                writer(
                    f'      A={_fmt(a)}  B={_fmt(b)}  Δ(B-A)={_fmt(signed_d)}  |Δ|={_fmt(abs_d)}'
                )

                if (
                    show_details
                    and level >= 10
                    and rank <= show_details
                    and A_map is not None
                    and B_map is not None
                ):
                    # Try to resolve row objects from the join tables.
                    # Item keys were serialized (lists); attempt to use the
                    # table's `get_row` API with a tuple form, which will
                    # reconstruct InstanceStatKey when appropriate.
                    ra = None
                    rb = None
                    if A_getrow is not None:
                        try:
                            lookup_key = tuple(k) if isinstance(k, list) else k
                            ra = A_getrow(lookup_key)
                        except Exception:
                            ra = None
                    if ra is None and A_map is not None:
                        ra = A_map.get(k, None)

                    if B_getrow is not None:
                        try:
                            lookup_key = tuple(k) if isinstance(k, list) else k
                            rb = B_getrow(lookup_key)
                        except Exception:
                            rb = None
                    if rb is None and B_map is not None:
                        rb = B_map.get(k, None)

                    rs_a = (
                        getattr(ra, 'request_state', None)
                        if ra is not None
                        else None
                    )
                    rs_b = (
                        getattr(rb, 'request_state', None)
                        if rb is not None
                        else None
                    )
                    if rs_a is None and isinstance(ra, dict):
                        rs_a = ra.get('request_state', None)
                    if rs_b is None and isinstance(rb, dict):
                        rs_b = rb.get('request_state', None)

                    # important: use repr, to avoid rendering newline chars.
                    pa = (
                        _smart_truncate(
                            repr(
                                ((rs_a or {}).get('request') or {}).get(
                                    'prompt', None
                                )
                            ),
                            prompt_chars,
                        )
                        if isinstance(rs_a, dict)
                        else ''
                    )
                    pb = (
                        _smart_truncate(
                            repr(
                                ((rs_b or {}).get('request') or {}).get(
                                    'prompt', None
                                )
                            ),
                            prompt_chars,
                        )
                        if isinstance(rs_b, dict)
                        else ''
                    )
                    prompts_equal = pa == pb
                    writer(f'      prompts_equal={prompts_equal}')

                    def _inst_input(rs: Any) -> str:
                        if not isinstance(rs, dict):
                            return ''
                        inst = rs.get('instance') or {}
                        inp = inst.get('input') or {}
                        if isinstance(inp, dict) and 'text' in inp:
                            return _smart_truncate(
                                repr(inp.get('text', None)), input_chars
                            )
                        # important: use repr, to avoid rendering newline chars.
                        return _smart_truncate(repr(inp), input_chars)

                    def _completion(rs: Any) -> str:
                        if not isinstance(rs, dict):
                            return ''
                        comps = (rs.get('result') or {}).get(
                            'completions'
                        ) or []
                        txt = comps[0].get('text', None) if comps else None
                        # important: use repr, to avoid rendering newline chars.
                        return _smart_truncate(repr(txt), completion_chars)

                    # --- Inputs / completions (avoid duplicates) ---
                    input_a = _inst_input(rs_a) if rs_a is not None else None
                    input_b = _inst_input(rs_b) if rs_b is not None else None
                    comp_a = _completion(rs_a) if rs_a is not None else None
                    comp_b = _completion(rs_b) if rs_b is not None else None

                    inputs_equal = (input_a == input_b) and (
                        input_a is not None
                    )
                    comps_equal = (comp_a == comp_b) and (comp_a is not None)

                    # Input
                    if inputs_equal:
                        writer(f'      input (same): {input_a}')
                    else:
                        if input_a is not None:
                            writer(f'      [{self.a_name}] input: {input_a}')
                        if input_b is not None:
                            writer(f'      [{self.b_name}] input: {input_b}')

                    # Completion
                    if comps_equal:
                        writer(f'      completion (same): {comp_a}')
                    else:
                        if comp_a is not None:
                            writer(
                                f'      [{self.a_name}] completion: {comp_a}'
                            )
                        if comp_b is not None:
                            writer(
                                f'      [{self.b_name}] completion: {comp_b}'
                            )

                    if level >= 20:
                        if isinstance(rs_a, dict) and isinstance(rs_b, dict):
                            diffs = _walker_diff(
                                rs_a, rs_b, max_paths=diff_max_items
                            )
                            writer(
                                f'      request_state_diff: {ub.urepr(diffs)}'
                            )

                writer('')

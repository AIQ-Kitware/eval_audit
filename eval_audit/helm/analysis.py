"""eval_audit.helm.analysis

Single-run analysis utilities wrapped in an object.

NOTE
----
This module is the legacy HELM-shape analysis surface. After the EEE
refactor, the comparison/report code paths consume
:mod:`eval_audit.normalized` instead. ``HelmRunAnalysis`` and
``HelmRunDiff`` are still used for the run-spec semantic diagnosis that
reads ``run_spec.json`` directly. Do not extend this module for new
comparison features — add them under :mod:`eval_audit.normalized.compare`
so they are loader-format-agnostic.

Why this exists
--------------
``HelmRun`` (in :mod:`eval_audit.compat.helm_outputs`; magnet's
``magnet.backends.helm.helm_outputs`` is the equivalent upstream reader)
is intentionally a *reader*.
This module defines :class:`HelmRunAnalysis`, which *wraps* a ``HelmRun`` and
adds cached analyses / indices that make higher-level tasks (e.g. run diffs)
much easier to write.

Design goals (match notebook-style workflows)
--------------------------------------------
* Keep computations *lazy* and cache results.
* Keep the public API tight (a few high-value methods).
* Provide stable-ish identifiers where HELM uses dict-typed "names".

Notes
-----
* We primarily operate on the json view (``run.json``) for speed and
  robustness across HELM versions.
* We do **conservative** canonicalization for hashing: only strip known
  environment-specific fields like path strings.
"""

from __future__ import annotations

from typing import Any

import ubelt as ub

from eval_audit.utils import hashers as helm_hashers
from eval_audit import metrics_taxonomy as helm_metrics
from eval_audit.utils.numeric import safe_float as _safe_float

# Implementation moved to helm.instance_stats / helm.analysis_report on
# 2026-06-11 (Phase 2 of docs/historical/planning/repo-refactor-plan.md).
# HelmRunAnalysis methods resolve summary/summary_dict through this
# module's globals; keep re-exporting. (The instance-level join stack
# was retired 2026-07-12 — plan item A2.)
from eval_audit.helm.instance_stats import StatMeta  # noqa: F401
from eval_audit.helm.analysis_report import (  # noqa: F401
    summary_dict,
    summary,
    summary_text,
)


class HelmRunAnalysis(ub.NiceRepr):
    """Wrap a ``HelmRun`` reader with cached analyses.

    Parameters
    ----------
    run:
        The underlying run reader.
    name:
        Optional human-friendly label used in summaries.

    Example:
        >>> from magnet.backends.helm.helm_outputs import HelmRun
        >>> from eval_audit.helm.analysis import HelmRunAnalysis
        >>> run = HelmRun.demo()
        >>> ana = HelmRunAnalysis(run)
        >>> info = ana.summary_dict(level=10)
        >>> assert 'run_spec_name' in info
        >>> ana.summary(level=1)
    """

    def __init__(self, run, *, name: str | None = None):
        self.run = run
        self.name = name
        # Raw JSON endpoints (expensive I/O) are cached here
        self._raw_cache: dict[str, Any] = {}
        # Derived analyses / indices are cached here
        self._cache: dict[Any, Any] = {}

    def __nice__(self):
        return self.name or str(self.run.path.name)

    # --- Raw JSON getters (cached) ------------------------------------

    def run_spec(self) -> dict[str, Any]:
        return self._raw('run_spec', lambda: self.run.json.run_spec())

    def scenario(self) -> dict[str, Any]:
        return self._raw('scenario', lambda: self.run.json.scenario())

    def scenario_state(self) -> dict[str, Any]:
        return self._raw(
            'scenario_state', lambda: self.run.json.scenario_state()
        )

    def stats(self) -> list[dict[str, Any]]:
        return self._raw('stats', lambda: self.run.json.stats())

    def per_instance_stats(self) -> list[dict[str, Any]]:
        return self._raw(
            'per_instance_stats', lambda: self.run.json.per_instance_stats()
        )

    def _raw(self, key: str, factory):
        if key not in self._raw_cache:
            self._raw_cache[key] = factory()
        return self._raw_cache[key]

    # --- Summaries -----------------------------------------------------

    def summary_dict(self, *args, **kwargs) -> dict[str, Any]:
        # Implementation deliberately lives in helm.analysis_report (see the
        # module-header note); this module stays a thin legacy surface.
        return summary_dict(self, *args, **kwargs)

    def summary(self, *args, **kwargs):
        # Hack for now while developing. TODO: move the implementation here and
        # fix the signature.
        return summary(self, *args, **kwargs)

    # --- Stats: inventory + index -------------------------------------

    def stat_index(
        self,
        *,
        drop_zero_count: bool = True,
        require_mean: bool = False,
        short_hash: int = 16,
    ) -> dict[str, 'StatMeta']:
        """Map a readable stat-key -> :class:`StatMeta`.

        The key starts with the metric name (and hints like split/pert name) and
        ends with a short hash to keep it stable and disambiguated.
        """
        cache_key = ('stat_index', drop_zero_count, require_mean, short_hash)
        if cache_key in self._cache:
            return self._cache[cache_key]

        idx: dict[str, StatMeta] = {}
        for row in self.stats():
            count = int(row.get('count', 0) or 0)
            if drop_zero_count and count == 0:
                continue
            mean = _safe_float(row.get('mean', None))
            if require_mean and mean is None:
                continue

            name_obj = row.get('name', None)
            metric = (
                name_obj.get('name', None)
                if isinstance(name_obj, dict)
                else None
            )
            split = (
                name_obj.get('split', None)
                if isinstance(name_obj, dict)
                else None
            )
            pert_id = None
            if isinstance(name_obj, dict) and isinstance(
                name_obj.get('perturbation', None), dict
            ):
                pert_id = helm_hashers.perturbation_id(
                    name_obj['perturbation'], short_hash=short_hash
                )
            is_pert = pert_id is not None

            mclass, mpref = helm_metrics.classify_metric(metric)
            fam = helm_metrics.metric_family(metric)

            key = helm_hashers.stat_key(name_obj, short_hash=short_hash)
            idx[key] = StatMeta(
                key=key,
                metric=metric,
                split=split,
                is_perturbed=is_pert,
                pert_id=pert_id,
                family=fam,
                metric_class=mclass,
                matched_prefix=mpref,
                count=count,
                mean=mean,
                name_obj=name_obj,
                raw=row,
            )

        self._cache[cache_key] = idx
        return idx


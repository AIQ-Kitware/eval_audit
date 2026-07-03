"""NormalizedDiff — the unified, framework-free comparison core.

Phase 3 sub-stage 4.3 (docs/planning/phase3-comparison-core-unification.md
§3.2): one comparison engine over two :class:`NormalizedRun` inputs,
assembling pieces that already existed —
:mod:`eval_audit.normalized.compare` for agreement rows,
the row-math helpers relocated here from
``reports.core_metric_curves`` (which now re-imports them), the
:mod:`eval_audit.normalized.diagnose` port for diagnosis, and the
:mod:`eval_audit.metrics_taxonomy` judge-dependence split for the
open-judge extension.

This sub-stage is **additive**: nothing routes through NormalizedDiff
yet. Sub-stage 4.6 points the HELM renderer at it (keeping the
HelmRunDiff run_spec *semantic* diff alongside, per design §3.2); the
EEE renderer adopts the facts-grade diagnosis at the same time.

Facts-grade diagnosis
---------------------
On the HELM path the diagnosis inputs come from a deep run_spec.json
semantic diff. Here they come from :class:`RecipeFacts` — scalar
metadata — so the semantic inputs are *facts-grade*: deployment /
scenario-class / instructions / max_eval_instances drift and run-name
mismatch are detectable; deep execution- and evaluation-spec drift is
not claimed either way. When either side's facts are ``unknown`` the
spec-level inputs stay neutral and no drift is asserted — mirroring
today's EEE behavior (``diagnosis = {}``) instead of inventing
claims. Run-level value drift is computed over core rows only:
``normalized.compare`` does not surface bookkeeping stats, so the
``bookkeeping_metric_drift`` reason cannot fire on this path (HELM
path keeps it via 4.6 calibration).
"""

from __future__ import annotations

from functools import cached_property
from typing import Any

import numpy as np

from eval_audit import metrics_taxonomy
from eval_audit.infra.profiling import profile
from eval_audit.normalized import compare as ncompare
from eval_audit.normalized.diagnose import diagnose_repro
from eval_audit.normalized.model import NormalizedRun
from eval_audit.normalized.recipe_facts import RecipeFacts

#: Same abs_tol sweep the core_metrics renderer uses (reports/core_metrics.py).
DEFAULT_ABS_TOL_THRESHOLDS = [
    0.0, 1e-12, 1e-9, 1e-6, 1e-4, 1e-3, 1e-2, 2e-2, 5e-2, 1e-1, 2.5e-1, 5e-1, 1.0,
]


# ---------------------------------------------------------------------------
# Row math (relocated verbatim from reports.core_metric_curves, which now
# re-imports these — Phase 2's curves module keeps its public surface).
# ---------------------------------------------------------------------------


@profile
def agreement_curve(rows: list[dict[str, Any]], thresholds: list[float]) -> list[dict[str, Any]]:
    """Count abs_delta-≤-threshold for each threshold via a single sort + searchsorted.

    Previously did ``sum(v <= t for v in vals)`` inside a per-threshold
    Python loop — O(N × K) Python comparisons per call. np.searchsorted
    on a sorted array does each threshold's count in O(log N) (binary
    search). For "≤ t" we use side='right': the rightmost insertion
    point equals the number of values ≤ t.

    Each subexpression is on its own line so the line profiler can
    attribute the dict-extract, the sort, the searchsorted, the ratio
    division, and the dict construction independently.
    """
    if not rows:
        return []
    n = len(rows)
    # Pull the numerical column. ``np.fromiter`` avoids materializing
    # an intermediate Python list at the cost of a generator-driven
    # fill; for n in the thousands the difference is small but it
    # keeps the GC heap quieter.
    arr = np.fromiter(
        (float(r['abs_delta']) for r in rows),
        dtype=np.float64,
        count=n,
    )
    arr.sort()
    thresh_arr = np.asarray(thresholds, dtype=np.float64)
    # side='right' = count of values ≤ t (rightmost insertion point).
    counts = np.searchsorted(arr, thresh_arr, side='right')
    # Ratios in one vector op rather than per-element division in
    # the comprehension below.
    ratios = counts / n
    # Pre-cast counts to a Python list so the per-element ``int(...)``
    # in the loop becomes a list-element fetch (numpy ints would JSON-
    # serialize fine but downstream consumers expect Python ints).
    counts_py = counts.tolist()
    thresholds_py = thresh_arr.tolist()
    ratios_py = ratios.tolist()
    return [
        {
            'abs_tol': t,
            'agree_ratio': r,
            'matched': c,
            'count': n,
        }
        for t, c, r in zip(thresholds_py, counts_py, ratios_py)
    ]


@profile
def group_quantiles(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute every reported quantile of ``abs_delta`` in a single pass.

    Previously called the pure-python ``_quantile`` helper six times, and
    each call did its own internal sort — so we sorted the same vector
    seven times. ``np.quantile`` does one sort and answers every q at
    once.
    """
    n = len(rows)
    if n == 0:
        return {
            'count': 0,
            'abs_delta': {
                'min': None, 'p50': None, 'p90': None,
                'p95': None, 'p99': None, 'max': None,
            },
        }
    arr = np.fromiter(
        (float(r['abs_delta']) for r in rows),
        dtype=np.float64,
        count=n,
    )
    # method='linear' matches the existing _quantile helper's
    # interpolation rule, so existing report numbers don't shift.
    qs = np.quantile(arr, [0.0, 0.5, 0.9, 0.95, 0.99, 1.0], method='linear')
    return {
        'count': n,
        'abs_delta': {
            'min': float(qs[0]),
            'p50': float(qs[1]),
            'p90': float(qs[2]),
            'p95': float(qs[3]),
            'p99': float(qs[4]),
            'max': float(qs[5]),
        },
    }


@profile
def metric_quantiles(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_metric: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        # Split into separate statements so the line profiler can
        # attribute the dict-key cast, the ``setdefault`` lookup, and
        # the list append independently. ``setdefault(...).append(...)``
        # collapses three operations onto one line.
        metric_key = str(row['metric'])
        bucket = by_metric.setdefault(metric_key, [])
        bucket.append(row)
    out = []
    for metric, items in sorted(by_metric.items()):
        info = group_quantiles(items)
        info['metric'] = metric
        out.append(info)
    return out


# ---------------------------------------------------------------------------
# Facts-grade semantic inputs
# ---------------------------------------------------------------------------


def _both(a: Any, b: Any) -> bool:
    return a is not None and b is not None


def facts_semantic_inputs(
    facts_a: RecipeFacts | None,
    facts_b: RecipeFacts | None,
) -> dict[str, Any]:
    """Build ``diagnose_repro`` spec-level inputs from two RecipeFacts.

    Returns the ``run_spec_name_ok`` / ``run_spec_semantic`` /
    ``scenario_semantic`` keyword subset. Honest by construction: a
    fact only contributes when **both** sides carry it; anything
    unknown stays neutral (no drift claimed, no cleanliness claimed —
    the planner's ``comparability_unknown:*`` warnings carry that
    signal). With both sides unknown the result is fully neutral,
    matching today's empty EEE diagnosis.
    """
    neutral = {
        "run_spec_name_ok": True,
        "run_spec_semantic": {"execution_ok": True},
        "scenario_semantic": {"known": False, "semantic_ok": None},
    }
    if facts_a is None or facts_b is None:
        return neutral
    if facts_a.source == "unknown" or facts_b.source == "unknown":
        return neutral

    run_spec_name_ok = True
    if _both(facts_a.run_spec_name, facts_b.run_spec_name):
        run_spec_name_ok = facts_a.run_spec_name == facts_b.run_spec_name

    execution_paths: list[str] = []
    execution_value_examples: list[dict[str, Any]] = []
    deployment_changed = False
    deployment: dict[str, Any] = {}
    if _both(facts_a.model_deployment, facts_b.model_deployment) and (
        facts_a.model_deployment != facts_b.model_deployment
    ):
        deployment_changed = True
        deployment = {"a": facts_a.model_deployment, "b": facts_b.model_deployment}
        execution_paths.append("adapter_spec.model_deployment")
    for field, path in [
        ("instructions", "adapter_spec.instructions"),
        ("max_eval_instances", "adapter_spec.max_eval_instances"),
    ]:
        value_a = getattr(facts_a, field)
        value_b = getattr(facts_b, field)
        if _both(value_a, value_b) and value_a != value_b:
            execution_paths.append(path)
            execution_value_examples.append({"path": path, "a": value_a, "b": value_b})

    scenario_known = _both(facts_a.scenario_class, facts_b.scenario_class)
    scenario_ok = (
        facts_a.scenario_class == facts_b.scenario_class if scenario_known else None
    )
    return {
        "run_spec_name_ok": run_spec_name_ok,
        "run_spec_semantic": {
            "execution_ok": not execution_paths,
            "execution_paths": execution_paths,
            "execution_value_examples": execution_value_examples,
            "deployment_paths": (
                ["adapter_spec.model_deployment"] if deployment_changed else []
            ),
            "deployment_changed": deployment_changed,
            "deployment": deployment,
            # Facts carry no metric-spec detail; make no evaluation claim.
            "evaluation_paths": [],
            "metric_specs_multiset_delta": {"equal_as_multiset": True},
        },
        "scenario_semantic": {
            "known": scenario_known,
            "semantic_ok": scenario_ok,
            "semantic_paths": (
                ["scenario_spec.class_name"] if scenario_known and not scenario_ok else []
            ),
        },
    }


def judge_fact_status(
    facts_a: RecipeFacts | None, facts_b: RecipeFacts | None
) -> str:
    """``same_judge`` status from two RecipeFacts: yes / no / unknown.

    Identities are resolved through the curated judge registry first so
    an official side (annotator class basename — HELM hard-codes the
    model) compares against a local side (explicit model ids) on equal
    terms. See docs/planning/judge-identity-inventory.md.
    """
    from eval_audit.judge_registry import resolve_judge_models

    if facts_a is None or facts_b is None:
        return "unknown"
    resolved_a = resolve_judge_models(facts_a.judge_models)
    resolved_b = resolve_judge_models(facts_b.judge_models)
    if resolved_a is None or resolved_b is None:
        return "unknown"
    return "yes" if resolved_a == resolved_b else "no"


# ---------------------------------------------------------------------------
# The diff core
# ---------------------------------------------------------------------------


class NormalizedDiff:
    """Pairwise comparison of two :class:`NormalizedRun` instances.

    Produces the same run-level / instance-level summary blocks the
    ``core_metrics`` renderer emits today (same arithmetic — the row
    builders and curve math are shared code, not a re-derivation),
    plus the facts-grade diagnosis and the judge-dependence
    metric-class split for the open-judge extension.
    """

    def __init__(
        self,
        run_a: NormalizedRun,
        run_b: NormalizedRun,
        *,
        label: str = "official_vs_local",
        thresholds: list[float] | None = None,
        recipe_facts_a: RecipeFacts | None = None,
        recipe_facts_b: RecipeFacts | None = None,
        substitutions: tuple[str, ...] = (),
    ) -> None:
        self.run_a = run_a
        self.run_b = run_b
        self.label = label
        self.thresholds = list(
            thresholds if thresholds is not None else DEFAULT_ABS_TOL_THRESHOLDS
        )
        self.recipe_facts_a = recipe_facts_a
        self.recipe_facts_b = recipe_facts_b
        self.substitutions = tuple(substitutions)

    # -- agreement rows -----------------------------------------------------

    @cached_property
    def run_rows(self) -> list[dict[str, Any]]:
        return ncompare.run_level_core_rows(self.run_a, self.run_b)

    @cached_property
    def _inst(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return ncompare.instance_level_core_rows(self.run_a, self.run_b)

    @property
    def inst_rows(self) -> list[dict[str, Any]]:
        return self._inst[0]

    @property
    def inst_stats(self) -> dict[str, Any]:
        return self._inst[1]

    # -- summary blocks (shape-identical to the current renderer) -----------

    def run_level_summary(self) -> dict[str, Any]:
        return {
            "n_rows": len(self.run_rows),
            "overall_quantiles": group_quantiles(self.run_rows),
            "by_metric": metric_quantiles(self.run_rows),
            "agreement_vs_abs_tol": agreement_curve(self.run_rows, self.thresholds),
        }

    def per_metric_curves(self) -> dict[str, list[dict[str, Any]]]:
        curves: dict[str, list[dict[str, Any]]] = {}
        by_metric: dict[str, list[dict[str, Any]]] = {}
        for row in self.inst_rows:
            by_metric.setdefault(str(row.get("metric", "unknown")), []).append(row)
        for metric, metric_rows in by_metric.items():
            curves[metric] = agreement_curve(metric_rows, self.thresholds)
        return curves

    def instance_level_summary(self) -> dict[str, Any]:
        return {
            "n_rows": len(self.inst_rows),
            "n_joined_pairs": int(self.inst_stats.get("n_joined_pairs", 0)),
            "n_nonfinite_dropped": int(self.inst_stats.get("n_nonfinite_dropped", 0)),
            "overall_quantiles": group_quantiles(self.inst_rows),
            "by_metric": metric_quantiles(self.inst_rows),
            "agreement_vs_abs_tol": agreement_curve(self.inst_rows, self.thresholds),
            "per_metric_agreement": self.per_metric_curves(),
        }

    def core_metrics(self) -> list[str]:
        return sorted({str(r["metric"]) for r in self.inst_rows})

    # -- judge-dependence split (R2) -----------------------------------------

    def metric_class_split(self) -> dict[str, dict[str, Any]]:
        """Instance-level agreement split by judge dependence.

        ``deterministic`` metrics are the reproduction control (must
        still agree under a judge substitution); ``judge_dependent``
        metric movement is the extension's measurement, reported and
        never conflated with the control.
        """
        groups: dict[str, list[dict[str, Any]]] = {
            "deterministic": [],
            "judge_dependent": [],
        }
        for row in self.inst_rows:
            cls, _ = metrics_taxonomy.classify_judge_dependence(
                str(row.get("metric", "")) or None
            )
            groups[cls].append(row)
        return {
            cls: {
                "n_rows": len(rows),
                "metrics": sorted({str(r["metric"]) for r in rows}),
                "agreement_vs_abs_tol": agreement_curve(rows, self.thresholds),
            }
            for cls, rows in groups.items()
        }

    # -- diagnosis ------------------------------------------------------------

    def value_summary(self, *, abs_tol: float = 0.0) -> dict[str, Any]:
        """Run-level value agreement in the shape diagnose_repro reads.

        Core rows only (see module docstring): bookkeeping agreement is
        not observable through ``normalized.compare``, so its slot stays
        empty and the bookkeeping drift reason cannot fire here.
        """
        core: dict[str, Any] = {}
        if self.run_rows:
            agree = sum(1 for r in self.run_rows if float(r["abs_delta"]) <= abs_tol)
            core = {
                "agree_ratio": agree / len(self.run_rows),
                "n": len(self.run_rows),
                "abs_tol": abs_tol,
            }
        return {"by_class": {"core": core, "bookkeeping": {}}}

    def diagnosis(self) -> dict[str, Any]:
        semantic = facts_semantic_inputs(self.recipe_facts_a, self.recipe_facts_b)
        substitution_fact_status = {}
        if "judge" in self.substitutions:
            substitution_fact_status["judge"] = judge_fact_status(
                self.recipe_facts_a, self.recipe_facts_b
            )
        return diagnose_repro(
            run_spec_name_ok=semantic["run_spec_name_ok"],
            run_spec_semantic=semantic["run_spec_semantic"],
            scenario_semantic=semantic["scenario_semantic"],
            dataset_overlap=None,
            value_summary=self.value_summary(),
            substitutions=self.substitutions,
            substitution_fact_status=substitution_fact_status,
        )

    # -- assembled pair dict ---------------------------------------------------

    def pair_summary(
        self,
        *,
        run_a_path: str | None = None,
        run_b_path: str | None = None,
        include_diagnosis: bool = True,
    ) -> dict[str, Any]:
        """The renderer-facing pair dict (same shape as ``_build_pair``)."""
        return {
            "label": self.label,
            "inputs": {
                "run_a": run_a_path or str(self.run_a.ref.artifact_path),
                "run_b": run_b_path or str(self.run_b.ref.artifact_path),
            },
            "diagnosis": self.diagnosis() if include_diagnosis else {},
            "core_metrics": self.core_metrics(),
            "run_level": self.run_level_summary(),
            "instance_level": self.instance_level_summary(),
            "_instance_rows": self.inst_rows,
        }


__all__ = [
    "DEFAULT_ABS_TOL_THRESHOLDS",
    "NormalizedDiff",
    "agreement_curve",
    "facts_semantic_inputs",
    "group_quantiles",
    "judge_fact_status",
    "metric_quantiles",
]

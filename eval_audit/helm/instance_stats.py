"""StatMeta: a compact, normalized view of a HELM stat row.

Split out of ``eval_audit.helm.analysis`` on 2026-06-11 (Phase 2 of
docs/historical/planning/repo-refactor-plan.md). The instance-level join
layer that used to live here (``InstanceVariantKey`` / ``InstanceStatKey``
/ ``InstanceStatRow`` / ``JoinedInstanceStatTable``) was retired on
2026-07-12 (plan item A2 of
docs/planning/repo-simplification-plan-2026-07-12.md): per-instance joins
moved to :mod:`eval_audit.normalized.joins` in the EEE refactor, leaving
the HELM-shape join reachable only from tests. ``StatMeta`` stays — it is
live via :meth:`HelmRunAnalysis.stat_index`.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class StatMeta:
    """A compact, normalized view of a HELM stat row."""

    key: str
    metric: str | None
    split: str | None
    is_perturbed: bool
    pert_id: str | None
    family: str
    metric_class: str
    matched_prefix: str | None
    count: int
    mean: float | None
    name_obj: Any
    raw: Mapping[str, Any]

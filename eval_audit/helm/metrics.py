"""Re-export shim: the metric taxonomy moved to ``eval_audit.metrics_taxonomy``.

The classification rules are pure string-prefix logic over metric names
— not HELM-specific — and the EEE-native comparison core needs them
without importing ``eval_audit.helm.*`` (Phase 3 sub-stage 4.0). The
implementation now lives in :mod:`eval_audit.metrics_taxonomy`; this
module keeps the old import path working for the HELM-shaped consumers
(``helm.analysis``, ``helm.diff``, ``reports.core_metrics``, ...).
"""

from __future__ import annotations

from eval_audit.metrics_taxonomy import (  # noqa: F401
    METRIC_PREFIXES,
    classify_judge_dependence,
    classify_metric,
    is_judge_dependent,
    metric_family,
)

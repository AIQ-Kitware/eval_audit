"""Shared plotly bar-chart helpers for the reports package.

Previously two diverging copies lived in ``reports/summary/plots.py``
and ``reports/filter_analysis_charts.py``. The count-tag table is the
union of both copies (lookups fall back to ``n_categories``, so extra
keys are harmless to either consumer); the tick-angle ladder is the
identical four-step rule both modules used. Plan item E2 of
docs/planning/repo-simplification-plan-2026-07-12.md.
"""

from __future__ import annotations

#: Axis key -> human count tag used in bar-chart axis titles.
AXIS_COUNT_TAGS: dict[str, str] = {
    "benchmark": "n_benchmarks",
    "model": "n_models",
    "dataset": "n_datasets",
    "scenario": "n_scenarios",
    "official_instance_agree_bucket": "n_buckets",
    "agreement_bucket": "n_buckets",
    "failure_reason": "n_failure_reasons",
    "category": "n_categories",
    "group_value": "n_categories",
    "candidate_pool": "n_candidate_pools",
    "reason_combo": "n_reason_combos",
}


def bar_count_label(axis_key: str, n_bars: int, *, axis_title: str | None = None) -> str:
    """Axis title carrying the category count, e.g. ``Model (n_models=5)``.

    ADR 10: labels must explain what is being counted. (The old
    filter-charts copy additionally appended ``, n_bars=<n>`` — dropped
    as redundant with the count tag, which reports the same number.)
    """
    label = axis_title if axis_title is not None else axis_key.replace("_", " ").title()
    count_tag = AXIS_COUNT_TAGS.get(axis_key, "n_categories")
    return f"{label} ({count_tag}={n_bars})"


def bar_tickangle(n_bars: int) -> int:
    """Tick rotation ladder shared by every bar chart (denser -> steeper)."""
    if n_bars > 50:
        return 90
    if n_bars > 25:
        return 75
    if n_bars > 12:
        return 60
    return -45


def bar_tickfont_size(n_bars: int) -> int:
    """Tick font-size ladder paired with :func:`bar_tickangle`."""
    if n_bars > 25:
        return 8
    if n_bars > 12:
        return 9
    return 10

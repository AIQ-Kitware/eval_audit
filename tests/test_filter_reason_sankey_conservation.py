"""P1-12 regression: the flat filter-reason sankey must conserve flow — one
row per run, not one per (run, reason). A run with multiple failure_reasons
used to emit multiple excluded rows, inflating the excluded flow past the run
count (root label rendered 'n=X n=Y' with Y>X)."""
from __future__ import annotations

from eval_audit.reports.filter_analysis_tables import build_filter_reason_sankey_rows


def test_one_row_per_run_even_with_multiple_reasons():
    inventory = [
        {"selection_status": "selected", "failure_reasons": []},
        {"selection_status": "excluded", "failure_reasons": ["gated_model", "gated_dataset", "no_local_deployment"]},
        {"selection_status": "excluded", "failure_reasons": ["gated_model"]},
        {"selection_status": "excluded", "failure_reasons": []},  # -> unclassified
        {"selection_status": "excluded", "is_structurally_incomplete": True, "failure_reasons": ["x", "y"]},
    ]
    rows = build_filter_reason_sankey_rows(inventory)

    # Exactly one row per input run (flow conserved).
    assert len(rows) == len(inventory)

    n_selected = sum(1 for r in rows if r["outcome"] == "selected")
    n_excluded = sum(1 for r in rows if r["outcome"] == "excluded")
    assert n_selected == 1
    assert n_excluded == 4

    # The multi-reason run is attributed to its primary (first) reason only.
    multi = [r for r in rows if r["filter_reason"] == "gated_model"]
    assert len(multi) == 2  # the two runs whose primary reason is gated_model
    # The structurally-incomplete run keeps its dedicated bucket.
    assert any(r["filter_reason"] == "structurally-incomplete" for r in rows)

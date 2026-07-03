"""P0-7 / P1-8 regression: the failure-taxonomy chart must render (it silently
never wrote its HTML/JPG since policy_blocked/recipe_error were added but had no
color -> KeyError -> swallowed by the outer except), and every classifier output
must map to a real category (not silently 'Unknown / Other')."""
from __future__ import annotations

from pathlib import Path

from eval_audit.reports.summary.failure_triage import (
    _FAILURE_CATEGORIES,
    _FAILURE_CATEGORY_LABELS,
    _FAILURE_CATEGORY_ORDER,
)
from eval_audit.reports.summary.plots import _write_failure_taxonomy_plot


def test_every_mapped_category_is_declared_in_order_and_labels():
    for _reason, (cat_key, _label) in _FAILURE_CATEGORIES.items():
        assert cat_key in _FAILURE_CATEGORY_ORDER, cat_key
        assert cat_key in _FAILURE_CATEGORY_LABELS, cat_key


def test_positively_identified_infra_failures_are_not_unknown():
    # P1-8: these five were previously unmapped -> charted as Unknown/Other.
    for reason in (
        "gpu_memory_or_cuda_failure",
        "process_killed_or_resource_exhausted",
        "network_or_remote_service_failure",
        "filesystem_permission_failure",
        "interrupted_run",
    ):
        cat_key, _ = _FAILURE_CATEGORIES[reason]
        assert cat_key != "unknown", reason


def test_truncated_runtime_is_not_labelled_hardware_timeout():
    # P1-8: no hardware evidence -> must not present as a hardware timeout.
    _, label = _FAILURE_CATEGORIES["truncated_or_incomplete_runtime"]
    assert "Timeout" not in label
    assert "Hardware" not in label


def test_taxonomy_html_is_written_for_failures_across_new_categories(tmp_path, monkeypatch):
    # P0-7: the trace loop must not KeyError on policy_blocked / recipe_error /
    # compute_resource / network categories. Skip the Chrome-dependent static
    # JPG; the HTML render is the regression surface.
    monkeypatch.setenv("HELM_AUDIT_SKIP_STATIC_IMAGES", "1")
    monkeypatch.delenv("HELM_AUDIT_SKIP_PLOTLY", raising=False)
    failed_rows = [
        {"benchmark": "mmlu", "failure_reason": "trust_remote_code_required"},
        {"benchmark": "mmlu", "failure_reason": "malformed_run_entry"},
        {"benchmark": "boolq", "failure_reason": "gpu_memory_or_cuda_failure"},
        {"benchmark": "boolq", "failure_reason": "network_or_remote_service_failure"},
        {"benchmark": "narrativeqa", "failure_reason": "filesystem_permission_failure"},
    ]
    result = _write_failure_taxonomy_plot(
        failed_rows, tmp_path / "failure_taxonomy", "Failure Taxonomy"
    )
    assert result["plotly_error"] is None, result["plotly_error"]
    assert result["html"] and Path(result["html"]).is_file()

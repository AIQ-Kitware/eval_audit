"""Regression test for IM-6: management summary discloses 1-of-N ovl pairs."""
from __future__ import annotations

from pathlib import Path

from eval_audit.reports import core_metric_tables


def _pair(comparison_id: str, kind: str) -> dict:
    return {
        "comparison_id": comparison_id,
        "comparison_kind": kind,
        "core_metrics": ["exact_match"],
        "diagnosis": {"label": "ok"},
        "run_level": {
            "n_rows": 1,
            "overall_quantiles": {"abs_delta": {"p90": 0.0, "max": 0.0}},
        },
        "instance_level": {
            "n_rows": 1,
            "agreement_vs_abs_tol": [],
            "overall_quantiles": {"abs_delta": {"p99": 0.0, "max": 0.0}},
        },
    }


def _report(pairs: list[dict]) -> dict:
    return {
        "pairs": pairs,
        "generated_utc": "2026-07-06T00:00:00Z",
        "run_spec_name": "boolq:model=x",
        "report_dpath": "/tmp/rp",
        "components_manifest_path": "/tmp/c.json",
        "comparisons_manifest_path": "/tmp/cmp.json",
        "single_run_mode": False,
        "diagnostic_flags": [],
        "packet_warnings": [],
        "packet_caveats": [],
        "warnings_manifest_path": None,
        "components": [],
        "comparisons": [],
        "comparability": {"facts": {}},
        "run_diagnostics": {},
    }


def test_management_summary_discloses_multiple_official_vs_local_pairs(tmp_path: Path):
    pairs = [
        _pair("local_repeat::a::b", "local_repeat"),
        _pair("official_vs_local::main", "official_vs_local"),
        _pair("official_vs_local::alt", "official_vs_local"),
    ]
    out = tmp_path / "management_summary.txt"
    core_metric_tables._write_management_summary(_report(pairs), out)
    text = out.read_text()
    assert "n_official_vs_local_pairs: 2; showing official_vs_local::main" in text


def test_management_summary_no_disclosure_for_single_pair(tmp_path: Path):
    pairs = [
        _pair("local_repeat::a::b", "local_repeat"),
        _pair("official_vs_local::main", "official_vs_local"),
    ]
    out = tmp_path / "management_summary.txt"
    core_metric_tables._write_management_summary(_report(pairs), out)
    text = out.read_text()
    assert "n_official_vs_local_pairs:" not in text

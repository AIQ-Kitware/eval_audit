from __future__ import annotations

from pathlib import Path

import pytest

from eval_audit.infra.index_io import latest_index_csv
from eval_audit.reports.summary import common as summary_common
from eval_audit.workflows import rebuild_core_report


def test_latest_index_csv_prefers_unstamped_canonical_name(tmp_path: Path):
    """P0-2: Stage 4 writes the unstamped ``audit_results_index.csv``; the
    resolver must find it. The old glob-only resolver raised FileNotFoundError
    on this exact (fresh-store) layout."""
    canonical = tmp_path / "audit_results_index.csv"
    canonical.write_text("run_entry\nbench:model=a\n")

    resolved = latest_index_csv(tmp_path)
    assert resolved.name == "audit_results_index.csv"
    assert resolved == canonical.resolve()


def test_latest_index_csv_unstamped_wins_over_stale_stamped(tmp_path: Path):
    """A store carrying a pre-2026-04-28 stamped file alongside the fresh
    unstamped one must analyse the unstamped (current) index, not the stamped
    (stale) one — the old resolver silently returned the stamped file."""
    (tmp_path / "audit_results_index_20260101T000000Z.csv").write_text("stale\n")
    canonical = tmp_path / "audit_results_index.csv"
    canonical.write_text("fresh\n")

    assert latest_index_csv(tmp_path) == canonical.resolve()


def test_latest_index_csv_falls_back_to_stamped_glob(tmp_path: Path):
    stamped = tmp_path / "audit_results_index_20260101T000000Z.csv"
    stamped.write_text("legacy\n")
    assert latest_index_csv(tmp_path) == stamped


def test_latest_index_csv_raises_when_absent(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        latest_index_csv(tmp_path)


def test_both_workflow_modules_reexport_the_shared_resolver():
    """R-6: the two previously copy-pasted helpers are single-sourced now."""
    assert summary_common.latest_index_csv is latest_index_csv
    assert rebuild_core_report.latest_index_csv is latest_index_csv

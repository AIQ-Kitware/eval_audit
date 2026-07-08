"""Shared resolution + loading for the Stage-4 local results index.

Single source of truth for "which local results index CSV is current"
and "read its rows". Consolidates the two copy-pasted ``latest_index_csv``
/ ``load_rows`` pairs that lived in ``workflows.rebuild_core_report`` and
``reports.summary.common`` (R-6 kickoff). Both modules now re-export from
here so existing import paths keep working.

P0-2: Stage 4 (``index_results``) writes the *unstamped* canonical name
``audit_results_index.csv`` (the date stamp was removed 2026-04-28b). The
old resolvers only globbed the stamped ``audit_results_index_*.csv`` name,
which cannot match the unstamped file — so a fresh store raised
``FileNotFoundError`` and a store still carrying a pre-2026-04-28 stamped
file was silently analysed *stale*. Resolve the unstamped canonical name
first, then fall back to the stamped glob (mirroring
``latest_official_index_csv``).
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


CANONICAL_LOCAL_INDEX_NAME = "audit_results_index.csv"


def latest_index_csv(index_dpath: Path) -> Path:
    """Resolve the current local results index CSV in ``index_dpath``.

    Prefers the unstamped canonical ``audit_results_index.csv``; falls back
    to the newest stamped ``audit_results_index_*.csv`` for legacy stores.
    """
    canonical = Path(index_dpath) / CANONICAL_LOCAL_INDEX_NAME
    if canonical.exists():
        return canonical.resolve()
    cands = sorted(Path(index_dpath).glob("audit_results_index_*.csv"), reverse=True)
    if not cands:
        raise FileNotFoundError(
            f"No local index csv ({CANONICAL_LOCAL_INDEX_NAME} or "
            f"audit_results_index_*.csv) found in {index_dpath}"
        )
    return cands[0]


def load_rows(index_fpath: Path) -> list[dict[str, Any]]:
    with Path(index_fpath).open(newline="") as file:
        return [
            {k: ("" if v is None else v) for k, v in row.items()}
            for row in csv.DictReader(file)
        ]


def latest_run_inventory_csv(history_root: Path) -> Path:
    """Resolve the newest ``run_inventory_*.csv`` under *history_root*.

    Single source of truth for the aggregate-summary run-inventory history
    lookup shared by the ``portfolio_status`` and ``analyze_backlog`` CLIs
    (R-6 consolidation). Names sort lexically newest-last, so a reverse sort
    puts the current file first.
    """
    cands = sorted(Path(history_root).rglob("run_inventory_*.csv"), reverse=True)
    if not cands:
        raise FileNotFoundError(
            f"No run_inventory_*.csv files found under {history_root}"
        )
    return cands[0]

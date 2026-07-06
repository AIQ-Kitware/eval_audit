"""CLI-guard tests for eval-audit-portfolio-status.

Focused on argument validation that must fail fast (audit item 7a) and on
the summary-history-root default no longer being derived from
``Path(__file__).parents[2]`` (item 7b), which would point into site-packages
under a non-editable install.
"""
from __future__ import annotations

import pytest

from eval_audit.cli import portfolio_status


def test_classify_backlog_without_experiment_name_errors():
    # --classify-backlog is only defined within a single experiment scope; a
    # bare invocation used to be a silent no-op. It must now parser.error()
    # (SystemExit code 2) before any inventory is loaded.
    with pytest.raises(SystemExit) as excinfo:
        portfolio_status.main(["--classify-backlog"])
    assert excinfo.value.code == 2


def test_default_summary_history_root_not_under_site_packages():
    root = portfolio_status._default_summary_history_root()
    parts = root.parts
    assert "site-packages" not in parts
    assert root.name == ".history"
    assert "all-results" in parts

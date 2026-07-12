"""Thin CLI shim: implementation lives in eval_audit.reports.portfolio.

Relocated 2026-07-12 (plan item D4 of
docs/planning/repo-simplification-plan-2026-07-12.md): the module was
~390 lines of aggregation/report logic living in cli/. The `python -m
eval_audit.cli.portfolio_status` and `eval-audit-portfolio-status`
entry points keep working through this shim.
"""
from eval_audit.reports.portfolio import (  # noqa: F401
    _default_summary_history_root,
    main,
    summarize_rows,
)

if __name__ == "__main__":
    main()

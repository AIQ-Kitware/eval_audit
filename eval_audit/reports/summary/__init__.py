"""Submodules of the aggregate summary build (Stage 6).

The orchestrator lives in ``eval_audit.workflows.build_reports_summary``
(kept there because runbooks invoke it via ``python -m``); these modules
hold the implementation, split along functional seams. Import direction:
``common`` <- ``classification``/``failure_triage`` <- everything else;
the orchestrator imports from all of them, never the reverse.
"""

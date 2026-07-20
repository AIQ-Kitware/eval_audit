"""Open-weight judge rejudging: snapshots, replay, and analysis.

Implements the execution/artifact layer of
``docs/planning/open-judge-plan.md``: frozen candidate **response
snapshots** (content-addressed by ``response_set_hash``) fanned out
across independently attributable **judgment attempts** with
open-weight judges. The existing analysis-policy seam
(``eval_audit.judge_registry``, ``judge_substitution_planned``) stays
unchanged; this package feeds it honest judge facts.
"""

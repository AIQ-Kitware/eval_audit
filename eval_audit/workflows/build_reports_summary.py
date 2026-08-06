"""Stage 6 orchestrator: aggregate reporting over analyzed experiments.

Builds the operational + reproducibility summary tree (sankeys, agreement
curves, per-metric breakdowns, prioritized examples, READMEs) for every
scope. The implementation lives in ``eval_audit.reports.summary``
(including the scope-rendering recursion, moved to
``reports.summary.scope`` in C1, 2026-07-12); this module keeps the CLI
``main`` plus compat re-exports.
Invoked as ``eval-audit-build-summary`` or
``python -m eval_audit.workflows.build_reports_summary``.
"""
from __future__ import annotations

import argparse
import shlex
from pathlib import Path
from eval_audit.infra.api import default_index_root
from eval_audit.infra.plotly_env import configure_plotly_chrome
from eval_audit.infra.logging import rich_link, setup_cli_logging
from eval_audit.infra.report_layout import (
    aggregate_summary_reports_root,
)
from loguru import logger
from eval_audit.infra.profiling import profile

# --- compat re-exports -------------------------------------------------
# The implementation moved to eval_audit.reports.summary.* on 2026-06-11
# (Phase 2 of docs/historical/planning/repo-refactor-plan.md). Tests and operational
# scripts import these names from this module; keep re-exporting them.
from eval_audit.infra.index_io import resolve_index_fpath
from eval_audit.reports.summary.common import (  # noqa: F401
    DEFAULT_BREAKDOWN_DIMS,
    CANONICAL_AGREEMENT_TOL,
    latest_index_csv,
    load_rows,
    slugify,
    _load_json,
    _write_json,
    _write_text,
    _find_pair,
    _find_curve_value,
    _normalize_text,
    _is_truthy_text,
    _coerce_float,
    _clean_optional_text,
    _preview_values,
    _safe_ratio,
    _safe_float,
    _coerce_listlike,
    _raise_fd_limit,
    _fd_count,
)
from eval_audit.reports.summary.classification import (  # noqa: F401
    _build_attempt_fallback_key_from_row,
    _resolve_attempt_identity,
    _storyline_status,
    _storyline_reason,
    _filter_inventory_lookup_by_run_entry,
    _storyline_metadata_for_model,
    _run_entry_metadata_lookup,
    _default_filter_inventory_json,
    _load_filter_inventory_rows,
    _bucket_agreement,
    agreement_bucket_label,
    FILTER_SELECTION_EXCLUDED_LABEL,
    FILTER_SELECTION_SELECTED_LABEL,
    ATTEMPTED_LABEL,
    NOT_ATTEMPTED_LABEL,
    _group_scope_rows_by_run_entry,
    _group_repro_rows_by_run_entry,
    _classify_execution_stage,
    _choose_repro_row_for_run_entry,
)
from eval_audit.reports.summary.failure_triage import (  # noqa: F401
    _read_log_tail,
    _classify_failure,
    _FAILURE_CATEGORIES,
    _FAILURE_CATEGORY_ORDER,
    _FAILURE_CATEGORY_LABELS,
)
from eval_audit.reports.summary.loading import (  # noqa: F401
    _load_all_repro_rows,
)
from eval_audit.reports.summary.sankeys import (  # noqa: F401
    _build_universe_to_scope_root,
    _build_scope_to_analyzed_root,
    _build_scope_to_analyzed_rows,
    _build_universe_to_scope_rows,
    _bucket_metric_delta,
    _expand_repro_rows_by_metric,
    _build_repro_sankey_rows_at_tol,
    _LEGACY_SANKEY_ALIAS_NAMES,
    _cleanup_legacy_sankey_aliases,
)
from eval_audit.reports.summary.multiplicity import (  # noqa: F401
    _build_analyzed_attempt_matchers,
    _analyzed_match_status,
    _build_run_multiplicity_summary,
    _format_run_multiplicity_summary_text,
    _build_off_story_summary,
    _format_off_story_summary_text,
)
from eval_audit.reports.summary.breakdown import (  # noqa: F401
    _QUANTILE_BUCKET_TARGETS,
    _TRIAGE_DIMENSION_PRIORITY,
    _TRIAGE_BUCKET_CLASS_ORDER,
    _TRIAGE_ABSOLUTE_BUCKETS,
    _TRIAGE_BUCKET_LABELS,
    _agreement_bucket_class,
    _triage_bucket_score,
    _flagged_bucket_score,
    _example_case_sort_key,
    _pick_example_cases,
    _triage_selection_reason,
    _selected_attempt_refs_for_repro_row,
    _attempt_ref_matches_row,
    _choose_parent_row_for_repro,
    _analyzed_dimension_values,
    _build_prioritized_breakdown_summary,
    _format_prioritized_breakdown_summary_text,
)
from eval_audit.reports.summary.publish import (  # noqa: F401
    _iter_prioritized_example_rows,
    _prioritized_example_artifact_names,
    _report_artifact_is_usable,
    _prioritized_example_missing_artifacts,
    _repair_prioritized_example_reports,
    _publish_prioritized_examples_tree,
)
from eval_audit.reports.summary.plots import (  # noqa: F401
    _ordered_unique_values,
    _abbreviate_label,
    _bar_count_label,
    _bar_tickangle,
    _compact_bar_figure_size,
    _write_plotly_bar,
    _write_agreement_curve_plot,
    _write_per_metric_agreement_plot,
    _write_coverage_matrix_plot,
    _write_failure_taxonomy_plot,
)
from eval_audit.reports.summary.publish import (  # noqa: F401
    _write_table_artifacts,
    _write_structured_summary_artifacts,
    _scope_summary_root,
    _scope_label,
    _scope_slug,
    _build_breakdown_rows,
    _build_filter_selection_by_model_rows,
    _summarize_by_dimension,
    _cardinality,
    _build_scope_cardinality_lines,
    _build_high_level_readme,
    _write_scope_level_aliases,
    _build_summary_cmd,
    _write_reproduce_sh,
    _write_delegating_script,
    _write_redraw_plots_sh,
    _FILTER_ARTIFACT_ALIAS_NAMES,
    _cleanup_filter_artifact_aliases,
)


# C1 (2026-07-12): the scope-render layer moved to reports.summary.scope*;
# re-export so callers/tests importing from this module keep working.
from eval_audit.reports.summary.scope_rows import (  # noqa: F401
    _build_enriched_scope_rows,
    _build_scope_sankey_rows,
)
from eval_audit.reports.summary.scope_sankeys import _render_scope_sankeys  # noqa: F401
from eval_audit.reports.summary.scope_plots import (  # noqa: F401
    _render_aggregate_score_diff,
    _render_scope_plots,
)
from eval_audit.reports.summary.scope_publish import (  # noqa: F401
    _write_scope_tables,
    _write_scope_scripts,
    _write_story_index,
)
from eval_audit.reports.summary.scope import (  # noqa: F401
    _render_breakdown_scopes,
    _render_scope_summary,
)


@profile
def main(argv: list[str] | None = None) -> None:
    setup_cli_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment-name", default=None)
    parser.add_argument("--index-fpath", default=None)
    parser.add_argument("--index-dpath", default=str(default_index_root()))
    parser.add_argument("--filter-inventory-json", default=None)
    parser.add_argument(
        "--no-filter-inventory",
        action="store_true",
        help=(
            "Skip loading the Stage-1 filter inventory entirely (overrides "
            "both --filter-inventory-json and the default fallback at "
            "<audit_store>/analysis/filter_inventory.json). Use this for "
            "scoped sub-experiments — e.g. virtual experiments — where the "
            "global filter funnel doesn't describe the report's denominator "
            "and would only mislead the reader. Filter sankeys, the model "
            "selection plot, and the discovered/selected cardinality lines "
            "all naturally drop out when the inventory is empty."
        ),
    )
    parser.add_argument("--summary-root", default=str(aggregate_summary_reports_root()))
    parser.add_argument(
        "--analysis-root",
        action="append",
        default=[],
        help=(
            "Extra directory to scan for per-packet core-report JSONs. "
            "Repeatable. Used for virtual experiments whose analysis lives "
            "under a custom output.root and would otherwise be invisible "
            "to the canonical/publication/legacy scan. Each root is globbed "
            "as <root>/*/core-reports/*/core_metric_report.json."
        ),
    )
    parser.add_argument(
        "--no-canonical-scan",
        action="store_true",
        help=(
            "Skip the default scan of canonical/publication/legacy "
            "experiment-analysis roots and use only the directories passed "
            "via --analysis-root. Useful when running the summary against a "
            "standalone analysis tree (e.g. eval-audit-from-eee) and you "
            "don't want pre-existing experiments on the host to bleed into "
            "the report."
        ),
    )
    parser.add_argument(
        "--breakdown-dims",
        nargs="*",
        default=DEFAULT_BREAKDOWN_DIMS,
    )
    parser.add_argument("--max-items-per-breakdown", type=int, default=12)
    parser.add_argument(
        "--plots-only",
        action="store_true",
        default=False,
        help=(
            "Marker flag emitted by redraw_plots.sh. The full build today "
            "still rebuilds the textual artifacts alongside the plots; the "
            "flag is plumbed through so a future fast-path can short-circuit "
            "the analysis steps and only re-render plotly/matplotlib "
            "outputs. Setting this today is a no-op but the redraw-plots "
            "helper script needs it as a stable contract."
        ),
    )
    args = parser.parse_args(argv)
    _ = args.plots_only  # currently advisory only; reserved for future use

    index_fpath = resolve_index_fpath(args.index_fpath, args.index_dpath)
    filter_inventory_json = (
        Path(args.filter_inventory_json).expanduser().resolve()
        if args.filter_inventory_json
        else None
    )
    rows = load_rows(index_fpath)
    filter_inventory_rows = _load_filter_inventory_rows(
        filter_inventory_json,
        skip=args.no_filter_inventory,
    )
    _raise_fd_limit()  # Note: this probably is not necessary, as fd limits are usually due to a VM issue.
    configure_plotly_chrome()
    unreadable_reports: list[str] = []
    all_repro_rows = _load_all_repro_rows(
        extra_analysis_roots=args.analysis_root,
        skip_canonical_scan=args.no_canonical_scan,
        unreadable_out=unreadable_reports,  # P1-10: surfaced in the manifest below
    )

    if args.experiment_name:
        scope_kind = "experiment_name"
        scope_value = args.experiment_name
        scope_rows = [row for row in rows if row.get("experiment_name") == args.experiment_name]
        if not scope_rows:
            raise SystemExit(f"No rows found for experiment_name={args.experiment_name!r}")
        repro_rows = [row for row in all_repro_rows if row.get("experiment_name") == args.experiment_name]
    else:
        scope_kind = "all_results"
        scope_value = None
        scope_rows = rows
        repro_rows = all_repro_rows

    scope_root = _scope_summary_root(
        Path(args.summary_root).expanduser().resolve(),
        _scope_slug(scope_kind, scope_value),
    )
    # P1-20: when --no-filter-inventory was passed, reproduce.sh must NOT carry
    # a --filter-inventory-json (the old default fallback re-included the
    # inventory the operator explicitly excluded, contradicting the flag below).
    if args.no_filter_inventory:
        filter_inventory_path_for_repro = None
    else:
        filter_inventory_path_for_repro = (
            filter_inventory_json
            if filter_inventory_json is not None
            else (_default_filter_inventory_json() if _default_filter_inventory_json().exists() else None)
        )
    # P1-20: thread the non-default invocation flags into reproduce.sh so a
    # from-eee/virtual build's script regenerates THE SAME report (its scoped
    # analysis root, excluded inventory, custom summary root) rather than a
    # different report at the default root. Canonical (all-results) builds set
    # none of these, so their reproduce.sh is unchanged.
    _repro_extra: list[str] = []
    if args.no_filter_inventory:
        _repro_extra.append("--no-filter-inventory")
    if args.no_canonical_scan:
        _repro_extra.append("--no-canonical-scan")
    for _root in (args.analysis_root or []):
        _repro_extra += ["--analysis-root", shlex.quote(str(Path(_root).expanduser().resolve()))]
    if Path(args.summary_root).expanduser().resolve() != aggregate_summary_reports_root().resolve():
        _repro_extra += ["--summary-root", shlex.quote(str(Path(args.summary_root).expanduser().resolve()))]
    reproduce_extra_args = " ".join(_repro_extra)
    _render_scope_summary(
        scope_kind=scope_kind,
        scope_value=scope_value,
        scope_rows=scope_rows,
        repro_rows=repro_rows,
        filter_inventory_rows=filter_inventory_rows,
        filter_inventory_json=filter_inventory_path_for_repro,
        index_fpath=index_fpath,
        summary_root=scope_root,
        breakdown_dims=list(args.breakdown_dims),
        max_items_per_breakdown=args.max_items_per_breakdown,
        unreadable_reports=unreadable_reports,
        reproduce_extra_args=reproduce_extra_args,
    )
    logger.info(f"Wrote executive summary root: {rich_link(scope_root)}")


if __name__ == "__main__":
    setup_cli_logging()
    main()

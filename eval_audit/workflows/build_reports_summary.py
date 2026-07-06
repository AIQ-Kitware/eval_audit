"""Stage 6 orchestrator: aggregate reporting over analyzed experiments.

Builds the operational + reproducibility summary tree (sankeys, agreement
curves, per-metric breakdowns, prioritized examples, READMEs) for every
scope. The implementation lives in ``eval_audit.reports.summary``; this
module keeps the scope-rendering recursion (``_render_scope_summary`` /
``_render_breakdown_scopes``), the CLI ``main``, and compat re-exports.
Invoked as ``eval-audit-build-summary`` or
``python -m eval_audit.workflows.build_reports_summary``.
"""
from __future__ import annotations

import argparse
import datetime as datetime_mod
import shlex
import shutil
from collections import Counter
from pathlib import Path
from typing import Any
from eval_audit.infra.api import default_index_root
from eval_audit.infra.plotly_env import configure_plotly_chrome
from eval_audit.infra.fs_publish import link_alias, symlink_to
from eval_audit.infra.logging import rich_link, setup_cli_logging
from eval_audit.infra.paths import experiment_analysis_dpath
from eval_audit.infra.report_layout import (
    aggregate_summary_reports_root,
    legacy_repo_publication_root,
    publication_experiments_root,
)
from eval_audit.model_registry import local_model_registry_by_name
from eval_audit.utils.sankey import emit_sankey_artifacts
from loguru import logger
from eval_audit.infra.profiling import profile

# --- compat re-exports -------------------------------------------------
# The implementation moved to eval_audit.reports.summary.* on 2026-06-11
# (Phase 2 of docs/historical/planning/repo-refactor-plan.md). Tests and operational
# scripts import these names from this module; keep re-exporting them.
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
    _iter_prioritized_example_rows,
    _prioritized_example_artifact_names,
    _report_artifact_is_usable,
    _prioritized_example_missing_artifacts,
    _repair_prioritized_example_reports,
    _publish_prioritized_examples_tree,
)
from eval_audit.reports.summary.plots import (  # noqa: F401
    _AXIS_COUNT_TAGS,
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


@profile
def _render_breakdown_scopes(
    *,
    enriched_rows: list[dict[str, Any]],
    all_repro_rows: list[dict[str, Any]],
    filter_inventory_rows: list[dict[str, Any]],
    filter_inventory_json: Path | None,
    index_fpath: Path,
    breakdown_dims: list[str],
    level_002: Path,
    max_items_per_breakdown: int,
    include_values_by_dim: dict[str, list[str]] | None = None,
) -> None:
    breakdowns_root = level_002 / "breakdowns"
    breakdowns_root.mkdir(parents=True, exist_ok=True)
    repro_keyed = {
        (str(row.get("experiment_name")), str(row.get("run_entry"))): row
        for row in all_repro_rows
        if row.get("experiment_name") and row.get("run_entry")
    }
    manifest_rows = []
    for dim in breakdown_dims:
        value_counts = Counter(str(row.get(dim) or "unknown") for row in enriched_rows)
        dim_root = breakdowns_root / f"by_{dim}"
        dim_root.mkdir(parents=True, exist_ok=True)
        top_values = [value for value, _ in value_counts.most_common(max_items_per_breakdown)]
        extra_values = [
            str(value)
            for value in (include_values_by_dim or {}).get(dim, [])
            if str(value) not in top_values
        ]
        top_values.extend(extra_values)
        summary_rows = _summarize_by_dimension(enriched_rows, dimension=dim, repro_keyed=repro_keyed)
        table_artifacts = _write_table_artifacts(summary_rows, dim_root / f"index_{slugify(dim)}")
        for kind in ["json", "csv", "txt"]:
            link_alias(Path(table_artifacts[kind]), dim_root, f"index.{kind}")
        for value in top_values:
            child_rows = [row for row in enriched_rows if str(row.get(dim) or "unknown") == value]
            child_repro = [
                row
                for row in all_repro_rows
                if (str(row.get("experiment_name")), str(row.get("run_entry"))) in {
                    (str(item.get("experiment_name")), str(item.get("run_entry"))) for item in child_rows
                }
            ]
            child_root = dim_root / slugify(value)
            _render_scope_summary(
                scope_kind=dim,
                scope_value=value,
                scope_rows=child_rows,
                repro_rows=child_repro,
                filter_inventory_rows=filter_inventory_rows,
                filter_inventory_json=filter_inventory_json,
                index_fpath=index_fpath,
                summary_root=child_root,
                breakdown_dims=[],
                max_items_per_breakdown=max_items_per_breakdown,
                include_visuals=False,
                # ``level_002.parent`` is the top-level summary_root for
                # this scope; the breakdown renders use it to write
                # delegating reproduce/redraw stubs that exec the
                # top-level scripts instead of emitting an invalid
                # ``--experiment-name <benchmark>`` invocation.
                top_level_summary_root=level_002.parent,
            )
            manifest_rows.append(
                {
                    "breakdown": dim,
                    "value": value,
                    "n_jobs": len(child_rows),
                    "summary_root": str(child_root),
                }
            )
        # P1-5: prune stale value dirs from a previous config (different
        # top_values / max_items) so the advertised filesystem-first navigation
        # never shows outdated slices as current.
        current_value_slugs = {slugify(value) for value in top_values}
        for child in sorted(dim_root.iterdir()):
            if child.is_dir() and child.name not in current_value_slugs:
                logger.info(f"Pruning stale breakdown value dir: {rich_link(child)}")
                shutil.rmtree(child, ignore_errors=True)
    # P1-5: prune whole by_<dim> trees for dimensions no longer requested.
    current_dim_names = {f"by_{dim}" for dim in breakdown_dims}
    for child in sorted(breakdowns_root.iterdir()):
        if child.is_dir() and child.name.startswith("by_") and child.name not in current_dim_names:
            logger.info(f"Pruning stale breakdown dimension dir: {rich_link(child)}")
            shutil.rmtree(child, ignore_errors=True)
    manifest_fpath = breakdowns_root / "manifest.json"
    _write_json(manifest_rows, manifest_fpath)


@profile
def _render_scope_summary(
    *,
    scope_kind: str,
    scope_value: str | None,
    scope_rows: list[dict[str, Any]],
    repro_rows: list[dict[str, Any]],
    filter_inventory_rows: list[dict[str, Any]],
    filter_inventory_json: Path | None,
    index_fpath: Path,
    summary_root: Path,
    breakdown_dims: list[str],
    max_items_per_breakdown: int,
    include_visuals: bool = True,
    top_level_summary_root: Path | None = None,
    unreadable_reports: list[str] | None = None,
    reproduce_extra_args: str = "",
) -> None:
    """Render a summary tree under ``summary_root``.

    When ``top_level_summary_root`` is None (default), this is the
    canonical/top-level call: scope is either ``all_results`` or a real
    ``--experiment-name`` value, so the emitted ``reproduce.sh`` /
    ``redraw_plots.sh`` invoke ``build_reports_summary`` with that scope
    directly.

    When ``top_level_summary_root`` is provided, this is a recursive
    breakdown render (e.g. ``by_benchmark/boolq/``). The scope (e.g.
    ``benchmark`` / ``boolq``) is *not* a filter the CLI knows how to
    honor — there is no ``--benchmark`` flag — so the breakdown's
    ``reproduce.sh`` / ``redraw_plots.sh`` are emitted as **delegating
    stubs** that exec the top-level scripts. Running the breakdown's
    script then regenerates the entire report (including this
    breakdown's slice) which is the only correct refresh for a
    derived view.
    """
    if not scope_rows:
        return

    generated_utc = datetime_mod.datetime.now(datetime_mod.UTC).strftime("%Y%m%dT%H%M%SZ")
    summary_root.mkdir(parents=True, exist_ok=True)
    level_001 = summary_root / "level_001"
    level_002 = summary_root / "level_002"
    level_001.mkdir(parents=True, exist_ok=True)
    level_002.mkdir(parents=True, exist_ok=True)
    level_001_machine = level_001 / "machine"
    level_001_interactive = level_001 / "interactive"
    level_001_static = level_001 / "static"
    level_002_machine = level_002 / "machine"
    level_002_static = level_002 / "static"
    for d in [level_001_machine, level_001_interactive, level_001_static, level_002_machine, level_002_static]:
        d.mkdir(parents=True, exist_ok=True)

    alt_tol_dpath = level_001 / "alt_tolerances"
    alt_tol_machine = alt_tol_dpath / "machine"
    alt_tol_interactive = alt_tol_dpath / "interactive"
    alt_tol_static = alt_tol_dpath / "static"
    for d in [alt_tol_machine, alt_tol_interactive, alt_tol_static]:
        d.mkdir(parents=True, exist_ok=True)

    repro_keyed = {
        (str(row.get("experiment_name")), str(row.get("run_entry"))): row
        for row in repro_rows
        if row.get("experiment_name") and row.get("run_entry")
    }
    filter_lookup = _filter_inventory_lookup_by_run_entry(filter_inventory_rows)
    registry_lookup = local_model_registry_by_name()

    enriched_rows: list[dict[str, Any]] = []
    failed_rows: list[dict[str, Any]] = []
    for row in scope_rows:
        enriched = dict(row)
        enriched["logical_run_key"] = str(row.get("run_entry") or "")
        enriched.update(_resolve_attempt_identity(row))
        filter_row = filter_lookup.get(str(row.get("run_entry") or ""))
        if filter_row is not None:
            for src_key, dst_key in [
                ("scenario", "scenario"),
                ("dataset", "dataset"),
                ("setting", "setting"),
                ("selection_status", "selection_status"),
                ("candidate_pool", "candidate_pool"),
            ]:
                if dst_key not in enriched or not enriched.get(dst_key):
                    enriched[dst_key] = filter_row.get(src_key)
        enriched.update(
            _storyline_metadata_for_model(
                model=_clean_optional_text(enriched.get("model")),
                registry_lookup=registry_lookup,
                filter_row=filter_row,
            )
        )
        completed = _is_truthy_text(row.get("has_run_spec"))
        enriched["completed_with_run_artifacts"] = completed
        enriched["lifecycle_stage"] = "completed_with_run_artifacts" if completed else "failed_or_incomplete"
        key = (str(row.get("experiment_name")), str(row.get("run_entry")))
        repro = repro_keyed.get(key)
        if repro is not None:
            enriched.update(
                {
                    "repro_report_dir": repro.get("report_dir"),
                    "official_instance_agree_tol0": repro.get("official_instance_agree_tol0"),
                    "official_instance_agree_bucket": repro.get("official_instance_agree_bucket"),
                    "official_diagnosis": repro.get("official_diagnosis"),
                    "repeat_diagnosis": repro.get("repeat_diagnosis"),
                }
            )
        elif completed:
            enriched["official_instance_agree_bucket"] = "completed_not_yet_analyzed"
        if not completed:
            failure = _classify_failure(Path(str(row.get("job_dpath"))).expanduser(), row)
            enriched.update(failure)
            failed_rows.append(enriched)
        enriched_rows.append(enriched)

    n_total = len(enriched_rows)
    n_completed = sum(1 for row in enriched_rows if row.get("completed_with_run_artifacts"))
    n_failed = n_total - n_completed
    n_analyzed = len(repro_rows)

    failure_counts = Counter(row.get("failure_reason") or "unknown_failure" for row in failed_rows)
    failure_reason_rows = [
        {"failure_reason": reason, "count": count, "share_of_failed": (count / n_failed) if n_failed else None}
        for reason, count in failure_counts.most_common()
    ]
    repro_bucket_counts = Counter(row.get("official_instance_agree_bucket") or "not_analyzed" for row in repro_rows)
    repro_bucket_rows = [
        {
            "official_instance_agree_bucket": bucket,
            "count": count,
            "share_of_analyzed": (count / n_analyzed) if n_analyzed else None,
        }
        for bucket, count in repro_bucket_counts.most_common()
    ]
    filter_selection_by_model_rows = _build_filter_selection_by_model_rows(filter_inventory_rows)

    benchmark_status_rows = _build_breakdown_rows(enriched_rows, group_key="benchmark", repro_keyed=repro_keyed)
    benchmark_summary = _summarize_by_dimension(enriched_rows, dimension="benchmark", repro_keyed=repro_keyed)
    run_inventory = enriched_rows
    repro_inventory = repro_rows
    off_story_summary = _build_off_story_summary(
        filter_inventory_rows=filter_inventory_rows,
        scope_rows=scope_rows,
        repro_rows=repro_rows,
    )
    run_multiplicity_summary = _build_run_multiplicity_summary(
        filter_inventory_rows=filter_inventory_rows,
        scope_rows=scope_rows,
        repro_rows=repro_rows,
    )
    prioritized_breakdowns_summary = _build_prioritized_breakdown_summary(
        enriched_rows=enriched_rows,
        repro_rows=repro_rows,
        run_multiplicity_summary=run_multiplicity_summary,
        breakdown_dims=breakdown_dims,
        level_002=level_002,
    )
    if breakdown_dims:
        _render_breakdown_scopes(
            enriched_rows=enriched_rows,
            all_repro_rows=repro_rows,
            filter_inventory_rows=filter_inventory_rows,
            filter_inventory_json=filter_inventory_json,
            index_fpath=index_fpath,
            breakdown_dims=breakdown_dims,
            level_002=level_002,
            max_items_per_breakdown=max_items_per_breakdown,
            include_values_by_dim=prioritized_breakdowns_summary.get("include_values_by_dim"),
        )

    operational_sankey_rows = []
    for row in enriched_rows:
        if row.get("completed_with_run_artifacts"):
            outcome = str(row.get("official_instance_agree_bucket") or "completed_not_yet_analyzed")
        else:
            outcome = str(row.get("failure_reason") or "unknown_failure")
        operational_sankey_rows.append(
            {
                "group": str(row.get("benchmark") or "unknown"),
                "lifecycle": str(row.get("lifecycle_stage") or "unknown"),
                "outcome": outcome,
            }
        )

    # IM-2: index enriched rows by (experiment_name, run_entry) once instead of
    # a per-repro-row linear scan (was O(n_repro x n_scope) per render). First
    # enriched row wins on a duplicate key, matching the previous ``next(...)``
    # first-match semantics.
    enriched_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for item in enriched_rows:
        key = (str(item.get("experiment_name")), str(item.get("run_entry")))
        enriched_by_key.setdefault(key, item)
    repro_sankey_rows = []
    for row in repro_rows:
        parent = enriched_by_key.get(
            (str(row.get("experiment_name")), str(row.get("run_entry")))
        )
        repro_sankey_rows.append(
            {
                "group": str((parent or {}).get("benchmark") or "unknown"),
                "repeatability": str(row.get("repeat_diagnosis") or "unknown"),
                "agreement": str(row.get("official_instance_agree_bucket") or "not_analyzed"),
                "diagnosis": str(row.get("official_diagnosis") or "unknown"),
            }
        )

    repro_tol001_rows = _build_repro_sankey_rows_at_tol(repro_rows, enriched_rows, "official_instance_agree_tol0p001")
    # P0-1: the tol010 variant is titled abs_tol=0.010 and must bucket on the
    # 0.01 curve point (``_010``), not the 0.1 point (``_01``).
    repro_tol010_rows = _build_repro_sankey_rows_at_tol(repro_rows, enriched_rows, "official_instance_agree_tol0p01")
    repro_tol050_rows = _build_repro_sankey_rows_at_tol(repro_rows, enriched_rows, "official_instance_agree_tol0p05")
    metric_sankey_rows = _expand_repro_rows_by_metric(repro_rows, enriched_rows)
    # Stage A — Universe -> Scope: pure filter-funnel ending at the
    # selection waist. No tolerance variant (Stage A is independent of
    # reproduction agreement). Replaces the legacy ``filter_to_attempt``
    # row-builder which also reached into post-selection territory.
    universe_to_scope_rows = _build_universe_to_scope_rows(filter_inventory_rows)

    # Stage B — Scope -> Attempt -> Execution -> Analysis -> Reproduction.
    # Source population is filter_inventory rows with selection_status='selected'
    # (the in-scope set). Tolerance variants drive the reproduction-stage
    # waist at different abs_tol values.
    scope_to_analyzed_exact_rows = _build_scope_to_analyzed_rows(
        filter_inventory_rows,
        scope_rows,
        repro_rows,
        tol_key="official_instance_agree_tol0",
    )
    scope_to_analyzed_tol001_rows = _build_scope_to_analyzed_rows(
        filter_inventory_rows,
        scope_rows,
        repro_rows,
        tol_key="official_instance_agree_tol0p001",
    )
    scope_to_analyzed_tol010_rows = _build_scope_to_analyzed_rows(
        filter_inventory_rows,
        scope_rows,
        repro_rows,
        # P0-1: 0.01 curve point (``_010``), not the 0.1 point (``_01``).
        tol_key="official_instance_agree_tol0p01",
    )
    scope_to_analyzed_tol050_rows = _build_scope_to_analyzed_rows(
        filter_inventory_rows,
        scope_rows,
        repro_rows,
        tol_key="official_instance_agree_tol0p05",
    )
    # The legacy combined Universe->Reproducible sankey (s04) is intentionally
    # dropped: Stage A and Stage B together carry the same information without
    # the eight-stage cramping that made the combined view unreadable. Anyone
    # reading both sankeys side-by-side recovers the full chain.

    scope_title = _scope_label(scope_kind, scope_value)
    if include_visuals:
        operational_art = emit_sankey_artifacts(
            rows=operational_sankey_rows,
            report_dpath=level_001,
            stamp=generated_utc,
            kind="s01_operational",
            title=f"Executive Operational Summary: {scope_title}",
            stage_defs={
                "group": ["benchmark family or suite"],
                "lifecycle": ["whether the run produced runnable artifacts"],
                "outcome": [
                    "for failed/incomplete runs: failure reason",
                    f"for completed runs: instance-level agreement bucket at abs_tol={CANONICAL_AGREEMENT_TOL:g}",
                    f"  exact_or_near_exact: >=99.9999% of instances agree within abs_tol={CANONICAL_AGREEMENT_TOL:g}",
                    f"  high_agreement_0.95+: >=95% of instances agree within abs_tol={CANONICAL_AGREEMENT_TOL:g}",
                    f"  moderate_agreement_0.80+: >=80% agree within abs_tol={CANONICAL_AGREEMENT_TOL:g}",
                    f"  low_agreement_0.00+: >0% agree within abs_tol={CANONICAL_AGREEMENT_TOL:g}",
                    f"  zero_agreement: no instances agree within abs_tol={CANONICAL_AGREEMENT_TOL:g}",
                ],
            },
            stage_order=[("group", "group"), ("lifecycle", "lifecycle"), ("outcome", "outcome")],
            machine_dpath=level_001_machine,
            interactive_dpath=level_001_interactive,
            static_dpath=level_001_static,
        )
        repro_art = emit_sankey_artifacts(
            rows=repro_sankey_rows,
            report_dpath=level_001,
            stamp=generated_utc,
            kind="s05_reproducibility",
            title=f"Reproducibility Summary (instance-level, abs_tol={CANONICAL_AGREEMENT_TOL:g} canonical): {scope_title}",
            stage_defs={
                "group": ["benchmark family or suite"],
                "repeatability": ["local repeatability diagnosis (run vs its own repeat)"],
                "agreement": [
                    f"official-vs-local agreement bucket at abs_tol={CANONICAL_AGREEMENT_TOL:g} (canonical)",
                    f"fraction = share of instances where |official_score - local_score| <= {CANONICAL_AGREEMENT_TOL:g}",
                    "  exact_or_near_exact: fraction >= 0.999999",
                    "  high_agreement_0.95+: fraction >= 0.95",
                    "  moderate_agreement_0.80+: fraction >= 0.80",
                    "  low_agreement_0.00+: fraction > 0.0",
                    "  zero_agreement: fraction == 0.0",
                ],
                "diagnosis": ["top-level diagnosis from official-vs-local comparison"],
            },
            stage_order=[
                ("group", "group"),
                ("repeatability", "repeatability"),
                ("agreement", "agreement"),
                ("diagnosis", "diagnosis"),
            ],
            machine_dpath=level_001_machine,
            interactive_dpath=level_001_interactive,
            static_dpath=level_001_static,
        )
        _repro_stage_order = [
            ("group", "group"),
            ("repeatability", "repeatability"),
            ("agreement", "agreement"),
            ("diagnosis", "diagnosis"),
        ]
        _repro_stage_defs = {
            "group": ["benchmark family or suite"],
            "repeatability": ["local repeatability diagnosis (run vs its own repeat)"],
            "agreement": [
                f"official-vs-local agreement bucket at the abs_tol stated in the title (canonical abs_tol={CANONICAL_AGREEMENT_TOL:g})",
                "fraction = share of instances where |official_score - local_score| <= abs_tol",
                "  exact_or_near_exact: fraction >= 0.999999",
                "  high_agreement_0.95+: fraction >= 0.95",
                "  moderate_agreement_0.80+: fraction >= 0.80",
                "  low_agreement_0.00+: fraction > 0.0",
                "  zero_agreement: fraction == 0.0",
            ],
            "diagnosis": ["top-level diagnosis from official-vs-local comparison"],
        }
        repro_tol001_art = emit_sankey_artifacts(
            rows=repro_tol001_rows,
            report_dpath=alt_tol_dpath,
            stamp=generated_utc,
            kind="repro_tol001",
            title=f"Reproducibility at abs_tol=0.001: {scope_title}",
            stage_defs=_repro_stage_defs,
            stage_order=_repro_stage_order,
            machine_dpath=alt_tol_machine,
            interactive_dpath=alt_tol_interactive,
            static_dpath=alt_tol_static,
        )
        repro_tol010_art = emit_sankey_artifacts(
            rows=repro_tol010_rows,
            report_dpath=alt_tol_dpath,
            stamp=generated_utc,
            kind="repro_tol010",
            title=f"Reproducibility at abs_tol=0.010: {scope_title}",
            stage_defs=_repro_stage_defs,
            stage_order=_repro_stage_order,
            machine_dpath=alt_tol_machine,
            interactive_dpath=alt_tol_interactive,
            static_dpath=alt_tol_static,
        )
        repro_tol050_art = emit_sankey_artifacts(
            rows=repro_tol050_rows,
            report_dpath=alt_tol_dpath,
            stamp=generated_utc,
            kind="repro_tol050",
            title=f"Reproducibility at abs_tol=0.050: {scope_title}",
            stage_defs=_repro_stage_defs,
            stage_order=_repro_stage_order,
            machine_dpath=alt_tol_machine,
            interactive_dpath=alt_tol_interactive,
            static_dpath=alt_tol_static,
        )
        repro_metric_art = emit_sankey_artifacts(
            rows=metric_sankey_rows,
            report_dpath=level_001,
            stamp=generated_utc,
            kind="repro_by_metric",
            title=f"Per-Metric Reproducibility Drift (run-level max |official - local|): {scope_title}",
            stage_defs={
                "group": ["benchmark family or suite"],
                "metric": ["core metric name (e.g. exact_match, f1_score, rouge_l)"],
                "drift_bucket": [
                    "signal: max absolute delta between official and local score across all runs",
                    "  exact_match:      max |official - local| == 0.0  (bit-perfect agreement)",
                    "  tiny_drift_0.001: max |official - local| <= 0.001",
                    "  small_drift_0.01: max |official - local| <= 0.01",
                    "  large_drift:      max |official - local|  > 0.01",
                    "  not_available:    metric not present in run-level data",
                ],
            },
            stage_order=[("group", "group"), ("metric", "metric"), ("drift_bucket", "drift_bucket")],
            machine_dpath=level_001_machine,
            interactive_dpath=level_001_interactive,
            static_dpath=level_001_static,
        )
        # Stage A — Universe -> Scope (no tolerance variant; tolerance is a
        # post-selection concept)
        a_root, a_stage_names, a_stage_defs = _build_universe_to_scope_root()
        empty_art = {"json": None, "txt": None, "key_txt": None, "html": None, "jpg": None, "plotly_error": "no filter inventory rows available"}
        universe_to_scope_art = emit_sankey_artifacts(
            rows=universe_to_scope_rows,
            report_dpath=level_001,
            stamp=generated_utc,
            kind="a_universe_to_scope",
            title=f"Stage A — Universe → Scope (filter funnel): {scope_title}",
            stage_defs=a_stage_defs,
            stage_order=[],
            root=a_root,
            explicit_stage_names=a_stage_names,
            machine_dpath=level_001_machine,
            interactive_dpath=level_001_interactive,
            static_dpath=level_001_static,
        ) if universe_to_scope_rows else dict(empty_art)

        # Stage B — Scope -> Attempt -> Execution -> Analysis -> Reproduction
        b_root, b_stage_names, b_stage_defs = _build_scope_to_analyzed_root()
        empty_b_art = {"json": None, "txt": None, "key_txt": None, "html": None, "jpg": None, "plotly_error": "no in-scope rows available"}
        scope_to_analyzed_art = emit_sankey_artifacts(
            rows=scope_to_analyzed_exact_rows,
            report_dpath=level_001,
            stamp=generated_utc,
            kind="b_scope_to_analyzed",
            title=f"Stage B — Scope → Analyzed at abs_tol=0: {scope_title}",
            stage_defs=b_stage_defs,
            stage_order=[],
            root=b_root,
            explicit_stage_names=b_stage_names,
            machine_dpath=level_001_machine,
            interactive_dpath=level_001_interactive,
            static_dpath=level_001_static,
        ) if scope_to_analyzed_exact_rows else dict(empty_b_art)
        scope_to_analyzed_tol001_art = emit_sankey_artifacts(
            rows=scope_to_analyzed_tol001_rows,
            report_dpath=alt_tol_dpath,
            stamp=generated_utc,
            kind="b_scope_to_analyzed_tol001",
            title=f"Stage B — Scope → Analyzed at abs_tol=0.001: {scope_title}",
            stage_defs=b_stage_defs,
            stage_order=[],
            root=b_root,
            explicit_stage_names=b_stage_names,
            machine_dpath=alt_tol_machine,
            interactive_dpath=alt_tol_interactive,
            static_dpath=alt_tol_static,
        ) if scope_to_analyzed_tol001_rows else dict(empty_b_art)
        scope_to_analyzed_tol010_art = emit_sankey_artifacts(
            rows=scope_to_analyzed_tol010_rows,
            report_dpath=alt_tol_dpath,
            stamp=generated_utc,
            kind="b_scope_to_analyzed_tol010",
            title=f"Stage B — Scope → Analyzed at abs_tol=0.010: {scope_title}",
            stage_defs=b_stage_defs,
            stage_order=[],
            root=b_root,
            explicit_stage_names=b_stage_names,
            machine_dpath=alt_tol_machine,
            interactive_dpath=alt_tol_interactive,
            static_dpath=alt_tol_static,
        ) if scope_to_analyzed_tol010_rows else dict(empty_b_art)
        scope_to_analyzed_tol050_art = emit_sankey_artifacts(
            rows=scope_to_analyzed_tol050_rows,
            report_dpath=alt_tol_dpath,
            stamp=generated_utc,
            kind="b_scope_to_analyzed_tol050",
            title=f"Stage B — Scope → Analyzed at abs_tol=0.050: {scope_title}",
            stage_defs=b_stage_defs,
            stage_order=[],
            root=b_root,
            explicit_stage_names=b_stage_names,
            machine_dpath=alt_tol_machine,
            interactive_dpath=alt_tol_interactive,
            static_dpath=alt_tol_static,
        ) if scope_to_analyzed_tol050_rows else dict(empty_b_art)
        # Backwards-compatible aliases for the old variable names so the
        # downstream manifest schema (and any callers reading it) keeps
        # working until they migrate to the new keys.
        filter_to_attempt_art = universe_to_scope_art
        attempted_to_repro_art = scope_to_analyzed_art
        attempted_to_repro_tol001_art = scope_to_analyzed_tol001_art
        attempted_to_repro_tol010_art = scope_to_analyzed_tol010_art
        attempted_to_repro_tol050_art = scope_to_analyzed_tol050_art
        # Combined Universe->Reproducible sankey is dropped (see comment above).
        end_to_end_art = dict(empty_art)
        end_to_end_tol001_art = dict(empty_art)
        end_to_end_tol010_art = dict(empty_art)
        end_to_end_tol050_art = dict(empty_art)
    else:
        operational_art = {"json": None, "txt": None, "key_txt": None, "html": None, "jpg": None, "plotly_error": None}
        repro_art = {"json": None, "txt": None, "key_txt": None, "html": None, "jpg": None, "plotly_error": None}
        repro_tol001_art = {"json": None, "txt": None, "key_txt": None, "html": None, "jpg": None, "plotly_error": None}
        repro_tol010_art = {"json": None, "txt": None, "key_txt": None, "html": None, "jpg": None, "plotly_error": None}
        repro_tol050_art = {"json": None, "txt": None, "key_txt": None, "html": None, "jpg": None, "plotly_error": None}
        repro_metric_art = {"json": None, "txt": None, "key_txt": None, "html": None, "jpg": None, "plotly_error": None}
        filter_to_attempt_art = {"json": None, "txt": None, "key_txt": None, "html": None, "jpg": None, "plotly_error": None}
        attempted_to_repro_art = {"json": None, "txt": None, "key_txt": None, "html": None, "jpg": None, "plotly_error": None}
        attempted_to_repro_tol001_art = {"json": None, "txt": None, "key_txt": None, "html": None, "jpg": None, "plotly_error": None}
        attempted_to_repro_tol010_art = {"json": None, "txt": None, "key_txt": None, "html": None, "jpg": None, "plotly_error": None}
        attempted_to_repro_tol050_art = {"json": None, "txt": None, "key_txt": None, "html": None, "jpg": None, "plotly_error": None}
        end_to_end_art = {"json": None, "txt": None, "key_txt": None, "html": None, "jpg": None, "plotly_error": None}
        end_to_end_tol001_art = {"json": None, "txt": None, "key_txt": None, "html": None, "jpg": None, "plotly_error": None}
        end_to_end_tol010_art = {"json": None, "txt": None, "key_txt": None, "html": None, "jpg": None, "plotly_error": None}
        end_to_end_tol050_art = {"json": None, "txt": None, "key_txt": None, "html": None, "jpg": None, "plotly_error": None}

    failure_table = _write_table_artifacts(failed_rows, level_001 / "failure_runs", machine_dpath=level_001_machine, static_dpath=level_001_static)
    failure_reason_table = _write_table_artifacts(failure_reason_rows, level_001 / "failure_reasons", machine_dpath=level_001_machine, static_dpath=level_001_static)
    benchmark_table = _write_table_artifacts(benchmark_summary, level_002 / "benchmark_summary", machine_dpath=level_002_machine, static_dpath=level_002_static)
    run_inventory_table = _write_table_artifacts(run_inventory, level_002 / "run_inventory", machine_dpath=level_002_machine, static_dpath=level_002_static)
    repro_table = _write_table_artifacts(repro_inventory, level_002 / "reproducibility_rows", machine_dpath=level_002_machine, static_dpath=level_002_static)
    off_story_table = _write_structured_summary_artifacts(
        rows=off_story_summary["rows"],
        payload={
            "generated_utc": generated_utc,
            "scope_title": scope_title,
            **off_story_summary,
        },
        txt_lines=_format_off_story_summary_text(
            scope_title=scope_title,
            generated_utc=generated_utc,
            summary=off_story_summary,
        ),
        stem=level_002 / "off_story_summary",
        machine_dpath=level_002_machine,
        static_dpath=level_002_static,
    )
    run_multiplicity_table = _write_structured_summary_artifacts(
        rows=run_multiplicity_summary["rows"],
        payload={
            "generated_utc": generated_utc,
            "scope_title": scope_title,
            **run_multiplicity_summary,
        },
        txt_lines=_format_run_multiplicity_summary_text(
            scope_title=scope_title,
            generated_utc=generated_utc,
            summary=run_multiplicity_summary,
        ),
        stem=level_002 / "run_multiplicity_summary",
        machine_dpath=level_002_machine,
        static_dpath=level_002_static,
    )
    prioritized_breakdowns_table = _write_structured_summary_artifacts(
        rows=prioritized_breakdowns_summary["rows"],
        payload={
            "generated_utc": generated_utc,
            "scope_title": scope_title,
            **prioritized_breakdowns_summary,
        },
        txt_lines=_format_prioritized_breakdown_summary_text(
            scope_title=scope_title,
            generated_utc=generated_utc,
            summary=prioritized_breakdowns_summary,
        ),
        stem=level_002 / "prioritized_breakdowns",
        machine_dpath=level_002_machine,
        static_dpath=level_002_static,
    )
    prioritized_example_repairs = _repair_prioritized_example_reports(
        summary=prioritized_breakdowns_summary,
        index_fpath=index_fpath,
    )
    prioritized_examples_tree = _publish_prioritized_examples_tree(
        level_002=level_002,
        generated_utc=generated_utc,
        summary=prioritized_breakdowns_summary,
        repair_results=prioritized_example_repairs,
    )

    if include_visuals:
        benchmark_plot = _write_plotly_bar(
            rows=benchmark_status_rows,
            x="group_value",
            y="count",
            color="status_bucket",
            title=f"Benchmark Coverage and Analysis Status (analyzed runs use abs_tol={CANONICAL_AGREEMENT_TOL:g}): {scope_title}",
            stem=level_001 / "benchmark_status",
            machine_dpath=level_001_machine,
            interactive_dpath=level_001_interactive,
            static_dpath=level_001_static,
            xaxis_title="Benchmark",
            xaxis_count_key="benchmark",
            yaxis_title="Job Count",
        )
        repro_bucket_plot = _write_plotly_bar(
            rows=repro_bucket_rows,
            x="official_instance_agree_bucket",
            y="count",
            color="official_instance_agree_bucket",
            title=f"Official vs Local Agreement Buckets (instance-level, abs_tol={CANONICAL_AGREEMENT_TOL:g} canonical): {scope_title}",
            stem=level_001 / "reproducibility_buckets",
            machine_dpath=level_001_machine,
            interactive_dpath=level_001_interactive,
            static_dpath=level_001_static,
            xaxis_title="Agreement Bucket",
            xaxis_count_key="official_instance_agree_bucket",
            yaxis_title="Run Count",
        )
        agreement_curve_plot = _write_agreement_curve_plot(
            repro_rows=repro_rows,
            enriched_rows=enriched_rows,
            stem=level_001 / "agreement_curve",
            title="Agreement Rate vs Tolerance (instance-level)",
            machine_dpath=level_001_machine,
            interactive_dpath=level_001_interactive,
            static_dpath=level_001_static,
            scope_title=scope_title,
        )
        per_metric_agreement_plot = _write_per_metric_agreement_plot(
            repro_rows=repro_rows,
            enriched_rows=enriched_rows,
            stem=level_001 / "agreement_curve_per_metric",
            title=f"Agreement Rate vs Tolerance (per-metric): {scope_title}",
            machine_dpath=level_001_machine,
            interactive_dpath=level_001_interactive,
            static_dpath=level_001_static,
        )
        coverage_matrix_plot = _write_coverage_matrix_plot(
            enriched_rows=enriched_rows,
            repro_rows=repro_rows,
            stem=level_001 / "coverage_matrix",
            title=f"Model × Benchmark Coverage and Reproducibility Status: {scope_title}",
            machine_dpath=level_001_machine,
            interactive_dpath=level_001_interactive,
            static_dpath=level_001_static,
        )
        failure_taxonomy_plot = _write_failure_taxonomy_plot(
            failed_rows=failed_rows,
            stem=level_001 / "failure_taxonomy",
            title=f"Why Jobs Failed: Root Cause Taxonomy by Benchmark: {scope_title}",
            machine_dpath=level_001_machine,
            interactive_dpath=level_001_interactive,
            static_dpath=level_001_static,
        )
        filter_selection_by_model_plot = _write_plotly_bar(
            rows=filter_selection_by_model_rows,
            x="model",
            y="count",
            color="selection_status",
            title=f"Selected vs Excluded Run Specs by Model: {scope_title}",
            stem=level_001 / "filter_selection_by_model",
            machine_dpath=level_001_machine,
            interactive_dpath=level_001_interactive,
            static_dpath=level_001_static,
            xaxis_title="Model",
            xaxis_count_key="model",
            yaxis_title="Run Spec Count",
        )
    else:
        benchmark_plot = {"json": None, "html": None, "jpg": None, "png": None, "plotly_error": None}
        repro_bucket_plot = {"json": None, "html": None, "jpg": None, "png": None, "plotly_error": None}
        agreement_curve_plot = {"json": None, "html": None, "jpg": None, "plotly_error": None}
        per_metric_agreement_plot = {"json": None, "html": None, "jpg": None, "plotly_error": None}
        coverage_matrix_plot = {"json": None, "html": None, "jpg": None, "plotly_error": None}
        failure_taxonomy_plot = {"json": None, "html": None, "jpg": None, "plotly_error": None}
        filter_selection_by_model_plot = {"json": None, "html": None, "jpg": None, "png": None, "plotly_error": None}

    level_001_readme = _build_high_level_readme(
        scope_title=scope_title,
        generated_utc=generated_utc,
        n_total=n_total,
        n_completed=n_completed,
        n_analyzed=n_analyzed,
        n_failed=n_failed,
        top_failure_rows=failure_reason_rows,
        top_repro_rows=repro_bucket_rows,
        breakdown_dims=breakdown_dims,
    )
    _write_text(level_001_readme, level_001 / "README.txt")

    cardinality_lines = _build_scope_cardinality_lines(
        filter_inventory_rows=filter_inventory_rows,
        enriched_rows=enriched_rows,
        scope_title=scope_title,
        generated_utc=generated_utc,
    )
    cardinality_fpath = level_001_static / "cardinality_summary.txt"
    _write_text(cardinality_lines, cardinality_fpath)
    # (the level_001_static self-alias was a no-op; keep the cross-dir alias)
    link_alias(cardinality_fpath, level_001, "cardinality_summary.txt")

    level_002_lines = [
        "Drilldown Summary",
        "",
        f"generated_utc: {generated_utc}",
        f"scope: {scope_title}",
        "",
        "contents:",
        "  - benchmark_summary.csv: benchmark-level counts and top failure reason",
        "  - run_inventory.csv: one row per scheduled job with completion, failure, repro, and attempt identity/provenance fields",
        "  - reproducibility_rows.csv: analyzed per-run reproducibility cases in this scope",
        "  - prioritized_breakdowns.{txt,csv,json}: ranked triage shortlist of breakdowns and example cases to inspect next",
        "  - prioritized_examples/: filesystem-first symlink tree for the shortlisted breakdowns and example report artifacts",
        "  - off_story_summary.{txt,csv,json}: off-story local extensions plus on-story context counts",
        "  - run_multiplicity_summary.{txt,csv,json}: logical-run multiplicity, attempt identity, machine spread, and experiment spread",
    ]
    if breakdown_dims:
        level_002_lines.append("  - breakdowns/: reusable summaries for additional cuts of the same data")
    _write_text(level_002_lines, level_002 / "README.txt")

    latest_pairs = [
        (level_001 / "README.txt", level_001, "README.txt"),
        (level_002 / "README.txt", level_002, "README.txt"),
        (Path(failure_table["json"]), level_001_machine, "failure_runs.json"),
        (Path(failure_table["csv"]), level_001_static, "failure_runs.csv"),
        (Path(failure_table["txt"]), level_001_static, "failure_runs.txt"),
        (Path(failure_reason_table["json"]), level_001_machine, "failure_reasons.json"),
        (Path(failure_reason_table["csv"]), level_001_static, "failure_reasons.csv"),
        (Path(failure_reason_table["txt"]), level_001_static, "failure_reasons.txt"),
        (Path(benchmark_table["json"]), level_002_machine, "benchmark_summary.json"),
        (Path(benchmark_table["csv"]), level_002_static, "benchmark_summary.csv"),
        (Path(benchmark_table["txt"]), level_002_static, "benchmark_summary.txt"),
        (Path(run_inventory_table["json"]), level_002_machine, "run_inventory.json"),
        (Path(run_inventory_table["csv"]), level_002_static, "run_inventory.csv"),
        (Path(run_inventory_table["txt"]), level_002_static, "run_inventory.txt"),
        (Path(repro_table["json"]), level_002_machine, "reproducibility_rows.json"),
        (Path(repro_table["csv"]), level_002_static, "reproducibility_rows.csv"),
        (Path(repro_table["txt"]), level_002_static, "reproducibility_rows.txt"),
        (Path(prioritized_breakdowns_table["json"]), level_002_machine, "prioritized_breakdowns.json"),
        (Path(prioritized_breakdowns_table["csv"]), level_002_static, "prioritized_breakdowns.csv"),
        (Path(prioritized_breakdowns_table["txt"]), level_002_static, "prioritized_breakdowns.txt"),
        (Path(off_story_table["json"]), level_002_machine, "off_story_summary.json"),
        (Path(off_story_table["csv"]), level_002_static, "off_story_summary.csv"),
        (Path(off_story_table["txt"]), level_002_static, "off_story_summary.txt"),
        (Path(run_multiplicity_table["json"]), level_002_machine, "run_multiplicity_summary.json"),
        (Path(run_multiplicity_table["csv"]), level_002_static, "run_multiplicity_summary.csv"),
        (Path(run_multiplicity_table["txt"]), level_002_static, "run_multiplicity_summary.txt"),
    ]
    for src, root, name in latest_pairs:
        link_alias(src, root, name)
    link_alias(prioritized_examples_tree, level_002, "prioritized_examples")

    if include_visuals:
        for base_name, artifact in [
            ("benchmark_status", benchmark_plot),
            ("reproducibility_buckets", repro_bucket_plot),
            ("agreement_curve", agreement_curve_plot),
            ("coverage_matrix", coverage_matrix_plot),
            ("failure_taxonomy", failure_taxonomy_plot),
            ("filter_selection_by_model", filter_selection_by_model_plot),
        ]:
            link_alias(Path(artifact["json"]), level_001_machine, f"{base_name}.json")
            if artifact.get("html"):
                link_alias(Path(str(artifact["html"])), level_001_interactive, f"{base_name}.html")
            if artifact.get("png"):
                link_alias(Path(str(artifact["png"])), level_001_static, f"{base_name}.png")
            if artifact.get("jpg"):
                link_alias(Path(str(artifact["jpg"])), level_001_static, f"{base_name}.jpg")

    manifest = {
        "generated_utc": generated_utc,
        "scope_kind": scope_kind,
        "scope_value": scope_value,
        "scope_title": scope_title,
        "summary_root": str(summary_root),
        # version_dpath was the per-stamp subdir under .history/ before the
        # 2026-04-28 history retirement; the field is kept in the manifest
        # for backwards compat but now equals summary_root.
        "version_dpath": str(summary_root),
        "level_001": str(level_001),
        "level_002": str(level_002),
        "n_total": n_total,
        "n_completed": n_completed,
        "n_failed": n_failed,
        "n_analyzed": n_analyzed,
        # P1-10: corrupt/unreadable core_metric_report.json bundles are excluded
        # from the analyzed set; surface how many + which, so a silent drop in
        # n_analyzed (runs falling back to "completed_not_yet_analyzed") is
        # visible rather than mysterious.
        "n_unreadable_reports": len(unreadable_reports or []),
        "unreadable_reports": list(unreadable_reports or []),
        "breakdown_dims": breakdown_dims,
        "operational_sankey": operational_art,
        "filter_to_attempt_sankey": filter_to_attempt_art,
        "attempted_to_repro_sankey": attempted_to_repro_art,
        "attempted_to_repro_sankey_tol001": attempted_to_repro_tol001_art,
        "attempted_to_repro_sankey_tol010": attempted_to_repro_tol010_art,
        "attempted_to_repro_sankey_tol050": attempted_to_repro_tol050_art,
        "end_to_end_sankey": end_to_end_art,
        "end_to_end_sankey_tol001": end_to_end_tol001_art,
        "end_to_end_sankey_tol010": end_to_end_tol010_art,
        "end_to_end_sankey_tol050": end_to_end_tol050_art,
        "reproducibility_sankey": repro_art,
        "reproducibility_sankey_tol001": repro_tol001_art,
        "reproducibility_sankey_tol010": repro_tol010_art,
        "reproducibility_sankey_tol050": repro_tol050_art,
        "reproducibility_sankey_by_metric": repro_metric_art,
        "benchmark_plot": benchmark_plot,
        "repro_bucket_plot": repro_bucket_plot,
        "agreement_curve_plot": agreement_curve_plot,
        "coverage_matrix_plot": coverage_matrix_plot,
        "failure_taxonomy_plot": failure_taxonomy_plot,
        "filter_selection_by_model_plot": filter_selection_by_model_plot,
        "prioritized_breakdowns": prioritized_breakdowns_table,
        "prioritized_examples": {
            "tree_root": str(prioritized_examples_tree),
            "repairs": prioritized_example_repairs,
        },
        "off_story_summary": off_story_table,
        "run_multiplicity_summary": run_multiplicity_table,
        "identity_contract": run_multiplicity_summary.get("definitions"),
    }
    manifest_fpath = level_001_machine / "summary_manifest.json"
    _write_json(manifest, manifest_fpath)

    if top_level_summary_root is None:
        # Top-level scope: emit real scripts that invoke the build CLI
        # with --experiment-name (or no scope arg for all_results).
        reproduce_sh_fpath = level_001 / "reproduce.sh"
        _write_reproduce_sh(
            reproduce_sh_fpath,
            scope_kind,
            scope_value,
            index_path=index_fpath,
            filter_inventory_json=filter_inventory_json,
            extra_args=reproduce_extra_args,
        )
        redraw_plots_fpath = level_001 / "redraw_plots.sh"
        _write_redraw_plots_sh(
            redraw_plots_fpath,
            scope_kind,
            scope_value,
            index_path=index_fpath,
            filter_inventory_json=filter_inventory_json,
        )
    else:
        # Breakdown sub-render: there is no CLI flag for "scope by
        # benchmark / model / machine / suite", so we cannot emit a
        # standalone build invocation. Stub scripts delegate to the
        # top-level scripts; running them rebuilds the whole report
        # (which regenerates this slice as a side effect — the only
        # correct refresh for a derived view).
        top_reproduce = top_level_summary_root / "reproduce.sh"
        top_redraw = top_level_summary_root / "redraw_plots.sh"
        _write_delegating_script(
            level_001 / "reproduce.sh",
            target_script=top_reproduce,
            purpose=(
                f"Delegating reproduce.sh for breakdown {scope_kind}={scope_value!r}."
            ),
        )
        _write_delegating_script(
            level_001 / "redraw_plots.sh",
            target_script=top_redraw,
            purpose=(
                f"Delegating redraw_plots.sh for breakdown {scope_kind}={scope_value!r}."
            ),
        )

    symlink_to(level_002, level_001 / "next_level")
    symlink_to(level_001, level_002 / "up_level")
    experiment_names = {str(row.get("experiment_name")) for row in enriched_rows if row.get("experiment_name")}
    if len(experiment_names) == 1:
        exp_name = next(iter(experiment_names))
        # Resolve the experiment-analysis target by checking the canonical
        # store location first, then the parameterized publication-side
        # symlink directory, then the in-repo legacy location. Anyone of
        # them may hold the live reference depending on when this experiment
        # was last analyzed.
        candidates = [
            experiment_analysis_dpath(exp_name),
            publication_experiments_root() / f"experiment-analysis-{slugify(exp_name)}",
            legacy_repo_publication_root() / f"experiment-analysis-{slugify(exp_name)}",
        ]
        analysis_dpath = next((c for c in candidates if c.exists()), None)
        if analysis_dpath is not None:
            symlink_to(analysis_dpath, level_002 / "experiment-analysis")

    story_index_lines = [
        "Story Index — Canonical Reading Order",
        "======================================",
        f"Generated: {generated_utc}",
        f"Scope: {scope_title}",
        "",
        "The reproducibility story has two stages plus an executive summary",
        "and a detail view. Read in order:",
        "",
        "s01 — Executive Operational Summary",
        "  All attempted runs: benchmark group → lifecycle status → outcome/failure reason.",
        "  File: sankey_s01_operational.{html,jpg,txt}",
        "",
        "Stage A — Universe → Scope (filter funnel)",
        "  How the source universe gets narrowed to the in-scope set. Every filter gate",
        "  (structural, model metadata, open-weight, tag/modality, deployment, size,",
        "  selection) is a stage; terminal nodes are 'selected' (in scope) or",
        "  'excluded: <reason>'. This is the context-establishment view.",
        "  File: sankey_a_universe_to_scope.{html,jpg,txt}",
        "",
        "Stage B — Scope → Attempt → Execution → Analysis → Reproduction",
        "  Of the in-scope rows, how many we attempted, completed, analyzed, and at",
        "  what agreement bucket they landed (abs_tol=0). This is the coverage view.",
        "  File: sankey_b_scope_to_analyzed.{html,jpg,txt}",
        "  Tolerance variants live under alt_tolerances/ as",
        "  sankey_b_scope_to_analyzed_tol{001,010,050}.",
        "",
        "s05 — Detailed Reproducibility Breakdown",
        "  Group → local repeatability → official-vs-local agreement → diagnosis.",
        "  File: sankey_s05_reproducibility.{html,jpg,txt}",
        "",
        "Supplementary",
        "  prioritized_breakdowns.txt: triage-first shortlist with direct paths",
        "  prioritized_examples/: filesystem-first symlink tree for shortlisted examples",
        "  off_story_summary.txt: off-story local extensions with stage counts",
        "  run_multiplicity_summary.txt: logical-result identity, repeats, machines",
        "  sankey_repro_by_metric: per-metric drift (max |official - local| across runs)",
        "  alt_tolerances/: tolerance sweep variants for Stage B and s05",
        "  agreement_curve.html: agreement-rate vs tolerance curve",
        "  coverage_matrix.html: model × benchmark reproducibility heat-map",
    ]
    story_index_fpath = level_001 / "story_index.txt"
    _write_text(story_index_lines, story_index_fpath)

    _write_scope_level_aliases(level_001, level_002, summary_root)

    # Always sweep legacy s02/s03/s04 aliases so a re-run after the
    # rename-refactor doesn't surface stale-named sankeys alongside the
    # new a/b ones.
    _cleanup_legacy_sankey_aliases(summary_root)

    if not filter_inventory_rows:
        # No filter inventory was loaded for this scope (e.g. virtual
        # experiments where the global Stage-1 funnel does not describe
        # the report's denominator). Remove any stale ``latest`` aliases
        # for the filter-side artifacts so a reader doesn't see a
        # misleading "selected vs excluded by model" plot or a
        # ``discovered -> attempted`` sankey rooted in a universe that
        # doesn't apply to this scope. Timestamped history files in
        # ``.history/`` are left alone — only the surfaced aliases are
        # cleaned up.
        _cleanup_filter_artifact_aliases(summary_root)


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

    index_fpath = (
        Path(args.index_fpath).expanduser().resolve()
        if args.index_fpath
        else latest_index_csv(Path(args.index_dpath).expanduser().resolve())
    )
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

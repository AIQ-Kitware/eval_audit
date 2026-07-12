"""The scope-render recursion (_render_scope_summary / _render_breakdown_scopes).

Moved verbatim out of ``workflows.build_reports_summary`` on
2026-07-12 (plan item C1 of
docs/planning/repo-simplification-plan-2026-07-12.md), finishing the
Phase-2 split: bodies unchanged; only the import wiring is new.
"""
from __future__ import annotations

import datetime as datetime_mod
import shutil
from collections import Counter
from pathlib import Path
from typing import Any
from eval_audit.infra.fs_publish import (
    link_alias,
    symlink_to,
)
from eval_audit.infra.logging import rich_link
from eval_audit.infra.paths import experiment_analysis_dpath
from eval_audit.infra.report_layout import (
    legacy_repo_publication_root,
    publication_experiments_root,
)
from loguru import logger
from eval_audit.infra.profiling import profile
from eval_audit.reports.summary.common import (
    slugify,
    _write_json,
    _write_text,
)
from eval_audit.reports.summary.classification import agreement_bucket_label
from eval_audit.reports.summary.sankeys import _cleanup_legacy_sankey_aliases
from eval_audit.reports.summary.multiplicity import (
    _build_run_multiplicity_summary,
    _build_off_story_summary,
)
from eval_audit.reports.summary.breakdown import _build_prioritized_breakdown_summary
from eval_audit.reports.summary.publish import (
    _write_table_artifacts,
    _scope_label,
    _build_breakdown_rows,
    _build_filter_selection_by_model_rows,
    _summarize_by_dimension,
    _build_scope_cardinality_lines,
    _build_high_level_readme,
    _write_scope_level_aliases,
    _cleanup_filter_artifact_aliases,
)
from eval_audit.reports.summary.scope_plots import _render_scope_plots
from eval_audit.reports.summary.scope_publish import _write_scope_scripts, _write_scope_tables, _write_story_index
from eval_audit.reports.summary.scope_rows import _build_enriched_scope_rows, _build_scope_sankey_rows
from eval_audit.reports.summary.scope_sankeys import _render_scope_sankeys

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

    enriched_rows, failed_rows, repro_keyed = _build_enriched_scope_rows(
        scope_rows=scope_rows,
        repro_rows=repro_rows,
        filter_inventory_rows=filter_inventory_rows,
    )

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
            # Legend/axis label with the bucket's threshold spelled out, so
            # "low / moderate / high / exact" always states its cutoff.
            "agreement_bucket": agreement_bucket_label(bucket),
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

    _sankey_rows = _build_scope_sankey_rows(
        enriched_rows=enriched_rows,
        repro_rows=repro_rows,
        filter_inventory_rows=filter_inventory_rows,
        scope_rows=scope_rows,
    )

    scope_title = _scope_label(scope_kind, scope_value)
    _sankey_arts = _render_scope_sankeys(
        include_visuals=include_visuals,
        sankey_rows=_sankey_rows,
        level_001=level_001,
        level_001_machine=level_001_machine,
        level_001_interactive=level_001_interactive,
        level_001_static=level_001_static,
        alt_tol_dpath=alt_tol_dpath,
        alt_tol_machine=alt_tol_machine,
        alt_tol_interactive=alt_tol_interactive,
        alt_tol_static=alt_tol_static,
        generated_utc=generated_utc,
        scope_title=scope_title,
    )
    operational_art = _sankey_arts["operational_art"]
    repro_art = _sankey_arts["repro_art"]
    repro_tol001_art = _sankey_arts["repro_tol001_art"]
    repro_tol010_art = _sankey_arts["repro_tol010_art"]
    repro_tol050_art = _sankey_arts["repro_tol050_art"]
    repro_metric_art = _sankey_arts["repro_metric_art"]
    filter_to_attempt_art = _sankey_arts["filter_to_attempt_art"]
    attempted_to_repro_art = _sankey_arts["attempted_to_repro_art"]
    attempted_to_repro_tol001_art = _sankey_arts["attempted_to_repro_tol001_art"]
    attempted_to_repro_tol010_art = _sankey_arts["attempted_to_repro_tol010_art"]
    attempted_to_repro_tol050_art = _sankey_arts["attempted_to_repro_tol050_art"]
    end_to_end_art = _sankey_arts["end_to_end_art"]
    end_to_end_tol001_art = _sankey_arts["end_to_end_tol001_art"]
    end_to_end_tol010_art = _sankey_arts["end_to_end_tol010_art"]
    end_to_end_tol050_art = _sankey_arts["end_to_end_tol050_art"]

    _scope_tables = _write_scope_tables(
        failed_rows=failed_rows,
        failure_reason_rows=failure_reason_rows,
        benchmark_summary=benchmark_summary,
        run_inventory=run_inventory,
        repro_inventory=repro_inventory,
        off_story_summary=off_story_summary,
        run_multiplicity_summary=run_multiplicity_summary,
        prioritized_breakdowns_summary=prioritized_breakdowns_summary,
        generated_utc=generated_utc,
        scope_title=scope_title,
        index_fpath=index_fpath,
        level_001=level_001,
        level_001_machine=level_001_machine,
        level_001_static=level_001_static,
        level_002=level_002,
        level_002_machine=level_002_machine,
        level_002_static=level_002_static,
    )
    failure_table = _scope_tables["failure_table"]
    failure_reason_table = _scope_tables["failure_reason_table"]
    benchmark_table = _scope_tables["benchmark_table"]
    run_inventory_table = _scope_tables["run_inventory_table"]
    repro_table = _scope_tables["repro_table"]
    off_story_table = _scope_tables["off_story_table"]
    run_multiplicity_table = _scope_tables["run_multiplicity_table"]
    prioritized_breakdowns_table = _scope_tables["prioritized_breakdowns_table"]
    prioritized_example_repairs = _scope_tables["prioritized_example_repairs"]
    prioritized_examples_tree = _scope_tables["prioritized_examples_tree"]

    _scope_plots = _render_scope_plots(
        include_visuals=include_visuals,
        benchmark_status_rows=benchmark_status_rows,
        repro_bucket_rows=repro_bucket_rows,
        repro_rows=repro_rows,
        enriched_rows=enriched_rows,
        failed_rows=failed_rows,
        filter_selection_by_model_rows=filter_selection_by_model_rows,
        scope_title=scope_title,
        level_001=level_001,
        level_001_machine=level_001_machine,
        level_001_interactive=level_001_interactive,
        level_001_static=level_001_static,
    )
    benchmark_plot = _scope_plots["benchmark_plot"]
    repro_bucket_plot = _scope_plots["repro_bucket_plot"]
    agreement_curve_plot = _scope_plots["agreement_curve_plot"]
    per_metric_agreement_plot = _scope_plots["per_metric_agreement_plot"]
    coverage_matrix_plot = _scope_plots["coverage_matrix_plot"]
    failure_taxonomy_plot = _scope_plots["failure_taxonomy_plot"]
    filter_selection_by_model_plot = _scope_plots["filter_selection_by_model_plot"]

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

    _write_scope_scripts(
        top_level_summary_root=top_level_summary_root,
        level_001=level_001,
        scope_kind=scope_kind,
        scope_value=scope_value,
        index_fpath=index_fpath,
        filter_inventory_json=filter_inventory_json,
        reproduce_extra_args=reproduce_extra_args,
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

    _write_story_index(
        level_001=level_001,
        generated_utc=generated_utc,
        scope_title=scope_title,
    )

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

"""Artifact publishing: tables, READMEs, aliases, reproduce/redraw scripts.

Split out of ``eval_audit.workflows.build_reports_summary`` on
2026-06-11 (Phase 2 of docs/historical/planning/repo-refactor-plan.md). Pure
relocation: function bodies are unchanged.
"""
from __future__ import annotations

import csv
import io
import os
import shlex
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from eval_audit.infra.fs_publish import link_alias, safe_unlink, write_text_atomic
from eval_audit.infra.logging import rich_link
from eval_audit.infra.report_layout import portable_repo_root_lines
from loguru import logger

from eval_audit.reports.summary.common import _is_truthy_text, _write_json, _write_text, slugify


def _write_table_artifacts(
    rows: list[dict[str, Any]],
    stem: Path,
    machine_dpath: Path | None = None,
    static_dpath: Path | None = None,
) -> dict[str, str]:
    if machine_dpath is not None:
        machine_dpath.mkdir(parents=True, exist_ok=True)
        json_fpath = (machine_dpath / stem.name).with_suffix(".json")
    else:
        json_fpath = stem.with_suffix(".json")
    if static_dpath is not None:
        static_dpath.mkdir(parents=True, exist_ok=True)
        csv_fpath = (static_dpath / stem.name).with_suffix(".csv")
        txt_fpath = (static_dpath / stem.name).with_suffix(".txt")
    else:
        csv_fpath = stem.with_suffix(".csv")
        txt_fpath = stem.with_suffix(".txt")
    _write_json(rows, json_fpath)
    fieldnames = sorted({key for row in rows for key in row.keys()})
    csv_buf = io.StringIO()
    writer = csv.DictWriter(csv_buf, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    write_text_atomic(csv_fpath, csv_buf.getvalue())
    if not rows:
        write_text_atomic(txt_fpath, "(no rows)\n")
    else:
        lines = [", ".join(fieldnames)]
        for row in rows[:200]:
            lines.append(", ".join(str(row.get(key, "")) for key in fieldnames))
        if len(rows) > 200:
            lines.append(f"... ({len(rows) - 200} more rows)")
        write_text_atomic(txt_fpath, "\n".join(lines) + "\n")
    return {"json": str(json_fpath), "csv": str(csv_fpath), "txt": str(txt_fpath)}


def _write_structured_summary_artifacts(
    *,
    rows: list[dict[str, Any]],
    payload: dict[str, Any],
    txt_lines: list[str],
    stem: Path,
    machine_dpath: Path,
    static_dpath: Path,
) -> dict[str, str]:
    machine_dpath.mkdir(parents=True, exist_ok=True)
    static_dpath.mkdir(parents=True, exist_ok=True)
    json_fpath = (machine_dpath / stem.name).with_suffix(".json")
    csv_fpath = (static_dpath / stem.name).with_suffix(".csv")
    txt_fpath = (static_dpath / stem.name).with_suffix(".txt")
    _write_json(payload, json_fpath)
    fieldnames = sorted({key for row in rows for key in row.keys()})
    with csv_fpath.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        if fieldnames:
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        else:
            file.write("")
    _write_text(txt_lines, txt_fpath)
    return {"json": str(json_fpath), "csv": str(csv_fpath), "txt": str(txt_fpath)}


def _scope_summary_root(summary_root: Path, scope_slug: str) -> Path:
    return summary_root / scope_slug


def _scope_label(scope_kind: str, scope_value: str | None) -> str:
    if scope_kind == "all_results":
        return "all_results"
    return f"{scope_kind}={scope_value}"


def _scope_slug(scope_kind: str, scope_value: str | None) -> str:
    if scope_kind == "all_results":
        return "all-results"
    return f"{scope_kind}-{slugify(str(scope_value))}"


def _build_breakdown_rows(
    enriched_rows: list[dict[str, Any]],
    *,
    group_key: str,
    repro_keyed: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for row in enriched_rows:
        group_value = str(row.get(group_key) or "unknown")
        if row.get("completed_with_run_artifacts"):
            repro = repro_keyed.get((str(row.get("experiment_name")), str(row.get("run_entry"))))
            status = "completed_not_yet_analyzed"
            if repro is not None:
                status = f"analyzed::{repro['official_instance_agree_bucket']}"
        else:
            status = f"failed::{row.get('failure_reason') or 'unknown_failure'}"
        counts[(group_value, status)] += 1
    return [
        {"group_value": group_value, "status_bucket": status, "count": count}
        for (group_value, status), count in sorted(counts.items())
    ]


def _build_filter_selection_by_model_rows(filter_inventory_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in filter_inventory_rows:
        model = str(row.get("model") or "unknown")
        selection_status = "selected" if row.get("selection_status") == "selected" else "excluded"
        counts[model][selection_status] += 1

    rows: list[dict[str, Any]] = []
    for model, status_counts in sorted(
        counts.items(),
        key=lambda item: (-(item[1]["selected"] + item[1]["excluded"]), -item[1]["selected"], item[0]),
    ):
        for selection_status in ["excluded", "selected"]:
            count = int(status_counts.get(selection_status, 0))
            if count:
                rows.append(
                    {
                        "model": model,
                        "selection_status": selection_status,
                        "count": count,
                    }
                )
    return rows


def _summarize_by_dimension(
    enriched_rows: list[dict[str, Any]],
    *,
    dimension: str,
    repro_keyed: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    by_value: dict[str, dict[str, Any]] = {}
    for row in enriched_rows:
        value = str(row.get(dimension) or "unknown")
        info = by_value.setdefault(
            value,
            {
                dimension: value,
                "total_jobs": 0,
                "completed_jobs": 0,
                "analyzed_jobs": 0,
                "failed_jobs": 0,
                "failure_reasons": Counter(),
            },
        )
        info["total_jobs"] += 1
        if row.get("completed_with_run_artifacts"):
            info["completed_jobs"] += 1
            key = (str(row.get("experiment_name")), str(row.get("run_entry")))
            if key in repro_keyed:
                info["analyzed_jobs"] += 1
        else:
            info["failed_jobs"] += 1
            info["failure_reasons"][row.get("failure_reason") or "unknown_failure"] += 1
    rows = []
    for value, info in sorted(by_value.items()):
        rows.append(
            {
                dimension: value,
                "total_jobs": info["total_jobs"],
                "completed_jobs": info["completed_jobs"],
                "analyzed_jobs": info["analyzed_jobs"],
                "failed_jobs": info["failed_jobs"],
                "completion_rate": (info["completed_jobs"] / info["total_jobs"]) if info["total_jobs"] else None,
                "top_failure_reason": info["failure_reasons"].most_common(1)[0][0] if info["failure_reasons"] else None,
            }
        )
    return rows


def _cardinality(rows: list[dict[str, Any]], *, model_key: str = "model", bench_key: str = "benchmark", scenario_key: str = "scenario") -> dict[str, int]:
    return {
        "n": len(rows),
        "models": len({r.get(model_key) for r in rows if r.get(model_key)}),
        "benchmarks": len({r.get(bench_key) for r in rows if r.get(bench_key)}),
        "scenarios": len({r.get(scenario_key) for r in rows if r.get(scenario_key)}),
        "model_bench_pairs": len({(r.get(model_key), r.get(bench_key)) for r in rows if r.get(model_key) and r.get(bench_key)}),
    }


def _build_scope_cardinality_lines(
    *,
    filter_inventory_rows: list[dict[str, Any]],
    enriched_rows: list[dict[str, Any]],
    scope_title: str,
    generated_utc: str,
) -> list[str]:
    header = f"{'Stage':<22} {'runs':>6}  {'models':>6}  {'benchmarks':>10}  {'scenarios':>9}  {'mod×bench':>9}"
    sep = "-" * len(header)
    lines = [
        f"Scope Cardinality Summary: {scope_title}",
        f"Generated: {generated_utc}",
        "",
        "Run-spec counts at each stage of the pipeline funnel.",
        "",
        header,
        sep,
    ]

    def row_line(label: str, c: dict[str, int]) -> str:
        return (
            f"{label:<22} {c['n']:>6}  {c['models']:>6}  {c['benchmarks']:>10}"
            f"  {c['scenarios']:>9}  {c['model_bench_pairs']:>9}"
        )

    if filter_inventory_rows:
        all_inv = filter_inventory_rows
        selected_inv = [r for r in filter_inventory_rows if r.get("selection_status") == "selected"]
        lines.append(row_line("discovered", _cardinality(all_inv)))
        lines.append(row_line("selected", _cardinality(selected_inv)))

    lines.append(row_line("attempted", _cardinality(enriched_rows)))

    completed_rows = [r for r in enriched_rows if _is_truthy_text(r.get("has_run_spec"))]
    lines.append(row_line("completed", _cardinality(completed_rows)))

    analyzed_rows = [r for r in enriched_rows if r.get("repro_report_dir") is not None]
    lines.append(row_line("analyzed", _cardinality(analyzed_rows)))

    lines += [
        "",
        "Columns: runs = total run entries; models/benchmarks/scenarios = unique values;",
        "         mod×bench = unique (model, benchmark) pairs in that subset.",
        "Stages: discovered = all runs seen by Stage 1 filter; selected = passed all filters",
        "        and chosen for reproduction; attempted = scheduled in this experiment;",
        "        completed = produced HELM artifacts; analyzed = have reproducibility report.",
        "Note: discovered/selected rows show the global filter universe; other rows are scoped",
        "      to this report's experiment/dimension filter.",
    ]
    return lines


def _build_high_level_readme(
    *,
    scope_title: str,
    generated_utc: str,
    n_total: int,
    n_completed: int,
    n_analyzed: int,
    n_failed: int,
    top_failure_rows: list[dict[str, Any]],
    top_repro_rows: list[dict[str, Any]],
    breakdown_dims: list[str],
) -> list[str]:
    lines = [
        "Executive Summary",
        "",
        f"generated_utc: {generated_utc}",
        f"scope: {scope_title}",
        f"total_jobs: {n_total}",
        f"completed_with_run_artifacts: {n_completed}",
        f"completed_and_analyzed: {n_analyzed}",
        f"failed_or_incomplete: {n_failed}",
        "",
        "key_takeaways:",
        f"  - {n_completed}/{n_total} jobs produced runnable HELM artifacts in this scope.",
        f"  - {n_analyzed} completed jobs in this scope already have reproducibility reports.",
    ]
    if top_failure_rows:
        lines.append("  - dominant failure reasons currently appear to be:")
        for row in top_failure_rows[:5]:
            lines.append(f"    * {row['failure_reason']}: {row['count']}")
    if top_repro_rows:
        lines.append("  - analyzed reproducibility buckets currently are:")
        for row in top_repro_rows[:5]:
            lines.append(f"    * {row['official_instance_agree_bucket']}: {row['count']}")
    lines.extend(
        [
            "",
            "start_here:",
            "  story_index.txt — canonical 5-step reading order for the sankey visualizations",
            "  cardinality_summary.txt — run/model/benchmark counts at each stage of the funnel",
            "  off_story_summary.txt — off-story local-extension models with selected/attempted/completed/analyzed counts",
            "  run_multiplicity_summary.txt — repeated attempts, machine spread, experiment spread, and UUID/fallback identity coverage",
            "  prioritized_breakdowns.txt — shortlist of benchmark/model/machine/experiment breakdowns to inspect next",
            "",
            "  understand_upstream_filtering:",
            "    1. What runs were excluded at Stage 1 (discovery)? See reports/filtering/ which contains",
            "       sankey_model_filter.html and filter_cardinality_summary.txt.",
            "    2. Read docs/pipeline.md for the full end-to-end workflow (stages 1-6).",
            "",
            "  explore_execution_coverage (read sankeys in order):",
            "    s01: sankey_s01_operational.html — all attempted runs: benchmark → lifecycle → outcome",
            "    a:   sankey_a_universe_to_scope.html — Stage A: Universe → Scope (filter funnel)",
            "    b:   sankey_b_scope_to_analyzed.html — Stage B: Scope → Attempt → Execution → Analysis → Reproduction (abs_tol=0)",
            "    s05: sankey_s05_reproducibility.html — detailed group → repeatability → agreement → diagnosis",
            "    sup: sankey_repro_by_metric.html — per-metric drift (run-level max |official - local|)",
            "    sup: filter_selection_by_model.html — selected vs excluded run-specs by model",
            "    sup: benchmark_status.html and coverage_matrix.html",
            "    alt: alt_tolerances/ — tolerance sweep variants (tol001, tol010, tol050) for s03/s04/s05",
            "",
            "  understand_reproducibility:",
            "    1. open agreement_curve.html to see how agreement changes across tolerance thresholds",
            "    2. open agreement_curve_per_metric.html for per-metric agreement curves",
            "    3. open reproducibility_buckets.html to see agreement distribution",
            "    4. for relaxed tolerances, see alt_tolerances/ subdirectory",
            "    5. aggregate_score_diff/ — run-level score drift heatmaps",
            "       (color = local − public aggregate score; cells annotated P=public / L=local).",
            "       aggregate_score_diff_headline.png collapses each benchmark to its headline",
            "       metric for a single holistic model × benchmark view; the per_metric/ subdir",
            "       breaks it out one plot per core metric.",
            "",
            "  diagnose_failures:",
            "    1. read failure_reasons.txt to see why incomplete jobs failed",
            "    2. open failure_taxonomy.html to see root-cause breakdown (hardware/data/infra)",
            "",
            "  drill_down_by_dimension:",
            "    - follow next_level/ for breakdown tables by benchmark, model, suite, machine, experiment",
            "    - use prioritized_breakdowns.* for a triage-first shortlist with direct breakdown paths",
            "    - use off_story_summary.* and run_multiplicity_summary.* for storyline/attempt identity tables",
            "    - run reproduce.sh to regenerate this report from current data",
            "",
            "default_breakdowns:",
        ]
    )
    for dim in breakdown_dims:
        lines.append(f"  - {dim}")
    return lines


def _write_scope_level_aliases(level_001: Path, level_002: Path, summary_root: Path) -> None:
    link_alias(level_001 / "README.txt", summary_root, "README.txt")
    link_alias(level_001 / "story_index.txt", summary_root, "story_index.txt")
    level_001_interactive = level_001 / "interactive"
    level_001_static = level_001 / "static"
    level_002_static = level_002 / "static"
    for src_name in [
        "sankey_s01_operational.html",
        "sankey_a_universe_to_scope.html",
        "sankey_b_scope_to_analyzed.html",
        "sankey_s05_reproducibility.html",
        "sankey_repro_by_metric.html",
        "benchmark_status.html",
        "reproducibility_buckets.html",
        "agreement_curve.html",
        "agreement_curve_per_metric.html",
        "coverage_matrix.html",
        "failure_taxonomy.html",
        "filter_selection_by_model.html",
    ]:
        src = level_001_interactive / src_name
        if src.exists() or src.is_symlink():
            link_alias(src, summary_root, src_name)
    for src_name in [
        "cardinality_summary.txt",
        "sankey_s01_operational.jpg",
        "sankey_s01_operational.txt",
        "sankey_a_universe_to_scope.jpg",
        "sankey_a_universe_to_scope.txt",
        "sankey_b_scope_to_analyzed.jpg",
        "sankey_b_scope_to_analyzed.txt",
        "sankey_s05_reproducibility.jpg",
        "sankey_s05_reproducibility.txt",
        "sankey_repro_by_metric.jpg",
        "sankey_repro_by_metric.txt",
        "benchmark_status.jpg",
        "reproducibility_buckets.jpg",
        "agreement_curve.jpg",
        "agreement_curve_per_metric.jpg",
        "coverage_matrix.jpg",
        "failure_taxonomy.jpg",
        "filter_selection_by_model.jpg",
        "failure_reasons.txt",
        "failure_runs.csv",
    ]:
        src = level_001_static / src_name
        if src.exists() or src.is_symlink():
            link_alias(src, summary_root, src_name)
    link_alias(level_001 / "reproduce.sh", summary_root, "reproduce.sh")
    link_alias(level_001 / "redraw_plots.sh", summary_root, "redraw_plots.sh")
    for src_name in [
        "benchmark_summary.csv",
        "run_inventory.csv",
        "reproducibility_rows.csv",
        "prioritized_breakdowns.csv",
        "off_story_summary.csv",
        "run_multiplicity_summary.csv",
    ]:
        src = level_002_static / src_name
        if src.exists() or src.is_symlink():
            link_alias(src, summary_root, src_name)
    for src_name in [
        "prioritized_breakdowns.txt",
        "prioritized_examples",
        "off_story_summary.txt",
        "run_multiplicity_summary.txt",
    ]:
        src = level_002_static / src_name if src_name.endswith(".txt") else level_002 / src_name
        if src.exists() or src.is_symlink():
            link_alias(src, summary_root, src_name)
    for src_name in [
        "prioritized_breakdowns.json",
        "off_story_summary.json",
        "run_multiplicity_summary.json",
    ]:
        src = level_002 / "machine" / src_name
        if src.exists() or src.is_symlink():
            link_alias(src, summary_root, src_name)

    machine_csv = level_002 / "breakdowns" / "by_machine_host" / "index.csv"
    if machine_csv.exists() or machine_csv.is_symlink():
        link_alias(machine_csv, summary_root, "machine_summary.csv")


def _build_summary_cmd(
    *,
    scope_kind: str,
    scope_value: str | None,
    index_path: Path | None,
    filter_inventory_json: Path | None,
    extra_args: str = "",
) -> str:
    cmd = 'PYTHONPATH="$REPO_ROOT" "$PYTHON_BIN" -m eval_audit.workflows.build_reports_summary'
    if scope_kind not in ("all_results", None) and scope_value:
        cmd += f" --experiment-name {scope_value}"
    if index_path is not None:
        cmd += f" --index-fpath {shlex.quote(str(index_path))}"
    if filter_inventory_json is not None:
        cmd += f" --filter-inventory-json {shlex.quote(str(filter_inventory_json))}"
    if extra_args:
        cmd += f" {extra_args}"
    return cmd + ' "$@"'


def _write_reproduce_sh(
    fpath: Path,
    scope_kind: str,
    scope_value: str | None,
    index_path: Path | None = None,
    filter_inventory_json: Path | None = None,
    extra_args: str = "",
) -> None:
    cmd = _build_summary_cmd(
        scope_kind=scope_kind,
        scope_value=scope_value,
        index_path=index_path,
        filter_inventory_json=filter_inventory_json,
        extra_args=extra_args,
    )
    lines = [
        "#!/usr/bin/env bash",
        "# Regenerate this summary report from the current index and analysis data.",
        f"# scope: {scope_kind}" + (f" / {scope_value}" if scope_value else ""),
        "set -euo pipefail",
        *portable_repo_root_lines(),
        'cd "$REPO_ROOT"',
        cmd,
    ]
    fpath.write_text("\n".join(lines) + "\n")
    logger.debug(f'Write to 💻: {rich_link(fpath)}')
    fpath.chmod(0o755)


def _write_delegating_script(
    fpath: Path,
    *,
    target_script: Path,
    purpose: str,
) -> None:
    """Emit a stub script that execs the canonical top-level script.

    Used inside breakdown sub-trees (``by_benchmark/boolq/`` etc.) where
    re-running an isolated scope rebuild is not meaningful — the only
    correct refresh is rebuilding the whole report at the top level.
    The stub locates ``target_script`` via a relative path (so the
    summary tree can be moved or symlinked without breaking) and execs
    it, forwarding ``$@``.
    """
    rel_target = os.path.relpath(target_script, start=fpath.parent)
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"# {purpose}",
        "# This is a sub-tree script — running it rebuilds the *whole* report",
        f"# at the top level ({target_script.parent}/), not just this slice,",
        "# because the per-benchmark / per-model / per-machine breakdowns are",
        "# derived views with no standalone scope filter on the build CLI.",
        "# Resolve through symlinks so invoking the breakdown-root alias",
        "# (e.g. <breakdown>/redraw_plots.sh, which is itself a symlink",
        "# into level_001/) finds the same target as invoking the canonical",
        "# script directly.",
        'SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"',
        'SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"',
        f'TARGET_SCRIPT="$SCRIPT_DIR/{rel_target}"',
        'if [[ ! -f "$TARGET_SCRIPT" ]]; then',
        '  echo "FAIL: top-level script not found at $TARGET_SCRIPT" >&2',
        '  exit 1',
        'fi',
        'exec bash "$TARGET_SCRIPT" "$@"',
    ]
    fpath.write_text("\n".join(lines) + "\n")
    logger.debug(f'Write delegating script: {rich_link(fpath)}')
    fpath.chmod(0o755)


def _write_redraw_plots_sh(
    fpath: Path,
    scope_kind: str,
    scope_value: str | None,
    index_path: Path | None = None,
    filter_inventory_json: Path | None = None,
) -> None:
    """Emit a redraw_plots.sh next to reproduce.sh.

    Same contract as the per-packet redraw_plots scripts: re-render the
    plot artifacts in this directory after a styling tweak in
    eval_audit/workflows/build_reports_summary.py or the shared sankey/
    chart helpers. Today the underlying entry point still rebuilds the
    textual artifacts too (the --plots-only flag is plumbed through but
    no fast-path implementation exists yet); the script is the stable
    surface that the eventual fast-path will plug into.
    """
    cmd = _build_summary_cmd(
        scope_kind=scope_kind,
        scope_value=scope_value,
        index_path=index_path,
        filter_inventory_json=filter_inventory_json,
        extra_args="--plots-only",
    )
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "# Redraws plots in this directory after a matplotlib/plotly styling tweak.",
        "# Today this still rebuilds the textual artifacts alongside (the underlying",
        "# --plots-only flag is plumbed through but currently advisory). Faster than",
        "# rerunning the per-packet reports; the heavy planner/index analysis stays",
        "# upstream.",
        f"# scope: {scope_kind}" + (f" / {scope_value}" if scope_value else ""),
        *portable_repo_root_lines(),
        'cd "$REPO_ROOT"',
        cmd,
    ]
    fpath.write_text("\n".join(lines) + "\n")
    logger.debug(f'Write to 💻: {rich_link(fpath)}')
    fpath.chmod(0o755)


_FILTER_ARTIFACT_ALIAS_NAMES = (
    "filter_selection_by_model.json",
    "filter_selection_by_model.html",
    "filter_selection_by_model.jpg",
    "filter_selection_by_model.png",
    # Stage-A funnel (new) — only meaningful when filter inventory is loaded.
    "sankey_a_universe_to_scope.html",
    "sankey_a_universe_to_scope.jpg",
    "sankey_a_universe_to_scope.txt",
    "sankey_a_universe_to_scope.json",
    # Legacy filter sankeys (s02 / s04) — kept here so historic builds get
    # their stale aliases cleaned up when re-run with --no-filter-inventory.
    "sankey_s02_filter_to_attempt.html",
    "sankey_s02_filter_to_attempt.jpg",
    "sankey_s02_filter_to_attempt.txt",
    "sankey_s02_filter_to_attempt.json",
    "sankey_s04_end_to_end.html",
    "sankey_s04_end_to_end.jpg",
    "sankey_s04_end_to_end.txt",
    "sankey_s04_end_to_end.json",
)


def _cleanup_filter_artifact_aliases(scope_root: Path) -> None:
    """Unlink any latest alias that surfaces a filter-funnel artifact.

    Used when a scope has no filter inventory; without this, latest
    aliases from a previous run (when one was loaded) still surface a
    misleading filter funnel for the current scope.
    """
    target_names = set(_FILTER_ARTIFACT_ALIAS_NAMES)
    for path in scope_root.rglob("*"):
        if path.name in target_names:
            safe_unlink(path)


# C3 (2026-07-12): the prioritized-example repair + publish-tree half of
# breakdown.py moved here (plan item C3) — artifact publication belongs
# with the other publish-side writers; breakdown.py keeps selection/triage.
from eval_audit.infra.fs_publish import symlink_to
from eval_audit.infra.profiling import profile
from eval_audit.reports.core_packet_summary import (
    load_core_report_bundle,
    prioritized_example_artifact_names,
)
from eval_audit.reports.summary.common import _clean_optional_text


def _iter_prioritized_example_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for section_rows in (summary.get("selected_by_section") or {}).values():
        for row in section_rows or []:
            for example_row in row.get("example_rows") or []:
                key = (
                    str(example_row.get("experiment_name") or ""),
                    str(example_row.get("run_entry") or ""),
                    str(example_row.get("report_dir") or ""),
                )
                if key in seen:
                    continue
                seen.add(key)
                rows.append(example_row)
    return rows


@profile
def _prioritized_example_artifact_names(report_dir: Path) -> list[str]:
    try:
        packet = load_core_report_bundle(report_dir / "core_metric_report.json")["packet"]
    except Exception:
        return [
            "core_metric_report.png",
            "core_metric_management_summary.txt",
            "components_manifest.json",
            "comparisons_manifest.json",
            "warnings.json",
            "warnings.txt",
        ]
    return prioritized_example_artifact_names(packet)


def _report_artifact_is_usable(fpath: Path) -> bool:
    return fpath.exists()


@profile
def _prioritized_example_missing_artifacts(report_dir: Path) -> list[str]:
    return [
        name for name in _prioritized_example_artifact_names(report_dir)
        if not _report_artifact_is_usable(report_dir / name)
    ]


@profile
def _repair_prioritized_example_reports(
    *,
    summary: dict[str, Any],
    index_fpath: Path,
) -> list[dict[str, Any]]:
    """Verify each prioritized example's report dir; **do not regenerate**.

    History
    -------
    This function used to actually *repair* — it would shell back into
    ``rebuild_core_report_main(argv)`` to re-render any per-pair report
    whose dir was missing one of the artifact filenames listed by
    ``prioritized_example_artifact_names``. That was always a layering
    violation: aggregate-summary should be a pure read-pass over
    artifacts the analyze step (rebuild_core_report) produced. If a
    per-pair report is incomplete, that's an analyze-step bug, not
    something the summary phase should hide by silently re-running the
    same code on the fly. Worse, the disabled-comparison and cosmetic
    artifact patterns made the "missing" check fire on every run, so
    the summary did a full re-render of every prioritized example every
    invocation — ``line_profiler`` showed this consuming 98% of
    ``_render_scope_summary`` (~15 s/example × 6 examples = ~88 s of
    a 88 s run for the EEE-only heatmap).

    Current behavior
    ----------------
    Iterate the prioritized examples, classify each:

      - ``already_ok``: report dir exists and the expected artifact
        list matches what's on disk. Nothing to do.
      - ``incomplete``: report dir exists but some expected artifacts
        are absent. Recorded with the missing list so downstream
        rendering and the README can flag it. **No regeneration.**
      - ``missing_report_dir``: the report dir referenced by the
        prioritized summary doesn't exist on disk. Recorded as such.

    The publish step (`_publish_prioritized_examples_tree`) already
    tolerates missing artifacts — it ``if exists()``-guards every
    ``symlink_to`` call. So skipping regeneration here doesn't break
    the navigation tree; it just leaves missing files genuinely
    missing instead of papering over them.

    The right place to fix incompleteness is the analyze step. If the
    user wants to regenerate a specific per-pair report, the report
    dir already contains a ``redraw_plots.sh`` and ``reproduce.sh`` for
    exactly that purpose.
    """
    repairs: list[dict[str, Any]] = []
    for example_row in _iter_prioritized_example_rows(summary):
        report_dir_text = _clean_optional_text(example_row.get("report_dir"))
        run_entry = _clean_optional_text(example_row.get("run_entry"))
        if not report_dir_text or not run_entry:
            continue
        report_dir = Path(report_dir_text).expanduser()
        if not report_dir.exists():
            repairs.append(
                {
                    "report_dir": str(report_dir),
                    "run_entry": run_entry,
                    "status": "missing_report_dir",
                    "missing_artifacts": _prioritized_example_artifact_names(report_dir),
                }
            )
            continue
        missing = _prioritized_example_missing_artifacts(report_dir)
        if missing:
            logger.warning(
                "prioritized example {}: incomplete (missing artifacts: {}). "
                "Run report_dir/redraw_plots.sh or reproduce.sh to regenerate.",
                report_dir, missing,
            )
        repairs.append(
            {
                "report_dir": str(report_dir),
                "run_entry": run_entry,
                "status": "already_ok" if not missing else "incomplete",
                "missing_artifacts": missing,
            }
        )
    return repairs


@profile
def _publish_prioritized_examples_tree(
    *,
    level_002: Path,
    generated_utc: str,
    summary: dict[str, Any],
    repair_results: list[dict[str, Any]] | None = None,
) -> Path:
    tree_root = level_002 / "prioritized_examples"
    tree_root.mkdir(parents=True, exist_ok=True)
    repairs_by_dir = {
        str(item.get("report_dir") or ""): item
        for item in (repair_results or [])
        if item.get("report_dir")
    }
    for section_name in ["score_ge_95", "best", "mid", "worst", "score_lt_80", "flagged"]:
        section_dpath = tree_root / section_name
        section_dpath.mkdir(parents=True, exist_ok=True)
        for row in (summary.get("selected_by_section") or {}).get(section_name, []):
            dim = str(row.get("dimension") or "unknown")
            value = str(row.get("dimension_value") or "unknown")
            rank = int(row.get("priority_rank") or 0)
            rec_dpath = section_dpath / f"{rank:02d}-{slugify(dim)}-{slugify(value)}"
            rec_dpath.mkdir(parents=True, exist_ok=True)
            metadata = {
                "bucket_class": section_name,
                "priority_rank": rank,
                "dimension": dim,
                "dimension_value": value,
                "selection_reason": row.get("selection_reason"),
                "breakdown_dir": row.get("breakdown_dir"),
                "breakdown_index_dir": row.get("breakdown_index_dir"),
                "interesting_flags": row.get("interesting_flags") or [],
                "example_report_dirs": [ex.get("report_dir") for ex in (row.get("example_rows") or []) if ex.get("report_dir")],
            }
            _write_json(metadata, rec_dpath / "metadata.json")
            breakdown_dir = _clean_optional_text(row.get("breakdown_dir"))
            if breakdown_dir and Path(breakdown_dir).exists():
                symlink_to(breakdown_dir, rec_dpath / "breakdown_dir")
            breakdown_index_dir = _clean_optional_text(row.get("breakdown_index_dir"))
            if breakdown_index_dir and Path(breakdown_index_dir).exists():
                symlink_to(breakdown_index_dir, rec_dpath / "breakdown_index_dir")
            for ex_idx, example_row in enumerate(row.get("example_rows") or [], start=1):
                run_entry = str(example_row.get("run_entry") or f"example-{ex_idx}")
                ex_dpath = rec_dpath / f"example_{ex_idx:02d}-{slugify(run_entry)}"
                ex_dpath.mkdir(parents=True, exist_ok=True)
                example_report_dir = _clean_optional_text(example_row.get("report_dir"))
                if example_report_dir and Path(example_report_dir).exists():
                    symlink_to(example_report_dir, ex_dpath / "report_dir")
                    for artifact_name in _prioritized_example_artifact_names(Path(example_report_dir)):
                        artifact_fpath = Path(example_report_dir) / artifact_name
                        if artifact_fpath.exists():
                            symlink_to(artifact_fpath, ex_dpath / artifact_name)
                repair_info = repairs_by_dir.get(str(example_report_dir or ""))
                if repair_info is not None:
                    _write_json(repair_info, ex_dpath / "repair_status.json")
    readme_lines = [
        "Prioritized Examples",
        "",
        f"generated_utc: {generated_utc}",
        "This tree is filesystem-first navigation for the prioritized breakdown shortlist.",
        "Each recommendation directory links to the selected breakdown, its parent index, and example report dirs with key latest artifacts.",
    ]
    _write_text(readme_lines, tree_root / "README.txt")
    return tree_root

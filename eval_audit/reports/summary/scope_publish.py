"""Table/script/story-index publication for one scope render.

Moved verbatim out of ``workflows.build_reports_summary`` on
2026-07-12 (plan item C1 of
docs/planning/repo-simplification-plan-2026-07-12.md), finishing the
Phase-2 split: bodies unchanged; only the import wiring is new.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from eval_audit.reports.summary.common import _write_text
from eval_audit.reports.summary.multiplicity import (
    _format_run_multiplicity_summary_text,
    _format_off_story_summary_text,
)
from eval_audit.reports.summary.publish import (
    _publish_prioritized_examples_tree,
    _repair_prioritized_example_reports,
)
from eval_audit.reports.summary.breakdown import (
    _format_prioritized_breakdown_summary_text,
)
from eval_audit.reports.summary.publish import (
    _write_table_artifacts,
    _write_structured_summary_artifacts,
    _write_reproduce_sh,
    _write_delegating_script,
    _write_redraw_plots_sh,
)

def _write_scope_tables(
    *,
    failed_rows: list[dict[str, Any]],
    failure_reason_rows: list[dict[str, Any]],
    benchmark_summary: list[dict[str, Any]],
    run_inventory: list[dict[str, Any]],
    repro_inventory: list[dict[str, Any]],
    off_story_summary: dict[str, Any],
    run_multiplicity_summary: dict[str, Any],
    prioritized_breakdowns_summary: dict[str, Any],
    generated_utc: str,
    scope_title: str,
    index_fpath: Path,
    level_001: Path,
    level_001_machine: Path,
    level_001_static: Path,
    level_002: Path,
    level_002_machine: Path,
    level_002_static: Path,
) -> dict[str, Any]:
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
    return {
        "failure_table": failure_table,
        "failure_reason_table": failure_reason_table,
        "benchmark_table": benchmark_table,
        "run_inventory_table": run_inventory_table,
        "repro_table": repro_table,
        "off_story_table": off_story_table,
        "run_multiplicity_table": run_multiplicity_table,
        "prioritized_breakdowns_table": prioritized_breakdowns_table,
        "prioritized_example_repairs": prioritized_example_repairs,
        "prioritized_examples_tree": prioritized_examples_tree,
    }


def _write_scope_scripts(
    *,
    top_level_summary_root: Path | None,
    level_001: Path,
    scope_kind: str,
    scope_value: str | None,
    index_fpath: Path,
    filter_inventory_json: Path | None,
    reproduce_extra_args: str,
) -> None:
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


def _write_story_index(
    *,
    level_001: Path,
    generated_utc: str,
    scope_title: str,
) -> None:
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

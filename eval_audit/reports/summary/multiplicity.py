"""Run-multiplicity and off-story summaries (attempt matching).

Split out of ``eval_audit.workflows.build_reports_summary`` on
2026-06-11 (Phase 2 of docs/historical/planning/repo-refactor-plan.md). Pure
relocation: function bodies are unchanged.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any
from eval_audit.model_registry import local_model_registry_by_name

from eval_audit.reports.summary.common import (
    _clean_optional_text,
    _coerce_float,
    _is_truthy_text,
    _preview_values,
)
from eval_audit.reports.summary.classification import (
    _filter_inventory_lookup_by_run_entry,
    _group_repro_rows_by_run_entry,
    _group_scope_rows_by_run_entry,
    _resolve_attempt_identity,
    _run_entry_metadata_lookup,
    _storyline_metadata_for_model,
)


def _build_off_story_summary(
    *,
    filter_inventory_rows: list[dict[str, Any]],
    scope_rows: list[dict[str, Any]],
    repro_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    registry_lookup = local_model_registry_by_name()
    run_entry_meta = _run_entry_metadata_lookup(filter_inventory_rows, scope_rows)
    model_filter_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    model_scope_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    model_analyzed_run_entries: dict[str, set[str]] = defaultdict(set)
    model_repro_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in filter_inventory_rows:
        model = _clean_optional_text(row.get("model"))
        if model:
            model_filter_rows[model].append(row)
    for row in scope_rows:
        model = _clean_optional_text(row.get("model"))
        if model:
            model_scope_rows[model].append(row)
    for row in repro_rows:
        run_entry = _clean_optional_text(row.get("run_entry"))
        if not run_entry:
            continue
        model = _clean_optional_text((run_entry_meta.get(run_entry) or {}).get("model"))
        if not model:
            continue
        model_analyzed_run_entries[model].add(run_entry)
        model_repro_rows[model].append(row)

    candidate_models = sorted(set(model_filter_rows) | set(model_scope_rows) | set(model_analyzed_run_entries))
    headline_sets: dict[str, dict[str, set[str]]] = {
        "off_story": {
            "models": set(),
            "selected_run_entries": set(),
            "attempted_run_entries": set(),
            "completed_run_entries": set(),
            "analyzed_run_entries": set(),
        },
        "on_story": {
            "models": set(),
            "selected_run_entries": set(),
            "attempted_run_entries": set(),
            "completed_run_entries": set(),
            "analyzed_run_entries": set(),
        },
    }

    off_story_rows: list[dict[str, Any]] = []
    for model in candidate_models:
        filter_rows = model_filter_rows.get(model, [])
        scope_model_rows = model_scope_rows.get(model, [])
        filter_row = filter_rows[0] if filter_rows else None
        story = _storyline_metadata_for_model(model=model, registry_lookup=registry_lookup, filter_row=filter_row)
        status = story["storyline_status"]
        if status not in headline_sets:
            continue

        selected_run_entries = {
            str(row.get("run_spec_name"))
            for row in filter_rows
            if row.get("selection_status") == "selected" and row.get("run_spec_name")
        }
        attempted_run_entries = {
            str(row.get("run_entry"))
            for row in scope_model_rows
            if row.get("run_entry")
        }
        completed_run_entries = {
            str(row.get("run_entry"))
            for row in scope_model_rows
            if row.get("run_entry") and _is_truthy_text(row.get("has_run_spec"))
        }
        analyzed_run_entries = set(model_analyzed_run_entries.get(model, set()))
        context = headline_sets[status]
        context["models"].add(model)
        context["selected_run_entries"].update(selected_run_entries)
        context["attempted_run_entries"].update(attempted_run_entries)
        context["completed_run_entries"].update(completed_run_entries)
        context["analyzed_run_entries"].update(analyzed_run_entries)

        if status != "off_story":
            continue

        off_story_rows.append(
            {
                "model": model,
                "storyline_status": status,
                "why_off_story": story["storyline_reason"],
                "expected_local_served": story["expected_local_served"],
                "replaces_helm_deployment": story["replaces_helm_deployment"],
                "local_registry_source": story["local_registry_source"],
                "registry_notes": story["registry_notes"],
                "n_selected_run_entries": len(selected_run_entries),
                "n_attempted_run_entries": len(attempted_run_entries),
                "n_completed_run_entries": len(completed_run_entries),
                "n_analyzed_run_entries": len(analyzed_run_entries),
                "n_attempt_rows": len(scope_model_rows),
                "n_completed_rows": sum(1 for row in scope_model_rows if _is_truthy_text(row.get("has_run_spec"))),
                "n_analysis_reports": len(model_repro_rows.get(model, [])),
                "selected_run_entries": _preview_values(sorted(selected_run_entries)),
                "attempted_run_entries": _preview_values(sorted(attempted_run_entries)),
                "analyzed_run_entries": _preview_values(sorted(analyzed_run_entries)),
                "attempted_experiment_names": _preview_values([
                    str(row.get("experiment_name")) for row in scope_model_rows if row.get("experiment_name")
                ]),
            }
        )

    off_story_rows.sort(
        key=lambda row: (
            -int(row.get("n_selected_run_entries") or 0),
            -int(row.get("n_attempted_run_entries") or 0),
            str(row.get("model") or ""),
        )
    )
    headline_counts = {
        status: {
            "n_models": len(values["models"]),
            "selected_run_entries": len(values["selected_run_entries"]),
            "attempted_run_entries": len(values["attempted_run_entries"]),
            "completed_run_entries": len(values["completed_run_entries"]),
            "analyzed_run_entries": len(values["analyzed_run_entries"]),
        }
        for status, values in headline_sets.items()
    }
    return {
        "definitions": {
            "off_story": "expected_local_served=True and replaces_helm_deployment is null in eval_audit/model_registry.py",
            "on_story": "expected_local_served=True and replaces_helm_deployment points at a public HELM deployment",
            "count_semantics": "selected counts are Stage 1 selected run_entry values; attempted/completed/analyzed counts are unique run_entry values observed in the current summary scope",
        },
        "headline_counts": headline_counts,
        "rows": off_story_rows,
    }


def _format_off_story_summary_text(
    *,
    scope_title: str,
    generated_utc: str,
    summary: dict[str, Any],
) -> list[str]:
    off_story_counts = summary["headline_counts"].get("off_story", {})
    on_story_counts = summary["headline_counts"].get("on_story", {})
    lines = [
        "Off-Story Local Serving Summary",
        "================================",
        f"Generated: {generated_utc}",
        f"Scope: {scope_title}",
        "",
        "Definitions:",
        f"  off_story: {summary['definitions']['off_story']}",
        f"  on_story:  {summary['definitions']['on_story']}",
        f"  counts:    {summary['definitions']['count_semantics']}",
        "",
        "Headline counts:",
        f"  off_story_models: {off_story_counts.get('n_models', 0)}",
        f"  off_story_selected_run_entries: {off_story_counts.get('selected_run_entries', 0)}",
        f"  off_story_attempted_run_entries: {off_story_counts.get('attempted_run_entries', 0)}",
        f"  off_story_completed_run_entries: {off_story_counts.get('completed_run_entries', 0)}",
        f"  off_story_analyzed_run_entries: {off_story_counts.get('analyzed_run_entries', 0)}",
        "",
        "On-story context:",
        f"  on_story_models: {on_story_counts.get('n_models', 0)}",
        f"  on_story_selected_run_entries: {on_story_counts.get('selected_run_entries', 0)}",
        f"  on_story_attempted_run_entries: {on_story_counts.get('attempted_run_entries', 0)}",
        f"  on_story_completed_run_entries: {on_story_counts.get('completed_run_entries', 0)}",
        f"  on_story_analyzed_run_entries: {on_story_counts.get('analyzed_run_entries', 0)}",
        "",
        "Per off-story model:",
    ]
    rows = summary.get("rows") or []
    if not rows:
        lines.append("  (no off-story models found in this scope)")
        return lines
    header = (
        f"{'model':<32} {'sel':>4} {'att':>4} {'cmp':>4} {'ana':>4} "
        f"{'source':<28} why_off_story"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for row in rows:
        lines.append(
            f"{str(row.get('model') or ''):<32} "
            f"{int(row.get('n_selected_run_entries') or 0):>4} "
            f"{int(row.get('n_attempted_run_entries') or 0):>4} "
            f"{int(row.get('n_completed_run_entries') or 0):>4} "
            f"{int(row.get('n_analyzed_run_entries') or 0):>4} "
            f"{str(row.get('local_registry_source') or ''):<28} "
            f"{str(row.get('why_off_story') or '')}"
        )
    return lines


def _build_analyzed_attempt_matchers(
    repro_rows: list[dict[str, Any]],
) -> tuple[dict[tuple[str, str], dict[str, set[str]]], set[tuple[str, str]]]:
    explicit_matchers: dict[tuple[str, str], dict[str, set[str]]] = {}
    analyzed_groups: set[tuple[str, str]] = set()
    for row in repro_rows:
        experiment_name = _clean_optional_text(row.get("experiment_name"))
        run_entry = _clean_optional_text(row.get("run_entry"))
        if not experiment_name or not run_entry:
            continue
        group_key = (experiment_name, run_entry)
        analyzed_groups.add(group_key)
        matcher = explicit_matchers.setdefault(
            group_key,
            {
                "run_dirs": set(),
                "attempt_identities": set(),
                "attempt_uuids": set(),
                "attempt_fallback_keys": set(),
            },
        )
        selected_run_dirs = row.get("analysis_selected_run_dirs") or []
        if isinstance(selected_run_dirs, str):
            try:
                selected_run_dirs = json.loads(selected_run_dirs)
            except Exception:
                selected_run_dirs = [selected_run_dirs]
        for run_dir in selected_run_dirs:
            run_dir_text = _clean_optional_text(run_dir)
            if run_dir_text:
                matcher["run_dirs"].add(run_dir_text)
        selected_attempt_refs = row.get("analysis_selected_attempt_refs") or []
        if isinstance(selected_attempt_refs, str):
            try:
                selected_attempt_refs = json.loads(selected_attempt_refs)
            except Exception:
                selected_attempt_refs = []
        for ref in selected_attempt_refs:
            if not isinstance(ref, dict):
                continue
            for src_key, dst_key in [
                ("run_dir", "run_dirs"),
                ("attempt_identity", "attempt_identities"),
                ("attempt_uuid", "attempt_uuids"),
                ("attempt_fallback_key", "attempt_fallback_keys"),
            ]:
                value = _clean_optional_text(ref.get(src_key))
                if value:
                    matcher[dst_key].add(value)
        selected_attempt_identities = row.get("analysis_selected_attempt_identities") or []
        if isinstance(selected_attempt_identities, str):
            try:
                selected_attempt_identities = json.loads(selected_attempt_identities)
            except Exception:
                selected_attempt_identities = [selected_attempt_identities]
        for identity in selected_attempt_identities:
            identity_text = _clean_optional_text(identity)
            if identity_text:
                matcher["attempt_identities"].add(identity_text)
    return explicit_matchers, analyzed_groups


def _analyzed_match_status(
    row: dict[str, Any],
    *,
    explicit_matchers: dict[tuple[str, str], dict[str, set[str]]],
    analyzed_groups: set[tuple[str, str]],
    completed_rows_by_group: dict[tuple[str, str], list[dict[str, Any]]],
) -> str:
    if not _is_truthy_text(row.get("has_run_spec")):
        return "not_completed"
    experiment_name = _clean_optional_text(row.get("experiment_name"))
    run_entry = _clean_optional_text(row.get("run_entry"))
    if not experiment_name or not run_entry:
        return "missing_group_key"
    group_key = (experiment_name, run_entry)
    if group_key not in analyzed_groups:
        return "not_in_analyzed_group"

    matcher = explicit_matchers.get(group_key)
    run_dir = _clean_optional_text(row.get("run_dir"))
    attempt_identity = _clean_optional_text(row.get("attempt_identity"))
    attempt_uuid = _clean_optional_text(row.get("attempt_uuid"))
    attempt_fallback_key = _clean_optional_text(row.get("attempt_fallback_key"))
    if matcher and any(matcher.values()):
        if run_dir and run_dir in matcher["run_dirs"]:
            return "explicit_run_dir"
        if attempt_identity and attempt_identity in matcher["attempt_identities"]:
            return "explicit_attempt_identity"
        if attempt_uuid and attempt_uuid in matcher["attempt_uuids"]:
            return "explicit_attempt_uuid"
        if attempt_fallback_key and attempt_fallback_key in matcher["attempt_fallback_keys"]:
            return "explicit_attempt_fallback_key"
        return "ambiguous_explicit_group_unmatched"

    completed_group_rows = completed_rows_by_group.get(group_key, [])
    if len(completed_group_rows) == 1:
        return "singleton_completed_group_fallback"
    if len(completed_group_rows) > 1:
        return "ambiguous_legacy_group_multi_completed"
    return "analyzed_group_without_completed_rows"


def _build_run_multiplicity_summary(
    *,
    filter_inventory_rows: list[dict[str, Any]],
    scope_rows: list[dict[str, Any]],
    repro_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    registry_lookup = local_model_registry_by_name()
    filter_lookup = _filter_inventory_lookup_by_run_entry(filter_inventory_rows)
    run_entry_meta = _run_entry_metadata_lookup(filter_inventory_rows, scope_rows)
    explicit_matchers, analyzed_groups = _build_analyzed_attempt_matchers(repro_rows)
    repro_by_run_entry = _group_repro_rows_by_run_entry(repro_rows)

    grouped_rows = _group_scope_rows_by_run_entry(scope_rows)
    summary_rows: list[dict[str, Any]] = []
    for run_entry, rows in grouped_rows.items():
        resolved_rows = []
        for row in rows:
            resolved = dict(row)
            resolved.update(_resolve_attempt_identity(row))
            resolved_rows.append(resolved)
        resolved_rows.sort(
            key=lambda row: (
                _coerce_float(row.get("manifest_timestamp")),
                str(row.get("experiment_name") or ""),
                str(row.get("job_id") or ""),
            ),
            reverse=True,
        )
        completed_rows = [row for row in resolved_rows if _is_truthy_text(row.get("has_run_spec"))]
        completed_rows_by_group: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in completed_rows:
            group_key = (
                _clean_optional_text(row.get("experiment_name")) or "",
                _clean_optional_text(row.get("run_entry")) or "",
            )
            completed_rows_by_group[group_key].append(row)
        analyzed_statuses = [
            _analyzed_match_status(
                row,
                explicit_matchers=explicit_matchers,
                analyzed_groups=analyzed_groups,
                completed_rows_by_group=completed_rows_by_group,
            )
            for row in resolved_rows
        ]
        analyzed_rows = [
            row for row, status in zip(resolved_rows, analyzed_statuses)
            if status in {
                "explicit_run_dir",
                "explicit_attempt_identity",
                "explicit_attempt_uuid",
                "explicit_attempt_fallback_key",
                "singleton_completed_group_fallback",
            }
        ]
        analyzed_match_status_counts = Counter(analyzed_statuses)
        attempt_ids = [str(row.get("attempt_identity")) for row in resolved_rows if row.get("attempt_identity")]
        attempt_uuids = [str(row.get("attempt_uuid")) for row in resolved_rows if row.get("attempt_uuid")]
        fallback_attempt_ids = [
            str(row.get("attempt_fallback_key"))
            for row in resolved_rows
            if not row.get("attempt_uuid") and row.get("attempt_fallback_key")
        ]
        manifest_timestamps = [str(row.get("manifest_timestamp")) for row in resolved_rows if row.get("manifest_timestamp") not in {None, ""}]
        experiment_names = [str(row.get("experiment_name")) for row in resolved_rows if row.get("experiment_name")]
        machine_hosts = [str(row.get("machine_host")) for row in resolved_rows if row.get("machine_host")]
        process_context_sources = [str(row.get("process_context_source")) for row in resolved_rows if row.get("process_context_source")]
        attempt_uuid_sources = [str(row.get("attempt_uuid_source")) for row in resolved_rows if row.get("attempt_uuid_source")]
        meta = run_entry_meta.get(run_entry, {})
        model = _clean_optional_text(meta.get("model")) or _clean_optional_text(resolved_rows[0].get("model"))
        benchmark = _clean_optional_text(meta.get("benchmark")) or _clean_optional_text(resolved_rows[0].get("benchmark"))
        scenario = _clean_optional_text(meta.get("scenario")) or benchmark or _clean_optional_text(meta.get("suite"))
        story = _storyline_metadata_for_model(
            model=model,
            registry_lookup=registry_lookup,
            filter_row=filter_lookup.get(run_entry),
        )
        summary_rows.append(
            {
                "logical_run_key": run_entry,
                "run_entry": run_entry,
                "model": model,
                "benchmark": benchmark,
                "scenario": scenario,
                "storyline_status": story["storyline_status"],
                "local_registry_source": story["local_registry_source"],
                "replaces_helm_deployment": story["replaces_helm_deployment"],
                "n_rows": len(resolved_rows),
                "n_completed_rows": len(completed_rows),
                "n_analyzed_rows": len(analyzed_rows),
                "n_analysis_reports": len(repro_by_run_entry.get(run_entry, [])),
                "n_experiments": len({item for item in experiment_names if item}),
                "n_machines": len({item for item in machine_hosts if item}),
                "n_manifest_timestamps": len({item for item in manifest_timestamps if item}),
                "n_attempt_ids": len(set(attempt_ids)),
                "n_attempt_uuids": len(set(attempt_uuids)),
                "n_rows_with_attempt_uuid": sum(1 for row in resolved_rows if row.get("attempt_uuid")),
                "n_rows_without_attempt_uuid": sum(1 for row in resolved_rows if not row.get("attempt_uuid")),
                "n_ambiguous_analyzed_candidates": int(
                    analyzed_match_status_counts.get("ambiguous_explicit_group_unmatched", 0)
                    + analyzed_match_status_counts.get("ambiguous_legacy_group_multi_completed", 0)
                ),
                "machine_hosts": _preview_values(machine_hosts, max_items=8),
                "experiment_names": _preview_values(experiment_names, max_items=8),
                "attempt_ids": _preview_values(attempt_ids, max_items=8),
                "attempt_uuids": _preview_values(attempt_uuids, max_items=8),
                "fallback_attempt_ids": _preview_values(fallback_attempt_ids, max_items=6),
                "process_context_sources": _preview_values(process_context_sources, max_items=4),
                "attempt_uuid_sources": _preview_values(attempt_uuid_sources, max_items=4),
                "manifest_timestamps": _preview_values(manifest_timestamps, max_items=6),
                "latest_manifest_timestamp": resolved_rows[0].get("manifest_timestamp"),
                "latest_attempt_identity": resolved_rows[0].get("attempt_identity"),
                "latest_attempt_identity_kind": resolved_rows[0].get("attempt_identity_kind"),
                "latest_attempt_uuid": resolved_rows[0].get("attempt_uuid"),
                "analyzed_match_status_counts": dict(analyzed_match_status_counts),
                "analyzed_match_modes": _preview_values(list(analyzed_match_status_counts.keys()), max_items=6),
                "analysis_report_dirs": _preview_values([
                    str(row.get("report_dir")) for row in repro_by_run_entry.get(run_entry, []) if row.get("report_dir")
                ], max_items=4),
            }
        )
    summary_rows.sort(
        key=lambda row: (
            -int(row.get("n_rows") or 0),
            -int(row.get("n_completed_rows") or 0),
            -int(row.get("n_analyzed_rows") or 0),
            str(row.get("run_entry") or ""),
        )
    )
    headline = {
        "n_logical_runs": len(summary_rows),
        "n_logical_runs_with_multiple_rows": sum(1 for row in summary_rows if int(row.get("n_rows") or 0) > 1),
        "n_logical_runs_with_multiple_completed_rows": sum(1 for row in summary_rows if int(row.get("n_completed_rows") or 0) > 1),
        "n_logical_runs_with_multiple_analyzed_rows": sum(1 for row in summary_rows if int(row.get("n_analyzed_rows") or 0) > 1),
        "n_logical_runs_with_ambiguous_analyzed_matching": sum(1 for row in summary_rows if int(row.get("n_ambiguous_analyzed_candidates") or 0) > 0),
        "n_logical_runs_spanning_multiple_machines": sum(1 for row in summary_rows if int(row.get("n_machines") or 0) > 1),
        "n_logical_runs_spanning_multiple_experiments": sum(1 for row in summary_rows if int(row.get("n_experiments") or 0) > 1),
        "n_logical_runs_with_multiple_manifest_timestamps": sum(1 for row in summary_rows if int(row.get("n_manifest_timestamps") or 0) > 1),
        "n_logical_runs_with_multiple_attempt_ids": sum(1 for row in summary_rows if int(row.get("n_attempt_ids") or 0) > 1),
        "n_logical_runs_with_multiple_attempt_uuids": sum(1 for row in summary_rows if int(row.get("n_attempt_uuids") or 0) > 1),
    }
    return {
        "definitions": {
            "logical_result": "logical_run_key == run_entry; this is the current report-layer identity for a logical result",
            "attempt": "one indexed kwdagger/materialize job row",
            "attempt_uuid": "process_context.properties.uuid when available from process_context.json or embedded adapter_manifest.process_context",
            "attempt_fallback_key": "fallback::experiment_name|job_id|run_entry|manifest_timestamp|machine_host|run_dir when UUID is missing",
            "attempt_identity": "attempt_uuid when present, otherwise attempt_fallback_key",
        "version": "a distinct attempt_identity observed under the same logical_run_key",
        "cross_machine_repeat": "same logical_run_key observed on more than one distinct machine_host",
        "analyzed_row": "a completed indexed row matched to report selection provenance by run_dir or attempt identity when available; otherwise only a singleton completed row in an analyzed legacy (experiment_name, run_entry) group",
    },
        "headline_counts": headline,
        "rows": summary_rows,
    }


def _format_run_multiplicity_summary_text(
    *,
    scope_title: str,
    generated_utc: str,
    summary: dict[str, Any],
) -> list[str]:
    counts = summary["headline_counts"]
    lines = [
        "Run Multiplicity Summary",
        "========================",
        f"Generated: {generated_utc}",
        f"Scope: {scope_title}",
        "",
        "Identity contract:",
        f"  logical_result: {summary['definitions']['logical_result']}",
        f"  attempt: {summary['definitions']['attempt']}",
        f"  attempt_uuid: {summary['definitions']['attempt_uuid']}",
        f"  attempt_fallback_key: {summary['definitions']['attempt_fallback_key']}",
        f"  attempt_identity: {summary['definitions']['attempt_identity']}",
        f"  version: {summary['definitions']['version']}",
        f"  cross_machine_repeat: {summary['definitions']['cross_machine_repeat']}",
        f"  analyzed_row: {summary['definitions']['analyzed_row']}",
        "",
        "Headline counts:",
        f"  n_logical_runs: {counts['n_logical_runs']}",
        f"  n_logical_runs_with_multiple_rows: {counts['n_logical_runs_with_multiple_rows']}",
        f"  n_logical_runs_with_multiple_completed_rows: {counts['n_logical_runs_with_multiple_completed_rows']}",
        f"  n_logical_runs_with_multiple_analyzed_rows: {counts['n_logical_runs_with_multiple_analyzed_rows']}",
        f"  n_logical_runs_with_ambiguous_analyzed_matching: {counts['n_logical_runs_with_ambiguous_analyzed_matching']}",
        f"  n_logical_runs_spanning_multiple_machines: {counts['n_logical_runs_spanning_multiple_machines']}",
        f"  n_logical_runs_spanning_multiple_experiments: {counts['n_logical_runs_spanning_multiple_experiments']}",
        f"  n_logical_runs_with_multiple_manifest_timestamps: {counts['n_logical_runs_with_multiple_manifest_timestamps']}",
        f"  n_logical_runs_with_multiple_attempt_ids: {counts['n_logical_runs_with_multiple_attempt_ids']}",
        f"  n_logical_runs_with_multiple_attempt_uuids: {counts['n_logical_runs_with_multiple_attempt_uuids']}",
        "",
        "Per logical run:",
    ]
    rows = summary.get("rows") or []
    if not rows:
        lines.append("  (no attempted runs found in this scope)")
        return lines
    header = (
        f"{'run_entry':<44} {'rows':>4} {'cmp':>4} {'ana':>4} {'amb':>4} {'exp':>4} "
        f"{'mach':>4} {'ids':>4} {'uuids':>5} latest_manifest"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for row in rows[:200]:
        lines.append(
            f"{str(row.get('run_entry') or ''):<44} "
            f"{int(row.get('n_rows') or 0):>4} "
            f"{int(row.get('n_completed_rows') or 0):>4} "
            f"{int(row.get('n_analyzed_rows') or 0):>4} "
            f"{int(row.get('n_ambiguous_analyzed_candidates') or 0):>4} "
            f"{int(row.get('n_experiments') or 0):>4} "
            f"{int(row.get('n_machines') or 0):>4} "
            f"{int(row.get('n_attempt_ids') or 0):>4} "
            f"{int(row.get('n_attempt_uuids') or 0):>5} "
            f"{str(row.get('latest_manifest_timestamp') or '')}"
        )
    if len(rows) > 200:
        lines.append(f"... ({len(rows) - 200} more rows)")
    return lines

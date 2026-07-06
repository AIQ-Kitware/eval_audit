"""Sankey assembly: funnel/universe/scope/attempt/repro flow diagrams.

Split out of ``eval_audit.workflows.build_reports_summary`` on
2026-06-11 (Phase 2 of docs/planning/repo-refactor-plan.md). Pure
relocation: function bodies are unchanged.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any
from eval_audit.infra.fs_publish import safe_unlink
from eval_audit.utils import sankey_builder

from eval_audit.reports.summary.classification import (
    ATTEMPTED_LABEL,
    FILTER_SELECTION_EXCLUDED_LABEL,
    FILTER_SELECTION_SELECTED_LABEL,
    NOT_ATTEMPTED_LABEL,
    _bucket_agreement,
    _choose_repro_row_for_run_entry,
    _classify_execution_stage,
    _classify_filter_gates,
    _group_repro_rows_by_run_entry,
    _group_scope_rows_by_run_entry,
)


def _build_universe_to_scope_root() -> tuple[sankey_builder.Root, list[str], dict[str, list[str]]]:
    """Stage A: Universe -> Scope.

    Chains the per-source eligibility gates to the selection waist.
    Terminal nodes are ``selected`` (= in scope) and the various
    ``excluded: <reason>`` outcomes. Stage B picks up from ``selected``.
    """
    root = sankey_builder.Root(label="Universe (all discovered runs)")
    structural = root.group(by="structural_gate", name="Structural Gate")
    structural["excluded: structurally incomplete"].connect(None)
    metadata = structural["kept: structurally complete"].group(by="metadata_gate", name="Metadata Gate")
    metadata["excluded: missing model metadata"].connect(None)
    open_weight = metadata["kept: model metadata resolved"].group(by="open_weight_gate", name="Open-Weight Gate")
    open_weight["excluded: not open weight"].connect(None)
    tag = open_weight["kept: open weight"].group(by="tag_gate", name="Tag Gate")
    tag["excluded: unsuitable text/modality tags"].connect(None)
    deployment = tag["kept: suitable text tags"].group(by="deployment_gate", name="Deployment Gate")
    deployment["excluded: no runnable local deployment"].connect(None)
    size = deployment["kept: runnable local deployment"].group(by="size_gate", name="Size Gate")
    size["excluded: exceeds size budget"].connect(None)
    selection = size["kept: within size budget"].connect(
        sankey_builder.Group(name="Selection", by="selection_gate")
    )
    assert isinstance(selection, sankey_builder.Group)
    # Terminal: selected = in scope; excluded = filtered out at selection time.
    # Stage B (sankey_b_scope_to_analyzed) picks up from the selected branch.
    selection[FILTER_SELECTION_EXCLUDED_LABEL].connect(None)
    selection[FILTER_SELECTION_SELECTED_LABEL].connect(None)

    stage_names = [
        "Structural Gate",
        "Metadata Gate",
        "Open-Weight Gate",
        "Tag Gate",
        "Deployment Gate",
        "Size Gate",
        "Selection",
    ]
    stage_defs = {
        "Structural Gate": [
            "excluded: structurally incomplete",
            "kept: structurally complete",
        ],
        "Metadata Gate": [
            "excluded: missing model metadata",
            "kept: model metadata resolved",
        ],
        "Open-Weight Gate": [
            "excluded: not open weight",
            "kept: open weight",
        ],
        "Tag Gate": [
            "excluded: unsuitable text/modality tags",
            "kept: suitable text tags",
        ],
        "Deployment Gate": [
            "excluded: no runnable local deployment",
            "kept: runnable local deployment",
        ],
        "Size Gate": [
            "excluded: exceeds size budget",
            "kept: within size budget",
        ],
        "Selection": [
            FILTER_SELECTION_SELECTED_LABEL,
            FILTER_SELECTION_EXCLUDED_LABEL,
        ],
    }
    return root, stage_names, stage_defs


def _build_scope_to_analyzed_root() -> tuple[sankey_builder.Root, list[str], dict[str, list[str]]]:
    """Stage B: Scope -> Attempt -> Execution -> Analysis -> Reproduction.

    Picks up from Stage A's ``selected`` branch. The first stage
    (``Attempt``) splits ``in scope`` into ``attempted`` vs
    ``selected but not attempted`` so the funnel surfaces the gap
    between "we wanted to run this" and "we actually ran this".
    """
    root = sankey_builder.Root(label="Scope (in-scope after Stage-A filtering)")
    attempt = root.group(by="attempt_stage", name="Attempt")
    attempt[NOT_ATTEMPTED_LABEL].connect(None)
    execution = attempt[ATTEMPTED_LABEL].group(by="execution_stage", name="Execution")
    execution["attempted_not_finished"].connect(None)
    execution["attempted_failed_or_incomplete"].connect(None)
    analysis = execution["completed_with_run_artifacts"].group(by="analysis_stage", name="Analysis")
    analysis["completed_not_yet_analyzed"].connect(None)
    analysis["analyzed"].group(by="reproduction_stage", name="Reproduction")
    stage_names = ["Attempt", "Execution", "Analysis", "Reproduction"]
    stage_defs = {
        "Attempt": [
            ATTEMPTED_LABEL,
            NOT_ATTEMPTED_LABEL,
        ],
        "Execution": [
            "attempted_not_finished",
            "attempted_failed_or_incomplete",
            "completed_with_run_artifacts",
        ],
        "Analysis": [
            "completed_not_yet_analyzed",
            "analyzed",
        ],
        "Reproduction": [
            "exact_or_near_exact",
            "high_agreement_0.95+",
            "moderate_agreement_0.80+",
            "low_agreement_0.00+",
            "zero_agreement",
        ],
    }
    return root, stage_names, stage_defs


def _build_scope_to_analyzed_rows(
    filter_inventory_rows: list[dict[str, Any]],
    scope_rows: list[dict[str, Any]],
    repro_rows: list[dict[str, Any]],
    *,
    tol_key: str,
) -> list[dict[str, str]]:
    """Stage B rows: in-scope (selected) -> attempt -> execution -> analysis -> reproduction.

    Source population is filter_inventory rows with selection_status=='selected'
    (i.e. the rows that *are* in scope after Stage A). Each row is then
    annotated with whether we attempted, completed, analyzed, and at what
    agreement level it landed.
    """
    scope_rows_by_run_entry = _group_scope_rows_by_run_entry(scope_rows)
    repro_rows_by_run_entry = _group_repro_rows_by_run_entry(repro_rows)
    scope_rows_by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in scope_rows:
        key = (str(row.get("experiment_name") or ""), str(row.get("run_entry") or ""))
        scope_rows_by_key[key].append(row)

    sankey_rows: list[dict[str, str]] = []
    for row in filter_inventory_rows:
        if row.get("selection_status") != "selected":
            continue
        run_entry = str(row.get("run_spec_name") or "")
        scope_rows_for_entry = scope_rows_by_run_entry.get(run_entry, [])
        repro_rows_for_entry = repro_rows_by_run_entry.get(run_entry, [])
        flow: dict[str, str] = {}
        if not scope_rows_for_entry:
            flow["attempt_stage"] = NOT_ATTEMPTED_LABEL
            sankey_rows.append(flow)
            continue
        flow["attempt_stage"] = ATTEMPTED_LABEL
        execution = _classify_execution_stage(scope_rows_for_entry)
        flow["execution_stage"] = execution
        if execution != "completed_with_run_artifacts":
            sankey_rows.append(flow)
            continue
        repro_row = _choose_repro_row_for_run_entry(repro_rows_for_entry, scope_rows_by_key)
        if repro_row is None:
            flow["analysis_stage"] = "completed_not_yet_analyzed"
            sankey_rows.append(flow)
            continue
        flow["analysis_stage"] = "analyzed"
        flow["reproduction_stage"] = _bucket_agreement(repro_row.get(tol_key))
        sankey_rows.append(flow)
    return sankey_rows


def _build_universe_to_scope_rows(
    filter_inventory_rows: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Stage A rows: pure filter-gate flow ending at the Selection waist.

    Delegates the six-gate ladder to the shared
    ``_classify_filter_gates`` classifier; the row dicts intentionally do
    *not* carry post-selection keys, so the Stage A sankey terminates at
    Selection.
    """
    return [_classify_filter_gates(row) for row in filter_inventory_rows]


def _build_end_to_end_funnel_root() -> tuple[sankey_builder.Root, list[str], dict[str, list[str]]]:
    root = sankey_builder.Root(label="All discovered historic HELM runs")
    structural = root.group(by="structural_gate", name="Structural Gate")
    structural["excluded: structurally incomplete"].connect(None)
    metadata = structural["kept: structurally complete"].group(by="metadata_gate", name="Metadata Gate")
    metadata["excluded: missing model metadata"].connect(None)
    open_weight = metadata["kept: model metadata resolved"].group(by="open_weight_gate", name="Open-Weight Gate")
    open_weight["excluded: not open weight"].connect(None)
    tag = open_weight["kept: open weight"].group(by="tag_gate", name="Tag Gate")
    tag["excluded: unsuitable text/modality tags"].connect(None)
    deployment = tag["kept: suitable text tags"].group(by="deployment_gate", name="Deployment Gate")
    deployment["excluded: no runnable local deployment"].connect(None)
    size = deployment["kept: runnable local deployment"].group(by="size_gate", name="Size Gate")
    size["excluded: exceeds size budget"].connect(None)
    selection = size["kept: within size budget"].connect(
        sankey_builder.Group(name="Selection", by="selection_gate")
    )
    assert isinstance(selection, sankey_builder.Group)
    selection[FILTER_SELECTION_EXCLUDED_LABEL].connect(None)
    execution = selection[FILTER_SELECTION_SELECTED_LABEL].group(by="execution_stage", name="Execution")
    execution["not_run_in_scope"].connect(None)
    execution["attempted_not_finished"].connect(None)
    execution["attempted_failed_or_incomplete"].connect(None)
    analysis = execution["completed_with_run_artifacts"].group(by="analysis_stage", name="Analysis")
    analysis["completed_not_yet_analyzed"].connect(None)
    analysis["analyzed"].group(by="reproduction_stage", name="Reproduction")

    stage_names = [
        "Structural Gate",
        "Metadata Gate",
        "Open-Weight Gate",
        "Tag Gate",
        "Deployment Gate",
        "Size Gate",
        "Selection",
        "Execution",
        "Analysis",
        "Reproduction",
    ]
    stage_defs = {
        "Structural Gate": [
            "excluded: structurally incomplete",
            "kept: structurally complete",
        ],
        "Metadata Gate": [
            "excluded: missing model metadata",
            "kept: model metadata resolved",
        ],
        "Open-Weight Gate": [
            "excluded: not open weight",
            "kept: open weight",
        ],
        "Tag Gate": [
            "excluded: unsuitable text/modality tags",
            "kept: suitable text tags",
        ],
        "Deployment Gate": [
            "excluded: no runnable local deployment",
            "kept: runnable local deployment",
        ],
        "Size Gate": [
            "excluded: exceeds size budget",
            "kept: within size budget",
        ],
        "Selection": [
            FILTER_SELECTION_SELECTED_LABEL,
            FILTER_SELECTION_EXCLUDED_LABEL,
        ],
        "Execution": [
            "not_run_in_scope",
            "attempted_not_finished",
            "attempted_failed_or_incomplete",
            "completed_with_run_artifacts",
        ],
        "Analysis": [
            "completed_not_yet_analyzed",
            "analyzed",
        ],
        "Reproduction": [
            "exact_or_near_exact",
            "high_agreement_0.95+",
            "moderate_agreement_0.80+",
            "low_agreement_0.00+",
            "zero_agreement",
        ],
    }
    return root, stage_names, stage_defs


def _bucket_metric_delta(max_delta: float | None) -> str:
    if max_delta is None:
        return "not_available"
    if max_delta == 0.0:
        return "exact_match"
    if max_delta <= 0.001:
        return "tiny_drift_0.001"
    if max_delta <= 0.01:
        return "small_drift_0.01"
    return "large_drift"


def _expand_repro_rows_by_metric(
    repro_rows: list[dict[str, Any]],
    enriched_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    enriched_lookup = {
        (str(r.get("experiment_name")), str(r.get("run_entry"))): r
        for r in enriched_rows
    }
    expanded = []
    for row in repro_rows:
        deltas = row.get("official_runlevel_metric_max_deltas") or {}
        if isinstance(deltas, str):
            try:
                deltas = json.loads(deltas)
            except Exception:
                deltas = {}
        metrics = row.get("core_metrics") or list(deltas.keys())
        if isinstance(metrics, str):
            try:
                metrics = json.loads(metrics)
            except Exception:
                metrics = [metrics] if metrics else []
        key = (str(row.get("experiment_name")), str(row.get("run_entry")))
        parent = enriched_lookup.get(key)
        for metric in (metrics or ["unknown"]):
            max_delta = deltas.get(metric)
            expanded.append({
                "group": str((parent or {}).get("benchmark") or "unknown"),
                "metric": str(metric),
                "drift_bucket": _bucket_metric_delta(max_delta),
            })
    return expanded


def _build_repro_sankey_rows_at_tol(
    repro_rows: list[dict[str, Any]],
    enriched_rows: list[dict[str, Any]],
    agree_field: str,
) -> list[dict[str, Any]]:
    enriched_lookup = {
        (str(r.get("experiment_name")), str(r.get("run_entry"))): r
        for r in enriched_rows
    }
    rows = []
    for row in repro_rows:
        key = (str(row.get("experiment_name")), str(row.get("run_entry")))
        parent = enriched_lookup.get(key)
        agree_val = row.get(agree_field)
        agree = float(agree_val) if agree_val is not None and agree_val != "" else None
        rows.append({
            "group": str((parent or {}).get("benchmark") or "unknown"),
            "repeatability": str(row.get("repeat_diagnosis") or "unknown"),
            "agreement": _bucket_agreement(agree),
            "diagnosis": str(row.get("official_diagnosis") or "unknown"),
        })
    return rows


_LEGACY_SANKEY_ALIAS_NAMES = (
    "sankey_s02_filter_to_attempt.html",
    "sankey_s02_filter_to_attempt.jpg",
    "sankey_s02_filter_to_attempt.txt",
    "sankey_s02_filter_to_attempt.json",
    "sankey_s03_attempted_to_repro.html",
    "sankey_s03_attempted_to_repro.jpg",
    "sankey_s03_attempted_to_repro.txt",
    "sankey_s03_attempted_to_repro.json",
    "sankey_s04_end_to_end.html",
    "sankey_s04_end_to_end.jpg",
    "sankey_s04_end_to_end.txt",
    "sankey_s04_end_to_end.json",
)


def _cleanup_legacy_sankey_aliases(scope_root: Path) -> None:
    """Unlink legacy s02/s03/s04 sankey aliases after the rename to a/b.

    Stage 2 of the funnel-decomposition refactor renamed:
      sankey_s02_filter_to_attempt → sankey_a_universe_to_scope
      sankey_s03_attempted_to_repro → sankey_b_scope_to_analyzed
      sankey_s04_end_to_end → dropped (decomposable into a + b)
    """
    target_names = set(_LEGACY_SANKEY_ALIAS_NAMES)
    for path in scope_root.rglob("*"):
        if path.name in target_names:
            safe_unlink(path)

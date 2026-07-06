"""Filter/stage taxonomy: classify rows into pools, stages, and storylines.

Split out of ``eval_audit.workflows.build_reports_summary`` on
2026-06-11 (Phase 2 of docs/planning/repo-refactor-plan.md). Pure
relocation: function bodies are unchanged.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any
from eval_audit.infra.api import default_store_root
from eval_audit.infra.logging import rich_link
from eval_audit.indexing.historic_filtering import CLOSED_JUDGE_REQUIRED_REASON
from loguru import logger

from eval_audit.reports.summary.common import (
    _clean_optional_text,
    _coerce_float,
    _is_truthy_text,
    _normalize_text,
)


def _build_attempt_fallback_key_from_row(row: dict[str, Any]) -> str:
    parts = {
        "experiment_name": _clean_optional_text(row.get("experiment_name")) or "unknown",
        "job_id": _clean_optional_text(row.get("job_id")) or "unknown",
        "run_entry": _clean_optional_text(row.get("run_entry")) or "unknown",
        "manifest_timestamp": _clean_optional_text(row.get("manifest_timestamp")) or "unknown",
        "machine_host": _clean_optional_text(row.get("machine_host")) or "unknown",
        "run_dir": _clean_optional_text(row.get("run_dir")) or "unknown",
    }
    return "fallback::" + "|".join(f"{key}={value}" for key, value in parts.items())


def _resolve_attempt_identity(row: dict[str, Any]) -> dict[str, str | None]:
    attempt_uuid = _clean_optional_text(row.get("attempt_uuid"))
    attempt_fallback_key = _clean_optional_text(row.get("attempt_fallback_key")) or _build_attempt_fallback_key_from_row(row)
    attempt_identity = _clean_optional_text(row.get("attempt_identity")) or attempt_uuid or attempt_fallback_key
    attempt_identity_kind = _clean_optional_text(row.get("attempt_identity_kind")) or ("attempt_uuid" if attempt_uuid else "fallback")
    return {
        "attempt_uuid": attempt_uuid,
        "attempt_fallback_key": attempt_fallback_key,
        "attempt_identity": attempt_identity,
        "attempt_identity_kind": attempt_identity_kind,
    }


def _storyline_status(expected_local_served: bool, replaces_helm_deployment: str | None) -> str:
    if expected_local_served and replaces_helm_deployment:
        return "on_story"
    if expected_local_served:
        return "off_story"
    return "not_local_story"


def _storyline_reason(expected_local_served: bool, replaces_helm_deployment: str | None) -> str:
    if expected_local_served and replaces_helm_deployment:
        return "expected_local_served=True and replaces_helm_deployment points to a public HELM deployment"
    if expected_local_served:
        return "expected_local_served=True but replaces_helm_deployment is null, so this is a local extension outside the public HELM storyline"
    return "model is not marked as expected_local_served in the checked-in local model registry"


def _filter_inventory_lookup_by_run_entry(filter_inventory_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for row in filter_inventory_rows:
        run_entry = _clean_optional_text(row.get("run_spec_name"))
        if not run_entry:
            continue
        lookup[run_entry] = row
    return lookup


def _storyline_metadata_for_model(
    *,
    model: str | None,
    registry_lookup: dict[str, Any],
    filter_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reg_entry = registry_lookup.get(model or "")
    if reg_entry is not None:
        expected_local_served = bool(reg_entry.expected_local_served)
        replaces_helm_deployment = reg_entry.replaces_helm_deployment
        local_registry_source = reg_entry.source
        registry_notes = reg_entry.notes or None
    else:
        expected_local_served = _is_truthy_text((filter_row or {}).get("expected_local_served"))
        replaces_helm_deployment = _clean_optional_text((filter_row or {}).get("replaces_helm_deployment"))
        local_registry_source = _clean_optional_text((filter_row or {}).get("local_registry_source"))
        registry_notes = None
    status = _storyline_status(expected_local_served, replaces_helm_deployment)
    return {
        "expected_local_served": expected_local_served,
        "replaces_helm_deployment": replaces_helm_deployment,
        "local_registry_source": local_registry_source,
        "registry_notes": registry_notes,
        "storyline_status": status,
        "storyline_reason": _storyline_reason(expected_local_served, replaces_helm_deployment),
    }


def _run_entry_metadata_lookup(
    filter_inventory_rows: list[dict[str, Any]],
    scope_rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for row in filter_inventory_rows:
        run_entry = _clean_optional_text(row.get("run_spec_name"))
        if not run_entry:
            continue
        info = lookup.setdefault(run_entry, {})
        for src_key, dst_key in [
            ("model", "model"),
            ("benchmark", "benchmark"),
            ("scenario", "scenario"),
            ("suite", "suite"),
            ("dataset", "dataset"),
            ("setting", "setting"),
        ]:
            value = _clean_optional_text(row.get(src_key))
            if value and not info.get(dst_key):
                info[dst_key] = value
    for row in scope_rows:
        run_entry = _clean_optional_text(row.get("run_entry"))
        if not run_entry:
            continue
        info = lookup.setdefault(run_entry, {})
        for src_key, dst_key in [
            ("model", "model"),
            ("benchmark", "benchmark"),
            ("suite", "suite"),
        ]:
            value = _clean_optional_text(row.get(src_key))
            if value and not info.get(dst_key):
                info[dst_key] = value
    return lookup


def _default_filter_inventory_json() -> Path:
    return default_store_root() / "analysis" / "filter_inventory.json"


def _load_filter_inventory_rows(
    filter_inventory_json: Path | None,
    *,
    skip: bool = False,
) -> list[dict[str, Any]]:
    """Load the Stage-1 filter inventory.

    When ``skip`` is True, return an empty list regardless of any explicit
    or default path. Use this for scoped sub-experiments (e.g. virtual
    experiments) where the global Stage-1 filter funnel does not describe
    the report's denominator and would only mislead the reader.
    """
    if skip:
        return []
    path = filter_inventory_json if filter_inventory_json is not None else _default_filter_inventory_json()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text())
    except Exception:
        logger.warning(f"Unable to load filter inventory: {rich_link(path)}")
        return []
    if not isinstance(payload, list):
        logger.warning(f"Filter inventory is not a list: {rich_link(path)}")
        return []
    return [row for row in payload if isinstance(row, dict)]


def _bucket_agreement(agree_ratio: float | None) -> str:
    if agree_ratio is None:
        return "not_analyzed"
    if agree_ratio >= 0.999999:
        return "exact_or_near_exact"
    if agree_ratio >= 0.95:
        return "high_agreement_0.95+"
    if agree_ratio >= 0.80:
        return "moderate_agreement_0.80+"
    if agree_ratio > 0.0:
        return "low_agreement_0.00+"
    return "zero_agreement"


FILTER_SELECTION_EXCLUDED_LABEL = "not selected for attempted runs"
FILTER_SELECTION_SELECTED_LABEL = "selected for attempted runs"
ATTEMPTED_LABEL = "attempted run"
NOT_ATTEMPTED_LABEL = "selected but not attempted"


def _classify_filter_gates(row: dict[str, Any]) -> dict[str, str]:
    """Compute the Stage-A filter-ladder flow for one filter-inventory row.

    Single source of truth for the six-gate ladder (structural → metadata →
    open-weight → tag → deployment → size → selection) that was previously
    triplicated across the sankey row-builders. Returns a flow dict carrying one
    label per gate the row reached, terminating at the first gate that excludes
    it (so an excluded row omits later-gate keys). The terminal key is
    ``selection_gate`` (selected vs excluded).
    """
    reasons = {str(r) for r in (row.get("failure_reasons") or []) if str(r)}
    flow: dict[str, str] = {}
    if row.get("is_structurally_incomplete"):
        flow["structural_gate"] = "excluded: structurally incomplete"
        return flow
    flow["structural_gate"] = "kept: structurally complete"
    if "missing-model-metadata" in reasons:
        flow["metadata_gate"] = "excluded: missing model metadata"
        return flow
    flow["metadata_gate"] = "kept: model metadata resolved"
    if "not-open-access" in reasons:
        flow["open_weight_gate"] = "excluded: not open weight"
        return flow
    flow["open_weight_gate"] = "kept: open weight"
    if ("excluded-tags" in reasons) or ("not-text-like" in reasons):
        flow["tag_gate"] = "excluded: unsuitable text/modality tags"
        return flow
    flow["tag_gate"] = "kept: suitable text tags"
    if "no-local-helm-deployment" in reasons:
        flow["deployment_gate"] = "excluded: no runnable local deployment"
        return flow
    flow["deployment_gate"] = "kept: runnable local deployment"
    if "too-large" in reasons:
        flow["size_gate"] = "excluded: exceeds size budget"
        return flow
    flow["size_gate"] = "kept: within size budget"
    # R-4c: attribute closed-judge exclusions to a dedicated judge gate so the
    # Stage-A funnel lines up with the hierarchical funnel (which already has
    # one); previously these rows fell through to the selection waist unattributed.
    if CLOSED_JUDGE_REQUIRED_REASON in reasons:
        flow["judge_gate"] = "excluded: requires closed-source judge"
        return flow
    flow["judge_gate"] = "kept: no closed-source judge dependency"
    if row.get("selection_status") != "selected":
        flow["selection_gate"] = FILTER_SELECTION_EXCLUDED_LABEL
    else:
        flow["selection_gate"] = FILTER_SELECTION_SELECTED_LABEL
    return flow


def _group_scope_rows_by_run_entry(scope_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in scope_rows:
        run_entry = str(row.get("run_entry") or "").strip()
        if run_entry:
            grouped[run_entry].append(row)
    return grouped


def _group_repro_rows_by_run_entry(repro_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in repro_rows:
        run_entry = str(row.get("run_entry") or "").strip()
        if run_entry:
            grouped[run_entry].append(row)
    return grouped


def _classify_execution_stage(scope_rows_for_entry: list[dict[str, Any]]) -> str:
    if not scope_rows_for_entry:
        return "not_run_in_scope"
    if any(_is_truthy_text(row.get("has_run_spec")) for row in scope_rows_for_entry):
        return "completed_with_run_artifacts"
    statuses = {_normalize_text(row.get("status")) for row in scope_rows_for_entry}
    if statuses & {"running", "queued"}:
        return "attempted_not_finished"
    return "attempted_failed_or_incomplete"


def _choose_repro_row_for_run_entry(
    repro_rows_for_entry: list[dict[str, Any]],
    scope_rows_by_key: dict[tuple[str, str], list[dict[str, Any]]],
) -> dict[str, Any] | None:
    if not repro_rows_for_entry:
        return None

    def _repro_row_rank(row: dict[str, Any]) -> tuple[float, str, str, str, str]:
        key = (str(row.get("experiment_name") or ""), str(row.get("run_entry") or ""))
        matching_scope_rows = scope_rows_by_key.get(key, [])
        manifest_ts = max(
            (_coerce_float(item.get("manifest_timestamp")) for item in matching_scope_rows),
            default=float("-inf"),
        )
        return (
            manifest_ts,
            str(row.get("experiment_name") or ""),
            str(row.get("packet_id") or ""),
            str(row.get("report_dir") or ""),
            str(row.get("report_json") or ""),
        )

    return max(repro_rows_for_entry, key=_repro_row_rank)

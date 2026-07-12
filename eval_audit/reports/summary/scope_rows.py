"""Row enrichment + sankey row-building for one scope render.

Moved verbatim out of ``workflows.build_reports_summary`` on
2026-07-12 (plan item C1 of
docs/planning/repo-simplification-plan-2026-07-12.md), finishing the
Phase-2 split: bodies unchanged; only the import wiring is new.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from eval_audit.model_registry import local_model_registry_by_name
from eval_audit.reports.summary.common import (
    _is_truthy_text,
    _clean_optional_text,
)
from eval_audit.reports.summary.classification import (
    _resolve_attempt_identity,
    _filter_inventory_lookup_by_run_entry,
    _storyline_metadata_for_model,
)
from eval_audit.reports.summary.failure_triage import _classify_failure
from eval_audit.reports.summary.sankeys import (
    _build_scope_to_analyzed_rows,
    _build_universe_to_scope_rows,
    _expand_repro_rows_by_metric,
    _build_repro_sankey_rows_at_tol,
)

def _build_enriched_scope_rows(
    *,
    scope_rows: list[dict[str, Any]],
    repro_rows: list[dict[str, Any]],
    filter_inventory_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
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
    return enriched_rows, failed_rows, repro_keyed


def _build_scope_sankey_rows(
    *,
    enriched_rows: list[dict[str, Any]],
    repro_rows: list[dict[str, Any]],
    filter_inventory_rows: list[dict[str, Any]],
    scope_rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
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
    return {
        "operational_sankey_rows": operational_sankey_rows,
        "repro_sankey_rows": repro_sankey_rows,
        "repro_tol001_rows": repro_tol001_rows,
        "repro_tol010_rows": repro_tol010_rows,
        "repro_tol050_rows": repro_tol050_rows,
        "metric_sankey_rows": metric_sankey_rows,
        "universe_to_scope_rows": universe_to_scope_rows,
        "scope_to_analyzed_exact_rows": scope_to_analyzed_exact_rows,
        "scope_to_analyzed_tol001_rows": scope_to_analyzed_tol001_rows,
        "scope_to_analyzed_tol010_rows": scope_to_analyzed_tol010_rows,
        "scope_to_analyzed_tol050_rows": scope_to_analyzed_tol050_rows,
    }

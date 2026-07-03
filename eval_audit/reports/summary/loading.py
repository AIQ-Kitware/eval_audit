"""Load all repro rows for the summary build (packet-manifest driven).

Split out of ``eval_audit.workflows.build_reports_summary`` on
2026-06-11 (Phase 2 of docs/planning/repo-refactor-plan.md). Pure
relocation: function bodies are unchanged.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from eval_audit.infra.report_layout import (
    experiments_analysis_root,
    legacy_repo_publication_root,
    publication_experiments_root,
)
from eval_audit.reports.core_packet_summary import (
    find_report_pair,
    load_core_report_bundle,
    packet_component_by_source_kind,
    packet_local_reference_component,
)
from eval_audit.utils.numeric import nested_get

from eval_audit.reports.summary.common import CANONICAL_AGREEMENT_TOL, _clean_optional_text, _find_curve_value
from eval_audit.reports.summary.classification import _bucket_agreement


def _load_all_repro_rows(
    extra_analysis_roots: list[Path] | None = None,
    *,
    skip_canonical_scan: bool = False,
) -> list[dict[str, Any]]:
    # Scan the canonical store location plus the publication-side and
    # legacy-repo symlink trees so experiments that haven't been re-run
    # since either layout migration are still found.
    #
    # ``extra_analysis_roots`` lets callers point the scan at additional
    # locations that hold the same ``<X>/<something>/core-reports/<packet>/...``
    # shape — virtual experiments, in particular, hold their per-packet
    # reports under their own ``output.root`` and would otherwise be
    # invisible to the aggregate summary.
    #
    # ``skip_canonical_scan`` is used by standalone callers (e.g. the
    # ``eval-audit-from-eee`` tutorial) that want the summary scoped to
    # just their own analysis dir; otherwise the system's pre-existing
    # experiment store would bleed into a tutorial-scope report.
    extra_roots = [Path(p).expanduser().resolve() for p in (extra_analysis_roots or [])]
    if skip_canonical_scan:
        canonical_paths: list[Path] = []
    else:
        canonical_root = experiments_analysis_root()
        publication_root_link_dir = publication_experiments_root()
        legacy_repo_root = legacy_repo_publication_root()
        canonical_paths = (
            list(canonical_root.glob("*/core-reports/*/core_metric_report.json"))
            + list(publication_root_link_dir.glob("experiment-analysis-*/core-reports/*/core_metric_report.json"))
            + list(legacy_repo_root.glob("experiment-analysis-*/core-reports/*/core_metric_report.json"))
        )
    report_jsons = sorted(
        canonical_paths
        + [
            p
            for root in extra_roots
            for p in root.glob("*/core-reports/*/core_metric_report.json")
        ]
    )
    deduped: dict[tuple[str | None, str | None], dict[str, Any]] = {}
    for report_json in report_jsons:
        try:
            bundle = load_core_report_bundle(report_json)
        except Exception:
            continue
        report = bundle["report"]
        packet = bundle["packet"]
        experiment_name = packet["components_manifest"].get("experiment_name")
        run_entry = packet["components_manifest"].get("run_entry")
        if not experiment_name or not run_entry:
            continue
        local_components = [
            component
            for component in packet.get("components", [])
            if component.get("source_kind") == "local"
        ]
        selected_run_dirs = sorted({
            str(component.get("run_path"))
            for component in local_components
            if component.get("run_path")
        })
        official = find_report_pair(report, "official_vs_local") or {}
        repeat = find_report_pair(report, "local_repeat") or {}
        official_diag = official.get("diagnosis", {}) or {}
        repeat_diag = repeat.get("diagnosis", {}) or {}
        official_instance_level = official.get("instance_level") or {}
        official_agree_curve = official_instance_level.get("agreement_vs_abs_tol") or []
        agree_0 = _find_curve_value(official_agree_curve, 0.0)
        agree_005 = _find_curve_value(official_agree_curve, CANONICAL_AGREEMENT_TOL)
        local_reference = packet_local_reference_component(packet)
        official_component = packet_component_by_source_kind(packet, "official_vs_local", "official")
        # Stage-5: surface the artifact_format provenance on every aggregate
        # row so the breakdowns can show whether a given comparison ran
        # against canonical EEE artifacts or in-memory HELM->EEE conversion.
        artifact_formats = sorted({
            (component.get("artifact_format") or "helm")
            for component in packet.get("components", [])
            if component.get("artifact_format") is not None
            or component.get("run_path")
        })
        row = {
            "experiment_name": experiment_name,
            "run_entry": run_entry,
            "packet_id": packet["components_manifest"].get("packet_id"),
            "selected_public_track": packet["components_manifest"].get("selected_public_track"),
            "run_spec_name": report.get("run_spec_name"),
            "report_dir": str(report_json.parent),
            "report_json": str(report_json),
            "components_manifest": str(packet["components_manifest_path"]),
            "comparisons_manifest": str(packet["comparisons_manifest_path"]),
            "warnings_manifest": str(packet["warnings_manifest_path"]),
            "analysis_local_reference_run": _clean_optional_text(local_reference.get("run_path")),
            "analysis_official_run": _clean_optional_text(official_component.get("run_path")),
            "analysis_selected_run_dirs": selected_run_dirs,
            "analysis_selected_attempt_refs": [component.get("selection_ref") for component in local_components if component.get("selection_ref")],
            "analysis_selected_attempt_identities": [component.get("attempt_identity") for component in local_components if component.get("attempt_identity")],
            "analysis_single_run": not bool(repeat),
            "repeat_diagnosis": repeat_diag.get("label"),
            "repeat_primary_reasons": repeat_diag.get("primary_reason_names") or [],
            "official_diagnosis": official_diag.get("label"),
            "official_primary_reasons": official_diag.get("primary_reason_names") or [],
            "official_instance_agree_0": agree_0,
            "official_instance_agree_005": agree_005,
            "official_instance_agree_bucket": _bucket_agreement(agree_005),
            "official_instance_agree_01": _find_curve_value(official_agree_curve, 0.1),
            # Dedicated abs_tol=0.01 point (curve grid contains 1e-2). The
            # ``_01`` key above is the abs_tol=0.1 point despite the terse
            # name; the tol010 sankeys (titled abs_tol=0.010) must bucket on
            # THIS key, not ``_01`` — see P0-1.
            "official_instance_agree_010": _find_curve_value(official_agree_curve, 0.01),
            "official_runlevel_abs_max": nested_get(official, "run_level", "overall_quantiles", "abs_delta", "max"),
            "official_runlevel_abs_p90": nested_get(official, "run_level", "overall_quantiles", "abs_delta", "p90"),
            "official_instance_agree_001": _find_curve_value(official_agree_curve, 0.001),
            "core_metrics": official.get("core_metrics") or [],
            "artifact_formats": artifact_formats,
            "artifact_format": ",".join(artifact_formats) if artifact_formats else "helm",
            "official_runlevel_metric_max_deltas": {
                m["metric"]: nested_get(m, "abs_delta", "max")
                for m in (nested_get(official, "run_level", "by_metric") or [])
            },
            "official_instance_agree_curve": [
                {"abs_tol": pt["abs_tol"], "agree_ratio": pt["agree_ratio"]}
                for pt in official_agree_curve
            ],
            "official_per_metric_agreement": nested_get(official, "instance_level", "per_metric_agreement") or {},
            "packet_warnings": (packet.get("warnings_manifest") or {}).get("packet_warnings", []),
            "packet_caveats": (packet.get("warnings_manifest") or {}).get("packet_caveats", []),
            "comparison_warning_count": sum(
                len(comparison.get("warnings") or [])
                + (1 if comparison.get("disabled_reason") else 0)
                for comparison in ((packet.get("warnings_manifest") or {}).get("comparisons") or [])
            ),
            "report_warning_count": len((packet.get("warnings_manifest") or {}).get("packet_warnings") or []),
            "has_report_warnings": bool(
                (packet.get("warnings_manifest") or {}).get("packet_warnings")
                or report.get("diagnostic_flags")
                or any(
                    comparison.get("warnings") or comparison.get("disabled_reason")
                    for comparison in ((packet.get("warnings_manifest") or {}).get("comparisons") or [])
                )
            ),
        }
        deduped[(experiment_name, run_entry, row["packet_id"] or row["report_dir"])] = row
    return list(deduped.values())

"""Sankey artifact emission for one scope render.

Moved verbatim out of ``workflows.build_reports_summary`` on
2026-07-12 (plan item C1 of
docs/planning/repo-simplification-plan-2026-07-12.md), finishing the
Phase-2 split: bodies unchanged; only the import wiring is new.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from eval_audit.utils.sankey import emit_sankey_artifacts
from eval_audit.reports.summary.common import CANONICAL_AGREEMENT_TOL
from eval_audit.reports.summary.sankeys import (
    _build_universe_to_scope_root,
    _build_scope_to_analyzed_root,
)

def _render_scope_sankeys(
    *,
    include_visuals: bool,
    sankey_rows: dict[str, list[dict[str, Any]]],
    level_001: Path,
    level_001_machine: Path,
    level_001_interactive: Path,
    level_001_static: Path,
    alt_tol_dpath: Path,
    alt_tol_machine: Path,
    alt_tol_interactive: Path,
    alt_tol_static: Path,
    generated_utc: str,
    scope_title: str,
) -> dict[str, dict[str, Any]]:
    operational_sankey_rows = sankey_rows["operational_sankey_rows"]
    repro_sankey_rows = sankey_rows["repro_sankey_rows"]
    repro_tol001_rows = sankey_rows["repro_tol001_rows"]
    repro_tol010_rows = sankey_rows["repro_tol010_rows"]
    repro_tol050_rows = sankey_rows["repro_tol050_rows"]
    metric_sankey_rows = sankey_rows["metric_sankey_rows"]
    universe_to_scope_rows = sankey_rows["universe_to_scope_rows"]
    scope_to_analyzed_exact_rows = sankey_rows["scope_to_analyzed_exact_rows"]
    scope_to_analyzed_tol001_rows = sankey_rows["scope_to_analyzed_tol001_rows"]
    scope_to_analyzed_tol010_rows = sankey_rows["scope_to_analyzed_tol010_rows"]
    scope_to_analyzed_tol050_rows = sankey_rows["scope_to_analyzed_tol050_rows"]
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
    return {
        "operational_art": operational_art,
        "repro_art": repro_art,
        "repro_tol001_art": repro_tol001_art,
        "repro_tol010_art": repro_tol010_art,
        "repro_tol050_art": repro_tol050_art,
        "repro_metric_art": repro_metric_art,
        "filter_to_attempt_art": filter_to_attempt_art,
        "attempted_to_repro_art": attempted_to_repro_art,
        "attempted_to_repro_tol001_art": attempted_to_repro_tol001_art,
        "attempted_to_repro_tol010_art": attempted_to_repro_tol010_art,
        "attempted_to_repro_tol050_art": attempted_to_repro_tol050_art,
        "end_to_end_art": end_to_end_art,
        "end_to_end_tol001_art": end_to_end_tol001_art,
        "end_to_end_tol010_art": end_to_end_tol010_art,
        "end_to_end_tol050_art": end_to_end_tol050_art,
    }

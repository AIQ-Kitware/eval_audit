"""Prioritized breakdown: triage scoring, example picking, publishing the examples tree.

Split out of ``eval_audit.workflows.build_reports_summary`` on
2026-06-11 (Phase 2 of docs/planning/repo-refactor-plan.md). Pure
relocation: function bodies are unchanged.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from eval_audit.infra.fs_publish import symlink_to
from eval_audit.reports.core_packet_summary import (
    load_core_report_bundle,
    prioritized_example_artifact_names,
    render_path_link,
)
from loguru import logger
from eval_audit.infra.profiling import profile

from eval_audit.reports.summary.common import (
    _clean_optional_text,
    _coerce_float,
    _coerce_listlike,
    _is_truthy_text,
    _preview_values,
    _safe_float,
    _safe_ratio,
    _write_json,
    _write_text,
    slugify,
)


_TRIAGE_DIMENSION_PRIORITY = {
    "benchmark": 0,
    "model": 1,
    "machine_host": 2,
    "experiment_name": 3,
    "suite": 4,
}

_QUANTILE_BUCKET_TARGETS: dict[str, float] = {
    "best": 1.0,
    "mid": 0.5,
    "worst": 0.0,
}

# Section taxonomy.
#
# - score_ge_95 / score_lt_80 : *absolute* threshold sections keyed off the
#                   agreement bucket label. They preserve the publication-
#                   quality narrative ("did we hit / fall below the bar?").
#                   The numeric thresholds are baked into the section name
#                   so a reader doesn't have to look up what "good" or "bad"
#                   meant in the schema.
# - best / mid / worst : *population-quantile* sections that pick rows at
#                   the top / median / bottom of whatever analyzed rows are
#                   in scope. Always populated when there is at least one
#                   analyzed row, so tightly-clustered virtual experiments
#                   still surface their actual range.
# - flagged       : signal-based section (multiplicity, machine spread,
#                   ambiguous matching, off-story, report warnings).
_TRIAGE_BUCKET_CLASS_ORDER = {
    "score_ge_95": 0,
    "best": 1,
    "mid": 2,
    "worst": 3,
    "score_lt_80": 4,
    "flagged": 5,
}

_TRIAGE_ABSOLUTE_BUCKETS = {
    "score_ge_95": ("exact_or_near_exact", "high_agreement_0.95+"),
    "score_lt_80": ("low_agreement_0.00+", "zero_agreement"),
}

# Backwards-compatible export for any external readers that previously
# inspected ``_TRIAGE_BUCKET_LABELS[<class>]``. The moderate-agreement key is
# intentionally gone — moderate rows now flow through the quantile-based
# ``mid`` section, which is not threshold-based.
_TRIAGE_BUCKET_LABELS = dict(_TRIAGE_ABSOLUTE_BUCKETS)


def _agreement_bucket_class(bucket: str | None) -> str | None:
    """Map an agreement bucket label to its absolute section, or None.

    Only ``score_ge_95`` and ``score_lt_80`` are absolute classifications.
    Moderate / zero-but-nonzero agreement labels return ``None``; quantile
    sections do their own population-relative selection downstream.
    """
    text = _clean_optional_text(bucket)
    if text is None:
        return None
    if text in _TRIAGE_ABSOLUTE_BUCKETS["score_ge_95"]:
        return "score_ge_95"
    if text in _TRIAGE_ABSOLUTE_BUCKETS["score_lt_80"]:
        return "score_lt_80"
    return None


def _triage_bucket_score(
    *,
    bucket_class: str,
    dimension: str,
    n_analyzed: int,
    target_count: int,
    target_share: float,
    mean_score: float | None,
) -> float:
    dim_priority = _TRIAGE_DIMENSION_PRIORITY.get(dimension, 99)
    dim_bonus = max(0, 500 - (dim_priority * 100))
    coverage_bonus = min(n_analyzed, 12) * 3.0
    target_bonus = min(target_count, 8) * 8.0 + (target_share * 80.0)
    score_bonus = 0.0
    if mean_score is not None:
        if bucket_class == "score_ge_95":
            score_bonus = mean_score * 12.0
        elif bucket_class == "score_lt_80":
            score_bonus = (1.0 - mean_score) * 18.0
    return dim_bonus + coverage_bonus + target_bonus + score_bonus


def _flagged_bucket_score(
    *,
    dimension: str,
    n_analyzed: int,
    has_multiplicity_signal: bool,
    has_machine_spread: bool,
    has_ambiguous_analyzed_matching: bool,
    has_off_story_signal: bool,
    bad_count: int,
) -> float:
    dim_priority = _TRIAGE_DIMENSION_PRIORITY.get(dimension, 99)
    dim_bonus = max(0, 500 - (dim_priority * 100))
    flag_bonus = (
        (18.0 if has_ambiguous_analyzed_matching else 0.0)
        + (14.0 if has_machine_spread else 0.0)
        + (12.0 if has_multiplicity_signal else 0.0)
        + (10.0 if has_off_story_signal else 0.0)
    )
    return dim_bonus + flag_bonus + min(n_analyzed, 10) * 2.0 + min(bad_count, 5) * 4.0


def _example_case_sort_key(row: dict[str, Any], bucket_class: str) -> tuple[float, str]:
    score = _safe_float(row.get("official_instance_agree_tol0p05"))
    if score is None:
        score = -1.0
    if bucket_class == "score_ge_95":
        primary = score
    elif bucket_class == "score_lt_80":
        primary = -score
    else:
        # Default for ad-hoc bucket_class strings (e.g. flagged-row fallback);
        # rank by closeness to the moderate band so the example list is stable.
        primary = -abs(score - 0.85)
    return (primary, str(row.get("run_entry") or ""))


def _pick_example_cases(
    *,
    rows: list[dict[str, Any]],
    bucket_class: str,
    max_examples: int = 3,
) -> list[dict[str, Any]]:
    target_rows = [row for row in rows if _agreement_bucket_class(row.get("official_instance_agree_bucket")) == bucket_class]
    candidates = target_rows or rows
    sorted_rows = sorted(candidates, key=lambda row: _example_case_sort_key(row, bucket_class), reverse=True)
    picked: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in sorted_rows:
        key = (str(row.get("experiment_name") or ""), str(row.get("run_entry") or ""))
        if key in seen:
            continue
        seen.add(key)
        picked.append(row)
        if len(picked) >= max_examples:
            break
    return picked


def _triage_selection_reason(
    *,
    bucket_class: str,
    dimension: str,
    target_count: int,
    target_share: float,
    n_analyzed: int,
    flags: list[str],
) -> str:
    bucket_label = {
        "score_ge_95": "high-agreement (>=0.95)",
        "score_lt_80": "low-agreement (<0.80)",
        "best": "best-of-population",
        "mid": "median-of-population",
        "worst": "worst-of-population",
        "flagged": "flagged",
    }.get(bucket_class, bucket_class)
    reason = (
        f"{dimension} group is a useful {bucket_label} exemplar: "
        f"{target_count}/{n_analyzed} analyzed rows in the target bucket class"
        f" ({target_share:.0%})"
    )
    if flags:
        reason += "; flags=" + ", ".join(flags)
    return reason


def _selected_attempt_refs_for_repro_row(repro: dict[str, Any]) -> list[dict[str, Any]]:
    refs = []
    for item in _coerce_listlike(repro.get("analysis_selected_attempt_refs")):
        if isinstance(item, dict):
            refs.append(item)
    return refs


def _attempt_ref_matches_row(ref: dict[str, Any], row: dict[str, Any]) -> bool:
    comparisons = [
        ("run_dir", "run_dir"),
        ("attempt_identity", "attempt_identity"),
        ("attempt_uuid", "attempt_uuid"),
        ("attempt_fallback_key", "attempt_fallback_key"),
    ]
    for ref_key, row_key in comparisons:
        ref_value = _clean_optional_text(ref.get(ref_key))
        row_value = _clean_optional_text(row.get(row_key))
        if ref_value and row_value and ref_value == row_value:
            return True
    return False


def _choose_parent_row_for_repro(
    repro: dict[str, Any],
    parent_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    if not parent_rows:
        return {}
    selected_refs = _selected_attempt_refs_for_repro_row(repro)
    for ref in selected_refs:
        for row in parent_rows:
            if _attempt_ref_matches_row(ref, row):
                return row
    return sorted(
        parent_rows,
        key=lambda row: (
            _coerce_float(row.get("manifest_timestamp")),
            str(row.get("job_id") or ""),
        ),
        reverse=True,
    )[0]


def _analyzed_dimension_values(case_row: dict[str, Any], dimension: str) -> list[str]:
    if dimension == "machine_host":
        hosts = sorted({
            str(host)
            for host in [
                _clean_optional_text(ref.get("machine_host"))
                for ref in (_selected_attempt_refs_for_repro_row(case_row))
            ]
            if host
        })
        if hosts:
            return hosts
    value = _clean_optional_text(case_row.get(dimension))
    return [value or "unknown"]


def _build_prioritized_breakdown_summary(
    *,
    enriched_rows: list[dict[str, Any]],
    repro_rows: list[dict[str, Any]],
    run_multiplicity_summary: dict[str, Any],
    breakdown_dims: list[str],
    level_002: Path,
) -> dict[str, Any]:
    enriched_lookup: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in enriched_rows:
        experiment_name = _clean_optional_text(row.get("experiment_name"))
        run_entry = _clean_optional_text(row.get("run_entry"))
        if experiment_name and run_entry:
            enriched_lookup[(experiment_name, run_entry)].append(row)
    multiplicity_lookup = {
        str(row.get("logical_run_key") or row.get("run_entry") or ""): row
        for row in (run_multiplicity_summary.get("rows") or [])
        if row.get("logical_run_key") or row.get("run_entry")
    }
    analyzed_case_rows: list[dict[str, Any]] = []
    for repro in repro_rows:
        key = (str(repro.get("experiment_name") or ""), str(repro.get("run_entry") or ""))
        parent_rows = enriched_lookup.get(key, [])
        parent = _choose_parent_row_for_repro(repro, parent_rows)
        logical_run_key = str(parent.get("logical_run_key") or repro.get("run_entry") or "")
        multiplicity = multiplicity_lookup.get(logical_run_key, {})
        selected_attempt_refs = _selected_attempt_refs_for_repro_row(repro)
        selected_machine_hosts = sorted({
            str(host)
            for host in [
                _clean_optional_text(ref.get("machine_host"))
                for ref in selected_attempt_refs
            ]
            if host
        })
        analyzed_case_rows.append(
            {
                **parent,
                **repro,
                "logical_run_key": logical_run_key,
                "selected_attempt_refs": selected_attempt_refs,
                "selected_machine_hosts": selected_machine_hosts,
                "official_instance_agree_bucket": repro.get("official_instance_agree_bucket") or parent.get("official_instance_agree_bucket"),
                "bucket_class": _agreement_bucket_class(repro.get("official_instance_agree_bucket")),
                "has_multiplicity_signal": bool(
                    int(multiplicity.get("n_attempt_ids") or 0) > 1 or int(multiplicity.get("n_rows") or 0) > 1
                ),
                "has_machine_spread": bool(int(multiplicity.get("n_machines") or 0) > 1),
                "has_ambiguous_analyzed_matching": bool(int(multiplicity.get("n_ambiguous_analyzed_candidates") or 0) > 0),
                "has_off_story_signal": str(parent.get("storyline_status") or "") == "off_story",
            }
        )

    attempted_by_dim: dict[str, dict[str, list[dict[str, Any]]]] = {}
    completed_by_dim: dict[str, dict[str, list[dict[str, Any]]]] = {}
    analyzed_by_dim: dict[str, dict[str, list[dict[str, Any]]]] = {}
    dims = [dim for dim in _TRIAGE_DIMENSION_PRIORITY if dim in breakdown_dims]
    for dim in dims:
        attempted_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        completed_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        analyzed_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in enriched_rows:
            value = str(row.get(dim) or "unknown")
            attempted_groups[value].append(row)
            if _is_truthy_text(row.get("has_run_spec")):
                completed_groups[value].append(row)
        for row in analyzed_case_rows:
            for value in _analyzed_dimension_values(row, dim):
                analyzed_groups[str(value or "unknown")].append(row)
        attempted_by_dim[dim] = attempted_groups
        completed_by_dim[dim] = completed_groups
        analyzed_by_dim[dim] = analyzed_groups

    all_group_rows: list[dict[str, Any]] = []
    for dim in dims:
        for value, analyzed_group_rows in analyzed_by_dim[dim].items():
            if not analyzed_group_rows:
                continue
            bucket_counts = Counter(str(row.get("official_instance_agree_bucket") or "unknown") for row in analyzed_group_rows)
            bucket_class_counts = Counter(
                _agreement_bucket_class(row.get("official_instance_agree_bucket")) or "other"
                for row in analyzed_group_rows
            )
            scores = [
                score for score in (_safe_float(row.get("official_instance_agree_tol0p05")) for row in analyzed_group_rows)
                if score is not None
            ]
            mean_score = (sum(scores) / len(scores)) if scores else None
            flags = {
                "multiplicity_signal": any(bool(row.get("has_multiplicity_signal")) for row in analyzed_group_rows),
                "machine_spread": any(bool(row.get("has_machine_spread")) for row in analyzed_group_rows),
                "ambiguous_analyzed_matching": any(bool(row.get("has_ambiguous_analyzed_matching")) for row in analyzed_group_rows),
                "off_story_signal": any(bool(row.get("has_off_story_signal")) for row in analyzed_group_rows),
                "report_warnings": any(bool(row.get("has_report_warnings")) for row in analyzed_group_rows),
            }
            dominant_bucket = max(bucket_counts.items(), key=lambda item: (item[1], item[0]))[0]
            dominant_bucket_class = _agreement_bucket_class(dominant_bucket) or "other"
            breakdown_dir = level_002 / "breakdowns" / f"by_{dim}" / slugify(value)
            breakdown_index_dir = level_002 / "breakdowns" / f"by_{dim}"
            all_group_rows.append(
                {
                    "dimension": dim,
                    "dimension_value": value,
                    "dimension_priority": _TRIAGE_DIMENSION_PRIORITY.get(dim, 99),
                    "rank_population": "breakdown groups ranked from analyzed reproducibility rows; attempted/completed counts come from all indexed rows in the same group",
                    "n_attempted": len(attempted_by_dim[dim].get(value, [])),
                    "n_completed": len(completed_by_dim[dim].get(value, [])),
                    "n_analyzed": len(analyzed_group_rows),
                    "machine_host_membership_source": (
                        "selected_attempt_refs.machine_host"
                        if dim == "machine_host" and any(row.get("selected_machine_hosts") for row in analyzed_group_rows)
                        else "coarse_parent_row"
                    ),
                    "bucket_counts": dict(bucket_counts),
                    "bucket_class_counts": dict(bucket_class_counts),
                    "dominant_bucket": dominant_bucket,
                    "dominant_bucket_class": dominant_bucket_class,
                    "mean_official_instance_agree_tol0p05": mean_score,
                    "has_multiplicity_signal": flags["multiplicity_signal"],
                    "has_machine_spread": flags["machine_spread"],
                    "has_ambiguous_analyzed_matching": flags["ambiguous_analyzed_matching"],
                    "has_off_story_signal": flags["off_story_signal"],
                    "has_report_warnings": flags["report_warnings"],
                    "breakdown_dir": str(breakdown_dir),
                    "breakdown_index_dir": str(breakdown_index_dir),
                    "rows": analyzed_group_rows,
                }
            )

    def _select_bucket_rows(bucket_class: str, limit: int = 3) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for row in all_group_rows:
            target_count = int(row["bucket_class_counts"].get(bucket_class, 0))
            if target_count <= 0:
                continue
            n_analyzed = int(row["n_analyzed"])
            target_share = float(target_count / n_analyzed) if n_analyzed else 0.0
            example_rows = _pick_example_cases(rows=row["rows"], bucket_class=bucket_class)
            flags = [
                name for name, enabled in [
                    ("multiplicity", row["has_multiplicity_signal"]),
                    ("multi_machine", row["has_machine_spread"]),
                    ("ambiguous_analysis", row["has_ambiguous_analyzed_matching"]),
                    ("off_story", row["has_off_story_signal"]),
                    ("report_warnings", row["has_report_warnings"]),
                ]
                if enabled
            ]
            out = dict(row)
            out.update(
                {
                    "bucket_class": bucket_class,
                    "primary_bucket_class": bucket_class,
                    "target_bucket_count": target_count,
                    "target_bucket_share": target_share,
                    "selection_score": _triage_bucket_score(
                        bucket_class=bucket_class,
                        dimension=str(row["dimension"]),
                        n_analyzed=n_analyzed,
                        target_count=target_count,
                        target_share=target_share,
                        mean_score=_safe_float(row.get("mean_official_instance_agree_tol0p05")),
                    ),
                    "example_rows": example_rows,
                    "selection_reason": _triage_selection_reason(
                        bucket_class=bucket_class,
                        dimension=str(row["dimension"]),
                        target_count=target_count,
                        target_share=target_share,
                        n_analyzed=n_analyzed,
                        flags=flags,
                    ),
                    "interesting_flags": flags,
                }
            )
            candidates.append(out)
        candidates.sort(
            key=lambda row: (
                -float(row["selection_score"]),
                int(row["dimension_priority"]),
                -int(row["n_analyzed"]),
                str(row["dimension_value"]),
            )
        )
        selected: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for row in candidates:
            key = (str(row["dimension"]), str(row["dimension_value"]))
            if key in seen:
                continue
            seen.add(key)
            selected.append(row)
            if len(selected) >= limit:
                break
        return selected

    def _select_flagged_rows(limit: int = 5) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for row in all_group_rows:
            flags = [
                name for name, enabled in [
                    ("multiplicity", row["has_multiplicity_signal"]),
                    ("multi_machine", row["has_machine_spread"]),
                    ("ambiguous_analysis", row["has_ambiguous_analyzed_matching"]),
                    ("off_story", row["has_off_story_signal"]),
                    ("report_warnings", row["has_report_warnings"]),
                ]
                if enabled
            ]
            if not flags:
                continue
            bad_count = int(row["bucket_class_counts"].get("score_lt_80", 0))
            out = dict(row)
            out.update(
                {
                    "bucket_class": "flagged",
                    "primary_bucket_class": str(row.get("dominant_bucket_class") or "other"),
                    "target_bucket_count": bad_count,
                    "target_bucket_share": _safe_ratio(bad_count, int(row["n_analyzed"])) or 0.0,
                    "selection_score": _flagged_bucket_score(
                        dimension=str(row["dimension"]),
                        n_analyzed=int(row["n_analyzed"]),
                        has_multiplicity_signal=bool(row["has_multiplicity_signal"]),
                        has_machine_spread=bool(row["has_machine_spread"]),
                        has_ambiguous_analyzed_matching=bool(row["has_ambiguous_analyzed_matching"]),
                        has_off_story_signal=bool(row["has_off_story_signal"]),
                        bad_count=bad_count,
                    ),
                    "example_rows": _pick_example_cases(
                        rows=row["rows"],
                        bucket_class="score_lt_80" if bad_count else str(row.get("dominant_bucket_class") or "flagged"),
                    ),
                    "selection_reason": "interesting investigative flags in an analyzed breakdown group: " + ", ".join(flags),
                    "interesting_flags": flags,
                }
            )
            candidates.append(out)
        candidates.sort(
            key=lambda row: (
                -float(row["selection_score"]),
                int(row["dimension_priority"]),
                -int(row["n_analyzed"]),
                str(row["dimension_value"]),
            )
        )
        selected: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for row in candidates:
            key = (str(row["dimension"]), str(row["dimension_value"]))
            if key in seen:
                continue
            seen.add(key)
            selected.append(row)
            if len(selected) >= limit:
                break
        return selected

    def _select_quantile_section(
        section_name: str,
        *,
        max_examples: int = 3,
    ) -> list[dict[str, Any]]:
        """Build a single synthetic-breakdown entry for a population-quantile section.

        Picks examples at the section's target quantile of the analyzed-row
        population (best=1.0, mid=0.5, worst=0.0), regardless of how those
        rows fall into absolute good/bad buckets. Returns a list to match the
        shape of the absolute-bucket selectors (length 1 or 0).
        """
        target_quantile = _QUANTILE_BUCKET_TARGETS[section_name]
        scored: list[tuple[dict[str, Any], float]] = []
        for case in analyzed_case_rows:
            score = _safe_float(case.get("official_instance_agree_tol0p05"))
            if score is None:
                continue
            scored.append((case, score))
        if not scored:
            return []
        scored.sort(key=lambda pair: pair[1])  # ascending: index 0 is worst
        n = len(scored)
        target_idx = round(target_quantile * (n - 1))
        # Sort candidates by distance from target_idx, breaking ties by score
        # in the direction that matches the section semantics so the leading
        # example reads naturally (highest score for "best", lowest for
        # "worst", closest-to-median for "mid").
        if section_name == "best":
            ranked = sorted(range(n), key=lambda i: (-scored[i][1], abs(i - target_idx)))
        elif section_name == "worst":
            ranked = sorted(range(n), key=lambda i: (scored[i][1], abs(i - target_idx)))
        else:  # mid
            ranked = sorted(range(n), key=lambda i: (abs(i - target_idx), scored[i][1]))
        seen_keys: set[tuple[str, str]] = set()
        example_rows: list[dict[str, Any]] = []
        for idx in ranked:
            row = scored[idx][0]
            key = (str(row.get("experiment_name") or ""), str(row.get("run_entry") or ""))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            example_rows.append(row)
            if len(example_rows) >= max_examples:
                break
        scores = [scored[i][1] for i in range(n)]
        section_label = {
            "best": "highest agreement (top of population)",
            "mid": "median agreement (population p50)",
            "worst": "lowest agreement (bottom of population)",
        }[section_name]
        synthetic_row = {
            "dimension": "agreement_quantile",
            "dimension_priority": _TRIAGE_BUCKET_CLASS_ORDER[section_name],
            "dimension_value": section_name,
            "rank_population": (
                "rows ranked across the analyzed-row population by official_instance_agree_tol0p05; "
                "examples picked at the section's target quantile (best=1.0, mid=0.5, worst=0.0)"
            ),
            "n_attempted": n,
            "n_completed": n,
            "n_analyzed": n,
            "target_bucket_count": len(example_rows),
            "target_bucket_share": _safe_ratio(len(example_rows), n) or 0.0,
            "dominant_bucket": None,
            "dominant_bucket_class": None,
            "bucket_counts": {},
            "bucket_class_counts": {},
            "machine_host_membership_source": None,
            "mean_official_instance_agree_tol0p05": (sum(scores) / n) if scores else None,
            "has_multiplicity_signal": False,
            "has_machine_spread": False,
            "has_ambiguous_analyzed_matching": False,
            "has_off_story_signal": False,
            "has_report_warnings": False,
            "breakdown_dir": None,
            "breakdown_index_dir": None,
            "rows": [pair[0] for pair in scored],
        }
        synthetic_row.update(
            {
                "bucket_class": section_name,
                "primary_bucket_class": section_name,
                "selection_score": 0.0,
                "example_rows": example_rows,
                "selection_reason": (
                    f"{section_label}: showing {len(example_rows)} example(s) "
                    f"out of {n} analyzed row(s); "
                    f"min={min(scores):.4f} median={scores[n // 2]:.4f} max={max(scores):.4f}"
                ),
                "interesting_flags": [],
            }
        )
        return [synthetic_row]

    selected_by_section = {
        "score_ge_95": _select_bucket_rows("score_ge_95", limit=4),
        "best": _select_quantile_section("best"),
        "mid": _select_quantile_section("mid"),
        "worst": _select_quantile_section("worst"),
        "score_lt_80": _select_bucket_rows("score_lt_80", limit=4),
        "flagged": _select_flagged_rows(limit=6),
    }

    flattened_rows: list[dict[str, Any]] = []
    for section_name in ["score_ge_95", "best", "mid", "worst", "score_lt_80", "flagged"]:
        for idx, row in enumerate(selected_by_section[section_name], start=1):
            example_rows = row.get("example_rows") or []
            flattened_rows.append(
                {
                    "priority_rank": idx,
                    "bucket_class": section_name,
                    "dimension": row["dimension"],
                    "dimension_priority": row["dimension_priority"],
                    "dimension_value": row["dimension_value"],
                    "rank_population": row["rank_population"],
                    "n_attempted": row["n_attempted"],
                    "n_completed": row["n_completed"],
                    "n_analyzed": row["n_analyzed"],
                    "target_bucket_count": row["target_bucket_count"],
                    "target_bucket_share": row["target_bucket_share"],
                    "dominant_bucket": row["dominant_bucket"],
                    "dominant_bucket_class": row["dominant_bucket_class"],
                    "bucket_counts": row["bucket_counts"],
                    "bucket_class_counts": row["bucket_class_counts"],
                    "machine_host_membership_source": row.get("machine_host_membership_source"),
                    "mean_official_instance_agree_tol0p05": row["mean_official_instance_agree_tol0p05"],
                    "has_multiplicity_signal": row["has_multiplicity_signal"],
                    "has_machine_spread": row["has_machine_spread"],
                    "has_ambiguous_analyzed_matching": row["has_ambiguous_analyzed_matching"],
                    "has_off_story_signal": row["has_off_story_signal"],
                    "interesting_flags": row["interesting_flags"],
                    "breakdown_dir": row["breakdown_dir"],
                    "breakdown_index_dir": row["breakdown_index_dir"],
                    "example_report_dirs": _preview_values([
                        str(item.get("report_dir")) for item in example_rows if item.get("report_dir")
                    ], max_items=3),
                    "example_run_entries": _preview_values([
                        str(item.get("run_entry")) for item in example_rows if item.get("run_entry")
                    ], max_items=3),
                    "example_models": _preview_values([
                        str(item.get("model")) for item in example_rows if item.get("model")
                    ], max_items=3),
                    "selection_reason": row["selection_reason"],
                    "selection_score": row["selection_score"],
                }
            )

    include_values_by_dim: dict[str, set[str]] = defaultdict(set)
    for row in flattened_rows:
        include_values_by_dim[str(row["dimension"])].add(str(row["dimension_value"]))

    def _serialize_example_row(row: dict[str, Any]) -> dict[str, Any]:
        keep = [
            "experiment_name",
            "run_entry",
            "packet_id",
            "report_dir",
            "report_json",
            "warnings_manifest",
            "has_report_warnings",
            "official_instance_agree_bucket",
            "official_instance_agree_tol0p05",
            "analysis_single_run",
        ]
        return {key: row.get(key) for key in keep if key in row}

    return {
        "definitions": {
            "rank_population": "breakdown groups ranked from analyzed reproducibility rows; attempted/completed counts are added from all indexed rows in the same group; machine_host membership uses selected attempt provenance when available",
            "section_classes": {
                "score_ge_95": {
                    "kind": "absolute",
                    "agreement_buckets": list(_TRIAGE_ABSOLUTE_BUCKETS["score_ge_95"]),
                    "purpose": "publication-quality threshold (>=0.95 instance-level agreement)",
                },
                "best": {
                    "kind": "quantile",
                    "target_quantile": _QUANTILE_BUCKET_TARGETS["best"],
                    "purpose": "top of the analyzed-row population by official_instance_agree_tol0p05, regardless of absolute bucket",
                },
                "mid": {
                    "kind": "quantile",
                    "target_quantile": _QUANTILE_BUCKET_TARGETS["mid"],
                    "purpose": "median of the analyzed-row population by official_instance_agree_tol0p05, regardless of absolute bucket",
                },
                "worst": {
                    "kind": "quantile",
                    "target_quantile": _QUANTILE_BUCKET_TARGETS["worst"],
                    "purpose": "bottom of the analyzed-row population by official_instance_agree_tol0p05, regardless of absolute bucket",
                },
                "score_lt_80": {
                    "kind": "absolute",
                    "agreement_buckets": list(_TRIAGE_ABSOLUTE_BUCKETS["score_lt_80"]),
                    "purpose": "publication-quality floor (<0.80 instance-level agreement)",
                },
                "flagged": {
                    "kind": "signal",
                    "purpose": "interesting investigative flags regardless of primary bucket",
                },
            },
            "dimension_priority": _TRIAGE_DIMENSION_PRIORITY,
        },
        "selected_by_section": {
            key: [
                {
                    **{
                        k: v for k, v in row.items()
                        if k != "rows" and k != "example_rows"
                    },
                    "example_rows": [
                        _serialize_example_row(example_row)
                        for example_row in (row.get("example_rows") or [])
                    ],
                }
                for row in value
            ]
            for key, value in selected_by_section.items()
        },
        "rows": flattened_rows,
        "include_values_by_dim": {dim: sorted(values) for dim, values in include_values_by_dim.items()},
    }


def _format_prioritized_breakdown_summary_text(
    *,
    scope_title: str,
    generated_utc: str,
    summary: dict[str, Any],
) -> list[str]:
    lines = [
        "Prioritized Breakdown Investigation Checklist",
        "=============================================",
        f"Generated: {generated_utc}",
        f"Scope: {scope_title}",
        "",
        "Population:",
        f"  {summary['definitions']['rank_population']}",
        "",
        "Dimension priority:",
    ]
    for dim, rank in _TRIAGE_DIMENSION_PRIORITY.items():
        lines.append(f"  {rank + 1}. {dim}")

    section_titles = [
        ("score_ge_95", "score_ge_95 — high-agreement breakdowns (absolute threshold, instance agreement >= 0.95)"),
        ("best", "best — best-of-population examples (quantile=1.0, regardless of absolute bucket)"),
        ("mid", "mid — median-of-population examples (quantile=0.5, regardless of absolute bucket)"),
        ("worst", "worst — worst-of-population examples (quantile=0.0, regardless of absolute bucket)"),
        ("score_lt_80", "score_lt_80 — low-agreement breakdowns (absolute threshold, instance agreement < 0.80)"),
        ("flagged", "flagged — special cases worth inspecting (signal-based, regardless of bucket)"),
    ]
    for section_key, section_title in section_titles:
        rows = [row for row in (summary.get("rows") or []) if row.get("bucket_class") == section_key]
        lines.extend(["", section_title, "-" * len(section_title)])
        if not rows:
            lines.append("  (none)")
            continue
        for row in rows:
            lines.append(
                f"[{row['priority_rank']}] {row['dimension']} = {row['dimension_value']} "
                f"({row['bucket_class']}; analyzed={row['n_analyzed']}, target={row['target_bucket_count']}, share={float(row['target_bucket_share'] or 0.0):.0%})"
            )
            lines.append(f"  reason: {row['selection_reason']}")
            lines.append(
                f"  counts: attempted={row['n_attempted']} completed={row['n_completed']} analyzed={row['n_analyzed']} "
                f"dominant_bucket={row['dominant_bucket']}"
            )
            if row["dimension"] == "machine_host" and row.get("machine_host_membership_source"):
                lines.append(f"  machine_host_membership_source: {row['machine_host_membership_source']}")
            flags = row.get("interesting_flags") or []
            if flags:
                lines.append(f"  flags: {', '.join(flags)}")
            lines.append(f"  breakdown_dir: {render_path_link(row['breakdown_dir'])}")
            lines.append(f"  breakdown_index_dir: {render_path_link(row['breakdown_index_dir'])}")
            example_runs = row.get("example_run_entries") or []
            example_reports = row.get("example_report_dirs") or []
            if example_runs:
                lines.append("  example_run_entries:")
                for item in example_runs:
                    lines.append(f"    - {item}")
            if example_reports:
                lines.append("  example_report_dirs:")
                for item in example_reports:
                    lines.append(f"    - {render_path_link(item)}")
    return lines


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

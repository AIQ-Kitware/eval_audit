"""Small shared helpers and constants for the aggregate summary build.

Split out of ``eval_audit.workflows.build_reports_summary`` on
2026-06-11 (Phase 2 of docs/historical/planning/repo-refactor-plan.md). Pure
relocation: function bodies are unchanged.
"""
from __future__ import annotations

import json
import os
import resource
from pathlib import Path
from typing import Any
import kwutil
from eval_audit.reports.core_packet_summary import find_report_pair
from eval_audit.infra.fs_publish import write_text_atomic


DEFAULT_BREAKDOWN_DIMS = [
    "experiment_name",
    "model",
    "benchmark",
    "suite",
    "machine_host",
]

CANONICAL_AGREEMENT_TOL = 0.05


# P0-2 / R-6: single source of truth for local-index resolution + loading.
# Re-exported here so existing `from ...summary.common import
# latest_index_csv` call sites (build_reports_summary) keep working.
from eval_audit.infra.index_io import latest_index_csv, load_rows  # noqa: F401

# R-6: the small scalar/text helpers below now live in one shared module.
# Re-exported under their historical private names so this subtree's callers
# (build_reports_summary, breakdown, classification, loading, multiplicity,
# publish, failure_triage) keep importing them from here unchanged.
from eval_audit.utils.coercion import (  # noqa: F401
    load_json as _load_json,
    normalize_text as _normalize_text,
    is_truthy_text as _is_truthy_text,
    coerce_float as _coerce_float,
    clean_optional_text as _clean_optional_text,
    find_curve_value as _find_curve_value,
)

# `_find_pair` was always a thin alias for the canonical pair-lookup.
_find_pair = find_report_pair


def slugify(text: str) -> str:
    return (
        text.replace("/", "-")
        .replace(":", "-")
        .replace(",", "-")
        .replace("=", "-")
        .replace("@", "-")
        .replace(" ", "-")
    )


def _write_json(payload: Any, fpath: Path) -> None:
    write_text_atomic(fpath, json.dumps(kwutil.Json.ensure_serializable(payload), indent=2))


def _write_text(lines: list[str], fpath: Path) -> None:
    write_text_atomic(fpath, "\n".join(lines).rstrip() + "\n")


def _preview_values(values: list[str], *, max_items: int = 6) -> list[str]:
    unique = sorted({value for value in values if _clean_optional_text(value)})
    if len(unique) <= max_items:
        return unique
    return unique[:max_items] + [f"... (+{len(unique) - max_items} more)"]


def _raise_fd_limit(target: int = 8192) -> None:
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        desired = min(max(soft, target), hard)
        if desired > soft:
            resource.setrlimit(resource.RLIMIT_NOFILE, (desired, hard))
    except Exception:
        pass


def _fd_count() -> int | None:
    try:
        return len(os.listdir("/proc/self/fd"))
    except Exception:
        return None


def _safe_ratio(numer: int, denom: int) -> float | None:
    return (numer / denom) if denom else None


def _safe_float(value: Any) -> float | None:
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except Exception:
        return None


def _coerce_listlike(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except Exception:
            return [value]
        if isinstance(parsed, list):
            return parsed
        return [parsed]
    return [value]

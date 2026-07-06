"""Small shared helpers and constants for the aggregate summary build.

Split out of ``eval_audit.workflows.build_reports_summary`` on
2026-06-11 (Phase 2 of docs/planning/repo-refactor-plan.md). Pure
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


def slugify(text: str) -> str:
    return (
        text.replace("/", "-")
        .replace(":", "-")
        .replace(",", "-")
        .replace("=", "-")
        .replace("@", "-")
        .replace(" ", "-")
    )


def _load_json(fpath: Path) -> dict[str, Any]:
    return json.loads(fpath.read_text())


def _write_json(payload: Any, fpath: Path) -> None:
    write_text_atomic(fpath, json.dumps(kwutil.Json.ensure_serializable(payload), indent=2))


def _write_text(lines: list[str], fpath: Path) -> None:
    write_text_atomic(fpath, "\n".join(lines).rstrip() + "\n")


def _find_pair(report: dict[str, Any], label: str) -> dict[str, Any]:
    return find_report_pair(report, label)


def _find_curve_value(rows: list[dict[str, Any]], abs_tol: float) -> float | None:
    for row in rows or []:
        try:
            if float(row.get("abs_tol")) == float(abs_tol):
                return float(row.get("agree_ratio"))
        except Exception:
            pass
    return None


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _is_truthy_text(value: Any) -> bool:
    return _normalize_text(value) in {"true", "1", "yes"}


def _coerce_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return float("-inf")


def _clean_optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.lower() in {"none", "nan"}:
        return None
    return text


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

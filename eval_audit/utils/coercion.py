"""eval_audit.utils.coercion

Small, framework-free value helpers shared across pipeline stages.

Single source of truth for a cluster of scalar/text coercion helpers that
had drifted into near-identical private copies across ``workflows/``,
``planning/``, ``reports/``, and ``normalized/`` (R-6 consolidation of the
2026-07-06 simplicity audit). Keeping them here — with no dependency on any
higher layer — lets non-``reports`` modules use them without importing from
``reports.summary`` (a layering violation the old ``_find_curve_value``
import from ``reports.aggregate`` into ``workflows.analyze_experiment``
introduced).

Two members are not strictly "coercion" but live here because they are the
same tiny pure helper duplicated across those layers: :func:`load_json`
(read+parse a JSON file) and :func:`find_curve_value` (look up an agreement
ratio by ``abs_tol`` in a list of curve rows).

Deliberately *excluded* — behaviourally distinct near-namesakes that were
verified different and left in place:
  * ``index_results._clean_optional_text`` does not drop ``"none"``/``"nan"``.
  * ``portfolio_status._coerce_float`` takes a caller-supplied ``default``.
  * ``core_metric_curves._find_pair`` / ``._find_curve_value`` use different
    lookup semantics (pairs-list scan; no float-coercion of the result).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json(fpath: Path) -> Any:
    """Read and parse a JSON file at *fpath*."""
    return json.loads(Path(fpath).read_text())


def normalize_text(value: Any) -> str:
    """Lower-cased, stripped string form of *value* (``None`` → ``""``)."""
    return str(value or "").strip().lower()


def is_truthy_text(value: Any) -> bool:
    """True when *value* stringifies to an affirmative token."""
    return normalize_text(value) in {"true", "1", "yes"}


def coerce_float(value: Any) -> float:
    """Cast *value* to ``float``, returning ``-inf`` on failure.

    The ``-inf`` sentinel makes unparseable values sort last under a
    reverse (descending) sort — the shared idiom in the callers that use it.
    """
    try:
        return float(value)
    except Exception:
        return float("-inf")


def clean_optional_text(value: Any) -> str | None:
    """Stripped string form of *value*, or ``None`` when empty/``none``/``nan``."""
    text = str(value or "").strip()
    if not text:
        return None
    if text.lower() in {"none", "nan"}:
        return None
    return text


def abbreviate_label(text: str, *, max_chars: int = 24) -> str:
    """Truncate *text* to *max_chars*, appending ``...`` when shortened."""
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return "." * max_chars
    return text[: max_chars - 3].rstrip() + "..."


def find_curve_value(rows: list[dict[str, Any]], abs_tol: float) -> float | None:
    """Return the ``agree_ratio`` for the curve row whose ``abs_tol`` matches.

    Tolerant of missing/non-numeric fields and ``None`` *rows*; returns
    ``None`` when no row matches.
    """
    for row in rows or []:
        try:
            if float(row.get("abs_tol")) == float(abs_tol):
                return float(row.get("agree_ratio"))
        except Exception:
            pass
    return None

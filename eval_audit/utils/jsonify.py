"""Strict-JSON coercion shared by the diff/diagnosis modules.

One implementation of ``json_compatible`` (plan item A3 of
docs/planning/repo-simplification-plan-2026-07-12.md). Previously two
private copies lived in ``helm.diff_primitives`` and
``normalized.diagnose`` under an unenforced "must stay identical"
comment — and they had already drifted: the diagnose copy carried the
IM-12 determinism fix (sets serialized in sorted order; plain
iteration is PYTHONHASHSEED-dependent) that the diff_primitives copy
lacked. The deterministic version wins — determinism is a hard project
requirement.
"""

from __future__ import annotations

import json
import math
from typing import Any

import ubelt as ub


def json_compatible(obj: Any) -> Any:
    """Recursively coerce to strict JSON-compatible types.

    Notably:

    - tuples -> lists
    - sets -> lists **sorted by serialized value** (IM-12: stable across
      interpreter hash seeds)
    - non-finite floats -> None
    - objects with ``as_tuple()`` -> their tuple, coerced
    - unknown objects -> string repr
    """
    if obj is None or isinstance(obj, (str, int, bool)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): json_compatible(v) for k, v in obj.items()}
    if isinstance(obj, set):
        return sorted(
            (json_compatible(v) for v in obj),
            key=lambda x: json.dumps(x, sort_keys=True, default=str),
        )
    if isinstance(obj, (list, tuple)):
        return [json_compatible(v) for v in obj]
    try:
        # common dataclass / custom key cases
        if hasattr(obj, 'as_tuple') and callable(getattr(obj, 'as_tuple')):
            return json_compatible(list(obj.as_tuple()))
    except Exception:
        pass
    try:
        return ub.urepr(obj, nl=0, compact=1)
    except Exception:
        return str(obj)

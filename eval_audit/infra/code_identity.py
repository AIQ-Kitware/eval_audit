"""Which code produced an artifact.

An input digest proves *same inputs*, not *same answer*: identical artifacts
run through changed code give a different number. So the code identity has to
travel with the digest rather than beside it — a comparison digest that omits
it silently weakens every time the analysis layer changes.

Cheap and cached, because the render path asks per packet and a store holds a
thousand of them.
"""
from __future__ import annotations

import subprocess
from functools import lru_cache
from typing import Any

from eval_audit import __version__
from eval_audit.infra.env import load_env


@lru_cache(maxsize=1)
def repo_git_sha() -> str | None:
    """HEAD of the eval_audit checkout, or None outside a git tree."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(load_env().repo_root),
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip() or None
    except Exception:
        return None


@lru_cache(maxsize=1)
def code_identity() -> dict[str, Any]:
    """The code-side half of a comparison's provenance."""
    return {
        "git_sha": repo_git_sha(),
        "eval_audit_version": __version__,
    }

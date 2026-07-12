"""Shared matplotlib helpers for the reports package.

Home for rendering utilities that were previously copy-pasted between
the matplotlib render modules (``core_metric_plots``,
``eee_heatmap_render``). Plan item E2 of
docs/planning/repo-simplification-plan-2026-07-12.md.
"""

from __future__ import annotations

from pathlib import Path

import safer


def atomic_savefig(fig, fpath: Path, **kwargs) -> Path:
    """matplotlib ``fig.savefig`` writing to ``fpath`` atomically via safer.

    Parent directories are auto-created; the format is inferred from the
    file suffix (defaults to png).
    """
    fpath = Path(fpath)
    suffix = fpath.suffix.lstrip(".") or "png"
    with safer.open(fpath, "wb", make_parents=True) as fp:
        fig.savefig(fp, format=suffix, **kwargs)
    return fpath

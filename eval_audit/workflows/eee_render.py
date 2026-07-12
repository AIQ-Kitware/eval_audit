"""Shared subprocess renderers for the EEE-only entry points.

``from_eee`` (fan-out over planner packets) and ``compare_pair_eee``
(single pair) both render per-packet reports by shelling out to
``eval_audit.reports.core_metrics`` with pre-written planner manifests,
and ``from_eee`` additionally shells out to the aggregate summary
builder. The command + environment assembly was copy-pasted three times
across the two CLIs (plan item D2 of
docs/planning/repo-simplification-plan-2026-07-12.md); this module is
the single home. Subprocess (not in-process) stays deliberate:
``from_eee`` fans packets out on a thread pool and each render gets a
clean interpreter.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from eval_audit.infra.paths import repo_root


def repo_pythonpath_env() -> dict[str, str]:
    """A copy of ``os.environ`` with the repo root prepended to PYTHONPATH.

    Ensures ``python -m eval_audit.*`` children resolve this checkout even
    when the package is not installed into the invoking interpreter.
    """
    env = os.environ.copy()
    root = str(repo_root())
    env["PYTHONPATH"] = root + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    return env


def write_packet_manifests(dpath: Path, packet: dict[str, Any]) -> None:
    """Write a planner packet's components/comparisons manifests into ``dpath``."""
    (dpath / "components_manifest.json").write_text(
        json.dumps(packet["components_manifest"], indent=2) + "\n"
    )
    (dpath / "comparisons_manifest.json").write_text(
        json.dumps(packet["comparisons_manifest"], indent=2) + "\n"
    )


def run_core_metrics(
    report_dpath: Path,
    *,
    render_heavy_plots: bool = False,
    plot_layout_args: list[str] | None = None,
) -> None:
    """Render one packet's core-metric report via ``python -m``.

    Expects the packet manifests to already exist in ``report_dpath``
    (see :func:`write_packet_manifests`). Always passes
    ``--instance-source eee-only``: the EEE entry points must never
    enrich instances from HELM origins (Phase 3 / 4.5 declared
    instance-source policy).
    """
    cmd: list[str] = [
        sys.executable, "-m", "eval_audit.reports.core_metrics",
        "--report-dpath", str(report_dpath),
        "--components-manifest", str(report_dpath / "components_manifest.json"),
        "--comparisons-manifest", str(report_dpath / "comparisons_manifest.json"),
        "--instance-source", "eee-only",
    ]
    if render_heavy_plots:
        cmd.append("--render-heavy-pairwise-plots")
    cmd += list(plot_layout_args or [])
    subprocess.run(cmd, check=True, env=repo_pythonpath_env())

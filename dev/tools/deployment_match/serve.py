"""Phase 2 — serve the grid and probe every cell (the two-tier driver).

Generalizes the OLMo ``run_matrix.sh`` to the two-tier grid: the expensive
serve-time knobs define infer-stack *endpoints* (one container each), and the
cheap request-time knobs are probed per request against the same running
container. So the loop is::

    infer-stack gc
    for endpoint in catalog:                 # one container per serve-recipe
        infer-stack acquire <endpoint>
        for cell on that endpoint:           # many request-variants, one container
            probe.query_cell(...) -> results/<cell>.json
        infer-stack release --evict
    infer-stack gc

infer-stack acquire/release/env/gc are shelled out (subprocess); the HTTP probe
runs in-process via :mod:`probe`. ``--dry`` prints the exact plan without
touching a GPU, so the whole flow is inspectable on CPU. Point
``INFER_STACK_CONFIG_DIR`` at the grid dir so the generated ``catalog.yaml`` +
``settings.yaml`` are the active catalog.

GPU selection is infer-stack's job, not ours: ``acquire --queue`` lets its
placement planner pick any available GPU (and wait when the fleet is busy). We do
NOT request specific GPUs. ``allowed_gpus`` is an *optional* operator restriction
(``INFER_STACK_ALLOWED_GPUS``) — left unset, placement uses every detected
(non-display) GPU; an operator's own exported ``INFER_STACK_ALLOWED_GPUS`` is
preserved (we only override it when ``allowed_gpus`` is explicitly passed).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import OrderedDict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import probe as probe_mod  # noqa: E402


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _sh(cmd: list[str], env: dict[str, str], *, dry: bool,
        capture: bool = False, check: bool = False) -> str:
    if dry:
        _log("    + " + " ".join(cmd))
        return ""
    proc = subprocess.run(cmd, env=env, text=True,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=None if capture else None, check=False)
    if check and proc.returncode != 0:
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(cmd)}")
    if proc.returncode != 0:
        _log(f"WARN: nonzero exit ({proc.returncode}): {' '.join(cmd)}")
    return (proc.stdout or "").strip() if capture else ""


def group_cells_by_endpoint(cells: list[dict[str, Any]]) -> "OrderedDict[str, list[dict[str, Any]]]":
    groups: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for c in cells:
        groups.setdefault(c["endpoint"], []).append(c)
    return groups


def run_grid(grid_dir: str | Path, out_dir: str | Path, *,
             allowed_gpus: str | None = None, litellm_port: int = 14042,
             base_url: str | None = None, timeout: float = 120.0,
             dry: bool = False, progress: bool = True) -> Path:
    grid_dir = Path(grid_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    oracle = json.loads((grid_dir / "oracle.json").read_text())
    cells = json.loads((grid_dir / "cells.json").read_text())
    sample = oracle["sample"]
    recipe = oracle.get("recipe", {})
    base = (base_url or f"http://localhost:{litellm_port}").rstrip("/")

    if not dry and not shutil.which("infer-stack"):
        raise SystemExit(
            "'infer-stack' CLI not found on PATH. Run on a GPU host with "
            "infer-stack installed, or use --dry to print the plan.")

    env = os.environ.copy()
    env["INFER_STACK_CONFIG_DIR"] = str(grid_dir)
    # Only restrict placement when an operator explicitly asks; otherwise leave
    # any exported INFER_STACK_ALLOWED_GPUS untouched and let infer-stack place.
    if allowed_gpus:
        env["INFER_STACK_ALLOWED_GPUS"] = allowed_gpus
        _log(f"[run] restricting placement to GPUs {allowed_gpus} "
             "(operator override); default is any available GPU")

    groups = group_cells_by_endpoint(cells)
    _log(f"[run] grid_dir={grid_dir} gateway={base} endpoints={len(groups)} "
         f"cells={len(cells)}{' (DRY)' if dry else ''}")

    envfile = tempfile.NamedTemporaryFile(
        prefix="dm-match.", suffix=".env", delete=False).name
    try:
        _sh(["infer-stack", "gc", "--yes"], env, dry=dry)
        master_key: str | None = None
        for endpoint, ep_cells in groups.items():
            _log(f"\n==================== {endpoint} "
                 f"({len(ep_cells)} cell(s)) ====================")
            # --queue: use whatever GPU infer-stack reports available, waiting for
            # one to free if the fleet is busy (we never request a specific GPU).
            _sh(["infer-stack", "acquire", endpoint, "--queue", "--yes",
                 "--env-file", envfile], env, dry=dry)
            if not dry and master_key is None:
                master_key = _sh(["infer-stack", "env", "LITELLM_MASTER_KEY"],
                                 env, dry=dry, capture=True) or None
            for cell in ep_cells:
                if dry:
                    _log(f"    would probe cell {cell['cell_id']} "
                         f"request={cell['request']} over {len(sample)} prompts")
                    continue
                _log(f"  cell {cell['cell_id']} request={cell['request']}")
                doc = probe_mod.query_cell(
                    base + "/v1", cell, sample, recipe,
                    api_key=master_key, timeout=timeout, progress=progress)
                out_path = out_dir / f"{cell['cell_id'].replace('::', '__')}.json"
                out_path.write_text(json.dumps(doc, indent=2))
            _sh(["infer-stack", "release", "--env-file", envfile, "--evict", "--yes"],
                env, dry=dry)
        _sh(["infer-stack", "gc", "--yes"], env, dry=dry)
    finally:
        try:
            os.unlink(envfile)
        except OSError:
            pass

    _log(f"\n[run] wrote {len(cells)} cell result(s) -> {out_dir}"
         if not dry else "\n[run] dry plan complete (no results written)")
    return out_dir

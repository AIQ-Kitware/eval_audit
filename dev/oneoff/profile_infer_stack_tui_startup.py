#!/usr/bin/env python3
"""Bisect `infer-stack tui` startup time into its blocking phases.

The TUI's on_mount is already async-clean (first paint renders from in-memory
ledger state; `docker compose ps` and log streaming run in worker threads), so
perceived startup lag must come from the SYNCHRONOUS work before the first
frame. This times each phase of the exact TuiCLI.main sequence, in order:

    A. interpreter + site startup        (subprocess: python -c pass)
    B. console-script entry resolution   (subprocess: infer-stack --help)
    C. import infer_stack.cli            (scriptconfig, leasing, ubelt, ...)
    D. import infer_stack.tui            (textual)
    E. settings/paths reads              (settings_path, backend setting)
    F. nvidia-smi inventory              (detect_inventory - NO timeout guard!)
    G. sqlite ledger open+sweep+status   (the _first_paint data source)
    H. catalog load                      (yaml)
    I. import requests                   (lazy, inside ComposeBackend.__init__)

Run ON THE HOST WHERE THE TUI IS SLOW (same venv):

    python dev/oneoff/profile_infer_stack_tui_startup.py

Interpretation hints printed at the end. For anything this misses, follow up
with `py-spy dump --pid <pid>` while the TUI is visibly stalled.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time

TIMINGS: list[tuple[str, float]] = []


def _phase(label: str):
    class _Ctx:
        def __enter__(self):
            self.t0 = time.perf_counter()
            return self

        def __exit__(self, *exc):
            TIMINGS.append((label, time.perf_counter() - self.t0))
            return False

    return _Ctx()


def main() -> int:
    with _phase("A interpreter+site (python -c pass)"):
        subprocess.run([sys.executable, "-c", "pass"], check=True)

    exe = shutil.which("infer-stack")
    if exe:
        with _phase("B console script (infer-stack --help)"):
            subprocess.run(
                [exe, "--help"], check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
    else:
        TIMINGS.append(("B console script (NOT ON PATH)", 0.0))

    with _phase("C import infer_stack.cli"):
        import infer_stack.cli  # noqa: F401

    with _phase("D import infer_stack.tui (textual)"):
        import infer_stack.tui  # noqa: F401

    with _phase("E settings/paths reads"):
        from infer_stack.paths import config_root, get_setting, settings_path

        settings_path().exists()
        backend = get_setting("backend")

    with _phase("F detect_inventory (nvidia-smi)"):
        from infer_stack.hardware import detect_inventory

        inventory = detect_inventory()

    with _phase("G ledger open+sweep+status (sqlite)"):
        from infer_stack.leasing.ledger import Ledger, default_ledger_path
        from infer_stack.leasing.store import SqliteStore

        ledger = Ledger(SqliteStore(str(default_ledger_path())))
        ledger.sweep()
        leases, deployments = ledger.status()

    with _phase("H catalog load (yaml)"):
        from infer_stack.leasing.catalog import Catalog

        catalog_path = config_root() / "catalog.yaml"
        n_endpoints = 0
        if catalog_path.exists():
            n_endpoints = len(Catalog.load(catalog_path).endpoints)

    with _phase("I import requests (ComposeBackend lazy dep)"):
        import requests  # noqa: F401

    total = sum(dt for _, dt in TIMINGS)
    print()
    print(f"{'phase':<45} {'seconds':>8}  {'% of total':>10}")
    print("-" * 68)
    for label, dt in TIMINGS:
        pct = (100.0 * dt / total) if total else 0.0
        bar = "#" * int(pct / 2)
        print(f"{label:<45} {dt:>8.3f}  {pct:>9.1f}% {bar}")
    print("-" * 68)
    print(f"{'TOTAL (approximates time-to-first-frame)':<45} {total:>8.3f}")
    print()
    print(f"context: backend={backend!r} gpus={inventory['gpu_count']} "
          f"leases={len(leases)} deployments={len(deployments)} "
          f"catalog_endpoints={n_endpoints}")
    print()
    print("hints:")
    print("  A slow  -> venv/site cost (many .pth / editable-install import hooks)")
    print("  B >> A+C -> entry-point resolution scanning a fat venv's metadata")
    print("  C/D slow -> import chain; rerun with python -X importtime to attribute")
    print("  F slow  -> nvidia-smi itself (driver state); NOTE detect_inventory has")
    print("             no subprocess timeout, so a wedged driver blocks forever")
    print("  G slow  -> ledger sqlite on slow/contended disk (data_root/leasing)")
    print("  none slow but TUI still lags -> py-spy dump --pid <tui pid> during the")
    print("             stall; suspects then: terminal handshake or textual CSS parse")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

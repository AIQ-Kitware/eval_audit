"""Capture the Phase 3 behavior baseline (matrix §8).

Runs the current code over the on-disk fixture cases and (re)writes the
committed snapshots in this directory. Run from the repo root::

    python tests/fixtures/phase3_baseline/capture_baseline.py

Re-capturing is only legitimate when a Phase 3 sub-stage *intends* a
recorded behavior change (per the matrix, additive keys only) — never
to make a red gate green. Review the snapshot diff like code.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "tests"))

from phase3_baseline_lib import CASES, run_case, write_baseline  # noqa: E402


def main() -> None:
    for case in CASES:
        with tempfile.TemporaryDirectory(prefix=f"phase3_{case}_") as tmp:
            snapshot = run_case(case, Path(tmp))
        fpath = write_baseline(case, snapshot)
        print(f"captured {case} -> {fpath}")


if __name__ == "__main__":
    main()

"""Capture the Phase 3 behavior baseline (matrix §8).

Runs the current code over the on-disk fixture cases and (re)writes the
committed snapshots in this directory. Run from the repo root::

    python tests/fixtures/phase3_baseline/capture_baseline.py

Captures both fixture families in one command:

* EEE cells **F3/F4** — ``compare-pair-eee`` over ``eee_only_demo``.
* HELM cells **F1/F2** — ``core_metrics`` (via manifests) over the
  ``every_eval_ever`` submodule HELM run fixture; F1 self-compare
  (strict 1.0), F2 official-vs-drifted-local. Skipped with a warning
  if the submodule fixture is not checked out.

**Still missing — F8** (mixed HELM×EEE packet, matrix "build for
4.3/4.6"): no on-disk fixture pairs a HELM run and an EEE artifact under
a shared logical run key, so it cannot be captured without first
building that coordinated fixture (matrix §7). It is deliberately not
captured here — see phase3_baseline_lib's module docstring and the A4
gate-prep note in docs/planning/repo-simplification-plan-2026-07-12.md.

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

from phase3_baseline_lib import (  # noqa: E402
    CASES,
    HELM_CASES,
    helm_fixture_available,
    run_case,
    run_helm_case,
    write_baseline,
)


def main() -> None:
    for case in CASES:
        with tempfile.TemporaryDirectory(prefix=f"phase3_{case}_") as tmp:
            snapshot = run_case(case, Path(tmp))
        fpath = write_baseline(case, snapshot)
        print(f"captured {case} -> {fpath}")

    if not helm_fixture_available():
        print("WARNING: HELM fixture not checked out; skipped HELM cells F1/F2")
        return
    for case in HELM_CASES:
        with tempfile.TemporaryDirectory(prefix=f"phase3_{case}_") as tmp:
            snapshot = run_helm_case(case, Path(tmp))
        fpath = write_baseline(case, snapshot)
        print(f"captured {case} -> {fpath}")


if __name__ == "__main__":
    main()

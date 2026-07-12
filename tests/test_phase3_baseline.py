"""Phase 3 behavior-equivalence gate against the committed baseline.

Re-runs the on-disk fixture cases through the *current* code and asserts
the normalized snapshot equals the committed capture in
``tests/fixtures/phase3_baseline/``. Covers two families:

* EEE cells **F3/F4** — ``compare-pair-eee`` over ``eee_only_demo``.
* HELM cells **F1/F2** — ``core_metrics`` (via manifests) over the
  ``every_eval_ever`` submodule HELM fixture. These pin exactly the
  HELM render path the A4 ``HelmRunDiff`` retirement changes, so A4
  cannot start until they are green (A4 gate-prep).

Every Phase 3 sub-stage must keep this green (matrix §1, the golden
rule); a legitimate behavior change re-captures via
``capture_baseline.py`` with the diff reviewed like code. (Matrix cell
F8 — mixed HELM×EEE packet — is intentionally absent: no on-disk
fixture supports it; see phase3_baseline_lib's module docstring.)

Marked slow: each case shells out to the planner / core_metrics
renderer (the HELM cells additionally run the HELM->EEE conversion).
Run with ``pytest --run-slow``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from phase3_baseline_lib import (
    CASES,
    FIXTURE_ROOT,
    HELM_CASES,
    baseline_fpath,
    helm_fixture_available,
    load_baseline,
    run_case,
    run_helm_case,
)

pytestmark = pytest.mark.slow


@pytest.mark.parametrize("case", CASES)
def test_outputs_match_committed_baseline(case: str, tmp_path: Path) -> None:
    if not FIXTURE_ROOT.exists():
        pytest.skip(f"EEE demo fixture missing: {FIXTURE_ROOT}")
    if not baseline_fpath(case).exists():
        pytest.fail(
            f"missing committed baseline {baseline_fpath(case)}; "
            "run tests/fixtures/phase3_baseline/capture_baseline.py"
        )
    snapshot = run_case(case, tmp_path)
    baseline = load_baseline(case)
    assert snapshot == baseline


@pytest.mark.parametrize("case", HELM_CASES)
def test_helm_outputs_match_committed_baseline(case: str, tmp_path: Path) -> None:
    if not helm_fixture_available():
        pytest.skip("HELM submodule fixture not checked out")
    pytest.importorskip("helm")
    pytest.importorskip("every_eval_ever")
    if not baseline_fpath(case).exists():
        pytest.fail(
            f"missing committed baseline {baseline_fpath(case)}; "
            "run tests/fixtures/phase3_baseline/capture_baseline.py"
        )
    snapshot = run_helm_case(case, tmp_path)
    baseline = load_baseline(case)
    assert snapshot == baseline

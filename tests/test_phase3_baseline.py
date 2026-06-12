"""Phase 3 behavior-equivalence gate against the committed baseline.

Re-runs the on-disk fixture cases (F3/F4) through the *current* code
and asserts the normalized snapshot equals the committed capture in
``tests/fixtures/phase3_baseline/``. Every Phase 3 sub-stage must keep
this green (matrix §1, the golden rule); a legitimate behavior change
re-captures via ``capture_baseline.py`` with the diff reviewed like
code.

Marked slow: each case shells out to the planner + core_metrics
renderer. Run with ``pytest --run-slow``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from phase3_baseline_lib import (
    CASES,
    FIXTURE_ROOT,
    baseline_fpath,
    load_baseline,
    run_case,
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

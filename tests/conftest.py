"""Pytest configuration for the eval_audit test suite.

Adds a `slow` marker that's deselected by default. Pass `--run-slow` to
include slow-marked tests. The full suite takes ~4 min when slow tests
run; the fast subset is ~30 s.
"""
from __future__ import annotations

from pathlib import Path

import pytest


# IM-11: single source of truth for the checked-in EEE-only demo fixture path
# and its skip-when-missing guard, previously duplicated across six test files
# (test_compare_pair_eee, phase3_baseline_lib, test_eee_only_demo,
# test_phase3_normalized_diff, test_phase3_judge_substitution,
# test_virtual_experiment_eee). Import these by name (``from conftest import
# EEE_DEMO_ROOT`` etc.) or depend on the ``eee_demo_root`` fixture.
EEE_DEMO_ROOT = Path(__file__).parent / "fixtures" / "eee_only_demo" / "eee_artifacts"
EEE_DEMO_OFFICIAL_DIR = EEE_DEMO_ROOT / "official" / "imdb" / "toy" / "m1-small"
EEE_DEMO_LOCAL_DIR = EEE_DEMO_ROOT / "local" / "primary" / "imdb" / "toy" / "m1-small"


def require_eee_demo() -> Path:
    """Skip the calling test when the checked-in EEE demo fixture is absent.

    Returns the fixture root so callers can ``root = require_eee_demo()``.
    """
    if not (EEE_DEMO_OFFICIAL_DIR.exists() and EEE_DEMO_LOCAL_DIR.exists()):
        pytest.skip(f"EEE demo fixture missing: {EEE_DEMO_ROOT}")
    return EEE_DEMO_ROOT


@pytest.fixture
def eee_demo_root() -> Path:
    """The checked-in EEE-only demo artifact root; skips when missing."""
    return require_eee_demo()


def pytest_addoption(parser):
    parser.addoption(
        "--run-slow",
        action="store_true",
        default=False,
        help="include tests marked @pytest.mark.slow (skipped by default)",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-slow"):
        return
    skip_slow = pytest.mark.skip(reason="slow; pass --run-slow to include")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)

"""IM-8: EEE-only CLIs must reject typo'd core_metrics pass-through flags."""
from __future__ import annotations

import argparse

import pytest

from eval_audit.cli.from_eee import _validate_core_metrics_passthrough


def _parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(prog="eval-audit-from-eee")


def test_known_passthrough_flags_accepted():
    remainder = ["--plot-figure-scale", "1.2", "--no-plots", "--skip-diagnosis"]
    assert _validate_core_metrics_passthrough(remainder, _parser()) == remainder


def test_flag_equals_value_form_accepted():
    remainder = ["--plot-figure-scale=1.5"]
    assert _validate_core_metrics_passthrough(remainder, _parser()) == remainder


def test_typo_flag_rejected():
    with pytest.raises(SystemExit):
        _validate_core_metrics_passthrough(["--plto-figure-scale", "1.2"], _parser())


def test_short_option_rejected():
    with pytest.raises(SystemExit):
        _validate_core_metrics_passthrough(["-x"], _parser())

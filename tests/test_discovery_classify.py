"""`discovery._classify` ambiguity resolution.

A token-subset entry that is a strict prefix of a more specific sibling (the
bare ``bbq:...`` vs its ``...,groups=ablation_multiple_choice`` superset) matches
BOTH dirs, but the exact dir strictly out-scores the superset — a unique best, so
it must RESOLVE, not fail as AMBIGUOUS. A genuine tie (the same run name in two
suites — the cross-suite dup the per-era corpus view guards) must stay AMBIGUOUS.

Regression for the classic_together_combined freeze:
    ValueError: cannot freeze run-entry 'bbq:...,model=together/gpt-j-6b':
    discovery is AMBIGUOUS ... (2 candidates).
"""
from __future__ import annotations

from pathlib import Path

import pytest

# The real matcher (magnet) drives scoring; skip cleanly where it is absent.
pytest.importorskip("magnet.backends.helm.cli.materialize_helm_run")

from eval_audit.integrations.infer_stack import discovery as dc  # noqa: E402


def _runs(*names: str, root: str = "/corpus") -> list[dc._Run]:
    return [dc._Run(name=n, path=Path(root) / n) for n in names]


BARE = "bbq:subject=all,method=multiple_choice_joint,model=together_gpt-j-6b"
ABLATION = BARE + ",groups=ablation_multiple_choice"
# The requested entries carry the model with a '/', as HELM run_specs do.
Q_BARE = "bbq:subject=all,method=multiple_choice_joint,model=together/gpt-j-6b"
Q_ABLATION = Q_BARE + ",groups=ablation_multiple_choice"


def test_subset_entry_resolves_to_exact_dir():
    """Bare entry matches both dirs but resolves to the exact (non-superset) one."""
    result = dc._classify(Q_BARE, _runs(BARE, ABLATION))
    assert result.status == "RESOLVED"
    assert len(result.candidates) == 2  # both matched the subset...
    assert result.best.name == BARE  # ...but the exact dir wins uniquely


def test_superset_entry_resolves_to_its_own_dir():
    """The '...,groups=...' entry only the superset dir satisfies."""
    result = dc._classify(Q_ABLATION, _runs(BARE, ABLATION))
    assert result.status == "RESOLVED"
    assert result.best.name == ABLATION


def test_cross_suite_duplicate_stays_ambiguous():
    """Identical run name under two suites is a true tie — still AMBIGUOUS."""
    dup = [
        dc._Run(name=BARE, path=Path("/corpus/v0.2.4") / BARE),
        dc._Run(name=BARE, path=Path("/corpus/v0.3.0") / BARE),
    ]
    result = dc._classify(Q_BARE, dup)
    assert result.status == "AMBIGUOUS"
    assert len(result.candidates) == 2

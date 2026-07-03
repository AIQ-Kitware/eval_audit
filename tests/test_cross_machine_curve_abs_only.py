"""P1-13 regression: the cross-machine curve overlaid on pure-abs_tol axes
must use the rel_tol=0 highlights, not the joint abs+rel highlights (whose
rel_tol rises to 1.0 and inflates agreement at the same x)."""
from __future__ import annotations

import json
from pathlib import Path

from eval_audit.reports.pair_report import abs_only_tolerances, default_tolerances
from eval_audit.reports.core_metric_curves import _load_optional_cross_machine_pair


def test_abs_only_tolerances_zero_rel_tol_and_keep_abs_grid():
    src = default_tolerances()
    out = abs_only_tolerances(src)
    assert [c["abs_tol"] for c in out] == [c["abs_tol"] for c in src]
    assert all(c["rel_tol"] == 0.0 for c in out)


def test_loader_prefers_abs_only_highlights(tmp_path: Path):
    cross = tmp_path / "cross-machine-aiq-gpu"
    cross.mkdir()
    # Inflated joint-tolerance curve vs honest abs-only curve at the same x.
    payload = {
        "display_labels": {"label_a": "aiq-gpu", "label_b": "namek"},
        "strict_summary": {"diagnosis": {"label": "clean"}},
        "distance_summary": {},
        "tolerance_highlights": {
            "run_level": [{"abs_tol": 0.1, "rel_tol": 1.0, "agree_ratio": 0.99}],
            "instance_level": [{"abs_tol": 0.1, "rel_tol": 1.0, "agree_ratio": 0.98}],
        },
        "tolerance_highlights_abs_only": {
            "run_level": [{"abs_tol": 0.1, "rel_tol": 0.0, "agree_ratio": 0.70}],
            "instance_level": [{"abs_tol": 0.1, "rel_tol": 0.0, "agree_ratio": 0.65}],
        },
    }
    (cross / "pair_report.json").write_text(json.dumps(payload))

    loaded = _load_optional_cross_machine_pair(tmp_path)
    assert loaded is not None
    # Curve must reflect the rel_tol=0 (honest) values, not the inflated ones.
    assert loaded["run_level"]["agreement_vs_abs_tol"][0]["agree_ratio"] == 0.70
    assert loaded["instance_level"]["agreement_vs_abs_tol"][0]["agree_ratio"] == 0.65


def test_loader_falls_back_to_joint_highlights_for_legacy_sidecar(tmp_path: Path):
    cross = tmp_path / "cross-machine-aiq-gpu"
    cross.mkdir()
    payload = {
        "display_labels": {"label_a": "aiq-gpu", "label_b": "namek"},
        "strict_summary": {},
        "distance_summary": {},
        "tolerance_highlights": {
            "run_level": [{"abs_tol": 0.1, "rel_tol": 1.0, "agree_ratio": 0.99}],
            "instance_level": [],
        },
    }
    (cross / "pair_report.json").write_text(json.dumps(payload))

    loaded = _load_optional_cross_machine_pair(tmp_path)
    assert loaded is not None
    assert loaded["run_level"]["agreement_vs_abs_tol"][0]["agree_ratio"] == 0.99

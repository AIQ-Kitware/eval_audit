"""P1-15 regression: non-finite (NaN/inf) scores are filtered at row
construction so they cannot poison quantiles, the agreement curve, or JSON
serialization; the dropped count is reported."""
from __future__ import annotations

import json
import math
from types import SimpleNamespace

from eval_audit.normalized import compare as ncompare
from eval_audit.normalized.diff import agreement_curve, group_quantiles
from eval_audit.normalized.model import InstanceRecord


def _rec(sample_id: str, score: float) -> InstanceRecord:
    return InstanceRecord(
        sample_id=sample_id,
        sample_hash=sample_id,
        metric_id="exact_match",
        metric_kind="core",
        score=score,
        is_correct=None,
        record=None,
    )


def _run(instances):
    # instance_level_core_rows only touches ``.instances``.
    return SimpleNamespace(instances=instances)


def test_instance_rows_drop_nonfinite_and_report_count():
    run_a = _run([_rec("s1", 1.0), _rec("s2", float("nan")), _rec("s3", float("inf"))])
    run_b = _run([_rec("s1", 1.0), _rec("s2", 0.0), _rec("s3", 0.0)])

    rows, stats = ncompare.instance_level_core_rows(run_a, run_b)

    # Only the finite pair (s1) survives.
    assert len(rows) == 1
    assert rows[0]["sample_id"] == "s1"
    assert stats["n_joined_pairs"] == 3
    assert stats["n_nonfinite_dropped"] == 2

    # Every surviving abs_delta is finite.
    assert all(math.isfinite(float(r["abs_delta"])) for r in rows)


def test_quantiles_and_agreement_are_finite_and_json_valid():
    run_a = _run([_rec("s1", 1.0), _rec("s2", float("nan"))])
    run_b = _run([_rec("s1", 1.0), _rec("s2", 0.0)])
    rows, _ = ncompare.instance_level_core_rows(run_a, run_b)

    q = group_quantiles(rows)
    curve = agreement_curve(rows, [0.0, 0.1])

    # No NaN literal in the serialized output (json.dumps default emits an
    # invalid-JSON ``NaN`` token when a NaN slips through).
    payload = json.dumps({"quantiles": q, "curve": curve})
    assert "NaN" not in payload
    assert "Infinity" not in payload
    # The one finite pair agrees perfectly.
    assert q["abs_delta"]["max"] == 0.0
    assert curve[0]["agree_ratio"] == 1.0

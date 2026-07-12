"""P1-17 regression: benchmark_output discovery order must be stable
(machine-independent), because dedupe_rows downstream is first-wins and Stage 1
outputs must be deterministic."""
from __future__ import annotations

from pathlib import Path

from eval_audit.run_entries import discover_benchmark_output_dirs


def test_discovery_order_is_sorted(tmp_path: Path):
    # Create several suite roots (out of alphabetical creation order) each
    # containing a benchmark_output dir.
    for name in ["zeta", "alpha", "mid", "beta"]:
        (tmp_path / name / "benchmark_output").mkdir(parents=True)

    found = list(discover_benchmark_output_dirs([tmp_path]))
    # Within one root the descent is sorted, so the emitted order is stable.
    assert found == sorted(found, key=str)
    names = [p.parent.name for p in found]
    assert names == sorted(names)


def test_discovery_is_repeatable(tmp_path: Path):
    for name in ["s3", "s1", "s2"]:
        (tmp_path / name / "benchmark_output").mkdir(parents=True)
    a = list(discover_benchmark_output_dirs([tmp_path]))
    b = list(discover_benchmark_output_dirs([tmp_path]))
    assert a == b

"""P1-20 regression: reproduce.sh must carry the actual invocation flags
(--no-filter-inventory / --no-canonical-scan / --analysis-root / --summary-root)
so a from-eee/virtual build regenerates the SAME report, not a different one at
the default root with the excluded inventory re-included."""
from __future__ import annotations

from pathlib import Path

from eval_audit.reports.summary.publish import _build_summary_cmd, _write_reproduce_sh


def test_build_summary_cmd_threads_extra_args():
    cmd = _build_summary_cmd(
        scope_kind="all_results",
        scope_value=None,
        index_path=Path("/x/audit_results_index.csv"),
        filter_inventory_json=None,
        extra_args="--no-filter-inventory --no-canonical-scan --analysis-root /out",
    )
    assert "--no-filter-inventory" in cmd
    assert "--no-canonical-scan" in cmd
    assert "--analysis-root /out" in cmd
    # No inventory flag when none was provided.
    assert "--filter-inventory-json" not in cmd


def test_reproduce_sh_written_with_extra_args(tmp_path: Path):
    fpath = tmp_path / "reproduce.sh"
    _write_reproduce_sh(
        fpath,
        "experiment_name",
        "eee-demo",
        index_path=tmp_path / "audit_results_index.csv",
        filter_inventory_json=None,
        extra_args="--no-canonical-scan --summary-root /out/agg",
    )
    text = fpath.read_text()
    assert "--no-canonical-scan" in text
    assert "--summary-root /out/agg" in text
    assert "--experiment-name eee-demo" in text

"""P1-19 regression: eval-audit-analyze-many must exit non-zero when a
per-experiment analysis fails (it previously exited 0 and still built the
aggregate summary over the incomplete set with no signal)."""
from __future__ import annotations

import pytest

from eval_audit.cli import analyze_many


def test_analyze_many_exits_nonzero_on_experiment_failure(tmp_path, monkeypatch, capsys):
    index = tmp_path / "audit_results_index.csv"
    index.write_text("run_entry\nbench:model=a\n")

    def _boom(argv):
        raise RuntimeError("analysis blew up")

    monkeypatch.setattr(analyze_many.analyze_experiment, "main", _boom)

    with pytest.raises(SystemExit) as exc:
        analyze_many.main(
            [
                "--index-fpath", str(index),
                "--experiment-name", "exp-a",
                "--experiment-name", "exp-b",
            ]
        )
    # Non-zero exit carrying the failure count.
    assert exc.value.code != 0
    assert "2 of 2 experiment(s) failed" in str(exc.value.code)


def test_analyze_many_warns_before_incomplete_summary(tmp_path, monkeypatch, capsys):
    index = tmp_path / "audit_results_index.csv"
    index.write_text("run_entry\nbench:model=a\n")

    monkeypatch.setattr(
        analyze_many.analyze_experiment, "main", lambda argv: (_ for _ in ()).throw(RuntimeError("x"))
    )
    monkeypatch.setattr(analyze_many.build_reports_summary, "main", lambda argv: None)

    with pytest.raises(SystemExit):
        analyze_many.main(
            [
                "--index-fpath", str(index),
                "--experiment-name", "exp-a",
                "--build-summary",
            ]
        )
    out = capsys.readouterr().out
    assert "INCOMPLETE set" in out

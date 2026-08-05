"""Store lint: packets whose number depends on an unrecorded choice (G14)."""
from __future__ import annotations

import json
from pathlib import Path

from eval_audit.cli.lint_store import audit_packet, audit_paths, main


def _pair(local_id: str, agreement: float | None, kind: str = "official_vs_local") -> dict:
    instance_level = {}
    if agreement is not None:
        instance_level = {"agreement_vs_abs_tol": [{"abs_tol": 0.0, "agree_ratio": agreement}]}
    return {
        "comparison_kind": kind,
        "reference_component_id": "official::mmlu::v1.1.0::mmlu:subject=anatomy",
        "component_ids": ["official::mmlu::v1.1.0::mmlu:subject=anatomy", local_id],
        "instance_level": instance_level,
    }


def _write_packet(root: Path, slug: str, pairs: list[dict]) -> Path:
    dpath = root / "analysis" / "core-reports" / slug
    dpath.mkdir(parents=True, exist_ok=True)
    fpath = dpath / "core_metric_report.json"
    fpath.write_text(json.dumps({"pairs": pairs}))
    return fpath


def test_single_attempt_is_not_flagged(tmp_path: Path) -> None:
    fpath = _write_packet(tmp_path, "solo", [_pair("local::exp::a", 0.99)])
    assert audit_packet(fpath, tol=1e-6) is None


def test_local_repeat_does_not_count_as_a_competing_attempt(tmp_path: Path) -> None:
    """A local_repeat is an intentional noise measurement, not a rival answer."""
    fpath = _write_packet(
        tmp_path,
        "with-repeat",
        [_pair("local::exp::a", 0.99), _pair("local::exp::b", 0.42, kind="local_repeat")],
    )
    assert audit_packet(fpath, tol=1e-6) is None


def test_disagreeing_attempts_are_material(tmp_path: Path) -> None:
    """The olmo-7b shape: a working attempt beside a collapsed one."""
    fpath = _write_packet(
        tmp_path,
        "collapsed-and-fixed",
        [_pair("local::exp::good", 0.99), _pair("local::exp::collapsed", 0.0)],
    )
    finding = audit_packet(fpath, tol=1e-6)
    assert finding is not None
    assert finding["severity"] == "MATERIAL"
    assert finding["n_attempts"] == 2
    assert finding["spread"] == 0.99


def test_agreeing_attempts_are_benign(tmp_path: Path) -> None:
    """Several attempts are harmless when any selection gives the same answer."""
    fpath = _write_packet(
        tmp_path,
        "agreeing",
        [_pair("local::exp::a", 0.99), _pair("local::exp::b", 0.99)],
    )
    finding = audit_packet(fpath, tol=1e-6)
    assert finding is not None
    assert finding["severity"] == "BENIGN"


def test_unscored_attempts_are_reported_not_silently_passed(tmp_path: Path) -> None:
    fpath = _write_packet(
        tmp_path,
        "unscored",
        [_pair("local::exp::a", None), _pair("local::exp::b", None)],
    )
    finding = audit_packet(fpath, tol=1e-6)
    assert finding is not None
    assert finding["severity"] == "UNSCORED"


def test_exit_code_fails_on_material_and_strict_fails_on_benign(tmp_path: Path) -> None:
    _write_packet(tmp_path, "ok", [_pair("local::exp::a", 0.99)])
    _write_packet(tmp_path, "benign", [_pair("local::exp::a", 0.99), _pair("local::exp::b", 0.99)])
    assert main([str(tmp_path)]) == 0
    assert main([str(tmp_path), "--strict"]) == 1

    _write_packet(tmp_path, "material", [_pair("local::exp::a", 0.99), _pair("local::exp::b", 0.10)])
    assert main([str(tmp_path)]) == 1


def test_finding_names_the_attempt_the_reporting_layer_would_pick(tmp_path: Path) -> None:
    """The lint's verdict and the rendered number must refer to the same attempt."""
    fpath = _write_packet(
        tmp_path,
        "collapsed-first",
        [_pair("local::exp::collapsed", 0.0), _pair("local::exp::rerun", 0.99)],
    )
    report = json.loads(fpath.read_text())
    report["components"] = [
        {"component_id": "local::exp::collapsed", "manifest_timestamp": "1"},
        {"component_id": "local::exp::rerun", "manifest_timestamp": "2"},
    ]
    fpath.write_text(json.dumps(report))

    finding = audit_packet(fpath, tol=1e-6)
    assert finding is not None
    assert finding["selection_rule"] == "latest_manifest_timestamp"
    assert finding["selected_agreement_at_zero"] == 0.99
    assert [row["selected"] for row in finding["attempts"]] == [False, True]


def test_summary_counts_every_packet_scanned(tmp_path: Path) -> None:
    _write_packet(tmp_path, "one", [_pair("local::exp::a", 0.99)])
    _write_packet(tmp_path, "two", [_pair("local::exp::a", 0.99), _pair("local::exp::b", 0.10)])
    result = audit_paths([tmp_path], tol=1e-6)
    assert result["n_packets"] == 2
    assert result["n_ambiguous"] == 1
    assert result["by_severity"] == {"MATERIAL": 1}

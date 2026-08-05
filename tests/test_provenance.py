"""Verifying that stored reports still describe the artifacts on disk."""
from __future__ import annotations

import json
from pathlib import Path

from eval_audit.cli.verify_provenance import main
from eval_audit.normalized.digests import component_digest
from eval_audit.reports.provenance import (
    Scope,
    benchmark_family,
    resolve_scope,
    verify_report,
    verify_root,
)

OFFICIAL_KEY = "mmlu:subject=anatomy,model=allenai/olmo-7b"


def _write_run(root: Path, name: str, *, scores: str = "1.0") -> Path:
    run_dpath = root / "runs" / name
    run_dpath.mkdir(parents=True, exist_ok=True)
    (run_dpath / "run_spec.json").write_text(json.dumps({"name": name}))
    (run_dpath / "stats.json").write_text(json.dumps([{"exact_match": scores}]))
    (run_dpath / "per_instance_stats.json").write_text(json.dumps([{"id": "i1", "v": scores}]))
    (run_dpath / "scenario_state.json").write_text(json.dumps({"text": "out"}))
    return run_dpath


def _component(component_id: str, run_dpath: Path, *, model: str = "allenai/olmo-7b") -> dict:
    return {
        "component_id": component_id,
        "artifact_format": "helm",
        "run_path": str(run_dpath),
        "model": model,
        "logical_run_key": OFFICIAL_KEY,
    }


def _write_packet(
    store: Path,
    slug: str,
    components: list[dict],
    *,
    with_digests: bool = True,
) -> Path:
    dpath = store / "analysis" / "core-reports" / slug
    dpath.mkdir(parents=True, exist_ok=True)
    report = {
        "packet_id": slug,
        "components": components,
        "code_identity": {"git_sha": "abc", "eval_audit_version": "0.0.0"},
    }
    if with_digests:
        report["component_digests"] = {
            component["component_id"]: component_digest(component) for component in components
        }
    fpath = dpath / "core_metric_report.json"
    fpath.write_text(json.dumps(report))
    return fpath


def test_unchanged_artifacts_match(tmp_path: Path) -> None:
    run_dpath = _write_run(tmp_path, "r1")
    _write_packet(tmp_path / "store", "p1", [_component("local::a", run_dpath)])

    result = verify_root(tmp_path / "store")
    assert result["n_packets"] == 1
    assert result["by_verdict"] == {"match": 1}


def test_changed_scores_are_drift(tmp_path: Path) -> None:
    """The report describes something that is no longer there."""
    run_dpath = _write_run(tmp_path, "r1")
    _write_packet(tmp_path / "store", "p1", [_component("local::a", run_dpath)])
    (run_dpath / "per_instance_stats.json").write_text(json.dumps([{"id": "i1", "v": "0.5"}]))

    result = verify_root(tmp_path / "store")
    assert result["by_verdict"] == {"drifted": 1}
    component = result["packets"][0]["components"][0]
    assert component["outcome"] == "drifted"
    assert component["recorded"] != component["actual"]


def test_changed_completions_are_drift_too(tmp_path: Path) -> None:
    run_dpath = _write_run(tmp_path, "r1")
    _write_packet(tmp_path / "store", "p1", [_component("local::a", run_dpath)])
    (run_dpath / "scenario_state.json").write_text(json.dumps({"text": "different"}))
    assert verify_root(tmp_path / "store")["by_verdict"] == {"drifted": 1}


def test_unrelated_sibling_file_is_not_drift(tmp_path: Path) -> None:
    """Only the named scoring inputs are hashed; logs must not raise an alarm."""
    run_dpath = _write_run(tmp_path, "r1")
    _write_packet(tmp_path / "store", "p1", [_component("local::a", run_dpath)])
    (run_dpath / "helm-run.log").write_text("noise\n")
    assert verify_root(tmp_path / "store")["by_verdict"] == {"match": 1}


def test_vanished_artifacts_are_missing(tmp_path: Path) -> None:
    run_dpath = _write_run(tmp_path, "r1")
    _write_packet(tmp_path / "store", "p1", [_component("local::a", run_dpath)])
    for fpath in run_dpath.iterdir():
        fpath.unlink()

    result = verify_root(tmp_path / "store")
    assert result["by_verdict"] == {"missing": 1}


def test_reports_without_digests_are_unhashed_not_verified(tmp_path: Path) -> None:
    """Every existing store is in this state; it must not read as 'verified'."""
    run_dpath = _write_run(tmp_path, "r1")
    _write_packet(tmp_path / "store", "p1", [_component("local::a", run_dpath)], with_digests=False)

    result = verify_root(tmp_path / "store")
    assert result["by_verdict"] == {"unhashed": 1}


def test_worst_component_decides_the_packet_verdict(tmp_path: Path) -> None:
    good = _write_run(tmp_path, "good")
    bad = _write_run(tmp_path, "bad")
    _write_packet(
        tmp_path / "store",
        "p1",
        [_component("local::a", good), _component("official::b", bad)],
    )
    (bad / "stats.json").write_text(json.dumps([{"exact_match": "9.9"}]))

    result = verify_root(tmp_path / "store")
    assert result["packets"][0]["verdict"] == "drifted"
    assert result["packets"][0]["outcomes"] == {"match": 1, "drifted": 1}


def test_scope_filters_by_store_and_model(tmp_path: Path) -> None:
    olmo = _write_run(tmp_path, "olmo")
    qwen = _write_run(tmp_path, "qwen")
    root = tmp_path / "root"
    _write_packet(root / "store-a", "p-olmo", [_component("local::a", olmo)])
    _write_packet(
        root / "store-b", "p-qwen", [_component("local::b", qwen, model="qwen/qwen1.5-7b")]
    )

    assert verify_root(root)["n_packets"] == 2
    assert verify_root(root, Scope(store="store-a"))["n_packets"] == 1
    assert verify_root(root, Scope(models=["qwen/qwen1.5-7b"]))["n_packets"] == 1
    assert verify_root(root, Scope(models=["nobody/nothing"]))["n_packets"] == 0


def test_resolve_scope_reports_what_stands_behind_the_packets(tmp_path: Path) -> None:
    run_dpath = _write_run(tmp_path, "r1")
    store = tmp_path / "store"
    _write_packet(store, "p1", [_component("local::a", run_dpath)])

    resolved = resolve_scope(store, Scope())
    assert resolved["n_packets"] == 1
    assert resolved["packet_ids"] == ["p1"]
    assert resolved["run_paths"] == [str(run_dpath)]
    assert resolved["models"] == ["allenai/olmo-7b"]
    assert resolved["benchmarks"] == ["mmlu"]
    assert resolved["digest_status"] == {"ok": 1}
    assert resolved["code_identities"] == [{"git_sha": "abc", "eval_audit_version": "0.0.0"}]


def test_resolve_scope_surfaces_more_than_one_build(tmp_path: Path) -> None:
    """A claim aggregating packets rendered by different builds is worth seeing."""
    store = tmp_path / "store"
    _write_packet(store, "p1", [_component("local::a", _write_run(tmp_path, "r1"))])
    second = json.loads((store / "analysis" / "core-reports" / "p1" / "core_metric_report.json").read_text())
    second["packet_id"] = "p2"
    second["code_identity"] = {"git_sha": "def", "eval_audit_version": "0.0.0"}
    dpath = store / "analysis" / "core-reports" / "p2"
    dpath.mkdir(parents=True)
    (dpath / "core_metric_report.json").write_text(json.dumps(second))

    assert len(resolve_scope(store, Scope())["code_identities"]) == 2


def test_unreadable_report_is_skipped_rather_than_fatal(tmp_path: Path) -> None:
    store = tmp_path / "store"
    dpath = store / "analysis" / "core-reports" / "broken"
    dpath.mkdir(parents=True)
    (dpath / "core_metric_report.json").write_text("{not json")
    assert verify_root(store)["n_packets"] == 0


def test_verify_report_of_a_packet_without_components(tmp_path: Path) -> None:
    assert verify_report({"packet_id": "empty"})["verdict"] == "match"


def test_benchmark_family_parsing() -> None:
    assert benchmark_family("mmlu:subject=anatomy,model=x") == "mmlu"
    assert benchmark_family("commonsense:dataset=openbookqa") == "commonsense"
    assert benchmark_family("bare") == "bare"


def test_exit_codes(tmp_path: Path) -> None:
    store = tmp_path / "store"
    run_dpath = _write_run(tmp_path, "r1")
    _write_packet(store, "clean", [_component("local::a", run_dpath)])
    assert main([str(store)]) == 0

    _write_packet(store, "nodigest", [_component("local::b", run_dpath)], with_digests=False)
    assert main([str(store)]) == 0, "unhashed packets must not fail by default"
    assert main([str(store), "--require-digests"]) == 1

    gone = _write_run(tmp_path, "gone")
    _write_packet(store, "missing", [_component("local::c", gone)])
    for fpath in gone.iterdir():
        fpath.unlink()
    assert main([str(store)]) == 1
    assert main([str(store), "--allow-missing"]) == 0

    (run_dpath / "stats.json").write_text(json.dumps([{"exact_match": "9.9"}]))
    assert main([str(store), "--allow-missing"]) == 1, "drift always fails"


def test_json_output_is_written(tmp_path: Path) -> None:
    store = tmp_path / "store"
    _write_packet(store, "p1", [_component("local::a", _write_run(tmp_path, "r1"))])
    out_fpath = tmp_path / "out" / "verify.json"
    assert main([str(store), "--json", str(out_fpath)]) == 0
    assert json.loads(out_fpath.read_text())["by_verdict"] == {"match": 1}

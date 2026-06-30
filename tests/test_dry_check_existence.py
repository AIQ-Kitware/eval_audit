"""Dry-check existence mode: validate a frozen exact-path manifest (§4.7).

Covers ``check_precomputed_discovery --manifest`` — it asserts each frozen
``run_spec_sources`` rel_path resolves to a readable ``run_spec.json`` (no token
discovery; NO_MATCH/AMBIGUOUS impossible), exiting nonzero when a frozen path is
missing.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from eval_audit.cli import check_precomputed_discovery as dc

_OFFICIAL = {
    "name": "mmlu:subject=anatomy,model=allenai_olmo-7b",
    "adapter_spec": {"model": "allenai/olmo-7b", "model_deployment": "together/olmo-7b"},
}
_REL = "mmlu/benchmark_output/runs/v1.1.0/mmlu:subject=anatomy,model=allenai_olmo-7b"


def _corpus(root: Path, rel: str = _REL) -> None:
    d = root / rel
    d.mkdir(parents=True)
    (d / "run_spec.json").write_text(json.dumps(_OFFICIAL))


def _manifest(tmp_path: Path, root: Path, sources: list[dict]) -> Path:
    doc = {
        "experiment_name": "x",
        "from_run_spec": True,
        "precomputed_root": str(root),
        "run_spec_sources": sources,
    }
    p = tmp_path / "manifest.yaml"
    p.write_text(yaml.safe_dump(doc))
    return p


def test_all_frozen_paths_ok(tmp_path: Path, capsys) -> None:
    root = tmp_path / "corpus"
    _corpus(root)
    mf = _manifest(
        tmp_path, root,
        [{"run_entry": "mmlu:subject=anatomy,model=allenai/olmo-7b",
          "rel_path": _REL, "model_deployment": "vllm/allenai-olmo-7b"}],
    )
    rc = dc.main(["--manifest", str(mf), "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["summary"] == {"OK": 1, "MISSING": 0}
    assert out["entries"][0]["status"] == "OK"
    # reports the OFFICIAL deployment (the rewrite "from")
    assert out["entries"][0]["official_deployment"] == "together/olmo-7b"


def test_missing_frozen_path_exits_nonzero(tmp_path: Path, capsys) -> None:
    root = tmp_path / "corpus"
    _corpus(root)  # only _REL exists
    mf = _manifest(
        tmp_path, root,
        [
            {"run_entry": "ok", "rel_path": _REL},
            {"run_entry": "bad", "rel_path": "mmlu/benchmark_output/runs/v1.1.0/does-not-exist"},
        ],
    )
    rc = dc.main(["--manifest", str(mf), "--json"])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["summary"] == {"OK": 1, "MISSING": 1}
    bad = next(e for e in out["entries"] if e["run_entry"] == "bad")
    assert bad["status"] == "MISSING"
    assert "does-not-exist" in bad["error"]


def test_manifest_without_sources_is_error(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    _corpus(root)
    mf = _manifest(tmp_path, root, [])
    with pytest.raises(SystemExit, match="run_spec_sources"):
        dc.main(["--manifest", str(mf)])


def test_manifest_without_precomputed_root_is_error(tmp_path: Path) -> None:
    doc = {"experiment_name": "x", "run_spec_sources": [{"run_entry": "a", "rel_path": _REL}]}
    p = tmp_path / "m.yaml"
    p.write_text(yaml.safe_dump(doc))
    with pytest.raises(SystemExit, match="precomputed_root"):
        dc.main(["--manifest", str(p)])


def test_manifest_excludes_preset(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    _corpus(root)
    mf = _manifest(tmp_path, root, [{"run_entry": "a", "rel_path": _REL}])
    with pytest.raises(SystemExit, match="cannot be combined"):
        dc.main(["--manifest", str(mf), "--preset", "allenai-olmo-7b-mmlu"])

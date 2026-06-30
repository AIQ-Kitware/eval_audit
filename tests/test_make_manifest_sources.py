"""make-manifest --run-spec-sources-fpath: producing exact-path replay manifests.

Covers ``eval_audit/manifests/builders.py`` (rel-path plan §4.5 schema part):
loading + validating run_spec sources, implying --from-run-spec, requiring
--precomputed-root, and keeping run_spec_sources aligned with run_entries through
filtering.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from eval_audit.infra.api import dump_yaml
from eval_audit.manifests import builders

_SOURCES = [
    {
        "run_entry": "mmlu:subject=anatomy,model=allenai/olmo-7b",
        "rel_path": "lite/benchmark_output/runs/v1.0.0/mmlu:subject=anatomy,model=allenai_olmo-7b",
        "model_deployment": "vllm/allenai-olmo-7b",
        "lease_endpoint": "olmo-7b-ep",
    },
    {
        "run_entry": "mmlu:subject=astronomy,model=allenai/olmo-7b",
        "rel_path": "lite/benchmark_output/runs/v1.0.0/mmlu:subject=astronomy,model=allenai_olmo-7b",
        "model_deployment": "vllm/allenai-olmo-7b",
    },
]


def _run(tmp_path: Path, sources: list[dict], *extra: str) -> dict:
    src = tmp_path / "sources.yaml"
    src.write_text(dump_yaml(sources))
    out = tmp_path / "manifest.yaml"
    builders.main(
        [
            "--output", str(out),
            "--experiment-name", "olmo",
            "--suite", "olmo",
            "--run-spec-sources-fpath", str(src),
            "--max-eval-instances", "5",
            *extra,
        ]
    )
    return yaml.safe_load(out.read_text())


def test_sources_manifest_implies_from_run_spec(tmp_path: Path) -> None:
    m = _run(tmp_path, _SOURCES, "--precomputed-root", "/data/crfm-helm-public")
    assert m["from_run_spec"] is True
    assert m["precomputed_root"] == "/data/crfm-helm-public"
    assert len(m["run_spec_sources"]) == 2
    first = m["run_spec_sources"][0]
    assert first["rel_path"].endswith("model=allenai_olmo-7b")
    assert first["model_deployment"] == "vllm/allenai-olmo-7b"
    assert first["lease_endpoint"] == "olmo-7b-ep"
    # the second source carries no lease/None fields (dropped on serialize)
    assert "lease_endpoint" not in m["run_spec_sources"][1]
    # labels become run_entries so the selection report + alignment work
    assert m["run_entries"] == [s["run_entry"] for s in _SOURCES]


def test_sources_require_precomputed_root(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="precomputed-root"):
        _run(tmp_path, _SOURCES)  # no --precomputed-root


def test_sources_stay_aligned_through_limit(tmp_path: Path) -> None:
    m = _run(
        tmp_path, _SOURCES, "--precomputed-root", "/data/crfm-helm-public",
        "--limit", "1",
    )
    assert len(m["run_spec_sources"]) == 1
    assert len(m["run_entries"]) == 1
    assert m["run_spec_sources"][0]["run_entry"] == m["run_entries"][0]


def test_sources_filtered_by_include_pattern(tmp_path: Path) -> None:
    m = _run(
        tmp_path, _SOURCES, "--precomputed-root", "/data/crfm-helm-public",
        "--include-pattern", "*anatomy*",
    )
    assert [s["run_entry"] for s in m["run_spec_sources"]] == [_SOURCES[0]["run_entry"]]


def test_sources_missing_required_key_is_rejected(tmp_path: Path) -> None:
    bad = [{"run_entry": "x"}]  # no rel_path
    with pytest.raises((ValueError, SystemExit)):
        _run(tmp_path, bad, "--precomputed-root", "/data/crfm-helm-public")

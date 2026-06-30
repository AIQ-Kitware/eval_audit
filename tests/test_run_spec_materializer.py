"""Host-side resolver + materializer for ``(public_root, relative_path)`` replay.

Covers ``eval_audit/manifests/run_spec_materializer.py`` (plan §4.1): exact-path
resolution, the raw-JSON no-drift substitution guarantee, the provenance sidecar,
determinism, and loud failure on a bad address.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval_audit.manifests.run_spec_materializer import (
    RunSpecSource,
    materialize_run_spec,
    materialize_run_specs,
    resolve_official_run_spec,
)


_OFFICIAL_SPEC = {
    "name": "mmlu:subject=anatomy,method=multiple_choice_joint,model=allenai_olmo-7b",
    "scenario_spec": {
        "class_name": "helm.benchmark.scenarios.mmlu_scenario.MMLUScenario",
        "args": {"subject": "anatomy"},
    },
    "adapter_spec": {
        "method": "multiple_choice_joint",
        "model": "allenai/olmo-7b",
        "model_deployment": "together/olmo-7b",
        "max_eval_instances": 1000,
        "temperature": 0.0,
        "global_prefix": "",
    },
    "metric_specs": [{"class_name": "helm.benchmark.metrics.basic_metrics.BasicMetric"}],
    "data_augmenter_spec": {"should_augment_train_instances": False},
    "groups": ["mmlu"],
}


def _write_official(root: Path, rel_dir: str) -> Path:
    run_dir = root / rel_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run_spec.json").write_text(json.dumps(_OFFICIAL_SPEC, indent=2))
    return run_dir


_REL_DIR = "lite/benchmark_output/runs/v1.0.0/mmlu:subject=anatomy,model=allenai_olmo-7b"


def test_resolve_accepts_dir_or_file(tmp_path: Path) -> None:
    _write_official(tmp_path, _REL_DIR)
    # rel_path naming the run dir
    p1 = resolve_official_run_spec(tmp_path, _REL_DIR)
    assert p1 == tmp_path / _REL_DIR / "run_spec.json"
    # rel_path naming the run_spec.json itself
    p2 = resolve_official_run_spec(tmp_path, _REL_DIR + "/run_spec.json")
    assert p2 == p1


def test_resolve_missing_is_loud_and_names_path(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError) as exc:
        resolve_official_run_spec(tmp_path, "does/not/exist")
    msg = str(exc.value)
    assert "does/not/exist" in msg and "run_spec.json" in msg


def test_substitution_changes_only_the_two_fields(tmp_path: Path) -> None:
    """The no-drift guarantee: only adapter_spec.{model_deployment,max_eval_instances}."""
    _write_official(tmp_path, _REL_DIR)
    staging = tmp_path / "staging"
    source = RunSpecSource(
        run_entry="mmlu:subject=anatomy,model=allenai/olmo-7b",
        rel_path=_REL_DIR,
        model_deployment="vllm/allenai-olmo-7b",
        max_eval_instances=10,
    )
    result = materialize_run_spec(
        source, precomputed_root=tmp_path, staging_dir=staging
    )

    materialized = json.loads(Path(result.run_spec_json).read_text())

    # adapter_spec: exactly the two fields differ; everything else identical.
    off_ad = dict(_OFFICIAL_SPEC["adapter_spec"])
    new_ad = dict(materialized["adapter_spec"])
    assert new_ad["model_deployment"] == "vllm/allenai-olmo-7b"
    assert new_ad["max_eval_instances"] == 10
    assert new_ad["model"] == off_ad["model"]  # model NEVER touched
    off_rest = {k: v for k, v in off_ad.items() if k not in ("model_deployment", "max_eval_instances")}
    new_rest = {k: v for k, v in new_ad.items() if k not in ("model_deployment", "max_eval_instances")}
    assert new_rest == off_rest

    # every NON-adapter_spec top-level key is byte-for-byte (structurally) identical
    for key, value in _OFFICIAL_SPEC.items():
        if key == "adapter_spec":
            continue
        assert materialized[key] == value, f"unexpected drift in {key!r}"
    # the run name is preserved (model not rewritten) => downstream pairing intact
    assert materialized["name"] == _OFFICIAL_SPEC["name"]


def test_substitutions_record_and_sidecar(tmp_path: Path) -> None:
    _write_official(tmp_path, _REL_DIR)
    staging = tmp_path / "staging"
    source = RunSpecSource(
        run_entry="mmlu:...,model=allenai/olmo-7b",
        rel_path=_REL_DIR,
        model_deployment="vllm/allenai-olmo-7b",
        lease_endpoint="catalog/olmo-7b",
        max_eval_instances=10,
    )
    result = materialize_run_spec(source, precomputed_root=tmp_path, staging_dir=staging)

    assert result.substitutions["model_deployment"] == {
        "from": "together/olmo-7b",
        "to": "vllm/allenai-olmo-7b",
    }
    assert result.substitutions["max_eval_instances"] == {"from": 1000, "to": 10}
    assert result.lease_endpoint == "catalog/olmo-7b"

    sidecar = json.loads((Path(result.run_spec_json).parent / "materialization.json").read_text())
    assert sidecar["official_run_spec_json"] == result.official_run_spec_json
    assert sidecar["rel_path"] == _REL_DIR
    assert sidecar["substitutions"] == result.substitutions
    assert sidecar["lease_endpoint"] == "catalog/olmo-7b"


def test_no_op_substitution_records_nothing(tmp_path: Path) -> None:
    """Rewriting to the official value (or omitting it) records no substitution."""
    _write_official(tmp_path, _REL_DIR)
    staging = tmp_path / "staging"
    # model_deployment equal to official; max_eval_instances omitted + no default
    source = RunSpecSource(
        run_entry="x", rel_path=_REL_DIR, model_deployment="together/olmo-7b"
    )
    result = materialize_run_spec(source, precomputed_root=tmp_path, staging_dir=staging)
    assert result.substitutions == {}
    materialized = json.loads(Path(result.run_spec_json).read_text())
    assert materialized["adapter_spec"] == _OFFICIAL_SPEC["adapter_spec"]


def test_default_max_eval_instances_applies_when_source_unset(tmp_path: Path) -> None:
    _write_official(tmp_path, _REL_DIR)
    staging = tmp_path / "staging"
    source = RunSpecSource(run_entry="x", rel_path=_REL_DIR)
    result = materialize_run_spec(
        source, precomputed_root=tmp_path, staging_dir=staging,
        default_max_eval_instances=25,
    )
    assert result.substitutions["max_eval_instances"] == {"from": 1000, "to": 25}
    # per-run override wins over the default
    source2 = RunSpecSource(run_entry="y", rel_path=_REL_DIR, max_eval_instances=5)
    result2 = materialize_run_spec(
        source2, precomputed_root=tmp_path, staging_dir=staging,
        default_max_eval_instances=25,
    )
    assert result2.substitutions["max_eval_instances"]["to"] == 5


def test_materialization_is_deterministic(tmp_path: Path) -> None:
    _write_official(tmp_path, _REL_DIR)
    staging = tmp_path / "staging"
    source = RunSpecSource(
        run_entry="x", rel_path=_REL_DIR, model_deployment="vllm/allenai-olmo-7b",
        max_eval_instances=10,
    )
    r1 = materialize_run_spec(source, precomputed_root=tmp_path, staging_dir=staging)
    bytes1 = Path(r1.run_spec_json).read_bytes()
    r2 = materialize_run_spec(source, precomputed_root=tmp_path, staging_dir=staging)
    assert r1.run_spec_json == r2.run_spec_json  # same deterministic staging id
    assert Path(r2.run_spec_json).read_bytes() == bytes1  # byte-identical


def test_distinct_deployments_do_not_collide(tmp_path: Path) -> None:
    _write_official(tmp_path, _REL_DIR)
    staging = tmp_path / "staging"
    a = RunSpecSource(run_entry="x", rel_path=_REL_DIR, model_deployment="vllm/a")
    b = RunSpecSource(run_entry="x", rel_path=_REL_DIR, model_deployment="vllm/b")
    [ra, rb] = materialize_run_specs(
        [a, b], precomputed_root=tmp_path, staging_dir=staging
    )
    assert ra.run_spec_json != rb.run_spec_json


def test_missing_adapter_spec_is_an_error(tmp_path: Path) -> None:
    run_dir = tmp_path / _REL_DIR
    run_dir.mkdir(parents=True)
    (run_dir / "run_spec.json").write_text(json.dumps({"name": "x"}))
    with pytest.raises(ValueError, match="adapter_spec"):
        materialize_run_spec(
            RunSpecSource(run_entry="x", rel_path=_REL_DIR, model_deployment="vllm/a"),
            precomputed_root=tmp_path, staging_dir=tmp_path / "staging",
        )

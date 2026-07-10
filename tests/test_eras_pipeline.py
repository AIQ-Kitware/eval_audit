"""Era manifest -> bridge pipeline selection + era<->image guard (commit 5).

Uses the real docker/eras.yaml keys (helm-v0.2.4 / helm-v0.3.0). No docker
daemon required: a digest-pinned image short-circuits digest resolution, and the
era<->image label guard is exercised via a monkeypatched image_label.

See docs/planning/era-pinned-helm-containers-plan.md.
"""
from __future__ import annotations

import pytest

pytest.importorskip("kwdagger")

from eval_audit.integrations import kwdagger_bridge as kb
from eval_audit.integrations.docker_provenance import ResolvedImage
from eval_audit.integrations.kwdagger_bridge import build_schedule_params
from eval_audit.manifests.run_spec_materializer import MaterializedRunSpec

_FAKE_IMAGE = ResolvedImage(
    requested="era-img:dev",
    run_ref="era-img@sha256:" + "a" * 64,
    digest="sha256:" + "a" * 64,
    digest_kind="repo_digest",
    pinned=True,
)


def _era_manifest():
    return {
        "experiment_name": "era-exp",
        "from_run_spec": True,
        "run_entries": [],
        "max_eval_instances": "official",
        "suite": "era-smoke",
        "precomputed_root": "/data/crfm-helm-public",
        "era": "helm-v0.2.4",
    }


def _materialized(n: int):
    return [
        MaterializedRunSpec(
            run_entry=f"babi_qa:task={i},model=eleutherai/pythia-6.9b",
            run_spec_json=f"/stage/run{i}/run_spec.json",
            official_run_spec_json=f"/data/.../run{i}/run_spec.json",
            rel_path=f"classic/.../run{i}",
            lease_endpoint=None,
            substitutions={},
        )
        for i in range(n)
    ]


def test_era_exact_path_selects_era_pipeline():
    params = build_schedule_params(
        _era_manifest(),
        resolved_image=_FAKE_IMAGE,
        materialized_runs=_materialized(2),
        staging_root="/exp/staging",
    )
    assert params["pipeline"] == kb._DOCKER_FROM_SPEC_ERA_PIPELINE
    assert "era" in params["pipeline"]
    assert len(params["matrix"]["submatrices"]) == 2


def test_modern_exact_path_selects_modern_pipeline():
    manifest = _era_manifest()
    manifest.pop("era")
    params = build_schedule_params(
        manifest,
        resolved_image=_FAKE_IMAGE,
        materialized_runs=_materialized(1),
        staging_root="/exp/staging",
    )
    assert params["pipeline"] == kb._DOCKER_FROM_SPEC_PIPELINE


def test_era_without_exact_path_is_rejected():
    manifest = _era_manifest()
    manifest["max_eval_instances"] = 10  # run-entry path needs a numeric cap
    manifest["run_entries"] = ["babi_qa:task=1,model=eleutherai/pythia-6.9b"]
    with pytest.raises(ValueError, match="exact-path"):
        build_schedule_params(
            manifest,
            resolved_image=_FAKE_IMAGE,
            materialized_runs=None,  # discovery/run-entry path
        )


def test_unknown_era_key_rejected():
    manifest = _era_manifest()
    manifest["era"] = "helm-v9.9.9"
    with pytest.raises(ValueError, match="unknown era"):
        build_schedule_params(
            manifest,
            resolved_image=_FAKE_IMAGE,
            materialized_runs=_materialized(1),
            staging_root="/exp/staging",
        )


def test_era_image_guard_matches(monkeypatch):
    monkeypatch.setattr(kb, "image_label", lambda *a, **k: "helm-v0.2.4")
    monkeypatch.setattr(kb, "resolve_image_digest", lambda *a, **k: _FAKE_IMAGE)
    monkeypatch.setattr(kb, "runtime_version", lambda *a, **k: "test")
    manifest = {"container_image": "era-img:dev", "era": "helm-v0.2.4"}
    resolved, prov = kb._prepare_container_execution(manifest, "era-exp")
    assert resolved is _FAKE_IMAGE
    assert prov["era"] == "helm-v0.2.4"
    assert prov["image_era_label"] == "helm-v0.2.4"


def test_era_image_guard_mismatch_raises(monkeypatch):
    monkeypatch.setattr(kb, "image_label", lambda *a, **k: "helm-v0.3.0")
    monkeypatch.setattr(kb, "resolve_image_digest", lambda *a, **k: _FAKE_IMAGE)
    monkeypatch.setattr(kb, "runtime_version", lambda *a, **k: "test")
    manifest = {"container_image": "era-img:dev", "era": "helm-v0.2.4"}
    with pytest.raises(ValueError, match="era<->image mismatch"):
        kb._prepare_container_execution(manifest, "era-exp")


def test_modern_manifest_on_era_image_raises(monkeypatch):
    monkeypatch.setattr(kb, "image_label", lambda *a, **k: "helm-v0.2.4")
    monkeypatch.setattr(kb, "resolve_image_digest", lambda *a, **k: _FAKE_IMAGE)
    monkeypatch.setattr(kb, "runtime_version", lambda *a, **k: "test")
    manifest = {"container_image": "era-img:dev"}  # no era
    with pytest.raises(ValueError, match="era<->image mismatch"):
        kb._prepare_container_execution(manifest, "modern-exp")

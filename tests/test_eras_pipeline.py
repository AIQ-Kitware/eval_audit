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

_FAKE_IMAGE_UNPINNED = ResolvedImage(
    requested="modern-img:dev",
    run_ref="sha256:" + "b" * 64,  # local image id (no registry digest)
    digest="sha256:" + "b" * 64,
    digest_kind="image_id",
    pinned=False,
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
    seen = {}

    def fake_label(image, key, **k):
        seen["image"] = image
        seen["pull_if_missing"] = k.get("pull_if_missing")
        return "helm-v0.2.4"

    monkeypatch.setattr(kb, "image_label", fake_label)
    monkeypatch.setattr(kb, "resolve_image_digest", lambda *a, **k: _FAKE_IMAGE)
    monkeypatch.setattr(kb, "runtime_version", lambda *a, **k: "test")
    manifest = {"container_image": "era-img:dev", "era": "helm-v0.2.4"}
    resolved, prov = kb._prepare_container_execution(manifest, "era-exp")
    assert resolved is _FAKE_IMAGE
    assert prov["era"] == "helm-v0.2.4"
    assert prov["image_era_label"] == "helm-v0.2.4"
    # Finding 4: label is read from the immutable run_ref, and the era path pulls
    # a not-present (digest-pinned) image before deciding.
    assert seen["image"] == _FAKE_IMAGE.run_ref
    assert seen["pull_if_missing"] is True


def test_era_image_guard_mismatch_raises(monkeypatch):
    monkeypatch.setattr(kb, "image_label", lambda *a, **k: "helm-v0.3.0")
    monkeypatch.setattr(kb, "resolve_image_digest", lambda *a, **k: _FAKE_IMAGE)
    monkeypatch.setattr(kb, "runtime_version", lambda *a, **k: "test")
    manifest = {"container_image": "era-img:dev", "era": "helm-v0.2.4"}
    with pytest.raises(ValueError, match="era<->image mismatch"):
        kb._prepare_container_execution(manifest, "era-exp")


def test_era_image_not_present_errors_actionably(monkeypatch):
    """Finding 4: an era image that can't be inspected surfaces image_label's
    actionable RuntimeError, NOT a misleading 'carries org.aiq.era=None' mismatch."""
    def boom(*a, **k):
        raise RuntimeError("cannot inspect image ... not present locally")

    monkeypatch.setattr(kb, "image_label", boom)
    monkeypatch.setattr(kb, "resolve_image_digest", lambda *a, **k: _FAKE_IMAGE)
    monkeypatch.setattr(kb, "runtime_version", lambda *a, **k: "test")
    manifest = {"container_image": "era-img:dev", "era": "helm-v0.2.4"}
    with pytest.raises(RuntimeError, match="not present locally"):
        kb._prepare_container_execution(manifest, "era-exp")


def test_modern_manifest_on_era_image_raises(monkeypatch):
    """A modern manifest on an UNPINNED image still gets the guard (the image is
    local post-resolve, so the label read is cheap)."""
    monkeypatch.setattr(kb, "image_label", lambda *a, **k: "helm-v0.2.4")
    monkeypatch.setattr(kb, "resolve_image_digest", lambda *a, **k: _FAKE_IMAGE_UNPINNED)
    monkeypatch.setattr(kb, "runtime_version", lambda *a, **k: "test")
    manifest = {"container_image": "modern-img:dev"}  # no era
    with pytest.raises(ValueError, match="era<->image mismatch"):
        kb._prepare_container_execution(manifest, "modern-exp")


def test_modern_pinned_manifest_skips_label_read(monkeypatch):
    """Finding 4: a pinned MODERN manifest performs NO label read — preserving the
    old no-runtime-needed behavior for an already-pinned image (which may not be
    present locally)."""
    def fail_if_called(*a, **k):
        raise AssertionError("image_label must not be called for a pinned modern manifest")

    monkeypatch.setattr(kb, "image_label", fail_if_called)
    monkeypatch.setattr(kb, "resolve_image_digest", lambda *a, **k: _FAKE_IMAGE)
    monkeypatch.setattr(kb, "runtime_version", lambda *a, **k: "test")
    manifest = {"container_image": "modern-img@sha256:" + "c" * 64}  # no era, pinned
    resolved, prov = kb._prepare_container_execution(manifest, "modern-exp")
    assert prov["era"] is None
    assert prov["image_era_label"] is None

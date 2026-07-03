"""Tests for the faithful-replay (``from_run_spec``) execution path.

Covers the eval_audit-side wiring (Changes 2-5 of
``docs/planning/run-from-run-spec-json-plan.md``): the from-spec docker node +
factory, the bridge pipeline selection, the manifest schema field, and the
``eval-audit-make-manifest`` CLI flags. None of this needs ``helm`` installed —
the in-container replay CLI lives in aiq-magnet and is exercised separately.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("kwdagger")

from eval_audit.integrations import kwdagger_bridge
from eval_audit.manifests import builders
from eval_audit.manifests.models import ManifestSpec
from eval_audit.pipelines.helm_docker_pipeline import (
    MaterializeHelmRunDockerNode,
    MaterializeHelmRunFromSpecDockerNode,
    helm_single_run_from_spec_docker_pipeline,
)

PINNED = "ghcr.io/aiq-kitware/eval-audit-helm-runner@sha256:" + "a" * 64


def _render_from_spec(config: dict, tmp_path: Path) -> str:
    pipe = helm_single_run_from_spec_docker_pipeline()
    pipe.configure(config, root_dpath=str(tmp_path / "results"))
    return pipe.node_dict["materialize_helm_run"].command


# --------------------------------------------------------------------------
# Change 2 — docker node + factory
# --------------------------------------------------------------------------


def test_from_spec_node_swaps_executable_and_adds_deployment_param():
    node = MaterializeHelmRunFromSpecDockerNode()
    # Identity / completion contract inherited unchanged.
    assert node.name == "helm"
    assert node.primary_out_key == "done_fname"
    assert node.out_paths == MaterializeHelmRunDockerNode.out_paths
    # The from-spec node extends the run-entry node's algo identity with:
    # model_deployment (the optional deployment-rewrite target, default None =>
    # pure by-name; from-spec-deployment-rewrite-plan.md Change 3), run_spec_json
    # (the materialized exact-path spec this run replays; default None on the
    # discovery path), and precomputed_root (P1-21: the corpus dir supplying the
    # official run_spec.json in discovery mode — recipe identity, promoted off
    # the base node's identity-neutral perf param). See
    # run-from-relative-path-plan.md §4.3.
    assert node.algo_params == {
        **MaterializeHelmRunDockerNode.algo_params,
        "model_deployment": None,
        "run_spec_json": None,
        "precomputed_root": None,
    }
    # P1-21: precomputed_root is algo identity here and must NOT also live in
    # perf (where it was identity-neutral and reused stale results across roots).
    assert "precomputed_root" in node.algo_params
    assert "precomputed_root" not in node.perf_params
    assert "precomputed_root" in MaterializeHelmRunDockerNode.perf_params
    # model is never an algo param — the model identity always replays verbatim.
    assert "model" not in node.algo_params
    assert node.algo_params["model_deployment"] is None
    # Only the inner executable differs.
    assert node.executable.endswith("materialize_helm_run_from_spec")


def test_from_spec_docker_command_uses_from_spec_cli(tmp_path: Path):
    cmd = _render_from_spec(
        {
            "helm.run_entry": "mmlu:subject=philosophy,model=openai/gpt2",
            "helm.suite": "audit-smoke",
            "helm.max_eval_instances": 2,
            "helm.precomputed_root": "/data/crfm-helm-public",
            "helm.container_image": PINNED,
            "helm.container_shm_size": "32g",
        },
        tmp_path,
    )
    # Same docker wrapper as the run-entry node (incl. the P2 pre-clean +
    # --name container-leak guard).
    assert cmd.startswith("docker rm -f eval-audit-helm-")
    assert "docker run --rm --name eval-audit-helm-" in cmd
    assert PINNED in cmd
    # The recipe source is bind-mounted read-only at its same path.
    assert "-v /data/crfm-helm-public:/data/crfm-helm-public:ro" in cmd
    # Inner command targets the from-spec replay CLI, not the run-entry one.
    assert "magnet.backends.helm.cli.materialize_helm_run_from_spec" in cmd
    assert "--run_entry=mmlu:subject=philosophy,model=openai/gpt2" in cmd
    assert "--precomputed_root=/data/crfm-helm-public" in cmd
    # Container knobs never leak into the inner CLI.
    assert "--container_image" not in cmd
    assert "--container_shm_size" not in cmd


# --------------------------------------------------------------------------
# Change 3 — bridge pipeline selection
# --------------------------------------------------------------------------


def _base_manifest(**extra) -> dict:
    return {
        "run_entries": ["mmlu:subject=philosophy,model=openai/gpt2"],
        "max_eval_instances": 2,
        "suite": "audit-smoke",
        **extra,
    }


def _stub_image():
    return SimpleNamespace(run_ref=PINNED)


def test_bridge_default_selects_run_entry_pipeline():
    params = kwdagger_bridge.build_schedule_params(
        _base_manifest(), resolved_image=_stub_image()
    )
    assert params["pipeline"] == kwdagger_bridge._DOCKER_PIPELINE


def test_bridge_from_run_spec_selects_replay_pipeline():
    params = kwdagger_bridge.build_schedule_params(
        _base_manifest(from_run_spec=True, precomputed_root="/data/crfm-helm-public"),
        resolved_image=_stub_image(),
    )
    assert params["pipeline"] == kwdagger_bridge._DOCKER_FROM_SPEC_PIPELINE
    assert params["matrix"]["helm.precomputed_root"] == "/data/crfm-helm-public"


def test_bridge_from_run_spec_requires_precomputed_root():
    with pytest.raises(ValueError, match="precomputed_root"):
        kwdagger_bridge.build_schedule_params(
            _base_manifest(from_run_spec=True), resolved_image=_stub_image()
        )


def test_bridge_from_run_spec_still_requires_container_image():
    # The from-spec selection sits *after* the mandatory-containerization raise,
    # so it inherits that guard for free (no image => reject even with a recipe).
    with pytest.raises(ValueError, match="containerized execution is required"):
        kwdagger_bridge.build_schedule_params(
            _base_manifest(from_run_spec=True, precomputed_root="/x"),
            resolved_image=None,
        )


# --------------------------------------------------------------------------
# Change 4 — manifest schema field
# --------------------------------------------------------------------------


def test_manifest_from_run_spec_defaults_off():
    spec = ManifestSpec(
        experiment_name="x",
        description="d",
        run_entries=["mmlu:subject=philosophy,model=openai/gpt2"],
        suite="s",
        max_eval_instances=2,
    )
    data = spec.to_dict()
    assert data["from_run_spec"] is False
    assert data["precomputed_root"] is None


# --------------------------------------------------------------------------
# Change 5 — eval-audit-make-manifest CLI flags
# --------------------------------------------------------------------------


def _write_run_specs(tmp_path: Path) -> Path:
    fpath = tmp_path / "run_specs.yaml"
    fpath.write_text(
        "- mmlu:subject=philosophy,model=openai/gpt2\n"
        "- mmlu:subject=anatomy,model=openai/gpt2\n"
    )
    return fpath


def _run_builder(tmp_path: Path, *flags: str) -> dict:
    import kwutil

    out = tmp_path / "manifest.yaml"
    builders.main(
        [
            "--output",
            str(out),
            "--selection-output",
            str(tmp_path / "selection.yaml"),
            "--run-specs-fpath",
            str(_write_run_specs(tmp_path)),
            "--experiment-name",
            "demo",
            "--suite",
            "audit-smoke",
            "--max-eval-instances",
            "2",
            *flags,
        ]
    )
    return kwutil.Yaml.load(out)


def test_make_manifest_from_run_spec_flags(tmp_path: Path):
    manifest = _run_builder(
        tmp_path, "--from-run-spec", "--precomputed-root", "/data/crfm-helm-public"
    )
    assert manifest["from_run_spec"] is True
    assert manifest["precomputed_root"] == "/data/crfm-helm-public"


def test_make_manifest_default_off(tmp_path: Path):
    manifest = _run_builder(tmp_path)
    assert manifest["from_run_spec"] is False
    assert manifest["precomputed_root"] is None


def test_make_manifest_from_run_spec_requires_precomputed_root(tmp_path: Path):
    with pytest.raises(SystemExit):
        _run_builder(tmp_path, "--from-run-spec")

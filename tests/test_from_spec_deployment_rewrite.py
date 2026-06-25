"""Tests for the from-spec deployment rewrite.

Covers ``docs/planning/from-spec-deployment-rewrite-plan.md``: the comparability
PROOF (rewriting adapter_spec.model_deployment to the local name un-masks the
engine substitution), the manifest -> bridge -> node plumbing that threads the
rewrite target, and the §3 invariant (each from-spec manifest's
``model_deployment`` names a deployment registered in its
``model_deployments.yaml``).

The exporter-side wiring (vLLM bundle binds the native name + emits the manifest
field; the hf override registers a local name) is covered in
``test_e2e_from_spec_bundle.py``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from eval_audit.infra.api import dump_yaml
from eval_audit.manifests import builders
from eval_audit.normalized.diff import facts_semantic_inputs
from eval_audit.normalized.recipe_facts import RecipeFacts

REPO = Path(__file__).resolve().parents[1]
MANIFEST_DIR = REPO / "dev" / "e2e-tests" / "manifests"
PHI2_RUN_ENTRY = (
    "mmlu:subject=philosophy,method=multiple_choice_joint,"
    "model=microsoft/phi-2,eval_split=test"
)


def _facts(deployment: str) -> RecipeFacts:
    # Everything matches the official EXCEPT the deployment — exactly the
    # faithful-replay situation (same scenario, same model, same run name).
    return RecipeFacts(
        source="sidecar",
        run_spec_name="mmlu:subject=philosophy,model=microsoft_phi-2",
        model="microsoft/phi-2",
        model_deployment=deployment,
        scenario_class="helm.benchmark.scenarios.mmlu_scenario.MMLUScenario",
    )


# --------------------------------------------------------------------------
# The comparability PROOF (the central claim of the plan)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("local", ["vllm/phi-2-local", "huggingface/phi-2-local"])
def test_deployment_rewrite_unmasks_substitution(local: str):
    # After the rewrite the local run records its LOCAL deployment, so the
    # comparison's deployment fact differs from the official together/phi-2 and
    # the engine substitution is visible (same_deployment=no).
    out = facts_semantic_inputs(_facts("together/phi-2"), _facts(local))
    sem = out["run_spec_semantic"]
    assert sem["deployment_changed"] is True
    assert sem["deployment"] == {"a": "together/phi-2", "b": local}
    assert sem["deployment_paths"] == ["adapter_spec.model_deployment"]
    # The model identity / run name still match, so only the endpoint label drifts.
    assert out["run_spec_name_ok"] is True
    assert out["scenario_semantic"]["semantic_ok"] is True


def test_pure_by_name_replay_masks_substitution():
    # The bug this feature exists to fix: a pure by-name replay records the
    # OFFICIAL deployment on the local run, so the comparison reports
    # same_deployment=yes and the engine substitution is invisible. This pins the
    # exact behavior the rewrite corrects (and that the test above proves gone).
    out = facts_semantic_inputs(_facts("together/phi-2"), _facts("together/phi-2"))
    assert out["run_spec_semantic"]["deployment_changed"] is False
    assert out["run_spec_semantic"]["deployment_paths"] == []


# --------------------------------------------------------------------------
# Plumbing: manifest -> bridge -> node renders --model_deployment
# --------------------------------------------------------------------------


def _make_manifest(tmp_path: Path, *, extra_argv: list[str]) -> dict:
    rs = tmp_path / "run_specs.yaml"
    rs.write_text(dump_yaml([PHI2_RUN_ENTRY]))
    out = tmp_path / "manifest.yaml"
    builders.main(
        [
            "--output", str(out),
            "--experiment-name", "probe",
            "--suite", "probe",
            "--run-specs-fpath", str(rs),
            "--max-eval-instances", "5",
            *extra_argv,
        ]
    )
    return yaml.safe_load(out.read_text())


def test_make_manifest_emits_model_deployment(tmp_path: Path):
    m = _make_manifest(
        tmp_path,
        extra_argv=[
            "--from-run-spec",
            "--precomputed-root", "/data/crfm-helm-public/mmlu",
            "--model-deployment", "huggingface/phi-2-local",
        ],
    )
    assert m["from_run_spec"] is True
    assert m["model_deployment"] == "huggingface/phi-2-local"


def test_make_manifest_model_deployment_defaults_none(tmp_path: Path):
    # Without --model-deployment the field is None (pure by-name replay).
    m = _make_manifest(
        tmp_path,
        extra_argv=[
            "--from-run-spec",
            "--precomputed-root", "/data/crfm-helm-public/mmlu",
        ],
    )
    assert m["model_deployment"] is None


def test_make_manifest_rejects_model_deployment_without_from_run_spec(tmp_path: Path):
    # --model-deployment only rewrites the replayed spec, so it is meaningless on
    # the run-entry path and must fail loud rather than be silently ignored.
    with pytest.raises(SystemExit):
        _make_manifest(
            tmp_path,
            extra_argv=["--model-deployment", "huggingface/phi-2-local"],
        )


def _resolved_image():
    from eval_audit.integrations.docker_provenance import ResolvedImage

    return ResolvedImage(
        requested="img:tag",
        run_ref="img@sha256:deadbeef",
        digest="sha256:deadbeef",
        digest_kind="id",
        pinned=True,
    )


def _from_spec_manifest(**overrides) -> dict:
    base = {
        "experiment_name": "e2e",
        "run_entries": [PHI2_RUN_ENTRY],
        "max_eval_instances": 5,
        "suite": "e2e",
        "from_run_spec": True,
        "precomputed_root": "/data/crfm-helm-public/mmlu",
        "model_deployments_fpath": "configs/debug/e2e_phi2_fromspec_overrides.yaml",
    }
    base.update(overrides)
    return base


def test_bridge_from_spec_branch_puts_model_deployment_on_matrix():
    from eval_audit.integrations.kwdagger_bridge import build_schedule_params

    params = build_schedule_params(
        _from_spec_manifest(model_deployment="huggingface/phi-2-local"),
        resolved_image=_resolved_image(),
    )
    assert params["pipeline"].endswith("helm_single_run_from_spec_docker_pipeline()")
    assert params["matrix"]["helm.model_deployment"] == ["huggingface/phi-2-local"]


def test_bridge_omits_model_deployment_when_unset():
    from eval_audit.integrations.kwdagger_bridge import build_schedule_params

    params = build_schedule_params(
        _from_spec_manifest(),  # no model_deployment => pure by-name
        resolved_image=_resolved_image(),
    )
    assert "helm.model_deployment" not in params["matrix"]


def test_run_entry_branch_never_carries_model_deployment():
    # Even if a stray model_deployment leaks into a run-entry manifest, the
    # run-entry node does not declare it as an algo param, so the bridge must not
    # put it on the matrix (kwdagger would reject the unknown key).
    from eval_audit.integrations.kwdagger_bridge import build_schedule_params

    params = build_schedule_params(
        _from_spec_manifest(from_run_spec=False, model_deployment="x"),
        resolved_image=_resolved_image(),
    )
    assert params["pipeline"].endswith("helm_single_run_docker_pipeline()")
    assert "helm.model_deployment" not in params["matrix"]


def test_from_spec_node_renders_model_deployment_flag():
    from eval_audit.pipelines.helm_docker_pipeline import (
        MaterializeHelmRunFromSpecDockerNode,
    )

    node = MaterializeHelmRunFromSpecDockerNode()
    assert "model_deployment" in node.algo_params

    cfg = dict(node.algo_params)
    cfg.update(node.perf_params)
    cfg.update(
        {
            "out_dpath": "/work/node",
            "done_fname": "DONE",
            "manifest_fname": "adapter_manifest.json",
            "run_entry": PHI2_RUN_ENTRY,
            "container_image": "img@sha256:deadbeef",
            "precomputed_root": "/data/crfm-helm-public/mmlu",
            "model_deployments_fpath": "/repo/x.yaml",
            "suite": "e2e",
            "max_eval_instances": 5,
        }
    )
    # final_config is a kwdagger-derived property; stub it for the render test.
    type(node).final_config = property(lambda self: cfg)
    try:
        cfg["model_deployment"] = "huggingface/phi-2-local"
        assert "--model_deployment=huggingface/phi-2-local" in node.command
        cfg["model_deployment"] = None
        # Precise '=' check: --model_deployment is a prefix of
        # --model_deployments_fpath, so a bare substring would false-positive.
        assert "--model_deployment=" not in node.command
    finally:
        del type(node).final_config


# --------------------------------------------------------------------------
# §3 invariant: manifest model_deployment names a registered deployment
# --------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["smoke", "full"])
def test_hf_from_spec_manifest_deployment_is_registered(mode: str):
    # A from-spec manifest whose model_deployment is NOT registered in its
    # model_deployments.yaml would make HELM fail "deployment not found" before any
    # instances run. Guard the checked-in hf manifests against that drift.
    manifest = yaml.safe_load(
        (MANIFEST_DIR / f"e2e-phi_2-huggingface-philosophy-{mode}.yaml").read_text()
    )
    assert manifest["from_run_spec"] is True
    deployment = manifest["model_deployment"]
    assert deployment, "from-spec manifest must carry a model_deployment rewrite target"
    override = REPO / manifest["model_deployments_fpath"]
    registered = [e["name"] for e in yaml.safe_load(override.read_text())["model_deployments"]]
    assert deployment in registered, (
        f"manifest model_deployment {deployment!r} is not registered in "
        f"{manifest['model_deployments_fpath']} (names: {registered})"
    )

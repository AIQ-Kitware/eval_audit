"""Per-run GPU-lease bracket (infer-stack acquire/release) wiring.

Covers the eval_audit side of the kwdagger fan-out described in
``docs/historical/planning/infer-stack-kwdagger-eval-audit-handoff.md``: each HELM run is a
``ProcessNode`` that brackets itself with an infer-stack lease (acquire --queue
before, release after), so kwdagger can fan many runs out without a per-model
serial serve loop. These tests render the bracket and the matrix knobs (no GPU /
docker box required) — the end-to-end behavior is a separate GPU-box gate-check.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
import yaml

pytest.importorskip("kwdagger")

from eval_audit.pipelines.helm_docker_pipeline import helm_single_run_docker_pipeline
from eval_audit.pipelines.lease_bracket import (
    render_lease_setup,
    render_lease_teardown,
    _parse_model_deployment,
    _resolve_lease_endpoint,
)
from eval_audit.integrations.kwdagger_bridge import (
    build_lease_matrix_entries,
    build_schedule_params,
    prepare_schedule_request,
)
from eval_audit.integrations.docker_provenance import ResolvedImage


_FAKE_IMAGE = ResolvedImage(
    requested="img:dev",
    run_ref="img@sha256:deadbeef",
    digest="sha256:deadbeef",
    digest_kind="repo_digest",
    pinned=True,
)


# --------------------------------------------------------------------------- #
# Endpoint resolution                                                         #
# --------------------------------------------------------------------------- #
def test_parse_model_deployment_extracts_token() -> None:
    entry = "mmlu:subject=philosophy,model=microsoft/phi-2,model_deployment=vllm/phi-2-local"
    assert _parse_model_deployment(entry) == "vllm/phi-2-local"


def test_parse_model_deployment_absent_returns_none() -> None:
    assert _parse_model_deployment("commonsense:dataset=openbookqa,model=allenai/olmo-7b") is None


def test_resolve_scalar_endpoint() -> None:
    cfg = {"run_entry": "mmlu:model=microsoft/phi-2", "lease_endpoint": "phi2-single"}
    assert _resolve_lease_endpoint(cfg) == "phi2-single"


def test_resolve_map_endpoint_per_run_entry() -> None:
    cfg = {
        "run_entry": "gsm:model=q,model_deployment=vllm/qwen-local",
        "lease_endpoints": json.dumps(
            {"vllm/qwen-local": "qwen-ep", "litellm/gpt-oss-local": "gptoss-ep"}
        ),
    }
    assert _resolve_lease_endpoint(cfg) == "qwen-ep"


def test_resolve_single_entry_map_without_deployment_token() -> None:
    # A 1-entry map degenerates to its sole value even when the run-entry omits
    # model_deployment (OLMo run-entries do).
    cfg = {
        "run_entry": "commonsense:dataset=openbookqa,model=allenai/olmo-7b",
        "lease_endpoints": json.dumps({"vllm/allenai-olmo-7b": "allenai-olmo-7b-single"}),
    }
    assert _resolve_lease_endpoint(cfg) == "allenai-olmo-7b-single"


def test_resolve_multi_map_unresolvable_raises() -> None:
    # An ambiguous multi-model map with no resolvable deployment must fail loud
    # (the C-3 name-chain hazard) rather than silently skip the lease.
    cfg = {
        "run_entry": "commonsense:dataset=openbookqa,model=allenai/olmo-7b",
        "lease_endpoints": json.dumps({"a": "ep-a", "b": "ep-b"}),
    }
    with pytest.raises(ValueError, match="cannot be resolved"):
        _resolve_lease_endpoint(cfg)


def test_resolve_no_lease_returns_none() -> None:
    assert _resolve_lease_endpoint({"run_entry": "mmlu:model=x"}) is None


# --------------------------------------------------------------------------- #
# Setup / teardown rendering                                                  #
# --------------------------------------------------------------------------- #
def test_setup_renders_queueing_acquire_with_per_node_env_file() -> None:
    cfg = {
        "out_dpath": "/results/exp/helm/abcd",
        "lease_endpoint": "phi2-single",
        "lease_ttl": "2h",
        "lease_catalog": "/repo/catalog.yaml",
    }
    setup = render_lease_setup(cfg)
    assert "mkdir -p /results/exp/helm/abcd" in setup
    assert "infer-stack acquire phi2-single" in setup
    assert "--ttl 2h" in setup
    assert "--queue" in setup
    assert "--yes" in setup
    assert "--env-file /results/exp/helm/abcd/lease.env" in setup
    assert "--catalog /repo/catalog.yaml" in setup


def test_teardown_releases_the_same_env_file() -> None:
    cfg = {"out_dpath": "/results/exp/helm/abcd", "lease_endpoint": "phi2-single"}
    assert render_lease_teardown(cfg) == (
        "infer-stack release --yes --env-file /results/exp/helm/abcd/lease.env"
    )


def test_teardown_mirrors_acquire_catalog_and_never_prompts() -> None:
    """Regression: teardown lacked --yes (release converges too — inside a trap
    a diff prompt wedges the worker on a bare pty) and --catalog (a release
    rendered against the default-path catalog rebuilds the gateway route table
    without the superset, recreating the gateway under concurrent runs)."""
    cfg = {
        "out_dpath": "/o",
        "lease_endpoint": "ep",
        "lease_catalog": "/repo/catalog.yaml",
    }
    teardown = render_lease_teardown(cfg)
    assert "--yes" in teardown
    assert "--catalog /repo/catalog.yaml" in teardown


def test_setup_renders_an_explicit_queue_timeout() -> None:
    """Regression: acquire carried no --timeout, so infer-stack's 600 s default
    capped the admission-queue wait — every worker queued behind a multi-hour
    run failed with PlacementError after 10 minutes."""
    setup = render_lease_setup({"out_dpath": "/o", "lease_endpoint": "ep"})
    assert "--timeout 14400" in setup  # _DEFAULT_LEASE_TIMEOUT = 4h

    setup = render_lease_setup(
        {"out_dpath": "/o", "lease_endpoint": "ep", "lease_timeout": "90m"}
    )
    assert "--timeout 5400" in setup

    setup = render_lease_setup(
        {"out_dpath": "/o", "lease_endpoint": "ep", "lease_timeout": 1800}
    )
    assert "--timeout 1800" in setup


def test_setup_defaults_ttl_and_queue() -> None:
    setup = render_lease_setup({"out_dpath": "/o", "lease_endpoint": "ep"})
    assert "--ttl 4h" in setup  # _DEFAULT_LEASE_TTL
    assert "--queue" in setup


def test_setup_no_queue_omits_flag() -> None:
    setup = render_lease_setup({"out_dpath": "/o", "lease_endpoint": "ep", "lease_queue": False})
    assert "--queue" not in setup


def test_setup_snapshot_is_scoped_best_effort() -> None:
    # The snapshot's `|| true` must live INSIDE its brace group so it swallows
    # only the snapshot — a failed `acquire` must still gate the whole chain
    # (PREAMBLE_OK=0). Assert the structural shape that guarantees that.
    setup = render_lease_setup({"out_dpath": "/o", "lease_endpoint": "ep"})
    assert setup.rstrip().endswith("|| true ; }")
    # acquire is chained with && BEFORE the snapshot brace (so acquire gates).
    acquire_idx = setup.index("infer-stack acquire")
    snap_idx = setup.index("infer-stack leases")
    assert acquire_idx < snap_idx
    assert " && {" in setup[acquire_idx:snap_idx]


def test_setup_snapshot_can_be_disabled() -> None:
    setup = render_lease_setup(
        {"out_dpath": "/o", "lease_endpoint": "ep", "lease_snapshot": False}
    )
    assert "infer-stack leases" not in setup


def test_no_lease_renders_nothing() -> None:
    assert render_lease_setup({"out_dpath": "/o", "run_entry": "mmlu:x"}) is None
    assert render_lease_teardown({"out_dpath": "/o", "run_entry": "mmlu:x"}) is None


# --------------------------------------------------------------------------- #
# Node integration (real configure → final_config → setup/command)            #
# --------------------------------------------------------------------------- #
def test_docker_node_brackets_lease_and_drops_client_gpu() -> None:
    pipe = helm_single_run_docker_pipeline()
    node = pipe.node_dict["materialize_helm_run"]
    with tempfile.TemporaryDirectory() as td:
        pipe.configure(
            {
                "helm.run_entry": "mmlu:subject=philosophy,model=microsoft/phi-2,model_deployment=vllm/phi-2-local",
                "helm.suite": "s",
                "helm.max_eval_instances": 5,
                "helm.container_image": "img@sha256:deadbeef",
                "helm.container_gpus": "none",
                "helm.container_network": "host",
                "helm.lease_endpoint": "phi2-single",
                "helm.lease_ttl": "2h",
                "helm.lease_catalog": "/repo/catalog.yaml",
                "helm.lease_queue": True,
            },
            root_dpath=td,
        )
        out_dpath = str(node.final_config["out_dpath"])
        setup = node.setup
        teardown = node.teardown
        command = node.command

    # The lease handle lives in THIS node's own working dir (per-job).
    assert f"--env-file {out_dpath}/lease.env" in setup
    assert f"--env-file {out_dpath}/lease.env" in teardown
    assert "infer-stack acquire phi2-single --ttl 2h" in setup
    # Client requests no GPU (infer-stack owns them): no --gpus in docker run.
    assert "--gpus" not in command
    # Lease knobs configure the bracket, not the inner materialize CLI.
    for key in ("lease_endpoint", "lease_ttl", "lease_catalog", "lease_queue"):
        assert f"--{key}=" not in command


def test_docker_node_without_lease_has_no_bracket() -> None:
    pipe = helm_single_run_docker_pipeline()
    node = pipe.node_dict["materialize_helm_run"]
    with tempfile.TemporaryDirectory() as td:
        pipe.configure(
            {
                "helm.run_entry": "mmlu:model=microsoft/phi-2",
                "helm.suite": "s",
                "helm.max_eval_instances": 5,
                "helm.container_image": "img@sha256:deadbeef",
            },
            root_dpath=td,
        )
        assert node.setup is None
        # P2: even without a lease, teardown removes the named container so an
        # aborted run doesn't leak it (there is no lease release to compose with).
        assert node.teardown is not None
        assert "docker rm -f eval-audit-helm-" in node.teardown


def test_docker_node_constructs_without_error() -> None:
    # Regression: setup/teardown are read-only-ish properties; ProcessNode.__init__
    # still assigns them (default None) via _classvar_init. The absorbing setters
    # must let construction succeed.
    pipe = helm_single_run_docker_pipeline()
    assert pipe.node_dict["materialize_helm_run"].setup is None


# --------------------------------------------------------------------------- #
# Bridge: lease matrix knobs                                                   #
# --------------------------------------------------------------------------- #
def test_build_lease_matrix_scalar(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text("endpoints: {}\n")
    manifest = {"lease_endpoint": "phi2-single", "lease_ttl": "2h", "lease_catalog": str(catalog)}
    entries = build_lease_matrix_entries(manifest)
    assert entries["helm.lease_endpoint"] == ["phi2-single"]
    assert entries["helm.lease_ttl"] == ["2h"]
    assert entries["helm.lease_catalog"] == [str(catalog.resolve())]
    assert entries["helm.lease_queue"] == [True]


def test_build_lease_matrix_map_is_json_encoded() -> None:
    manifest = {"lease_endpoints": {"vllm/a": "ep-a", "vllm/b": "ep-b"}}
    entries = build_lease_matrix_entries(manifest)
    assert json.loads(entries["helm.lease_endpoints"][0]) == {"vllm/a": "ep-a", "vllm/b": "ep-b"}


def test_build_lease_matrix_ttl_and_queue_overrides() -> None:
    manifest = {"lease_endpoint": "ep", "lease_ttl": "2h"}
    entries = build_lease_matrix_entries(manifest, ttl_override="30m", queue=False)
    assert entries["helm.lease_ttl"] == ["30m"]
    assert entries["helm.lease_queue"] == [False]


def test_build_lease_matrix_requires_endpoint() -> None:
    with pytest.raises(ValueError, match="lease_endpoint"):
        build_lease_matrix_entries({"suite": "s"})


def test_schedule_params_merges_lease_on_docker_path() -> None:
    manifest = {
        "run_entries": ["mmlu:model=x"],
        "max_eval_instances": 5,
        "suite": "s",
    }
    lease_entries = {"helm.lease_endpoint": ["phi2-single"], "helm.lease_queue": [True]}
    params = build_schedule_params(manifest, resolved_image=_FAKE_IMAGE, lease_entries=lease_entries)
    assert params["matrix"]["helm.lease_endpoint"] == ["phi2-single"]
    assert "helm_docker_pipeline" in params["pipeline"]


def test_schedule_params_requires_container_image() -> None:
    # Containerization is mandatory: no resolved image => raise, even with a lease
    # (leasing is the orthogonal axis on the docker node, not a bare fallback).
    manifest = {"run_entries": ["mmlu:model=x"], "max_eval_instances": 5, "suite": "s"}
    with pytest.raises(ValueError, match="containerized execution is required"):
        build_schedule_params(
            manifest, resolved_image=None, lease_entries={"helm.lease_endpoint": ["ep"]}
        )
    with pytest.raises(ValueError, match="containerized execution is required"):
        build_schedule_params(manifest, resolved_image=None, lease_entries=None)


# --------------------------------------------------------------------------- #
# prepare_schedule_request end-to-end (lease toggle)                          #
# --------------------------------------------------------------------------- #
def _write_manifest(tmp_path: Path, **extra) -> Path:
    doc = {
        "schema_version": 1,
        "experiment_name": "audit-phi2-smoke",
        "description": "d",
        "run_entries": ["mmlu:subject=philosophy,model=microsoft/phi-2,model_deployment=vllm/phi-2-local"],
        "max_eval_instances": 5,
        "suite": "audit-phi2-smoke",
        "model_deployments_fpath": str(tmp_path / "model_deployments.yaml"),
        **extra,
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(doc))
    return path


def test_prepare_request_lease_without_container_raises(tmp_path: Path) -> None:
    # Containerization is mandatory: --lease with no container image is rejected
    # (the bare host-venv pipelines were removed — leasing rides on the docker
    # node, not a bare fallback).
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text("endpoints: {}\n")
    manifest = _write_manifest(
        tmp_path,
        lease_endpoint="phi2-single",
        lease_ttl="2h",
        lease_catalog=str(catalog),
    )
    with pytest.raises(ValueError, match="containerized execution is required"):
        prepare_schedule_request(manifest, lease=True)


def test_prepare_request_lease_defaults_client_to_no_gpu(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text("endpoints: {}\n")
    # A digest-pinned ref short-circuits docker resolution (no docker daemon
    # needed in CI); the lease wiring is what we're exercising here.
    pinned = "img@sha256:" + "d" * 64
    manifest = _write_manifest(
        tmp_path,
        lease_endpoint="phi2-single",
        lease_ttl="2h",
        lease_catalog=str(catalog),
        container_image=pinned,
    )
    req = prepare_schedule_request(manifest, lease=True, container_image=pinned)
    params = yaml.safe_load(req.params_text)
    matrix = params["matrix"]
    assert matrix["helm.lease_endpoint"] == ["phi2-single"]
    assert matrix["helm.lease_ttl"] == ["2h"]
    # Design rule #1: the client requests no GPU (infer-stack owns them).
    assert matrix["helm.container_gpus"] == ["none"]

"""In-process HuggingFace reproduction path: engine resolver, HF deployment
entry (fp32), the reserve-GPU lease bracket, and the docker node's reserved-GPU
wiring.

These render strings / build dicts only — no GPU or docker box (the end-to-end
acceptance run is a separate GPU-host gate; see
``docs/planning/huggingface-in-process-reserved-gpu-plan.md``).
"""

from __future__ import annotations

import tempfile

import pytest

from eval_audit.hf_inprocess import (
    FP32_TORCH_DTYPE,
    hf_inprocess_deployment_entry,
    is_huggingface_client,
    official_client_class,
    official_is_huggingface_inprocess,
)
from eval_audit.pipelines.lease_bracket import (
    _resolve_lease_request,
    render_lease_setup,
    render_lease_teardown,
)

# A public OLMoE HuggingFaceClient deployment shipped in HELM's config.
_OLMOE = "huggingface/olmoe-1b-7b-0125-instruct"


# --------------------------------------------------------------------------- #
# Engine resolver + HF deployment entry                                       #
# --------------------------------------------------------------------------- #
def test_is_huggingface_client_matches_on_suffix() -> None:
    assert is_huggingface_client("helm.clients.huggingface_client.HuggingFaceClient")
    assert is_huggingface_client("HuggingFaceClient")
    assert not is_huggingface_client("helm.clients.together_client.TogetherClient")
    assert not is_huggingface_client(None)


def test_official_client_class_resolves_olmoe_as_huggingface() -> None:
    assert is_huggingface_client(official_client_class(_OLMOE))
    assert official_is_huggingface_inprocess(_OLMOE) is True


def test_unknown_deployment_is_not_huggingface_inprocess() -> None:
    assert official_client_class("no/such-deployment") is None
    assert official_is_huggingface_inprocess("no/such-deployment") is False


def test_hf_entry_pins_fp32_and_preserves_official_facts() -> None:
    entry = hf_inprocess_deployment_entry(_OLMOE)
    assert is_huggingface_client(entry["client_spec"]["class_name"])
    args = entry["client_spec"]["args"]
    assert args["torch_dtype"] == FP32_TORCH_DTYPE == "torch.float32"
    assert args["device_map"] == "auto"
    # official HELM aliases carried through unchanged
    assert entry["model_name"] == "allenai/olmoe-1b-7b-0125-instruct"
    assert entry["tokenizer_name"] == "allenai/olmoe-1b-7b-0125-instruct"
    # name kept (pure by-name replay) unless a local name is requested
    assert entry["name"] == _OLMOE


def test_hf_entry_can_rename_to_a_local_deployment() -> None:
    entry = hf_inprocess_deployment_entry(_OLMOE, local_name="hf-local/olmoe")
    assert entry["name"] == "hf-local/olmoe"
    assert entry["model_name"] == "allenai/olmoe-1b-7b-0125-instruct"  # model untouched


def test_hf_entry_rejects_non_huggingface_deployment() -> None:
    with pytest.raises(ValueError, match="not a HuggingFaceClient"):
        # a Together-hosted deployment is not an in-process HF run
        hf_inprocess_deployment_entry("together/mistral-7b-v0.1")


# --------------------------------------------------------------------------- #
# Reserve-GPU lease bracket                                                    #
# --------------------------------------------------------------------------- #
def test_resolve_lease_request_classifies_modes() -> None:
    assert _resolve_lease_request({"lease_reserve_gpus": 2}) == ("reserved", 2)
    assert _resolve_lease_request({"lease_endpoint": "ep"}) == ("served", "ep")
    assert _resolve_lease_request({}) is None
    # reserve wins over a stray endpoint (mutually exclusive; never route in-process at the gateway)
    assert _resolve_lease_request(
        {"lease_reserve_gpus": 1, "lease_endpoint": "ep"}
    ) == ("reserved", 1)


def test_setup_renders_reserve_acquire_not_an_endpoint() -> None:
    setup = render_lease_setup(
        {
            "out_dpath": "/o/helm/abcd",
            "lease_reserve_gpus": 2,
            "lease_ttl": "2h",
            "lease_catalog": "/repo/catalog.yaml",
        }
    )
    assert "infer-stack acquire --reserve-gpus 2" in setup
    assert "--ttl 2h" in setup
    assert "--queue" in setup
    assert "--timeout 14400" in setup
    assert "--env-file /o/helm/abcd/lease.env" in setup
    # even a reserve acquire converges the shared stack -> keep the gateway catalog
    assert "--catalog /repo/catalog.yaml" in setup


def test_reserve_teardown_releases_by_env_file() -> None:
    cfg = {"out_dpath": "/o", "lease_reserve_gpus": 1}
    teardown = render_lease_teardown(cfg)
    assert teardown == "infer-stack release --yes --env-file /o/lease.env"


# --------------------------------------------------------------------------- #
# Docker node: pin the container to the reserved GPU                          #
# --------------------------------------------------------------------------- #
def test_docker_node_reserve_pins_gpu_and_sources_lease_env() -> None:
    pytest.importorskip("kwdagger")
    from eval_audit.pipelines.helm_docker_pipeline import (
        helm_single_run_docker_pipeline,
    )

    pipe = helm_single_run_docker_pipeline()
    node = pipe.node_dict["materialize_helm_run"]
    with tempfile.TemporaryDirectory() as td:
        pipe.configure(
            {
                "helm.run_entry": "mmlu:subject=philosophy,model=allenai/olmoe,"
                "model_deployment=huggingface/olmoe-1b-7b-0125-instruct",
                "helm.suite": "s",
                "helm.max_eval_instances": 5,
                "helm.container_image": "img@sha256:deadbeef",
                # NB: no container_gpus -> the reserve branch must pin from the lease
                "helm.lease_reserve_gpus": 1,
                "helm.lease_ttl": "2h",
            },
            root_dpath=td,
        )
        out_dpath = str(node.final_config["out_dpath"])
        setup = node.setup
        command = node.command

    # setup reserves a GPU (no endpoint)
    assert "infer-stack acquire --reserve-gpus 1" in setup
    # the command sources the lease env-file (for CUDA_VISIBLE_DEVICES) before docker run
    assert f". {out_dpath}/lease.env" in command
    # and pins the container to exactly the reserved card, failing closed
    assert '--gpus "device=${CUDA_VISIBLE_DEVICES:?' in command
    # lease knobs never leak into the inner materialize CLI
    assert "--lease_reserve_gpus=" not in command


def test_docker_node_reserve_does_not_forward_cvd_into_container() -> None:
    pytest.importorskip("kwdagger")
    from eval_audit.pipelines.helm_docker_pipeline import (
        helm_single_run_docker_pipeline,
    )

    pipe = helm_single_run_docker_pipeline()
    node = pipe.node_dict["materialize_helm_run"]
    with tempfile.TemporaryDirectory() as td:
        pipe.configure(
            {
                "helm.run_entry": "mmlu:model=allenai/olmoe,"
                "model_deployment=huggingface/olmoe-1b-7b-0125-instruct",
                "helm.suite": "s",
                "helm.max_eval_instances": 5,
                "helm.container_image": "img@sha256:deadbeef",
                "helm.lease_reserve_gpus": 1,
            },
            root_dpath=td,
        )
        command = node.command
    # --gpus device=k already isolates the card (renumbered to 0 inside); forwarding
    # CUDA_VISIBLE_DEVICES into the container would wrongly reference a host index.
    assert "-e CUDA_VISIBLE_DEVICES" not in command


# --------------------------------------------------------------------------- #
# Bridge: reserve knobs propagate; endpoint no longer required                #
# --------------------------------------------------------------------------- #
def test_broadcast_lease_knobs_carry_reserve_count() -> None:
    pytest.importorskip("kwdagger")
    from eval_audit.integrations.kwdagger_bridge import build_broadcast_lease_knobs

    entries = build_broadcast_lease_knobs({"lease_reserve_gpus": 2})
    assert entries["helm.lease_reserve_gpus"] == [2]


def test_matrix_entries_accept_reserve_without_endpoint() -> None:
    pytest.importorskip("kwdagger")
    from eval_audit.integrations.kwdagger_bridge import build_lease_matrix_entries

    # a reserve manifest has no lease_endpoint(s) — this must NOT raise
    entries = build_lease_matrix_entries({"lease_reserve_gpus": 1})
    assert entries["helm.lease_reserve_gpus"] == [1]
    assert "helm.lease_endpoint" not in entries


def test_matrix_entries_still_require_some_lease_fact() -> None:
    pytest.importorskip("kwdagger")
    from eval_audit.integrations.kwdagger_bridge import build_lease_matrix_entries

    with pytest.raises(ValueError, match="lease_reserve_gpus"):
        build_lease_matrix_entries({})

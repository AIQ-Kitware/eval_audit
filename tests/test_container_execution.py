from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("kwdagger")

from eval_audit.integrations import kwdagger_bridge
from eval_audit.integrations.docker_provenance import (
    ResolvedImage,
    resolve_image_digest,
)
from eval_audit.pipelines.helm_docker_pipeline import (
    helm_single_run_docker_pipeline,
)

PINNED = "ghcr.io/aiq-kitware/eval-audit-helm-runner@sha256:" + "a" * 64


def _render(config: dict, tmp_path: Path) -> str:
    pipe = helm_single_run_docker_pipeline()
    pipe.configure(config, root_dpath=str(tmp_path / "results"))
    return pipe.node_dict["materialize_helm_run"].command


def test_docker_node_command_renders_expected(tmp_path: Path):
    cmd = _render(
        {
            "helm.run_entry": "mmlu:subject=philosophy,model=openai/gpt2",
            "helm.suite": "audit-smoke",
            "helm.max_eval_instances": 10,
            "helm.precomputed_root": "/data/crfm-helm-public",
            "helm.container_image": PINNED,
            "helm.hf_cache_dir": "/data/hf-audit",
            "helm.container_shm_size": "32g",
        },
        tmp_path,
    )
    # Wrapper shape
    assert cmd.startswith("docker run --rm")
    assert '--gpus "device=${CUDA_VISIBLE_DEVICES:-all}"' in cmd
    assert "--shm-size=32g" in cmd
    assert "-e HOST_UID=$(id -u) -e HOST_GID=$(id -g)" in cmd
    assert "-e HF_HOME=/hf-cache" in cmd
    assert "-e HF_TOKEN -e HUGGING_FACE_HUB_TOKEN" in cmd
    # Digest pinned + recorded as env for provenance
    assert PINNED in cmd
    assert f"-e EVAL_AUDIT_CONTAINER_DIGEST=sha256:{'a' * 64}" in cmd
    # HF cache + precomputed mounts
    assert "-v /data/hf-audit:/hf-cache" in cmd
    assert "-v /data/crfm-helm-public:/data/crfm-helm-public:ro" in cmd
    # Inner command present, container knobs NOT leaked into the inner CLI
    assert "python -m magnet.backends.helm.cli.materialize_helm_run" in cmd
    assert "--run_entry=mmlu:subject=philosophy,model=openai/gpt2" in cmd
    assert "--container_image" not in cmd
    assert "--hf_cache_dir" not in cmd
    assert "--container_shm_size" not in cmd
    # Default network is Docker's bridge => no --network flag emitted.
    assert "--network" not in cmd


def test_out_dpath_mounted_at_same_absolute_path(tmp_path: Path):
    cmd = _render(
        {
            "helm.run_entry": "boolq:model=openai/gpt2",
            "helm.container_image": PINNED,
        },
        tmp_path,
    )
    # The node dir is bind-mounted at the identical absolute path and is the
    # working dir, so kwdagger's DONE check + reuse symlinks resolve on the host.
    node_dpath = str((tmp_path / "results").resolve())
    # find the rendered out_dpath token
    out_lines = [ln for ln in cmd.splitlines() if "--out_dpath=" in ln]
    assert out_lines, cmd
    out_dpath = out_lines[0].split("--out_dpath=", 1)[1].strip().rstrip(" \\")
    assert out_dpath.startswith(node_dpath)
    assert f"-v {out_dpath}:{out_dpath}" in cmd
    assert f"-w {out_dpath}" in cmd


def test_cpu_variant_omits_gpus(tmp_path: Path):
    cmd = _render(
        {
            "helm.run_entry": "boolq:model=openai/gpt2",
            "helm.container_image": PINNED,
            "helm.container_gpus": "none",
        },
        tmp_path,
    )
    assert "--gpus" not in cmd


def test_network_host_variant(tmp_path: Path):
    # container_network: host => --network host, so the in-container HELM client
    # can reach a model server (e.g. vLLM/LiteLLM) published on the host's
    # localhost. (A default bridge container's localhost is its own namespace.)
    cmd = _render(
        {
            "helm.run_entry": "mmlu:subject=philosophy,model=microsoft/phi-2",
            "helm.container_image": PINNED,
            "helm.container_network": "host",
        },
        tmp_path,
    )
    assert "--network host" in cmd
    # A container knob, not forwarded to the inner materialize CLI.
    assert "--container_network" not in cmd


def test_ipc_host_variant(tmp_path: Path):
    cmd = _render(
        {
            "helm.run_entry": "boolq:model=openai/gpt2",
            "helm.container_image": PINNED,
            "helm.container_ipc_host": True,
        },
        tmp_path,
    )
    assert "--ipc=host" in cmd
    assert "--shm-size" not in cmd


def test_resolve_image_digest_already_pinned_is_pure():
    # An already-pinned ref needs no docker calls and is returned unchanged.
    resolved = resolve_image_digest(PINNED)
    assert resolved.pinned is True
    assert resolved.run_ref == PINNED
    assert resolved.digest == "sha256:" + "a" * 64
    assert resolved.digest_kind == "already_pinned"


def _write_container_manifest(tmp_path: Path) -> Path:
    manifest_fpath = tmp_path / "manifest.yaml"
    manifest_fpath.write_text(
        "\n".join(
            [
                "experiment_name: demo-container",
                "description: demo",
                "run_entries:",
                "  - boolq:model=openai/gpt2,data_augmentation=canonical",
                "suite: audit-smoke",
                "max_eval_instances: 10",
                "container_image: eval-audit-helm-runner:dev",
                "container_network: host",
                f"hf_cache_dir: {tmp_path / 'hf'}",
            ]
        )
        + "\n"
    )
    return manifest_fpath


def test_prepare_schedule_request_container(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        kwdagger_bridge,
        "resolve_image_digest",
        lambda image, runtime="docker": ResolvedImage(
            requested=image,
            run_ref=PINNED,
            digest="sha256:" + "a" * 64,
            digest_kind="repo_digest",
            pinned=True,
        ),
    )
    monkeypatch.setattr(kwdagger_bridge, "runtime_version", lambda runtime="docker": "29.0.0")

    manifest_fpath = _write_container_manifest(tmp_path)
    request = kwdagger_bridge.prepare_schedule_request(
        manifest_fpath, run=False, root_dpath=tmp_path / "results"
    )

    # Pipeline switched to the docker factory and the pinned image is in params.
    assert "helm_single_run_docker_pipeline()" in request.params_text
    assert "helm.container_image" in request.params_text
    assert "helm.container_network" in request.params_text
    assert PINNED in request.params_text
    # Resolved image + provenance carried on the request.
    assert request.resolved_image is not None
    assert request.resolved_image.run_ref == PINNED
    assert request.container_provenance is not None
    assert request.container_provenance["image"]["run_ref"] == PINNED
    # HF cache dir was resolved to an absolute path and created host-owned.
    assert (tmp_path / "hf").is_dir()


def _stub_image_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        kwdagger_bridge,
        "resolve_image_digest",
        lambda image, runtime="docker": ResolvedImage(
            requested=image,
            run_ref=PINNED,
            digest="sha256:" + "a" * 64,
            digest_kind="repo_digest",
            pinned=True,
        ),
    )
    monkeypatch.setattr(kwdagger_bridge, "runtime_version", lambda runtime="docker": "29.0.0")


def test_prepare_schedule_request_materializes_hf_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # The docker node's bare ``-e HF_TOKEN`` cannot reach the container under the
    # tmux backend (fresh pane, empty worker environ), so the scheduler — which
    # DOES inherit the user's env — writes the resolved token into the mounted HF
    # cache as ``<hf_cache_dir>/token``, the path the container reads via
    # HF_HOME=/hf-cache. See kwdagger_bridge._prepare_container_execution.
    _stub_image_resolution(monkeypatch)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    monkeypatch.setenv("HF_TOKEN", "hf_unit_test_token")

    manifest_fpath = _write_container_manifest(tmp_path)
    kwdagger_bridge.prepare_schedule_request(
        manifest_fpath, run=False, root_dpath=tmp_path / "results"
    )

    token_fpath = tmp_path / "hf" / "token"
    assert token_fpath.read_text().strip() == "hf_unit_test_token"
    # Secret on disk => owner-only perms.
    assert (token_fpath.stat().st_mode & 0o777) == 0o600


def test_prepare_schedule_request_no_token_writes_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # With no token in the scheduling env there is nothing to promote, and we must
    # not clobber a token a user may have logged into the dir directly: leave it.
    _stub_image_resolution(monkeypatch)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)

    manifest_fpath = _write_container_manifest(tmp_path)
    kwdagger_bridge.prepare_schedule_request(
        manifest_fpath, run=False, root_dpath=tmp_path / "results"
    )

    assert (tmp_path / "hf").is_dir()
    assert not (tmp_path / "hf" / "token").exists()


def test_prepare_schedule_request_requires_container_image(tmp_path: Path):
    # Containerization is mandatory: a manifest with no container_image (and no
    # --container-image override) is rejected — the bare host-venv pipeline was
    # removed.
    manifest_fpath = tmp_path / "manifest.yaml"
    manifest_fpath.write_text(
        "\n".join(
            [
                "experiment_name: demo-bare",
                "description: demo",
                "run_entries:",
                "  - boolq:model=openai/gpt2,data_augmentation=canonical",
                "suite: audit-smoke",
                "max_eval_instances: 10",
            ]
        )
        + "\n"
    )
    with pytest.raises(ValueError, match="containerized execution is required"):
        kwdagger_bridge.prepare_schedule_request(
            manifest_fpath, run=False, root_dpath=tmp_path / "results"
        )

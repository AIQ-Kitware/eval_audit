from __future__ import annotations

from pathlib import Path

import pytest

from eval_audit.integrations import kwdagger_bridge
from eval_audit.integrations.kwdagger_bridge import (
    kwdagger_schedule_argv,
    prepare_schedule_request,
    run_kwdagger_schedule,
)
from eval_audit.infra.paths import repo_root
from eval_audit.manifests import builders as manifest_builders
from eval_audit.workflows import run_from_manifest as run_workflow

# Containerization is mandatory, so every schedulable manifest needs an image. A
# digest-pinned ref short-circuits docker resolution (no daemon needed in CI).
_PINNED_IMAGE = "ghcr.io/aiq-kitware/eval-audit-helm-runner@sha256:" + "a" * 64


def _write_manifest(tmp_path: Path) -> Path:
    manifest_fpath = tmp_path / "manifest.yaml"
    manifest_fpath.write_text(
        "\n".join(
            [
                "experiment_name: demo-exp",
                "description: demo",
                "run_entries:",
                "  - boolq:model=openai/gpt2,data_augmentation=canonical",
                "suite: audit-smoke",
                "max_eval_instances: 10",
                "devices: 2,3",
                "tmux_workers: 4",
                "backend: tmux",
                f"container_image: {_PINNED_IMAGE}",
            ]
        )
        + "\n"
    )
    return manifest_fpath


def test_kwdagger_argv_differs_between_preview_and_execute(tmp_path: Path):
    manifest_fpath = _write_manifest(tmp_path)
    preview = prepare_schedule_request(manifest_fpath, run=False)
    execute = prepare_schedule_request(manifest_fpath, run=True)
    preview_argv = kwdagger_schedule_argv(preview)
    execute_argv = kwdagger_schedule_argv(execute)
    assert "--run=0" in preview_argv
    assert "--run=1" in execute_argv
    # Preview and execute must differ ONLY in the --run flag. The old
    # assertion compared argv[:-1], assuming --run was the last element — but
    # --log / --monitor / --virtualenv_cmd follow it, so drop the --run token
    # from both sides and compare the remainder.
    def _without_run(argv: list[str]) -> list[str]:
        return [a for a in argv if not a.startswith("--run=")]

    assert _without_run(preview_argv) == _without_run(execute_argv)


def test_run_from_manifest_preview_does_not_execute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    manifest_fpath = _write_manifest(tmp_path)
    called = {"count": 0}

    def _unexpected_call(request):
        called["count"] += 1
        raise AssertionError("preview should not execute kwdagger")

    monkeypatch.setattr(run_workflow, "run_kwdagger_schedule", _unexpected_call)
    info = run_workflow.run_from_manifest(manifest_fpath, run=False, root_dpath=tmp_path / "results")
    assert info["mode"] == "preview"
    assert "--run=0" in info["argv"]
    assert "kwdagger schedule" in info["command"]
    assert called["count"] == 0


def test_run_from_manifest_execute_calls_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    manifest_fpath = _write_manifest(tmp_path)
    called = {"count": 0}

    class _Proc:
        returncode = 0

    def _fake_run(request):
        called["count"] += 1
        assert request.runtime.run is True
        return _Proc()

    monkeypatch.setattr(run_workflow, "run_kwdagger_schedule", _fake_run)
    info = run_workflow.run_from_manifest(manifest_fpath, run=True, root_dpath=tmp_path / "results")
    assert info["mode"] == "execute"
    assert info["returncode"] == 0
    assert "--run=1" in info["argv"]
    assert called["count"] == 1


def test_run_kwdagger_schedule_spills_params_to_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Execution must pass --params as an on-disk .yaml path, not inline YAML.

    A large fan-out overflows ARG_MAX when the whole grid rides inline in argv
    (OSError [Errno 7] Argument list too long). Regression guard: the executed
    argv references a kwdagger_params.yaml file whose contents equal the request's
    params_text, and the (potentially huge) inline YAML never appears in argv.
    """
    manifest_fpath = _write_manifest(tmp_path)
    root_dpath = tmp_path / "results"
    request = prepare_schedule_request(manifest_fpath, run=True, root_dpath=root_dpath)

    captured: dict[str, list[str]] = {}

    class _Proc:
        returncode = 0

    def _fake_subprocess_run(argv, **kwargs):
        captured["argv"] = argv
        return _Proc()

    monkeypatch.setattr(kwdagger_bridge.subprocess, "run", _fake_subprocess_run)
    run_kwdagger_schedule(request)

    argv = captured["argv"]
    params_flags = [a for a in argv if a.startswith("--params=")]
    assert len(params_flags) == 1
    params_value = params_flags[0][len("--params=") :]
    params_fpath = Path(params_value)
    # A real, existing .yaml path (the extension is load-bearing for kwdagger's
    # existing_file_with_extension coercion) — never the inline grid text.
    assert params_fpath.suffix == ".yaml"
    assert params_fpath.is_file()
    assert params_fpath.parent == request.runtime.root_dpath
    assert params_fpath.read_text() == request.params_text
    assert request.params_text not in "".join(argv)


def test_choose_model_override_for_qwen_and_vicuna():
    qwen_entry = "gsm:model=qwen/qwen2.5-7b-instruct-turbo,stop=none"
    vicuna_entry = "boolq:model=lmsys/vicuna-7b-v1.3,data_augmentation=canonical"
    assert manifest_builders._choose_model_override([qwen_entry], False) == (
        "configs/debug/repro_model_overrides.yaml"
    )
    assert manifest_builders._choose_model_override([vicuna_entry], False) == (
        "configs/debug/repro_model_overrides.yaml"
    )


def test_kwdagger_manifest_propagates_model_override(tmp_path: Path):
    manifest_fpath = tmp_path / "manifest.yaml"
    manifest_fpath.write_text(
        "\n".join(
            [
                "experiment_name: demo-exp",
                "description: demo",
                "run_entries:",
                "  - gsm:model=qwen/qwen2.5-7b-instruct-turbo,stop=none",
                "suite: audit-smoke",
                "max_eval_instances: 10",
                "devices: 2,3",
                "tmux_workers: 4",
                "backend: tmux",
                "model_deployments_fpath: configs/debug/repro_model_overrides.yaml",
                f"container_image: {_PINNED_IMAGE}",
            ]
        )
        + "\n"
    )
    request = prepare_schedule_request(manifest_fpath, run=False)
    params_text = request.params_text
    assert "helm.model_deployments_fpath" in params_text
    assert str((repo_root() / "configs/debug/repro_model_overrides.yaml").resolve()) in params_text


def test_manifest_propagates_registry_sidecars(tmp_path: Path):
    # Registry sidecars (net-new model ids, e.g. qwen/qwen3.5-9b-base): both
    # fpaths must reach the node params resolved to absolute host paths,
    # exactly like the deployments override, so the in-container magnet CLI
    # can copy them into prod_env. Repo-relative paths in the manifest resolve
    # against the repo root (the qwen35 sidecars double as the fixture).
    manifest_fpath = tmp_path / "manifest.yaml"
    manifest_fpath.write_text(
        "\n".join(
            [
                "experiment_name: sidecar-demo",
                "description: demo",
                "run_entries:",
                "  - mmlu:subject=anatomy,method=multiple_choice_joint,model=qwen/qwen3.5-9b-base",
                "suite: audit-smoke",
                "max_eval_instances: 5",
                "backend: tmux",
                "model_deployments_fpath: configs/debug/repro_model_overrides.yaml",
                "model_metadata_fpath: configs/local_models/qwen35_9b_vllm/model_metadata.yaml",
                "tokenizer_configs_fpath: configs/local_models/qwen35_9b_vllm/tokenizer_configs.yaml",
                f"container_image: {_PINNED_IMAGE}",
            ]
        )
        + "\n"
    )
    request = prepare_schedule_request(manifest_fpath, run=False)
    params_text = request.params_text
    assert "helm.model_metadata_fpath" in params_text
    assert str(
        (repo_root() / "configs/local_models/qwen35_9b_vllm/model_metadata.yaml").resolve()
    ) in params_text
    assert "helm.tokenizer_configs_fpath" in params_text
    assert str(
        (repo_root() / "configs/local_models/qwen35_9b_vllm/tokenizer_configs.yaml").resolve()
    ) in params_text

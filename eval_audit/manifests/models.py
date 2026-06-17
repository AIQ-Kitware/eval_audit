from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ManifestSpec:
    experiment_name: str
    description: str
    run_entries: list[str]
    suite: str
    max_eval_instances: int
    backend: str = "tmux"
    mode: str = "compute_if_missing"
    materialize: str = "symlink"
    devices: str = "0,1"
    tmux_workers: int = 2
    local_path: str = "prod_env"
    precomputed_root: str | None = None
    require_per_instance_stats: bool = True
    model_deployments_fpath: str | None = None
    enable_huggingface_models: list[str] = field(default_factory=list)
    enable_local_huggingface_models: list[str] = field(default_factory=list)
    # Containerized execution (opt-in). When ``container_image`` is set, Stage 3
    # runs each HELM run-entry inside a pinned Docker image instead of the host
    # venv, and records which image (by sha256 digest) produced each run. When
    # it is None the historic bare-python execution path is used unchanged.
    container_image: str | None = None
    container_runtime: str = "docker"
    hf_cache_dir: str | None = None
    container_gpus: str | None = None
    container_shm_size: str = "32g"
    container_ipc_host: bool = False
    container_mounts: list[str] = field(default_factory=list)
    # Docker ``--network`` for the run container. None => Docker's default
    # bridge network (correct when HELM loads the model in-process, e.g. the
    # enable_huggingface_models path). Set to "host" when HELM must reach a
    # model server published on the host's localhost (e.g. a vLLM/LiteLLM
    # endpoint) — a bridge container's localhost is its own namespace, not the
    # host's. See docs/container-execution.md.
    container_network: str | None = None
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

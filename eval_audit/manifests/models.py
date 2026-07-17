from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ManifestSpec:
    experiment_name: str
    description: str
    run_entries: list[str]
    suite: str
    # Integer instance cap, or the literal string "official" (D-5 verbatim-replay
    # sentinel: keep the official run_spec.json cap; exact-path replay only).
    max_eval_instances: int | str
    backend: str = "tmux"
    mode: str = "compute_if_missing"
    materialize: str = "symlink"
    devices: str = "0,1"
    tmux_workers: int = 2
    local_path: str = "prod_env"
    precomputed_root: str | None = None
    require_per_instance_stats: bool = True
    model_deployments_fpath: str | None = None
    # Optional HELM registry sidecars, copied into <local_path>/ alongside
    # model_deployments.yaml. They register net-new model/tokenizer ids via
    # HELM's own register_configs_from_directory, so a model unknown to the
    # (venv or baked-in-container) HELM needs no HELM-source edit.
    model_metadata_fpath: str | None = None
    tokenizer_configs_fpath: str | None = None
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
    # Faithful-replay execution (opt-in). When True, Stage 3 replays each run's
    # fully-resolved ``run_spec.json`` directly (HELM ``from_json`` +
    # ``run_benchmarking``) instead of reconstructing a run-entry string and
    # re-parsing it through ``helm-run``. The bridge then routes to the from-spec
    # docker pipeline, which requires ``precomputed_root`` (the recipe source the
    # official ``run_spec.json`` is read from). Substitution stays by-name, so the
    # produced run dir keeps the official ``run_spec.name`` and Stages 4-6 are
    # unchanged. See docs/planning/run-from-run-spec-json-plan.md.
    from_run_spec: bool = False
    # Optional deployment-rewrite target for ``from_run_spec`` replays. When set,
    # the from-spec CLI rewrites ``adapter_spec.model_deployment`` to this LOCAL
    # deployment name after deserialization, so the produced run records the
    # endpoint that actually served it and the audit reports ``same_deployment=no``
    # instead of masking the engine substitution behind the official name. It MUST
    # name a deployment registered in the run's ``model_deployments.yaml`` (the
    # by-name override for hf, the bundle for vLLM). Inert on the run-entry path.
    # See docs/historical/planning/from-spec-deployment-rewrite-plan.md.
    model_deployment: str | None = None
    # Exact-path replay (rel-path plan). When non-empty (with ``from_run_spec=True``),
    # each entry fully specifies one official run to replay by its path relative to
    # ``precomputed_root`` instead of run-entry token discovery:
    # ``{run_entry (label), rel_path, model_deployment?, lease_endpoint?, max_eval_instances?}``.
    # At schedule time the materializer reads ``<precomputed_root>/<rel_path>/run_spec.json``,
    # applies the declared substitutions as raw-JSON edits, and Stage 3 replays the
    # materialized copy verbatim (so the in-container rewrite is not exercised on this
    # path). When empty, the run-entry path (``run_entries``) is used unchanged.
    # See docs/historical/planning/run-from-relative-path-plan.md.
    run_spec_sources: list[dict[str, Any]] = field(default_factory=list)
    # Era-pinned replay (pre-v0.5). When set to an era key from docker/eras.yaml
    # (e.g. "helm-v0.2.4"), Stage 3 replays each run inside the era image via the
    # era shim (``helm_era_shim.replay``) instead of magnet's from-spec CLI, and
    # the bridge guards the pinned image's ``org.aiq.era`` label against this
    # value. None (the default) is the modern era — existing image + magnet CLI,
    # unchanged. Only valid with ``from_run_spec=True`` + exact-path
    # ``run_spec_sources`` (era replay is verbatim, exact-path only).
    # See docs/planning/era-pinned-helm-containers-plan.md.
    era: str | None = None
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

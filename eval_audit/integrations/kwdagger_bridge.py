from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from eval_audit.infra.api import audit_root
from eval_audit.infra.yaml_io import dump_yaml, load_manifest
from eval_audit.infra.paths import experiment_result_dpath
from eval_audit.integrations.docker_provenance import (
    ResolvedImage,
    resolve_image_digest,
    runtime_version,
    write_container_provenance,
)

# Containerized execution is mandatory: every HELM run goes through the docker
# pipeline (which pins the software environment so it stops being a confounding
# variable in the reproducibility comparison). Leasing is the orthogonal axis —
# the docker node carries the lease bracket (eval_audit.pipelines.lease_bracket),
# which renders only when the manifest names a lease endpoint. The historic bare
# host-venv pipelines have been removed.
_DOCKER_PIPELINE = (
    "eval_audit.pipelines.helm_docker_pipeline.helm_single_run_docker_pipeline()"
)


def _detect_virtualenv_cmd() -> str | None:
    """Return a shell command that activates the venv eval-audit-run is
    running in, or ``None`` if no venv is detected.

    Why this matters: kwdagger spawns each job in a fresh shell (tmux
    pane, slurm job, or serial subprocess). The shell loads the user's
    rc files and then runs the command. Whether ``.venv`` is activated
    in that shell depends on whether the user's dotfiles auto-activate
    it — most don't. Without an explicit activation step, the job's
    ``python`` may resolve to a pyenv shim, ``/usr/bin/python3``, or a
    different uv-managed interpreter than the one running this CLI.
    The symptoms range from silent "wrong package version" mismatches
    to outright ``ModuleNotFoundError``.

    We use the venv's ``bin/activate`` script when available since it
    sets ``VIRTUAL_ENV`` and prepends the right PATH the same way an
    interactive ``source .venv/bin/activate`` does. Fall back to
    ``None`` when nothing looks like a venv — better to inherit the
    parent's environment than to inject a broken activation command.
    """
    venv = os.environ.get("VIRTUAL_ENV")
    if not venv:
        # Not running under an activated venv. Try to derive from
        # ``sys.prefix`` — uv-run / pipx style invocations don't set
        # VIRTUAL_ENV but ``sys.prefix`` still points at the env root.
        if sys.prefix != sys.base_prefix:
            venv = sys.prefix
    if not venv:
        return None
    activate = Path(venv) / "bin" / "activate"
    if not activate.is_file():
        return None
    return f"source {shlex.quote(str(activate))}"


@dataclass(frozen=True)
class KWDaggerRuntime:
    queue_name: str
    root_dpath: Path
    devices: str
    tmux_workers: int
    backend: str
    run: bool
    skip_existing: bool = True


@dataclass(frozen=True)
class KWDaggerScheduleRequest:
    manifest_fpath: Path
    manifest: dict[str, Any]
    runtime: KWDaggerRuntime
    params_text: str
    # Populated only for containerized (``container_image``) manifests.
    resolved_image: ResolvedImage | None = None
    container_provenance: dict[str, Any] | None = None


def _resolve_manifest_override_path(value: str | None) -> str | None:
    if value is None:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = audit_root() / path
    return str(path.resolve())


def build_lease_matrix_entries(
    manifest: dict[str, Any],
    *,
    ttl_override: str | None = None,
    catalog_override: str | None = None,
    queue: bool = True,
) -> dict[str, Any]:
    """Build the per-run GPU-lease matrix knobs from a manifest's lease facts.

    The facts (``lease_endpoint`` for single-model manifests, or a
    ``lease_endpoints`` ``{model_deployment: catalog_endpoint}`` map for
    multi-model ones, plus ``lease_ttl`` / ``lease_catalog``) are baked into the
    manifest by ``export-benchmark-bundle``. These flow into the docker node's
    ``final_config`` where its ``setup``/``teardown`` properties render the
    ``infer-stack acquire``/``release`` bracket (one lease per run; ref-counting
    coalesces same-model runs, design §4). ``catalog`` is resolved to an absolute
    path so the lease command works from any job cwd. Raises if ``--lease`` was
    requested but the manifest carries no lease endpoint.
    """
    endpoint = manifest.get("lease_endpoint")
    endpoints = manifest.get("lease_endpoints")
    if not endpoint and not endpoints:
        raise ValueError(
            "leasing was requested but the manifest declares neither "
            "'lease_endpoint' nor 'lease_endpoints'. Re-materialize the bundle "
            "(eval-audit export-benchmark-bundle bakes the lease facts in) or "
            "schedule without --lease."
        )
    entries: dict[str, Any] = {}
    if endpoints:
        # JSON-encode the map so it crosses the kwdagger --params boundary as a
        # single scalar (the node's _coerce_map parses it back).
        entries["helm.lease_endpoints"] = [
            endpoints if isinstance(endpoints, str) else json.dumps(endpoints)
        ]
    if endpoint:
        entries["helm.lease_endpoint"] = [str(endpoint)]
    ttl = ttl_override or manifest.get("lease_ttl")
    if ttl:
        entries["helm.lease_ttl"] = [str(ttl)]
    catalog = catalog_override or manifest.get("lease_catalog")
    if catalog:
        entries["helm.lease_catalog"] = [str(Path(catalog).expanduser().resolve())]
    entries["helm.lease_queue"] = [bool(queue)]
    return entries


def build_schedule_params(
    manifest: dict[str, Any],
    resolved_image: ResolvedImage | None = None,
    lease_entries: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the ``kwdagger schedule --params`` payload from a manifest.

    Containerized execution is mandatory, so ``resolved_image`` must be set;
    every run goes through the docker pipeline. ``lease_entries`` (the per-run
    GPU-lease matrix knobs) merge in when present — leasing is the orthogonal
    axis, rendered by the docker node's lease bracket. Raises if no image was
    resolved (the bare host-venv pipelines have been removed).
    """
    matrix = {
        "helm.run_entry": list(manifest["run_entries"]),
        "helm.max_eval_instances": [manifest["max_eval_instances"]],
        "helm.precomputed_root": manifest.get("precomputed_root", None),
        "helm.suite": [manifest.get("suite", "audit-smoke")],
        "helm.require_per_instance_stats": [
            manifest.get("require_per_instance_stats", True)
        ],
        "helm.mode": [manifest.get("mode", "compute_if_missing")],
        "helm.materialize": [manifest.get("materialize", "symlink")],
        "helm.local_path": [manifest.get("local_path", "prod_env")],
    }
    model_deployments_fpath = manifest.get("model_deployments_fpath", None)
    if model_deployments_fpath is not None:
        matrix["helm.model_deployments_fpath"] = [model_deployments_fpath]
    enable_hf = manifest.get("enable_huggingface_models", [])
    if enable_hf:
        matrix["helm.enable_huggingface_models"] = [json.dumps(enable_hf)]
    enable_local_hf = manifest.get("enable_local_huggingface_models", [])
    if enable_local_hf:
        matrix["helm.enable_local_huggingface_models"] = [json.dumps(enable_local_hf)]

    if resolved_image is None:
        raise ValueError(
            "containerized execution is required: pass --container-image or set "
            "container_image in the manifest. The bare host-venv pipeline has "
            "been removed — every HELM run is pinned to a container image."
        )

    # Containerized execution: pin the (already-resolved) image and pass the
    # docker-runner knobs through to MaterializeHelmRunDockerNode.
    matrix["helm.container_image"] = [resolved_image.run_ref]
    matrix["helm.container_shm_size"] = [str(manifest.get("container_shm_size", "32g"))]
    if manifest.get("hf_cache_dir"):
        matrix["helm.hf_cache_dir"] = [manifest["hf_cache_dir"]]
    if manifest.get("container_gpus"):
        matrix["helm.container_gpus"] = [manifest["container_gpus"]]
    if manifest.get("container_ipc_host"):
        matrix["helm.container_ipc_host"] = [True]
    if manifest.get("container_network"):
        matrix["helm.container_network"] = [manifest["container_network"]]
    container_mounts = manifest.get("container_mounts") or []
    if container_mounts:
        matrix["helm.container_mounts"] = [json.dumps(container_mounts)]
    if lease_entries:
        matrix.update(lease_entries)
    return {"pipeline": _DOCKER_PIPELINE, "matrix": matrix}


def prepare_schedule_request(
    manifest_fpath: str | Path,
    *,
    run: bool = False,
    root_dpath: str | Path | None = None,
    queue_name: str | None = None,
    devices: str | None = None,
    tmux_workers: int | None = None,
    backend: str | None = None,
    container_image: str | None = None,
    lease: bool = False,
    lease_ttl: str | None = None,
    lease_catalog: str | None = None,
    lease_queue: bool = True,
) -> KWDaggerScheduleRequest:
    manifest_path = Path(manifest_fpath).expanduser().resolve()
    manifest = load_manifest(manifest_path)
    manifest = dict(manifest)
    manifest["model_deployments_fpath"] = _resolve_manifest_override_path(
        manifest.get("model_deployments_fpath", None)
    )
    experiment_name = str(manifest["experiment_name"])
    runtime_queue_name = (queue_name or f"audit-{experiment_name}").translate(
        str.maketrans({c: "-" for c in " !@#$%^&*()+={}[]|\\:;\"'<>,?/~`"})
    )

    # Per-run GPU leasing (opt-in, §5/§13). infer-stack owns every GPU, so the
    # HELM *client* must request none. Leasing and containerization are
    # orthogonal (see eval_audit.pipelines.lease_bracket): a containerized leased
    # client gets container_gpus="none"; a bare host-venv leased client is just
    # an HTTP caller to the served endpoint and uses no GPU regardless. Default
    # container_gpus to "none" so the *containerized* leased path never fights
    # infer-stack over GPU indices (inert on the bare path, which ignores
    # container knobs).
    lease_entries: dict[str, Any] | None = None
    if lease:
        manifest.setdefault("container_gpus", "none")
        lease_entries = build_lease_matrix_entries(
            manifest,
            ttl_override=lease_ttl,
            catalog_override=lease_catalog,
            queue=lease_queue,
        )

    # Containerized execution (opt-in). A CLI override wins over the manifest.
    if container_image is not None:
        manifest["container_image"] = container_image
    resolved_image, container_provenance = _prepare_container_execution(
        manifest, experiment_name
    )

    params = build_schedule_params(
        manifest, resolved_image=resolved_image, lease_entries=lease_entries
    )
    runtime = KWDaggerRuntime(
        queue_name=runtime_queue_name,
        root_dpath=(
            Path(root_dpath).expanduser().resolve()
            if root_dpath is not None
            else experiment_result_dpath(experiment_name)
        ),
        devices=str(devices if devices is not None else manifest.get("devices", "0,1")),
        tmux_workers=int(
            tmux_workers
            if tmux_workers is not None
            else manifest.get("tmux_workers", 2)
        ),
        backend=str(backend if backend is not None else manifest.get("backend", "tmux")),
        run=bool(run),
    )
    return KWDaggerScheduleRequest(
        manifest_fpath=manifest_path,
        manifest=manifest,
        runtime=runtime,
        params_text=dump_yaml(params),
        resolved_image=resolved_image,
        container_provenance=container_provenance,
    )


def _prepare_container_execution(
    manifest: dict[str, Any],
    experiment_name: str,
) -> tuple[ResolvedImage | None, dict[str, Any] | None]:
    """Resolve+pin the container image and build the experiment provenance record.

    Mutates ``manifest`` in place to hold absolute host paths for the HF cache
    and precomputed root (so the docker node can bind-mount them at identical
    paths). Returns ``(None, None)`` for the bare-python path.
    """
    container_image = manifest.get("container_image")
    if not container_image:
        return None, None

    runtime_name = str(manifest.get("container_runtime", "docker"))

    # HF cache: resolve to an absolute path and create it as the host user so the
    # bind mount does not materialize a root-owned source dir under the container.
    hf_cache_dir = manifest.get("hf_cache_dir")
    if hf_cache_dir:
        hf_path = Path(hf_cache_dir).expanduser().resolve()
        hf_path.mkdir(parents=True, exist_ok=True)
        manifest["hf_cache_dir"] = str(hf_path)

    # precomputed_root is bind-mounted at the same absolute path inside the
    # container, so resolve it now.
    precomputed_root = manifest.get("precomputed_root")
    if precomputed_root:
        manifest["precomputed_root"] = str(Path(precomputed_root).expanduser().resolve())

    resolved_image = resolve_image_digest(str(container_image), runtime=runtime_name)
    provenance = {
        "schema": "eval-audit/container-provenance/1",
        "experiment_name": experiment_name,
        "container_runtime": runtime_name,
        "runtime_version": runtime_version(runtime_name),
        "image": resolved_image.to_dict(),
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    }
    return resolved_image, provenance


def kwdagger_schedule_argv(request: KWDaggerScheduleRequest) -> list[str]:
    # FIXME(kwdagger): kwdagger currently makes this integration awkward because
    # --params may be either inline YAML text or a YAML file path.
    argv = [
        "kwdagger",
        "schedule",
        f"--queue_name={request.runtime.queue_name}",
        f"--params={request.params_text}",
        f"--devices={request.runtime.devices}",
        f"--tmux_workers={request.runtime.tmux_workers}",
        f"--root_dpath={request.runtime.root_dpath}",
        f"--backend={request.runtime.backend}",
        f"--skip_existing={1 if request.runtime.skip_existing else 0}",
        f"--run={1 if request.runtime.run else 0}",
        # Tee per-node stdout/stderr to info_dpath/status/<pathid>.logs so
        # cmd_queue's failure surfacing has content to display when a job
        # crashes before helm-run starts (i.e. before
        # materialize_helm.run_helm captures cmd_stdout.txt/cmd_stderr.txt).
        "--log=True",
        "--monitor=tmux",
    ]
    # Auto-activate the running venv inside each spawned job. See
    # _detect_virtualenv_cmd for rationale. This is purely additive —
    # if no venv is detected, the spawned jobs inherit the parent's
    # environment as before.
    venv_cmd = _detect_virtualenv_cmd()
    if venv_cmd:
        argv.append(f"--virtualenv_cmd={venv_cmd}")
    return argv


def kwdagger_schedule_command_text(request: KWDaggerScheduleRequest) -> str:
    return shlex.join(kwdagger_schedule_argv(request))


def run_kwdagger_schedule(request: KWDaggerScheduleRequest) -> subprocess.CompletedProcess[str]:
    request.runtime.root_dpath.mkdir(parents=True, exist_ok=True)
    # Record the experiment-level container provenance (requested → resolved
    # digest) alongside the results so the image is auditable after the fact.
    if request.container_provenance is not None:
        write_container_provenance(request.runtime.root_dpath, request.container_provenance)
    return subprocess.run(
        kwdagger_schedule_argv(request),
        check=True,
        text=True,
    )

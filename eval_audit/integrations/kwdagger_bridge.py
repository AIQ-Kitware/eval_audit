from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from eval_audit.manifests.run_spec_materializer import MaterializedRunSpec

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

# Faithful-replay variant: deserialize the official run_spec.json and replay the
# resolved recipe verbatim (instead of reconstructing a run-entry string). Same
# containerized wrapper; selected when the manifest sets ``from_run_spec: true``.
# Because the selection sits *after* the ``resolved_image is None`` raise in
# build_schedule_params, this path inherits mandatory containerization for free.
_DOCKER_FROM_SPEC_PIPELINE = (
    "eval_audit.pipelines.helm_docker_pipeline.helm_single_run_from_spec_docker_pipeline()"
)


def _locator_run_entry(run_entry: str) -> str:
    """Drop any ``model_deployment=<name>`` token from a run-entry label.

    The from-spec node uses the run-entry as ``requested_desc`` to LOCATE the
    produced run dir (``find_run_in_out_dpath`` / ``find_best_precomputed_run``,
    a token-subset match). But HELM run dir names encode ``model=…`` and never
    ``model_deployment=…`` — the deployment rewrite leaves ``run_spec.name``
    unchanged (see ``apply_adapter_substitutions``). So an inline
    ``model_deployment=`` token — carried by exact-path *multi-deployment*
    run-entries as the per-run rewrite target (rel-path plan §6) — is spurious
    for locating: it is never a subset of the produced dir name, so the match
    fails and the node raises "produced run directory could not be located". Strip
    it here so the locator query is the same bare discovery key the single-model
    path (and ``08_check_discovery``) already resolve 1:1. The deployment itself is
    unaffected — it is baked into the materialized ``run_spec.json`` and recorded
    on the index row.

    R-8: delegates to the shared unconditional strip.
    """
    from eval_audit.helm.run_entries import strip_model_deployment

    return strip_model_deployment(run_entry)[0]


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
    timeout_override: str | None = None,
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
    entries.update(
        build_broadcast_lease_knobs(
            manifest, ttl_override=ttl_override,
            timeout_override=timeout_override,
            catalog_override=catalog_override, queue=queue,
        )
    )
    return entries


def build_broadcast_lease_knobs(
    manifest: dict[str, Any],
    *,
    ttl_override: str | None = None,
    timeout_override: str | None = None,
    catalog_override: str | None = None,
    queue: bool = True,
) -> dict[str, Any]:
    """The endpoint-free lease knobs (ttl / catalog / queue), broadcast to all runs.

    Used directly by the **exact-path** replay path, where each run's
    ``lease_endpoint`` is carried per-run in the submatrix (resolved at schedule
    time) rather than parsed from a run-entry — so only these three broadcast
    knobs come from the manifest. ``build_lease_matrix_entries`` reuses this for
    the run-entry path after adding the endpoint(s).
    """
    entries: dict[str, Any] = {}
    ttl = ttl_override or manifest.get("lease_ttl")
    if ttl:
        entries["helm.lease_ttl"] = [str(ttl)]
    timeout = timeout_override or manifest.get("lease_timeout")
    if timeout:
        entries["helm.lease_timeout"] = [str(timeout)]
    catalog = catalog_override or manifest.get("lease_catalog")
    if catalog:
        entries["helm.lease_catalog"] = [str(Path(catalog).expanduser().resolve())]
    entries["helm.lease_queue"] = [bool(queue)]
    return entries


def build_schedule_params(
    manifest: dict[str, Any],
    resolved_image: ResolvedImage | None = None,
    lease_entries: dict[str, Any] | None = None,
    *,
    materialized_runs: list["MaterializedRunSpec"] | None = None,
    staging_root: str | None = None,
) -> dict[str, Any]:
    """Build the ``kwdagger schedule --params`` payload from a manifest.

    Containerized execution is mandatory, so ``resolved_image`` must be set;
    every run goes through the docker pipeline. ``lease_entries`` (the GPU-lease
    matrix knobs) merge in when present — leasing is the orthogonal axis,
    rendered by the docker node's lease bracket. Raises if no image was resolved
    (the bare host-venv pipelines have been removed).

    Three execution shapes, in priority order:

    * **Exact-path replay** (``from_run_spec`` + ``materialized_runs``): the
      schedule-time materializer already resolved + substituted each official
      ``run_spec.json`` into a staging copy. Each run is one ``submatrices`` entry
      (kwdagger's zip primitive) carrying its materialized spec path, label, and
      per-run lease endpoint — no run-entry token discovery, no corpus mount.
    * **From-spec discovery** (``from_run_spec``, no materialized runs): the legacy
      run-entry path that locates the official dir in-container; requires
      ``precomputed_root``.
    * **Run-entry reconstruction** (default).
    """
    if resolved_image is None:
        raise ValueError(
            "containerized execution is required: pass --container-image or set "
            "container_image in the manifest. The bare host-venv pipeline has "
            "been removed — every HELM run is pinned to a container image."
        )

    # Broadcast knobs shared by every execution shape.
    matrix: dict[str, Any] = {
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

    # Containerized execution: pin the (already-resolved) image and pass the
    # docker-runner knobs through to the docker node.
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

    # --- Exact-path replay (materialized run_spec.json copies) -----------------
    # The submatrix is kwdagger's zip primitive (verified in
    # tests/test_kwdagger_submatrix_contract.py): one dict per run carrying that
    # run's materialized spec path + label + (optional) lease endpoint, so the
    # per-run tuple travels together with no NxN cross-product. Substitution is
    # already baked into the copy, so no model_deployment / max_eval_instances
    # rewrite is emitted, and precomputed_root is NOT mounted — the recipe source
    # is the tiny staging dir, not the corpus. See run-from-relative-path-plan.md.
    if manifest.get("from_run_spec") and materialized_runs:
        if staging_root:
            matrix["helm.staging_root"] = [str(staging_root)]
        submatrices: list[dict[str, Any]] = []
        for rec in materialized_runs:
            entry: dict[str, Any] = {
                "helm.run_spec_json": rec.run_spec_json,
                # Strip the inline model_deployment token: the node uses this as
                # the locator query, and HELM dir names never encode
                # model_deployment (see _locator_run_entry). Without this, a
                # multi-deployment freeze can't locate its produced run dir.
                "helm.run_entry": _locator_run_entry(rec.run_entry),
            }
            # The frozen source records each run's lease endpoint, but leasing is
            # opt-in: only emit it (⇒ only render the acquire/release bracket) when
            # leasing was actually requested (lease_entries present). Otherwise a
            # frozen endpoint would force-lease a non-leased run. Mirrors the
            # run-entry path, where lease_endpoint reaches cfg only via lease_entries.
            if lease_entries and rec.lease_endpoint:
                entry["helm.lease_endpoint"] = rec.lease_endpoint
            submatrices.append(entry)
        matrix["submatrices"] = submatrices
        return {"pipeline": _DOCKER_FROM_SPEC_PIPELINE, "matrix": matrix}

    # --- Run-entry axis (run-entry reconstruction OR from-spec discovery) ------
    # D-5: the "official" verbatim-replay sentinel is only realizable on the
    # exact-path replay branch (which returned above); these paths pass the cap to
    # helm-run as an integer and cannot honor it. builders.main already blocks this
    # combination, so reaching here is a contract violation.
    if manifest.get("max_eval_instances") == "official":
        raise ValueError(
            "max_eval_instances='official' is only supported on the exact-path "
            "replay path (run_spec_sources); the run-entry / from-spec-discovery "
            "paths require a numeric cap."
        )
    matrix["helm.run_entry"] = list(manifest["run_entries"])
    matrix["helm.max_eval_instances"] = [manifest["max_eval_instances"]]
    matrix["helm.precomputed_root"] = manifest.get("precomputed_root", None)

    if manifest.get("from_run_spec"):
        if not manifest.get("precomputed_root"):
            raise ValueError(
                "from_run_spec=true requires either 'run_spec_sources' (exact-path "
                "replay, the preferred form) or 'precomputed_root' (run-entry "
                "discovery): the official run_spec.json must be locatable. Set "
                "--run-spec-rel-path / --precomputed-root (eval-audit-make-manifest)."
            )
        # Deployment-rewrite target (opt-in). When set, the from-spec node rewrites
        # adapter_spec.model_deployment to this LOCAL name so the produced run
        # records the served endpoint (same_deployment=no). Only added on the
        # from-spec branch — the run-entry node does not declare model_deployment,
        # so it would reject the matrix key. Omitted when unset (pure by-name).
        model_deployment = manifest.get("model_deployment")
        if model_deployment is not None:
            matrix["helm.model_deployment"] = [model_deployment]
        return {"pipeline": _DOCKER_FROM_SPEC_PIPELINE, "matrix": matrix}
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
    lease_timeout: str | None = None,
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

    # Containerized execution (opt-in). A CLI override wins over the manifest.
    # Done before materialization so precomputed_root is resolved to an absolute
    # host path (the materializer reads the official run_spec.json from it).
    if container_image is not None:
        manifest["container_image"] = container_image
    resolved_image, container_provenance = _prepare_container_execution(
        manifest, experiment_name
    )

    # Experiment result root (the runtime uses it too) — resolved up-front so the
    # materializer can stage substituted run_spec.json copies under it.
    resolved_root = (
        Path(root_dpath).expanduser().resolve()
        if root_dpath is not None
        else experiment_result_dpath(experiment_name)
    )

    # Exact-path replay (rel-path plan): on the host, before kwdagger, resolve
    # each (precomputed_root, rel_path) to the official run_spec.json and write a
    # substituted copy to a staging dir. The matrix then carries materialized copy
    # paths (one submatrix entry per run), not run-entry token-discovery keys.
    materialized_runs: list[MaterializedRunSpec] | None = None
    staging_root: str | None = None
    if manifest.get("from_run_spec") and manifest.get("run_spec_sources"):
        from eval_audit.manifests.run_spec_materializer import (
            coerce_sources,
            materialize_run_specs,
        )

        precomputed_root = manifest.get("precomputed_root")
        if not precomputed_root:
            raise ValueError(
                "from_run_spec with run_spec_sources requires 'precomputed_root' "
                "(the host root the rel_paths resolve against)."
            )
        staging_root = str((resolved_root / "materialized_run_specs").resolve())
        # D-5: the "official" sentinel means "keep the official run_spec.json cap".
        # Translate it to default_max_eval_instances=None so the materializer leaves
        # adapter_spec.max_eval_instances untouched (records no substitution).
        manifest_cap = manifest.get("max_eval_instances")
        default_cap = None if manifest_cap == "official" else manifest_cap
        materialized_runs = materialize_run_specs(
            coerce_sources(manifest["run_spec_sources"]),
            precomputed_root=precomputed_root,
            staging_dir=staging_root,
            default_max_eval_instances=default_cap,
        )

    # Per-run GPU leasing (opt-in, §5/§13). infer-stack owns every GPU, so the
    # HELM *client* must request none (container_gpus="none"). On the exact-path
    # replay each run's lease endpoint is carried per-run in the submatrix, so
    # only the broadcast knobs (ttl/catalog/queue) come from the manifest here;
    # the run-entry path resolves the endpoint from the manifest's lease facts.
    lease_entries: dict[str, Any] | None = None
    if lease:
        manifest.setdefault("container_gpus", "none")
        if materialized_runs is not None:
            lease_entries = build_broadcast_lease_knobs(
                manifest, ttl_override=lease_ttl,
                timeout_override=lease_timeout,
                catalog_override=lease_catalog, queue=lease_queue,
            )
        else:
            lease_entries = build_lease_matrix_entries(
                manifest, ttl_override=lease_ttl,
                timeout_override=lease_timeout,
                catalog_override=lease_catalog, queue=lease_queue,
            )

    params = build_schedule_params(
        manifest,
        resolved_image=resolved_image,
        lease_entries=lease_entries,
        materialized_runs=materialized_runs,
        staging_root=staging_root,
    )
    runtime = KWDaggerRuntime(
        queue_name=runtime_queue_name,
        root_dpath=resolved_root,
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

        # Materialize the resolved HF token into the mounted cache as the
        # on-disk credential the container actually reads. The docker node also
        # emits ``-e HF_TOKEN -e HUGGING_FACE_HUB_TOKEN``, but that bare
        # ``-e VAR`` form only forwards a value already present in the *job*
        # shell's environment — and kwdagger runs each job in a fresh tmux pane
        # that does NOT inherit this scheduling shell's ad-hoc exports
        # (cmd_queue's tmux backend ships an empty worker environ by design, to
        # avoid logging secrets to plaintext). So a token exported by e.g.
        # reproduce/olmo_models/_lib.sh never reaches the container that way.
        # This process *did* inherit it (eval-audit-run runs in the user's
        # shell), so write it to ``<hf_cache_dir>/token`` — which the container
        # reads at ``$HF_HOME/token`` (HF_HOME=/hf-cache) — restoring the on-disk
        # auth channel the bare host-venv path used to get for free. Mirrors
        # huggingface_hub's own precedence (env wins over file): write whenever
        # the env carries a token, but stay idempotent so we don't churn a token
        # a user logged into this dir directly (env empty => left untouched).
        env_token = (
            os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN") or ""
        ).strip()
        if env_token:
            token_fpath = hf_path / "token"
            if not token_fpath.exists() or token_fpath.read_text().strip() != env_token:
                # P2: write_text + later chmod left a world-readable window on
                # the secret. Open O_CREAT|O_TRUNC and fchmod to 0600 BEFORE
                # writing the token, so it is never on disk with looser perms.
                fd = os.open(str(token_fpath), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                try:
                    os.fchmod(fd, 0o600)
                    os.write(fd, (env_token + "\n").encode())
                finally:
                    os.close(fd)

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

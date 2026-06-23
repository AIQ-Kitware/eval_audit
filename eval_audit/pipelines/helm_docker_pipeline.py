r"""Containerized variant of the magnet HELM materialization pipeline.

This is the eval_audit-owned counterpart of
``magnet.backends.helm.pipeline.helm_single_run_pipeline``. It reuses the magnet
:class:`MaterializeHelmRunNode` contract (same ``out_paths`` / ``DONE`` sentinel
/ algo identity) but overrides the rendered command so each run executes inside
a pinned Docker image:

    docker run --rm --gpus ... -v <out>:<out> ... <image>@sha256:<digest> \
        python -m magnet.backends.helm.cli.materialize_helm_run --run_entry=... --out_dpath=<out> ...

The aiq-magnet submodule is left untouched; the containerization concern lives
with the auditor. The bridge (``eval_audit.integrations.kwdagger_bridge``)
selects this pipeline factory and supplies the pinned ``container_image`` digest
plus mount/runtime params when a manifest opts into containerized execution.

Correctness notes (validated against kwdagger / cmd_queue):
* ``out_dpath`` resolves to the absolute node directory in ``final_config`` at
  command-render time, and is bind-mounted at the *same* absolute path so
  kwdagger's DONE completion check and any reuse symlinks resolve identically on
  the host.
* ``precomputed_root`` / ``model_deployments_fpath`` / local HF model dirs are
  mounted read-only at their same host paths so the in-container CLI reads them
  unchanged.
* ``$CUDA_VISIBLE_DEVICES`` is set per worker by cmd_queue's tmux/serial backend;
  ``--gpus "device=$CUDA_VISIBLE_DEVICES"`` exposes exactly the assigned GPU(s).
* ``docker run`` propagates the container exit code, and the inner CLI writes
  DONE last, so failures surface and ``skip_existing`` keeps working.
"""

from __future__ import annotations

import json
import shlex
from typing import Any

import kwdagger

from magnet.backends.helm.pipeline import MaterializeHelmRunNode

# final_config keys that configure the container wrapper. They are consumed when
# rendering ``docker run`` and are NOT forwarded to the inner materialize CLI.
_CONTAINER_KEYS = frozenset(
    {
        "container_image",
        "hf_cache_dir",
        "container_gpus",
        "container_shm_size",
        "container_ipc_host",
        "container_mounts",
        "container_network",
    }
)

# final_config keys that configure the per-run infer-stack GPU lease (the job
# setup/teardown bracket). Like the container knobs, they shape the rendered
# ``setup``/``teardown`` shell and are NOT forwarded to the inner materialize
# CLI. ``lease_endpoint`` is the scalar single-model form; ``lease_endpoints`` is
# a JSON map ``{model_deployment_name: catalog_endpoint}`` for multi-model
# manifests, resolved per run-entry against the entry's ``model_deployment=``.
_LEASE_KEYS = frozenset(
    {
        "lease_endpoint",
        "lease_endpoints",
        "lease_ttl",
        "lease_catalog",
        "lease_queue",
        "lease_snapshot",
    }
)

# Default soft TTL for a pipeline lease when a manifest does not pin one. Must
# exceed worst-case (model cold-load + the run) so a hard-killed job's leaked
# lease is reclaimed by the admission queue's sweep / ``infer-stack gc`` rather
# than expiring mid-run (design §8). Per-endpoint overrides flow via the
# manifest's ``lease_ttl``.
_DEFAULT_LEASE_TTL = "4h"

# The fixed gateway path each lease.env is written under, relative to the node's
# own output dir (``out_dpath``). setup writes it (``acquire --env-file``);
# teardown reads it (``release --env-file``). Per-node so concurrent jobs never
# share a lease handle.
_LEASE_ENV_BASENAME = "lease.env"


def _coerce_list(value: Any) -> list[Any]:
    """Coerce a matrix value (list, or JSON/space string) into a list."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return [text]
        return parsed if isinstance(parsed, list) else [parsed]
    return [value]


def _coerce_map(value: Any) -> dict[str, str]:
    """Coerce a matrix value (dict, or JSON string) into a ``{str: str}`` map."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError(f"lease_endpoints must be a JSON object, got {value!r}")
        return {str(k): str(v) for k, v in parsed.items()}
    raise ValueError(f"cannot coerce {value!r} into a lease-endpoints map")


def _parse_model_deployment(run_entry: Any) -> str | None:
    """Extract the ``model_deployment=<name>`` token from a HELM run-entry.

    HELM run-entries are ``scenario:key=value,key=value,...`` — the
    ``model_deployment`` knob names the model_deployments.yaml entry (and, via
    the preset profile, the catalog endpoint). Returns ``None`` if absent.
    """
    if not run_entry:
        return None
    for token in str(run_entry).split(","):
        token = token.strip()
        if token.startswith("model_deployment="):
            return token.split("=", 1)[1].strip()
    return None


def _resolve_lease_endpoint(cfg: dict[str, Any]) -> str | None:
    """Resolve the catalog endpoint this run must lease, or ``None`` if leasing
    is not requested for it.

    Scalar ``lease_endpoint`` wins for single-model manifests. A
    ``lease_endpoints`` map (multi-model manifests) is resolved against the
    run-entry's ``model_deployment=`` token; a single-entry map degenerates to
    its sole value. A non-empty map that cannot be resolved is a hard error (the
    C-3 name-chain hazard — better to fail the schedule than route a run at a
    model that was never leased).
    """
    endpoints_map = _coerce_map(cfg.get("lease_endpoints"))
    if endpoints_map:
        deployment = _parse_model_deployment(cfg.get("run_entry"))
        if deployment is not None and deployment in endpoints_map:
            return endpoints_map[deployment]
        if deployment is None and len(endpoints_map) == 1:
            return next(iter(endpoints_map.values()))
        raise ValueError(
            "lease_endpoints map cannot be resolved for run_entry "
            f"{cfg.get('run_entry')!r}: model_deployment={deployment!r} not in "
            f"{sorted(endpoints_map)}. Pin model_deployment=<name> in the "
            "run-entry so its lease endpoint is unambiguous."
        )
    endpoint = cfg.get("lease_endpoint")
    return str(endpoint) if endpoint else None


def render_lease_setup(cfg: dict[str, Any]) -> str | None:
    """Render the infer-stack ``acquire`` setup for one HELM-run node.

    The setup is a *gating precondition* (cmd_queue runs it before the command
    and skips the command if it fails). It queues-and-waits for the model's GPU
    (``--queue``), writes a per-node lease handle, and — best-effort — snapshots
    the concurrency context (co-held lease ``demand``) the determinism study
    consumes (design §7). Returns ``None`` when no lease is requested.
    """
    endpoint = _resolve_lease_endpoint(cfg)
    if not endpoint:
        return None
    out_dpath = str(cfg["out_dpath"])
    q = shlex.quote
    env_file = q(f"{out_dpath}/{_LEASE_ENV_BASENAME}")
    ttl = str(cfg.get("lease_ttl") or _DEFAULT_LEASE_TTL)
    queue = cfg.get("lease_queue")
    queue = True if queue is None else bool(queue)

    acquire = ["infer-stack", "acquire", q(endpoint), "--ttl", q(ttl), "--yes"]
    if queue:
        acquire.append("--queue")
    acquire += ["--env-file", env_file]
    catalog = cfg.get("lease_catalog")
    if catalog:
        acquire += ["--catalog", q(str(catalog))]

    # Ensure the node dir exists before acquire writes lease.env into it (the
    # inner materialize CLI creates out_dpath, but it has not run yet at setup
    # time). Chained with && so a failed acquire still gates the command.
    parts = [f"mkdir -p {q(out_dpath)}", " ".join(acquire)]

    snapshot = cfg.get("lease_snapshot")
    snapshot = True if snapshot is None else bool(snapshot)
    if snapshot:
        snap_path = q(f"{out_dpath}/concurrency_snapshot.json")
        # Best-effort: a snapshot hiccup must never fail the (gating) setup. The
        # ``|| true`` is scoped INSIDE the brace group so it swallows only the
        # snapshot's status — the preceding ``&& acquire`` still gates, so a
        # failed acquire keeps the whole chain false (PREAMBLE_OK=0) and the
        # HELM command is correctly skipped. Records co-held demand at run start
        # for the agreement-vs-concurrency analysis (design §7).
        parts.append(f"{{ infer-stack leases --json > {snap_path} 2>/dev/null || true ; }}")
    return " && ".join(parts)


def render_lease_teardown(cfg: dict[str, Any]) -> str | None:
    """Render the infer-stack ``release`` teardown for one HELM-run node.

    cmd_queue arms this as an always-run trap (EXIT/INT/TERM) *only* if setup
    succeeded, so it releases exactly the lease setup acquired — on success,
    failure, and SIGTERM. Returns ``None`` when no lease is requested.
    """
    endpoint = _resolve_lease_endpoint(cfg)
    if not endpoint:
        return None
    out_dpath = str(cfg["out_dpath"])
    env_file = shlex.quote(f"{out_dpath}/{_LEASE_ENV_BASENAME}")
    return f"infer-stack release --env-file {env_file}"


class MaterializeHelmRunDockerNode(MaterializeHelmRunNode):
    """Run ``materialize_helm_run`` inside a pinned Docker image.

    Inherits the magnet node's ``name='helm'``, ``out_paths``,
    ``primary_out_key='done_fname'`` and inner ``executable`` so the kwdagger
    completion/identity contract is unchanged; only ``command`` is overridden.
    """

    # ``container_image`` participates in algo identity: a different image ⇒ a
    # new job folder (so re-running against a new image recomputes).
    algo_params = {
        **MaterializeHelmRunNode.algo_params,
        "container_image": None,
    }

    # Runtime/mount knobs do not change the logical meaning of the output.
    # The lease_* knobs configure the per-run GPU-lease bracket (setup/teardown);
    # they describe *how* the model is served, not *what* is computed, so they
    # are perf (recorded, identity-neutral) and are stripped from the inner CLI.
    perf_params = {
        **MaterializeHelmRunNode.perf_params,
        "hf_cache_dir": None,
        "container_gpus": None,
        "container_shm_size": "32g",
        "container_ipc_host": False,
        "container_mounts": None,
        "container_network": None,
        "lease_endpoint": None,
        "lease_endpoints": None,
        "lease_ttl": None,
        "lease_catalog": None,
        "lease_queue": None,
        "lease_snapshot": None,
    }

    @property
    def setup(self) -> str | None:
        """Acquire this run's model lease before the HELM command (cmd_queue
        gating precondition). Computed per matrix-point from ``final_config`` so
        each run brackets its own endpoint + per-node lease handle; ``None`` when
        the manifest requests no lease (the bare, pre-leasing behavior)."""
        return render_lease_setup(dict(self.final_config))

    @setup.setter
    def setup(self, value: Any) -> None:
        # ``ProcessNode.__init__`` assigns ``setup`` (default ``None``) via
        # _classvar_init's setattr; we derive it dynamically from final_config
        # instead, so absorb and ignore that one construction-time assignment.
        pass

    @property
    def teardown(self) -> str | None:
        """Release this run's model lease after the HELM command, always (trap
        EXIT/INT/TERM). ``None`` when the manifest requests no lease."""
        return render_lease_teardown(dict(self.final_config))

    @teardown.setter
    def teardown(self, value: Any) -> None:
        # See ``setup.setter``: absorb _classvar_init's construction assignment.
        pass

    def _render_inner_command(self, cfg: dict[str, Any]) -> str:
        """Render the inner ``materialize_helm_run`` command (magnet style)."""
        parts: list[str] = []
        for key, value in cfg.items():
            if value is None:
                continue
            if isinstance(value, list) and len(value) == 0:
                continue
            if isinstance(value, dict):
                from kwutil.util_yaml import Yaml

                value_text = shlex.quote(Yaml.dumps(value))
                if "\n" in value_text and value_text[0] == "'":
                    value_text = "'\n" + value_text[1:]
            else:
                value_text = shlex.quote(str(value))
            parts.append(f"--{key}={value_text}")
        if not parts:
            return self.executable
        return self.executable + " \\\n        " + " \\\n        ".join(parts)

    @property
    def command(self) -> str:
        cfg = dict(self.final_config)

        image = cfg.get("container_image")
        if not image:
            raise ValueError(
                "MaterializeHelmRunDockerNode requires a 'container_image' "
                "(the bridge should supply a pinned digest reference)."
            )
        image = str(image)
        out_dpath = str(cfg["out_dpath"])

        q = shlex.quote
        gpus = cfg.get("container_gpus")
        shm_size = cfg.get("container_shm_size") or "32g"
        ipc_host = bool(cfg.get("container_ipc_host"))
        network = cfg.get("container_network")

        lines: list[str] = ["docker run --rm"]

        # Network namespace. Omitted => Docker's default bridge (correct when
        # HELM loads the model in-process). "host" => share the host namespace
        # so the in-container HELM client can reach a model server published on
        # the host's localhost (e.g. a vLLM/LiteLLM endpoint).
        if network is not None and str(network).strip():
            lines.append(f"--network {q(str(network))}")

        # GPU exposure. None => follow the scheduler's per-worker assignment;
        # "none"/"" => omit (CPU runs / local smoke tests); else use verbatim.
        if gpus is None:
            lines.append('--gpus "device=${CUDA_VISIBLE_DEVICES:-all}"')
        elif str(gpus).strip().lower() not in ("none", ""):
            lines.append(f"--gpus {q(str(gpus))}")

        # Shared memory for torch dataloaders / NCCL.
        if ipc_host:
            lines.append("--ipc=host")
        else:
            lines.append(f"--shm-size={q(str(shm_size))}")

        # Environment. Shell substitutions are intentionally unquoted so they
        # expand in the job's bash at run time. Bare ``-e VAR`` forwards the
        # value from the worker environment only when it is set.
        lines.append("-e HOST_UID=$(id -u) -e HOST_GID=$(id -g)")
        lines.append("-e HF_HOME=/hf-cache")
        lines.append("-e HF_TOKEN -e HUGGING_FACE_HUB_TOKEN")
        lines.append(f"-e EVAL_AUDIT_OUT_DPATH={q(out_dpath)}")
        lines.append(f"-e EVAL_AUDIT_CONTAINER_IMAGE={q(image)}")
        if "@sha256:" in image:
            lines.append(f"-e EVAL_AUDIT_CONTAINER_DIGEST={q(image.split('@', 1)[1])}")

        # Mounts. out_dpath at the same absolute path (read-write); inputs are
        # mounted read-only at their same host paths.
        lines.append(f"-v {q(out_dpath)}:{q(out_dpath)}")
        hf_cache_dir = cfg.get("hf_cache_dir")
        if hf_cache_dir:
            lines.append(f"-v {q(str(hf_cache_dir))}:/hf-cache")
        precomputed_root = cfg.get("precomputed_root")
        if precomputed_root:
            p = q(str(precomputed_root))
            lines.append(f"-v {p}:{p}:ro")
        model_deployments_fpath = cfg.get("model_deployments_fpath")
        if model_deployments_fpath:
            m = q(str(model_deployments_fpath))
            lines.append(f"-v {m}:{m}:ro")
        for local_model in _coerce_list(cfg.get("enable_local_huggingface_models")):
            d = q(str(local_model))
            lines.append(f"-v {d}:{d}:ro")
        for mount in _coerce_list(cfg.get("container_mounts")):
            lines.append(f"-v {q(str(mount))}")

        lines.append(f"-w {q(out_dpath)}")
        lines.append(q(image))

        docker_prefix = " \\\n    ".join(lines)

        inner_cfg = {
            k: v
            for k, v in cfg.items()
            if k not in _CONTAINER_KEYS and k not in _LEASE_KEYS
        }
        inner_command = self._render_inner_command(inner_cfg)

        return docker_prefix + " \\\n    " + inner_command


def helm_single_run_docker_pipeline():
    """kwdagger pipeline factory: a single containerized HELM materialize node.

    Referenced by ``kwdagger schedule`` via the fully-qualified path
    ``eval_audit.pipelines.helm_docker_pipeline.helm_single_run_docker_pipeline()``.
    """
    nodes = {
        "materialize_helm_run": MaterializeHelmRunDockerNode(),
    }
    return kwdagger.Pipeline(nodes)

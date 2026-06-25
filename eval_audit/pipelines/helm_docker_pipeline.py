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

The per-run infer-stack GPU-lease bracket (``setup``/``teardown``) is
transport-agnostic and lives in :mod:`eval_audit.pipelines.lease_bracket`; this
node mixes it in. The bare host-venv counterpart
(:mod:`eval_audit.pipelines.helm_leased_pipeline`) mixes in the *same* bracket —
leasing and containerization are orthogonal.

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

from eval_audit.pipelines.lease_bracket import (
    LEASE_KEYS as _LEASE_KEYS,
    LEASE_PERF_PARAMS,
    LeaseBracketMixin,
    render_magnet_command,
)

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


class MaterializeHelmRunDockerNode(LeaseBracketMixin, MaterializeHelmRunNode):
    """Run ``materialize_helm_run`` inside a pinned Docker image.

    Inherits the magnet node's ``name='helm'``, ``out_paths``,
    ``primary_out_key='done_fname'`` and inner ``executable`` so the kwdagger
    completion/identity contract is unchanged; only ``command`` is overridden.
    The ``setup``/``teardown`` lease bracket comes from :class:`LeaseBracketMixin`.
    """

    # ``container_image`` participates in algo identity: a different image ⇒ a
    # new job folder (so re-running against a new image recomputes).
    algo_params = {
        **MaterializeHelmRunNode.algo_params,
        "container_image": None,
    }

    # Runtime/mount knobs do not change the logical meaning of the output.
    # The lease_* knobs (LEASE_PERF_PARAMS) configure the per-run GPU-lease
    # bracket (setup/teardown); they describe *how* the model is served, not
    # *what* is computed, so they are perf (recorded, identity-neutral) and are
    # stripped from the inner CLI.
    perf_params = {
        **MaterializeHelmRunNode.perf_params,
        "hf_cache_dir": None,
        "container_gpus": None,
        "container_shm_size": "32g",
        "container_ipc_host": False,
        "container_mounts": None,
        "container_network": None,
        **LEASE_PERF_PARAMS,
    }

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

        # The container/lease knobs configure the wrapper + the lease bracket;
        # strip them so only the materialize algo/perf params reach the inner CLI.
        inner_command = render_magnet_command(
            self.executable, cfg, exclude=_CONTAINER_KEYS | _LEASE_KEYS
        )
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


class MaterializeHelmRunFromSpecDockerNode(MaterializeHelmRunDockerNode):
    """Run ``materialize_helm_run_from_spec`` inside a pinned Docker image.

    The faithful-replay counterpart of :class:`MaterializeHelmRunDockerNode`:
    instead of reconstructing a run-entry string and re-parsing it through
    ``helm-run``, the in-container CLI deserializes the official
    ``run_spec.json`` (delivered by the ``precomputed_root`` ``:ro`` mount, which
    in this mode is the *recipe source*) and replays the fully-resolved recipe
    verbatim via ``run_benchmarking``.

    Everything about the container wrapper is identical — same mounts,
    ``out_paths``, ``primary_out_key='done_fname'``, and lease bracket — so only
    the inner ``executable`` and the added ``model_deployment`` algo param differ.

    ``model_deployment`` is the optional deployment-rewrite target: when set, the
    inner CLI rewrites ``adapter_spec.model_deployment`` to this LOCAL name so the
    produced run records the served endpoint (``same_deployment=no``) instead of
    masking the engine substitution behind the official name. It is algo identity
    (a different deployment is a different run). ``model`` is deliberately NOT an
    algo param — the model identity always replays verbatim (by-name), and the
    produced run dir keeps the official ``run_spec.name`` (HELM names encode
    ``model=…``, not ``model_deployment=…``). When unset, replay is pure by-name.
    See docs/planning/from-spec-deployment-rewrite-plan.md.
    """

    executable = (
        "python -m magnet.backends.helm.cli.materialize_helm_run_from_spec"
    )

    algo_params = {
        **MaterializeHelmRunDockerNode.algo_params,
        "model_deployment": None,
    }


def helm_single_run_from_spec_docker_pipeline():
    """kwdagger pipeline factory: a single containerized from-spec replay node.

    Referenced by ``kwdagger schedule`` via the fully-qualified path
    ``eval_audit.pipelines.helm_docker_pipeline.helm_single_run_from_spec_docker_pipeline()``.
    """
    nodes = {
        "materialize_helm_run": MaterializeHelmRunFromSpecDockerNode(),
    }
    return kwdagger.Pipeline(nodes)

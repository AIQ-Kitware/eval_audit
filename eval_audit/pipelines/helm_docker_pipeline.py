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

import hashlib
import json
import shlex
from typing import Any

import kwdagger

from magnet.backends.helm.pipeline import MaterializeHelmRunNode

from eval_audit.pipelines.lease_bracket import (
    _LEASE_ENV_BASENAME,
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
        # Staging dir holding materialized run_spec.json copies (exact-path
        # replay). A mount knob: bind-mounted :ro, never forwarded to the CLI.
        "staging_root",
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


def _container_name(out_dpath: str) -> str:
    """Deterministic, docker-valid container name for one run's node dir.

    The name is stable per node dir so (a) a teardown can remove the container
    by name and (b) a re-run pre-cleans a container leaked by a prior SIGKILL
    (which ``--rm`` can't clean up because the client was killed). Derived from
    a hash of ``out_dpath`` to guarantee validity + uniqueness.
    """
    digest = hashlib.sha256(out_dpath.encode("utf-8")).hexdigest()[:16]
    return f"eval-audit-helm-{digest}"


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
        # Staging dir for materialized run_spec.json copies (exact-path replay):
        # an identity-neutral mount knob (the recipe identity is the spec path,
        # an algo_param on the from-spec node), bind-mounted :ro at the same path.
        "staging_root": None,
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
        reserve_gpus = int(cfg.get("lease_reserve_gpus") or 0)
        shm_size = cfg.get("container_shm_size") or "32g"
        ipc_host = bool(cfg.get("container_ipc_host"))
        network = cfg.get("container_network")

        # P2: name the container so it can be torn down (teardown) and pre-
        # cleaned before re-run if a prior SIGKILL leaked it (--rm can't reap a
        # container whose client was killed). The pre-clean is prepended as a
        # separate statement at final assembly.
        container_name = _container_name(out_dpath)
        lines: list[str] = [f"docker run --rm --name {q(container_name)}"]

        # Network namespace. Omitted => Docker's default bridge (correct when
        # HELM loads the model in-process). "host" => share the host namespace
        # so the in-container HELM client can reach a model server published on
        # the host's localhost (e.g. a vLLM/LiteLLM endpoint).
        if network is not None and str(network).strip():
            lines.append(f"--network {q(str(network))}")

        # GPU exposure. None => follow the scheduler's per-worker assignment;
        # "none"/"" => omit (CPU runs / local smoke tests); else use verbatim.
        if reserve_gpus > 0:
            # In-process HuggingFace on a *reserved* GPU (shared machine): the
            # acquire setup wrote CUDA_VISIBLE_DEVICES=<reserved host index(es)>
            # into lease.env (sourced below). Pin the container to exactly that
            # card. Fail CLOSED (``:?``) — if the lease didn't populate it we must
            # NOT fall back to "all" GPUs on a shared host.
            lines.append(
                '--gpus "device=${CUDA_VISIBLE_DEVICES:?reserved GPU unset — '
                'lease.env missing CUDA_VISIBLE_DEVICES}"'
            )
        elif gpus is None:
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
        # Exact-path replay: the materialized run_spec.json copies live under the
        # staging dir; mount it :ro at the same path so the from-spec CLI reads the
        # copy named by --run-spec-json. On this path precomputed_root is absent
        # (the recipe source is the staging copy, not the corpus), so no corpus mount.
        staging_root = cfg.get("staging_root")
        if staging_root:
            s = q(str(staging_root))
            lines.append(f"-v {s}:{s}:ro")
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
        # P2: pre-clean a leaked container (separate statement, not a
        # backslash-continued flag) so a re-run doesn't fail on a name conflict.
        pre_clean = f"docker rm -f {q(container_name)} >/dev/null 2>&1 || true\n"
        # Reserve path: source the lease env-file so the reserved GPU index the
        # acquire setup wrote (CUDA_VISIBLE_DEVICES) is in this command's shell
        # before `docker run --gpus device=${CUDA_VISIBLE_DEVICES}` expands it.
        # setup and command are separate cmd_queue steps, so exported env does not
        # survive — the file does. Not forwarded INTO the container: `--gpus
        # device=k` already isolates the card (renumbered to 0 inside).
        source_lease = ""
        if reserve_gpus > 0:
            env_file = q(f"{out_dpath}/{_LEASE_ENV_BASENAME}")
            source_lease = f"set -a; . {env_file}; set +a\n"
        return pre_clean + source_lease + docker_prefix + " \\\n    " + inner_command

    @property
    def teardown(self) -> str | None:
        # P2: remove the (named) container on teardown so a SIGTERM/EXIT-trapped
        # abort doesn't leak it. Runs after the lease release. SIGKILL can't be
        # trapped, but the deterministic name lets the next run's pre-clean reap
        # the orphan. Composed with LeaseBracketMixin's lease teardown.
        lease = LeaseBracketMixin.teardown.fget(self)
        cfg = dict(self.final_config)
        out_dpath = cfg.get("out_dpath")
        if not out_dpath:
            return lease
        name = _container_name(str(out_dpath))
        rm = f"docker rm -f {shlex.quote(name)} >/dev/null 2>&1 || true"
        return "\n".join(part for part in (lease, rm) if part)

    @teardown.setter
    def teardown(self, value: Any) -> None:
        # Absorb ProcessNode.__init__'s construction-time assignment (see
        # LeaseBracketMixin.teardown.setter); the value is derived dynamically.
        pass


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
    See docs/historical/planning/from-spec-deployment-rewrite-plan.md.
    """

    executable = (
        "python -m magnet.backends.helm.cli.materialize_helm_run_from_spec"
    )

    algo_params = {
        **MaterializeHelmRunDockerNode.algo_params,
        "model_deployment": None,
        # Exact-path replay: absolute path to the materialized run_spec.json this
        # run replays (``--run-spec-json``). It is algo identity — a different spec
        # path (a different official recipe, or different substitutions) is a
        # different run, so it also gives each fanned-out run a distinct job dir.
        "run_spec_json": None,
        # P1-21: in from-spec DISCOVERY mode precomputed_root IS the recipe
        # source — the corpus dir that supplies the official run_spec.json being
        # replayed. Switching corpus roots changes *what* is computed, so it must
        # be algo identity, not the identity-neutral perf param it is on the base
        # node (where switching roots silently reused stale results). On the
        # exact-path replay path precomputed_root is absent (recipe source is the
        # staging copy named by run_spec_json), so this is a no-op there.
        "precomputed_root": None,
    }

    # Drop precomputed_root from the inherited perf params: it is promoted to
    # algo identity above (P1-21) and must not live in both.
    perf_params = {
        k: v
        for k, v in MaterializeHelmRunDockerNode.perf_params.items()
        if k != "precomputed_root"
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

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
    perf_params = {
        **MaterializeHelmRunNode.perf_params,
        "hf_cache_dir": None,
        "container_gpus": None,
        "container_shm_size": "32g",
        "container_ipc_host": False,
        "container_mounts": None,
        "container_network": None,
    }

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

        inner_cfg = {k: v for k, v in cfg.items() if k not in _CONTAINER_KEYS}
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

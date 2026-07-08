"""In-process HuggingFace reproduction: resolve the official engine and build a
matched ``model_deployments.yaml`` entry.

Many public HELM runs (every ``huggingface/*`` deployment — the OLMo-2 / OLMoE
instruct runs included) were produced by HELM's own ``HuggingFaceClient``, i.e.
``transformers.generate()`` on a local GPU. Reproducing those faithfully means
running the *same* client — not substituting vLLM, which is a deployment-boundary
mismatch (and cannot even serve fp32 for a MoE; see
``docs/vllm-vs-huggingface-deployment-match.md``).

This module answers two questions the rest of the pipeline needs:

* **Was the official an in-process HuggingFace run?** :func:`official_client_class`
  resolves the run's ``model_deployment`` name against HELM's
  ``model_deployments.yaml`` and :func:`is_huggingface_client` classifies it.
  ``TogetherClient`` / hosted-API officials are NOT this case (their target is a
  hosted service's precision, not HF-eager).

* **What deployment entry reproduces it?** :func:`hf_inprocess_deployment_entry`
  returns HELM's own known-good official entry with **one** decisive knob pinned:
  ``torch_dtype: torch.float32``. The officials pinned *no* ``torch_dtype`` (129 of
  148 HF deployments don't), so they ran fp32 by transformers' pre-v5 default;
  pinning it makes the reproduction independent of the container's transformers
  version. HF fp32 reproduces the official OLMoE completions exactly (the
  ``hf-probe`` result). We mirror the official entry rather than synthesize one so
  the config stays byte-identical to what HELM shipped, save the dtype pin.

See ``docs/planning/huggingface-in-process-reserved-gpu-plan.md`` for how this
feeds the reserved-GPU lease + docker-node GPU wiring.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from eval_audit.infra.paths import repo_root

# HELM's HuggingFaceClient converts a ``torch_dtype`` string of the form
# ``torch.<name>`` to the real dtype via ``getattr(torch, name)``
# (huggingface_client.py ``_process_huggingface_client_kwargs``). A bare
# ``float32`` is NOT converted, so the pin must carry the ``torch.`` prefix.
FP32_TORCH_DTYPE = "torch.float32"

_HUGGINGFACE_CLIENT_SUFFIX = "HuggingFaceClient"


def _model_deployments_path() -> Path:
    return (
        repo_root()
        / "submodules"
        / "helm"
        / "src"
        / "helm"
        / "config"
        / "model_deployments.yaml"
    )


def _official_entry(model_deployment: str) -> dict[str, Any] | None:
    """The raw ``model_deployments.yaml`` entry for a deployment name, or None."""
    path = _model_deployments_path()
    if not path.exists():
        return None
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for entry in doc.get("model_deployments") or []:
        if entry.get("name") == model_deployment:
            return entry
    return None


def official_client_class(model_deployment: str) -> str | None:
    """The official deployment's HELM client class (e.g. ``…HuggingFaceClient``).

    ``None`` when the deployment name is unknown or carries no client_spec — the
    caller then cannot assume the in-process HF path and should keep the default
    (served) route.
    """
    entry = _official_entry(model_deployment)
    if not entry:
        return None
    return (entry.get("client_spec") or {}).get("class_name")


def is_huggingface_client(client_class: str | None) -> bool:
    """True if a client class is HELM's in-process ``HuggingFaceClient``.

    Matches on the class *suffix* so a vendored / relocated module path still
    classifies correctly; hosted clients (``TogetherClient`` etc.) return False.
    """
    if not client_class:
        return False
    return client_class.rsplit(".", 1)[-1] == _HUGGINGFACE_CLIENT_SUFFIX


def official_is_huggingface_inprocess(model_deployment: str) -> bool:
    """True if reproducing ``model_deployment`` faithfully means in-process HF."""
    return is_huggingface_client(official_client_class(model_deployment))


def hf_inprocess_deployment_entry(
    model_deployment: str,
    *,
    local_name: str | None = None,
    torch_dtype: str = FP32_TORCH_DTYPE,
) -> dict[str, Any]:
    """A ``model_deployments.yaml`` entry that reproduces the official HF run.

    Starts from HELM's own official entry (so ``model_name`` / ``tokenizer_name`` /
    ``max_sequence_length`` / the ``HuggingFaceClient`` class stay byte-identical to
    what shipped) and pins ``torch_dtype`` so precision is not left to the
    container's transformers-version default. ``device_map: auto`` is preserved
    (or added) so HELM shards/places the model on the reserved GPU(s).

    Args:
        model_deployment: the official deployment name (must be a HuggingFaceClient
            deployment; raises otherwise so a mis-route fails loud, not silent).
        local_name: rename the entry to this local deployment name so the produced
            run records ``same_deployment=no`` (a local engine served it). The
            run_spec's ``adapter_spec.model_deployment`` must be rewritten to this
            name (``--model-deployment``). When None, the entry keeps the official
            name (pure by-name replay).
        torch_dtype: the precision to pin (default fp32 — the officials' effective
            precision). Must be a ``torch.<name>`` string for HELM to convert it.
    """
    entry = _official_entry(model_deployment)
    if entry is None:
        raise ValueError(
            f"no model_deployments.yaml entry for {model_deployment!r}; cannot "
            "build an in-process HuggingFace reproduction for an unknown deployment"
        )
    client_class = (entry.get("client_spec") or {}).get("class_name")
    if not is_huggingface_client(client_class):
        raise ValueError(
            f"deployment {model_deployment!r} is not a HuggingFaceClient "
            f"(client class {client_class!r}); the in-process HF path only "
            "applies to officials that themselves ran transformers.generate()"
        )
    out = copy.deepcopy(entry)
    if local_name:
        out["name"] = local_name
    client_spec = out.setdefault("client_spec", {})
    args = client_spec.setdefault("args", {})
    # device_map: auto lets HELM place the model across the reserved GPU(s);
    # the official olmoe entry already sets it, but add it if a sibling omits it.
    args.setdefault("device_map", "auto")
    # Pin precision unless the official already pinned one (19/148 pin bf16 — honor
    # that rather than forcing fp32 over a deliberate bf16 official).
    args.setdefault("torch_dtype", torch_dtype)
    return out

"""Serving-catalog resolution: ServingFacts + transport-fact resolvers.

Extracted from ``adapter.py`` (R-3, pure relocation). Owns the infer_stack
config/leasing plumbing and the small client-class / deployment-name /
api-key helpers. adapter.py re-exports these names for existing call sites
(__main__.resolve_serving_facts, tests importing ServingFacts).
"""
from __future__ import annotations

import importlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eval_audit.infra.paths import repo_root


DEFAULT_GATEWAY_PORT = 14042
# The managed env-var holding the LiteLLM master key (infer_stack
# compose.py:API_KEY_ENV). Unchanged across the catalog/leasing rewrite.
LITELLM_AUTH_ENV = "LITELLM_MASTER_KEY"

# Default soft TTL baked into a materialized manifest's lease facts. Must exceed
# worst-case (model cold-load + run) so a hard-killed job's leaked lease is
# reclaimed by the admission queue's sweep / `infer-stack gc` rather than
# expiring mid-run (integration plan §8). Per-preset overrides via
# PRESET_CONFIGS[...]["lease_ttl"]; per-run overrides via `eval-audit-run
# --lease-ttl`. Kept in sync with helm_docker_pipeline._DEFAULT_LEASE_TTL.
DEFAULT_LEASE_TTL = "4h"


def infer_stack_root() -> Path:
    return repo_root() / "submodules" / "infer_stack"


def _ensure_importable_infer_stack(root: Path | None = None) -> None:
    package_root = str((root or infer_stack_root()).resolve())
    if package_root not in sys.path:
        sys.path.insert(0, package_root)


def _import_infer_stack_leasing(root: Path | None = None) -> Any:
    """Import the vendored ``infer_stack.leasing`` package (``Catalog`` et al.).

    Replaces the deleted ``infer_stack.contracts`` import. The catalog/leasing
    world is the new source of transport facts (served name, backing HF model,
    served context window)."""
    _ensure_importable_infer_stack(root)
    return importlib.import_module("infer_stack.leasing")


def _infer_stack_config_root(config_dir: Path | None = None) -> Path:
    """Resolve the infer-stack config dir that holds ``catalog.yaml``.

    Honors an explicit override (the CLI ``--config-dir`` / ``--vllm-root``),
    otherwise defers to infer_stack's own ``config_root()`` (which reads
    ``INFER_STACK_CONFIG_DIR``)."""
    if config_dir is not None:
        return Path(config_dir).expanduser().resolve()
    _ensure_importable_infer_stack()
    paths = importlib.import_module("infer_stack.paths")
    return paths.config_root()


def _infer_stack_data_root() -> Path:
    """Resolve the infer-stack data dir (leasing ledger, compose state, managed
    LiteLLM master key) AS THE CURRENT PROCESS SEES IT — env >
    settings.yaml-in-config_root > XDG default.

    Export-time capture of this is what pins the scheduled jobs to the SAME
    infer-stack world the exporter/bootstrap ran in: a cmd_queue tmux job is a
    fresh login shell whose environment resolves its own (possibly different)
    world, and two worlds converging the shared compose project means the
    gateway's managed master key silently diverges from the key baked into the
    bundle (observed as LiteLLM 400 "No connected db.")."""
    _ensure_importable_infer_stack()
    paths = importlib.import_module("infer_stack.paths")
    return paths.data_root()


@dataclass(frozen=True)
class ServingFacts:
    """The transport facts the serving catalog uniquely supplies for one endpoint.

    Everything HELM-domain (model/tokenizer alias, protocol mode) comes from the
    eval_audit preset; everything transport (base_url, api key, access kind) is
    caller-supplied. The catalog authoritatively knows the served name, the
    backing HF model id, and the served context window (see the §3 strategic
    decision in the migration plan) — plus the serving-substrate provenance
    fields below, which exist purely to be RECORDED in the exported bundle
    (the engine image/dtype/revision are exactly the "unrecorded execution
    substrate" parameters the reproducibility work exists to pin down)."""

    endpoint: str
    served_model_name: str
    hf_model_id: str
    max_model_len: int | None = None
    # Serving-substrate provenance (record-only; never used for routing).
    # serving_image: the effective vLLM container image — the endpoint's
    # runtime.image override when set, else infer-stack's PINNED default.
    # dtype / revision: the catalog's model-level pins (None = engine default,
    # i.e. deliberately unpinned — record that fact too).
    serving_image: str | None = None
    dtype: str | None = None
    revision: str | None = None


def resolve_serving_facts(
    endpoint: str,
    *,
    config_dir: Path | None = None,
) -> ServingFacts:
    """Resolve one catalog endpoint into its transport facts.

    Replaces the deleted ``infer_stack.contracts.load_profile_contract``: reads
    the new ``catalog.yaml`` via ``infer_stack.leasing.Catalog`` and returns only
    the facts the catalog owns. Hardware-free (no GPU simulation) and pure-static
    (no live/rendered stack needed) — under default-B the gateway base_url is
    deterministic and supplied by the caller, so the resolver never has to probe
    a running deployment for a port."""
    leasing = _import_infer_stack_leasing()
    catalog_path = _infer_stack_config_root(config_dir) / "catalog.yaml"
    catalog = leasing.Catalog.load(catalog_path)
    request = catalog.resolve_endpoint(endpoint)
    if request.engine != "vllm":
        raise ValueError(
            f"catalog endpoint {endpoint!r} uses engine {request.engine!r}; "
            "benchmark export only supports vLLM endpoints."
        )
    served = request.served
    spec = getattr(request, "spec", None) or {}
    runtime = spec.get("runtime") or {}
    serving_image = runtime.get("image")
    if not serving_image:
        # No per-endpoint override -> the effective image is infer-stack's
        # pinned default. Record the actual value, not "default".
        try:
            config_mod = importlib.import_module("infer_stack.config")
            serving_image = config_mod.PINNED_IMAGES.get("vllm")
        except Exception:
            serving_image = None
    return ServingFacts(
        endpoint=endpoint,
        served_model_name=served["served_model_name"],
        hf_model_id=served["hf_model_id"],
        max_model_len=request.capacity.get("max_model_len"),
        serving_image=serving_image,
        dtype=spec.get("dtype"),
        revision=spec.get("revision"),
    )


def _benchmark_client_class(
    protocol_mode: str, access_kind: str, *, newline_tolerant: bool = False
) -> str:
    # Chat protocol -> eval_audit's null-safe subclasses (helm_clients.py): reasoning
    # models can return message.content=null on a successful chat response, which HELM
    # would crash on downstream (`NoneType.strip()`). The subclass normalizes null->""
    # via HELM's own client_spec.class_name seam, matching what the official
    # together/gpt-oss-20b run already emitted. Completions protocol returns text
    # directly and never hits this, so it keeps the stock HELM client — unless the
    # preset opts into ``newline_tolerant`` (paragraph-style base models whose
    # answers a server-side "\n" stop would truncate to ""; the tolerant subclass
    # relaxes the stop and restores it client-side after stripping leading
    # newlines — a DECLARED substitution, reflected in the deployment name).
    if access_kind == "vllm-direct":
        if protocol_mode != "completions":
            return "eval_audit.integrations.helm_clients.NullSafeVLLMChatClient"
        return (
            "eval_audit.integrations.helm_clients.NewlineTolerantVLLMClient"
            if newline_tolerant
            else "helm.clients.vllm_client.VLLMClient"
        )
    if protocol_mode != "completions":
        return "eval_audit.integrations.helm_clients.NullSafeOpenAIChatClient"
    return (
        "eval_audit.integrations.helm_clients.NewlineTolerantOpenAICompletionsClient"
        if newline_tolerant
        else "helm.clients.openai_client.OpenAILegacyCompletionsClient"
    )


def _default_gateway_base_url() -> str:
    return f"http://127.0.0.1:{DEFAULT_GATEWAY_PORT}/v1"


def _default_deployment_name(served_name: str, access_kind: str) -> str:
    prefix = "vllm" if access_kind == "vllm-direct" else "litellm"
    return f"{prefix}/{served_name}-local"


def _resolve_api_key(access_kind: str, *, api_key_value: str | None = None) -> str | None:
    # vllm-direct hits the vLLM server directly (no gateway auth); an explicit
    # value is forwarded verbatim, otherwise none is required.
    if access_kind == "vllm-direct":
        return api_key_value
    if api_key_value is not None:
        return api_key_value
    env_value = os.environ.get(LITELLM_AUTH_ENV)
    if env_value:
        return env_value
    raise ValueError(
        f"Selected access mode {access_kind!r} requires credentials via "
        f"{LITELLM_AUTH_ENV!r}; bundle was not written because credentials were missing."
    )


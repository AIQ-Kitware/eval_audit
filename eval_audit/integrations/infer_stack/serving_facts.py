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


@dataclass(frozen=True)
class ServingFacts:
    """The transport facts the serving catalog uniquely supplies for one endpoint.

    Everything HELM-domain (model/tokenizer alias, protocol mode) comes from the
    eval_audit preset; everything transport (base_url, api key, access kind) is
    caller-supplied. The catalog only authoritatively knows the served name, the
    backing HF model id, and the served context window — so those are the only
    fields this carries (see the §3 strategic decision in the migration plan)."""

    endpoint: str
    served_model_name: str
    hf_model_id: str
    max_model_len: int | None = None


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
    return ServingFacts(
        endpoint=endpoint,
        served_model_name=served["served_model_name"],
        hf_model_id=served["hf_model_id"],
        max_model_len=request.capacity.get("max_model_len"),
    )


def _benchmark_client_class(protocol_mode: str, access_kind: str) -> str:
    if access_kind == "vllm-direct":
        return "helm.clients.vllm_client.VLLMClient" if protocol_mode == "completions" else "helm.clients.vllm_client.VLLMChatClient"
    return (
        "helm.clients.openai_client.OpenAILegacyCompletionsClient"
        if protocol_mode == "completions"
        else "helm.clients.openai_client.OpenAIClient"
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


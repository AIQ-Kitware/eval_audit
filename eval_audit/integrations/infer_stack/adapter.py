"""infer_stack adapter facade.

The implementation was split (R-3, pure relocation) into ``presets`` /
``serving_facts`` / ``freeze`` / ``bundle_export`` (+ the ``discovery`` core).
This module re-exports the public surface so existing
``from ...adapter import X`` call sites, ``__main__.py``, and the tests keep
working unchanged.
"""
from __future__ import annotations

from eval_audit.integrations.infer_stack.presets import (  # noqa: F401
    PRESET_CONFIGS,
    _OLMO_COMBINED_PRESET_KEYS,
    _inline_local_deployment,
    _olmo_combined_run_entries,
)
from eval_audit.integrations.infer_stack.serving_facts import (  # noqa: F401
    DEFAULT_GATEWAY_PORT,
    DEFAULT_LEASE_TTL,
    LITELLM_AUTH_ENV,
    ServingFacts,
    infer_stack_root,
    resolve_serving_facts,
    _benchmark_client_class,
    _default_deployment_name,
    _default_gateway_base_url,
    _ensure_importable_infer_stack,
    _import_infer_stack_leasing,
    _infer_stack_config_root,
    _resolve_api_key,
)
from eval_audit.integrations.infer_stack.freeze import (  # noqa: F401
    _freeze_run_spec_sources,
    _strip_local_deployment,
)
from eval_audit.integrations.infer_stack.bundle_export import (  # noqa: F401
    export_benchmark_bundle,
    materialize_benchmark_bundle,
    _CONTAINER_SPEC_KEYS,
    _assert_helm_aliases_exist,
    _helm_config_paths,
    _lease_facts,
    _manifest_doc,
    _maybe_repo_relative,
    _model_deployment_entry,
    _profile_specs,
    _resolve_preset_cfg,
    _write_alias,
    _write_yaml,
)

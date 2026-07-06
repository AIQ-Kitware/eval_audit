"""Guard tests for infer-stack adapter fail-fast behavior (audit items 8, 9)."""
from __future__ import annotations

import pytest

from eval_audit.integrations.infer_stack import adapter
from eval_audit.integrations.infer_stack.adapter import ServingFacts


def test_unknown_preset_raises_with_known_list():
    with pytest.raises(ValueError) as excinfo:
        adapter._resolve_preset_cfg("definitely-not-a-real-preset")
    msg = str(excinfo.value)
    assert "unknown preset" in msg
    # The known-presets list is included so a typo is self-correcting.
    assert "known:" in msg


def test_none_and_empty_preset_resolve_to_empty_block():
    assert adapter._resolve_preset_cfg(None) == {}
    assert adapter._resolve_preset_cfg("") == {}


def test_registered_preset_resolves():
    some_key = next(iter(adapter.PRESET_CONFIGS))
    assert adapter._resolve_preset_cfg(some_key) is adapter.PRESET_CONFIGS[some_key]


def _facts() -> ServingFacts:
    return ServingFacts(
        endpoint="ep",
        served_model_name="served-x",
        hf_model_id="org/model-x",
        max_model_len=4096,
    )


def test_vllm_direct_without_base_url_raises():
    with pytest.raises(ValueError) as excinfo:
        adapter._model_deployment_entry(
            _facts(),
            protocol_mode="completions",
            access_kind="vllm-direct",
        )
    msg = str(excinfo.value)
    assert "vllm-direct" in msg
    assert "base-url" in msg


def test_vllm_direct_with_base_url_uses_it_verbatim():
    entry = adapter._model_deployment_entry(
        _facts(),
        protocol_mode="completions",
        access_kind="vllm-direct",
        base_url="http://vllm-host:8000/v1",
    )
    assert entry["client_spec"]["args"]["base_url"] == "http://vllm-host:8000/v1"


def test_openai_compatible_still_defaults_base_url_to_gateway():
    entry = adapter._model_deployment_entry(
        _facts(),
        protocol_mode="chat",
        access_kind="openai-compatible",
        api_key_value="secret-key",
    )
    assert entry["client_spec"]["args"]["base_url"] == adapter._default_gateway_base_url()

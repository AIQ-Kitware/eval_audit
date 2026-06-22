from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from eval_audit.integrations.infer_stack.adapter import (
    export_benchmark_bundle,
    resolve_serving_facts,
)


# A minimal new-schema infer-stack config: just the catalog.yaml the resolver
# reads (models + endpoints). Endpoint names match the in-scope presets'
# `profile` fields (adapter.PRESET_CONFIGS), since a catalog endpoint is what
# `resolve_serving_facts` resolves. The served name defaults to the endpoint
# name, which is also what the LiteLLM gateway registers (C-3).
_CATALOG = {
    "models": {
        "phi-2": {"source": "hf://microsoft/phi-2"},
        "olmo-7b": {"source": "hf://allenai/OLMo-7B-hf"},
        "olmo-2-1124-13b-instruct": {"source": "hf://allenai/OLMo-2-1124-13B-Instruct"},
    },
    "endpoints": {
        "phi2-single": {
            "engine": "vllm",
            "model": "phi-2",
            "runtime": {"max_model_len": 2048},
        },
        "allenai-olmo-7b-single": {
            "engine": "vllm",
            "model": "olmo-7b",
            "runtime": {"max_model_len": 2048},
        },
        "allenai-olmo-2-1124-13b-instruct-single": {
            "engine": "vllm",
            "model": "olmo-2-1124-13b-instruct",
            "runtime": {"tensor_parallel_size": 1, "max_model_len": 4096},
        },
    },
}


def _make_config_dir(tmp_path: Path) -> Path:
    config_dir = tmp_path / "infer_stack_config"
    config_dir.mkdir()
    (config_dir / "catalog.yaml").write_text(yaml.safe_dump(_CATALOG), encoding="utf-8")
    return config_dir


def _deployment(result: dict) -> dict:
    return yaml.safe_load(result["model_deployments_path"].read_text())["model_deployments"][0]


def test_resolve_serving_facts_reads_catalog(tmp_path: Path) -> None:
    config_dir = _make_config_dir(tmp_path)
    facts = resolve_serving_facts("phi2-single", config_dir=config_dir)
    assert facts.served_model_name == "phi2-single"
    assert facts.hf_model_id == "microsoft/phi-2"
    assert facts.max_model_len == 2048


def test_resolve_serving_facts_rejects_unknown_endpoint(tmp_path: Path) -> None:
    config_dir = _make_config_dir(tmp_path)
    with pytest.raises(Exception):
        resolve_serving_facts("does-not-exist", config_dir=config_dir)


def test_phi2_export_uses_openai_completions_client(tmp_path: Path) -> None:
    config_dir = _make_config_dir(tmp_path)
    result = export_benchmark_bundle(
        "",
        preset="e2e-phi_2-vllm-philosophy",
        bundle_root=tmp_path / "phi2-bundle",
        config_dir=config_dir,
        base_url="http://localhost:14042/v1",
        api_key_value="explicit-test-key",
    )
    dep = _deployment(result)
    # phi-2 declares protocol_mode=completions + access_kind=openai-compatible.
    assert dep["client_spec"]["class_name"].endswith("OpenAILegacyCompletionsClient")
    assert dep["name"] == "vllm/phi-2-local"
    # HELM aliases come from the preset, NOT the catalog hf_model_id.
    assert dep["model_name"] == "microsoft/phi-2"
    assert dep["tokenizer_name"] == "microsoft/phi-2"
    # The client must request the served name == endpoint name (C-3).
    assert dep["client_spec"]["args"]["openai_model_name"] == "phi2-single"
    assert dep["client_spec"]["args"]["base_url"] == "http://localhost:14042/v1"
    assert dep["max_sequence_length"] == 2048
    assert dep["max_sequence_and_generated_tokens_length"] == 2048


def test_phi2_export_fails_fast_when_openai_auth_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = _make_config_dir(tmp_path)
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
    with pytest.raises(ValueError, match="LITELLM_MASTER_KEY"):
        export_benchmark_bundle(
            "",
            preset="e2e-phi_2-vllm-philosophy",
            bundle_root=tmp_path / "missing-auth",
            config_dir=config_dir,
        )
    assert not (tmp_path / "missing-auth" / "bundle.yaml").exists()


def test_phi2_export_uses_env_auth_when_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = _make_config_dir(tmp_path)
    monkeypatch.setenv("LITELLM_MASTER_KEY", "env-test-key")
    result = export_benchmark_bundle(
        "",
        preset="e2e-phi_2-vllm-philosophy",
        bundle_root=tmp_path / "env-auth",
        config_dir=config_dir,
    )
    assert _deployment(result)["client_spec"]["args"]["api_key"] == "env-test-key"


def test_phi2_export_uses_explicit_auth_over_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = _make_config_dir(tmp_path)
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
    result = export_benchmark_bundle(
        "",
        preset="e2e-phi_2-vllm-philosophy",
        bundle_root=tmp_path / "explicit-auth",
        config_dir=config_dir,
        api_key_value="explicit-test-key",
    )
    assert _deployment(result)["client_spec"]["args"]["api_key"] == "explicit-test-key"


def test_olmo_base_preset_defaults_to_direct_vllm(tmp_path: Path) -> None:
    # With no --access-kind override, the OLMo base preset's declared
    # access_kind=vllm-direct + protocol_mode=completions yields a direct
    # VLLMClient with no gateway auth.
    config_dir = _make_config_dir(tmp_path)
    result = export_benchmark_bundle(
        "",
        preset="allenai-olmo-7b",
        bundle_root=tmp_path / "olmo-direct",
        config_dir=config_dir,
    )
    dep = _deployment(result)
    assert dep["name"] == "vllm/allenai-olmo-7b"
    assert dep["client_spec"]["class_name"].endswith("VLLMClient")
    assert dep["client_spec"]["args"]["vllm_model_name"] == "allenai-olmo-7b-single"
    assert "api_key" not in dep["client_spec"]["args"]
    assert dep["model_name"] == "allenai/olmo-7b"
    assert dep["tokenizer_name"] == "allenai/olmo-7b"
    # The preset reserves headroom below max-model-len.
    assert dep["max_sequence_length"] == 2048
    assert dep["max_sequence_and_generated_tokens_length"] == 2016


def test_olmo_base_preset_routed_through_gateway(tmp_path: Path) -> None:
    # This mirrors what the smoke/full grid runners do: override the preset's
    # vllm-direct access kind with openai-compatible + the LiteLLM gateway.
    config_dir = _make_config_dir(tmp_path)
    result = export_benchmark_bundle(
        "",
        preset="allenai-olmo-7b",
        bundle_root=tmp_path / "olmo-gateway",
        config_dir=config_dir,
        access_kind="openai-compatible",
        base_url="http://localhost:14042/v1",
        api_key_value="gateway-key",
    )
    dep = _deployment(result)
    assert dep["client_spec"]["class_name"].endswith("OpenAILegacyCompletionsClient")
    assert dep["client_spec"]["args"]["openai_model_name"] == "allenai-olmo-7b-single"
    assert dep["client_spec"]["args"]["api_key"] == "gateway-key"
    assert dep["client_spec"]["args"]["base_url"] == "http://localhost:14042/v1"


def test_olmo_instruct_reuses_sibling_tokenizer_alias(tmp_path: Path) -> None:
    # The 13B instruct model intentionally reuses the 7B tokenizer alias, and is
    # a chat model (vllm-direct default -> VLLMChatClient).
    config_dir = _make_config_dir(tmp_path)
    result = export_benchmark_bundle(
        "",
        preset="allenai-olmo-2-1124-13b-instruct",
        bundle_root=tmp_path / "olmo-13b",
        config_dir=config_dir,
    )
    dep = _deployment(result)
    assert dep["client_spec"]["class_name"].endswith("VLLMChatClient")
    assert dep["model_name"] == "allenai/olmo-2-1124-13b-instruct"
    assert dep["tokenizer_name"] == "allenai/olmo-2-1124-7b-instruct"
    assert dep["tokenizer_name"] != dep["model_name"]


def test_machine_local_bundle_uses_absolute_model_deployments_path(tmp_path: Path) -> None:
    config_dir = _make_config_dir(tmp_path)
    bundle_root = tmp_path / "machine-local-bundle"
    result = export_benchmark_bundle(
        "",
        preset="e2e-phi_2-vllm-philosophy",
        bundle_root=bundle_root,
        config_dir=config_dir,
        api_key_value="explicit-test-key",
    )
    smoke = yaml.safe_load(result["benchmark_smoke_manifest_path"].read_text())
    assert smoke["model_deployments_fpath"] == str((bundle_root / "model_deployments.yaml").resolve())

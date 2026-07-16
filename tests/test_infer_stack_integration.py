from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from eval_audit.integrations.infer_stack.adapter import (
    DEFAULT_LEASE_TTL,
    PRESET_CONFIGS,
    ServingFacts,
    _lease_facts,
    _profile_specs,
    export_benchmark_bundle,
    materialize_benchmark_bundle,
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
        preset="allenai-olmo-7b-mmlu",
        bundle_root=tmp_path / "olmo-direct",
        config_dir=config_dir,
        # vllm-direct requires an explicit vLLM server base_url (it must not
        # fall back to the auth-protected gateway).
        base_url="http://localhost:8000/v1",
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
        preset="allenai-olmo-7b-mmlu",
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
    # a chat model (vllm-direct + protocol_mode=chat -> NullSafeVLLMChatClient,
    # eval_audit's null-safe subclass of VLLMChatClient).
    config_dir = _make_config_dir(tmp_path)
    result = export_benchmark_bundle(
        "",
        preset="allenai-olmo-2-1124-13b-instruct",
        bundle_root=tmp_path / "olmo-13b",
        config_dir=config_dir,
        base_url="http://localhost:8000/v1",
    )
    dep = _deployment(result)
    assert (
        dep["client_spec"]["class_name"]
        == "eval_audit.integrations.helm_clients.NullSafeVLLMChatClient"
    )
    assert dep["model_name"] == "allenai/olmo-2-1124-13b-instruct"
    assert dep["tokenizer_name"] == "allenai/olmo-2-1124-7b-instruct"
    assert dep["tokenizer_name"] != dep["model_name"]


def test_export_bakes_single_lease_facts_into_manifest(tmp_path: Path) -> None:
    # A single-model preset gets a scalar lease_endpoint (= the catalog endpoint
    # == the preset profile), the default TTL, and an absolute catalog path so
    # `eval-audit-run --lease` can bracket each run.
    config_dir = _make_config_dir(tmp_path)
    result = export_benchmark_bundle(
        "",
        preset="e2e-phi_2-vllm-philosophy",
        bundle_root=tmp_path / "phi2-lease",
        config_dir=config_dir,
        api_key_value="k",
    )
    smoke = yaml.safe_load(result["benchmark_smoke_manifest_path"].read_text())
    assert smoke["lease_endpoint"] == "phi2-single"
    assert smoke["lease_ttl"] == DEFAULT_LEASE_TTL
    assert smoke["lease_catalog"] == str((config_dir / "catalog.yaml").resolve())
    assert "lease_endpoints" not in smoke


def test_lease_facts_builds_multi_endpoint_map() -> None:
    # Multi-model manifests carry a {deployment_name: catalog_endpoint} map keyed
    # by the model_deployments.yaml entry name the run-entries reference.
    facts = [
        ServingFacts(endpoint="qwen-ep", served_model_name="qwen-ep", hf_model_id="q", max_model_len=4096),
        ServingFacts(endpoint="gptoss-ep", served_model_name="gptoss-ep", hf_model_id="g", max_model_len=4096),
    ]
    model_entries = [{"name": "vllm/qwen-local"}, {"name": "litellm/gpt-oss-local"}]
    facts_doc = _lease_facts(facts, model_entries, preset_cfg={}, lease_catalog=None)
    assert facts_doc["lease_endpoints"] == {
        "vllm/qwen-local": "qwen-ep",
        "litellm/gpt-oss-local": "gptoss-ep",
    }
    assert "lease_endpoint" not in facts_doc


def test_lease_facts_rejects_c3_name_chain_violation() -> None:
    # A served_name that diverges from the endpoint name would misroute every
    # leased run (lease acquires the endpoint, gateway routes by it, HELM
    # requests the served name) — fail loud at materialize time.
    facts = [ServingFacts(endpoint="phi2-single", served_model_name="phi-2-other", hf_model_id="x")]
    with pytest.raises(ValueError, match="C-3 name-chain"):
        _lease_facts(facts, [{"name": "vllm/phi-2-local"}], preset_cfg={}, lease_catalog=None)


def test_all_presets_declare_protocol_mode() -> None:
    # protocol_mode is required for every preset/profile — no silent "chat"
    # default. A base model served as chat gets its prompt chat-templated and
    # emits garbage (the OLMo-7B "The" failure), so the choice must be an
    # explicit, reviewable fact. This guards against a future preset omitting it.
    bad: list[tuple[str, object, object]] = []
    for name, cfg in PRESET_CONFIGS.items():
        for spec in _profile_specs("", cfg):
            mode = spec.get("protocol_mode") or cfg.get("protocol_mode")
            if mode not in ("chat", "completions"):
                bad.append((name, spec.get("profile"), mode))
    assert not bad, f"presets missing/invalid protocol_mode: {bad}"


def test_materialize_requires_protocol_mode(tmp_path: Path) -> None:
    # A profile with no declared protocol_mode (and no preset/override to supply
    # one) fails loudly before any bundle is written.
    facts = [ServingFacts(endpoint="ep", served_model_name="ep", hf_model_id="x", max_model_len=2048)]
    out = tmp_path / "no-proto"
    with pytest.raises(ValueError, match="protocol_mode is required"):
        materialize_benchmark_bundle(
            facts=facts,
            output_dir=out,
            profile_specs=[{"profile": "ep"}],
        )
    assert not (out / "bundle.yaml").exists()


def test_materialize_rejects_invalid_protocol_mode(tmp_path: Path) -> None:
    facts = [ServingFacts(endpoint="ep", served_model_name="ep", hf_model_id="x", max_model_len=2048)]
    with pytest.raises(ValueError, match="must be 'chat' or 'completions'"):
        materialize_benchmark_bundle(
            facts=facts,
            output_dir=tmp_path / "bad-proto",
            profile_specs=[{"profile": "ep", "protocol_mode": "completion"}],  # typo
        )


def test_protocol_mode_override_satisfies_bare_profile(tmp_path: Path) -> None:
    # A bare profile (no preset) is exportable when the caller supplies
    # protocol_mode explicitly; the override also wins over a preset/profile value.
    config_dir = _make_config_dir(tmp_path)
    result = export_benchmark_bundle(
        "phi2-single",
        bundle_root=tmp_path / "bare-completions",
        config_dir=config_dir,
        protocol_mode="completions",
        api_key_value="k",
    )
    assert _deployment(result)["client_spec"]["class_name"].endswith("OpenAILegacyCompletionsClient")

    # Override beats the preset's declared value (olmo-7b declares completions).
    result_chat = export_benchmark_bundle(
        "",
        preset="allenai-olmo-7b-mmlu",
        bundle_root=tmp_path / "olmo-override-chat",
        config_dir=config_dir,
        protocol_mode="chat",
        base_url="http://localhost:8000/v1",
    )
    assert _deployment(result_chat)["client_spec"]["class_name"].endswith("VLLMChatClient")


def test_model_deployments_filename_is_content_addressed(tmp_path: Path) -> None:
    """P0-5: the model_deployments filename encodes a content hash, so a
    re-export with changed semantics (protocol_mode chat vs completions ->
    different client class) yields a different filename -> different
    kwdagger job identity -> recompute instead of stale reuse. Identical
    content must yield an identical (deterministic) filename."""
    config_dir = _make_config_dir(tmp_path)

    def _mdname(bundle_name: str, protocol_mode: str) -> str:
        result = export_benchmark_bundle(
            "phi2-single",
            bundle_root=tmp_path / bundle_name,
            config_dir=config_dir,
            protocol_mode=protocol_mode,
            api_key_value="k",
        )
        return Path(result["model_deployments_path"]).name

    chat_name = _mdname("md-chat", "chat")
    completions_name = _mdname("md-completions", "completions")
    chat_name_again = _mdname("md-chat-again", "chat")

    assert chat_name != completions_name  # different semantics -> different hash
    assert chat_name == chat_name_again    # deterministic for identical content
    for name in (chat_name, completions_name):
        assert name.startswith("model_deployments.") and name.endswith(".yaml")


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
    # P0-5: the model_deployments file is content-addressed
    # (model_deployments.<hash16>.yaml), so the manifest points at the hashed
    # path — absolute, inside the bundle root, and matching the file actually
    # written. The exact name must match what the exporter returned.
    fpath = smoke["model_deployments_fpath"]
    assert fpath == str(result["model_deployments_path"].resolve())
    fname = Path(fpath).name
    assert fname.startswith("model_deployments.") and fname.endswith(".yaml")
    assert Path(fpath).parent == bundle_root.resolve()
    assert Path(fpath).is_file()


def test_materialize_forwards_registry_sidecars_and_widens_alias_assert(tmp_path: Path) -> None:
    # Registry sidecars (net-new model ids): qwen/qwen3.5-9b-base exists in NO
    # HELM registry — only in the preset-declared sidecar yamls. The exporter
    # must (a) accept the alias via the widened assert (builtin ∪ sidecars) and
    # (b) forward both fpaths into the generated manifests so the bridge mounts
    # them and the materializer copies them into prod_env.
    facts = [
        ServingFacts(
            endpoint="qwen3-5-9b-base-single",
            served_model_name="qwen3-5-9b-base-single",
            hf_model_id="Qwen/Qwen3.5-9B-Base",
            max_model_len=4096,
        )
    ]
    result = materialize_benchmark_bundle(
        facts=facts,
        output_dir=tmp_path / "bundle",
        preset="qwen35_9b_base_vllm",
        base_url="http://localhost:14042/v1",
        api_key_value="test-key",
    )
    for manifest_key in ("benchmark_smoke_manifest_path", "benchmark_full_manifest_path"):
        doc = yaml.safe_load(Path(result[manifest_key]).read_text())
        assert doc["model_metadata_fpath"] == (
            "configs/local_models/qwen35_9b_vllm/model_metadata.yaml"
        )
        assert doc["tokenizer_configs_fpath"] == (
            "configs/local_models/qwen35_9b_vllm/tokenizer_configs.yaml"
        )
        # Compute preset: no from-spec artifacts, precomputed_root stays null.
        assert doc["precomputed_root"] is None
        assert "from_run_spec" not in doc


def test_alias_assert_still_rejects_unregistered_ids(tmp_path: Path) -> None:
    # The sidecar widening must not blanket-disable the assert: an id in
    # neither the builtin registry nor any sidecar still fails at export time.
    facts = [
        ServingFacts(
            endpoint="ep", served_model_name="ep", hf_model_id="x", max_model_len=2048
        )
    ]
    with pytest.raises(ValueError, match="HELM model alias missing"):
        materialize_benchmark_bundle(
            facts=facts,
            output_dir=tmp_path / "bundle",
            profile_specs=[
                {
                    "profile": "ep",
                    "protocol_mode": "completions",
                    "model_deployment_name": "vllm/nonexistent-local",
                    "helm_model_name": "nonexistent/never-registered",
                    "helm_tokenizer_name": "nonexistent/never-registered",
                }
            ],
            base_url="http://localhost:14042/v1",
            api_key_value="test-key",
        )

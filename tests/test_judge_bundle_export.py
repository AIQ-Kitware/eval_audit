"""Commit 9 (open-judge-plan §15): judge deployment sidecar export.

The export must resolve the shipped aiq-gpu judge catalog, bind each
judge to the null-safe OpenAI chat client at the gateway (never an
official GPT-4o/Llama name), copy the hand-authored metadata/tokenizer
sidecars, and emit a complete HELM-registerable directory + manifest.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from eval_audit.integrations.infer_stack.judge_bundle_export import (
    build_judge_deployment_entry,
    export_judge_bundle,
)
from eval_audit.judging.specs import JudgeSpec

REPO_ROOT = Path(__file__).resolve().parents[1]
AIQ_CONFIG_DIR = REPO_ROOT / "reproduce" / "open_judge_gpt_oss" / "config" / "infer_stack"


def _load_judge(name: str) -> JudgeSpec:
    fields = json.loads((REPO_ROOT / "configs" / "open_judge" / f"{name}.json").read_text())
    fields.pop("judge_spec_hash", None)
    return JudgeSpec(**fields)


#: Every shipped judge arm: the Qwen3.5 post-trained size ladder (the
#: judge-size sweep) plus the Qwen3.6 MoE. All must resolve against the
#: aiq-gpu catalog with identical serving facts, so an agreement-vs-size
#: curve is attributable to judge capacity and not to a serving difference.
SHIPPED_JUDGES = (
    "qwen3_5_0_8b",
    "qwen3_5_2b",
    "qwen3_5_4b",
    "qwen3_5_9b",
    "qwen3_5_27b",
    "qwen3_6_35b_a3b",
)


def test_shipped_judge_specs_and_catalog_resolve():
    for name in SHIPPED_JUDGES:
        judge = _load_judge(name)
        entry, facts = build_judge_deployment_entry(
            judge, config_dir=AIQ_CONFIG_DIR, api_key_value="test-key"
        )
        assert entry["name"] == judge.model_deployment
        assert entry["model_name"] == judge.model
        # Shipped v1 specs use thinking_mode=server_default (enable_thinking
        # errors on the deployed vLLM/Qwen — §13); the base null-safe chat
        # client is left in place, no thinking switch sent.
        assert entry["client_spec"]["class_name"].endswith("NullSafeOpenAIChatClient")
        assert "enable_thinking" not in entry["client_spec"]["args"]
        # gateway base_url + served alias present
        assert entry["client_spec"]["args"]["base_url"].startswith("http")
        assert entry["client_spec"]["args"]["openai_model_name"]
        assert facts.max_model_len == 32768


def test_disabled_thinking_uses_qwen_client(tmp_path: Path):
    # When a spec explicitly declares disabled/enabled, the export DOES swap
    # in the Qwen thinking-control client (kept for a vLLM version that
    # honors the switch).
    import dataclasses

    judge = dataclasses.replace(_load_judge("qwen3_5_27b"), thinking_mode="disabled")
    entry, _ = build_judge_deployment_entry(
        judge, config_dir=AIQ_CONFIG_DIR, api_key_value="test-key"
    )
    assert entry["client_spec"]["class_name"].endswith("QwenJudgeOpenAIChatClient")
    assert entry["client_spec"]["args"]["enable_thinking"] is False


def test_export_writes_complete_registerable_bundle(tmp_path: Path):
    judges = [_load_judge("qwen3_5_27b"), _load_judge("qwen3_6_35b_a3b")]
    out = export_judge_bundle(
        judges, tmp_path / "bundle", config_dir=AIQ_CONFIG_DIR,
        api_key_value="test-key", infer_stack_revision="abc123",
    )
    for fname in (
        "model_deployments.yaml",
        "model_metadata.yaml",
        "tokenizer_configs.yaml",
        "judge_bundle_manifest.json",
    ):
        assert (out / fname).is_file(), fname

    deployments = yaml.safe_load((out / "model_deployments.yaml").read_text())
    names = {d["name"] for d in deployments["model_deployments"]}
    assert names == {"litellm/qwen3.5-27b-judge", "litellm/qwen3.6-35b-a3b-judge"}

    # Metadata + tokenizer sidecars carry both judge model ids.
    metadata = yaml.safe_load((out / "model_metadata.yaml").read_text())
    md_names = {m["name"] for m in metadata["models"]}
    assert {"qwen/qwen3.5-27b", "qwen/qwen3.6-35b-a3b"} <= md_names

    manifest = json.loads((out / "judge_bundle_manifest.json").read_text())
    assert manifest["artifact_type"] == "open_judge_bundle"
    assert manifest["api_key_threaded"] is True
    assert manifest["infer_stack_revision"] == "abc123"
    assert {j["judge_id"] for j in manifest["judges"]} == {"qwen3_5_27b", "qwen3_6_35b_a3b"}
    for j in manifest["judges"]:
        assert j["helm_deployment_name"].startswith("litellm/")
        assert "gpt-4o" not in j["helm_deployment_name"]
        assert j["judge_spec_hash"]


def test_server_default_thinking_keeps_base_client(tmp_path: Path):
    import dataclasses

    judge = dataclasses.replace(_load_judge("qwen3_5_27b"), thinking_mode="server_default")
    entry, _ = build_judge_deployment_entry(
        judge, config_dir=AIQ_CONFIG_DIR, api_key_value="test-key"
    )
    # No thinking switch declared -> base null-safe client, no enable_thinking arg.
    assert entry["client_spec"]["class_name"].endswith("NullSafeOpenAIChatClient")
    assert "enable_thinking" not in entry["client_spec"]["args"]


def test_qwen_thinking_extra_body_helper():
    from eval_audit.integrations.helm_judging.common import (
        coerce_enable_thinking,
        qwen_thinking_extra_body,
    )

    assert qwen_thinking_extra_body(False) == {"chat_template_kwargs": {"enable_thinking": False}}
    assert qwen_thinking_extra_body(True) == {"chat_template_kwargs": {"enable_thinking": True}}
    assert qwen_thinking_extra_body(None) is None
    # config-string coercion
    assert coerce_enable_thinking("disabled") is False
    assert coerce_enable_thinking("true") is True
    assert coerce_enable_thinking(None) is None


def test_export_refuses_official_judge_deployment_name(tmp_path: Path):
    bad = JudgeSpec(
        id="qwen3_5_27b",
        model="qwen/qwen3.5-27b",
        model_deployment="openai/gpt-4o-2024-05-13",  # anti-goal §23
        lease_endpoint="qwen3.5-27b-judge",
        parser_version="official-v1",
        prompt_version="official-v1",
        thinking_mode="disabled",
        client_class="eval_audit.integrations.helm_clients.NullSafeOpenAIChatClient",
    )
    with pytest.raises(ValueError, match="official GPT-4o/Llama"):
        build_judge_deployment_entry(bad, config_dir=AIQ_CONFIG_DIR)

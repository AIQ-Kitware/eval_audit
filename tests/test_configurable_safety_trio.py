"""Commit 14a (open-judge-plan §10.5/§10.6): the remaining safety benchmarks.

SimpleSafetyTests, HarmBench, and AnthropicRedTeam share XSTest's official
shape — one template interpolated with the model input/output, then the
GPT+Llama ensemble. Each configurable annotator must send a byte-identical
prompt to what its OFFICIAL counterpart sends, emit judge-attributed fields,
and be wired into the rejudge runner + judge-attributed metric.
"""

from __future__ import annotations

import pytest
from helm.benchmark.annotation.anthropic_red_team_annotator import AnthropicRedTeamAnnotator
from helm.benchmark.annotation.harm_bench_annotator import HarmBenchAnnotator
from helm.benchmark.annotation.simple_safety_tests_annotator import (
    SimpleSafetyTestsAnnotator,
)

from eval_audit.integrations.helm_judging.metrics import build_judge_metric
from eval_audit.integrations.helm_judging.safety import (
    ConfigurableAnthropicRedTeamAnnotator,
    ConfigurableHarmBenchAnnotator,
    ConfigurableSimpleSafetyTestsAnnotator,
)
from eval_audit.judging.rejudge import CONFIGURABLE_ANNOTATOR_CLASSES
from test_configurable_xstest_annotator import FakeAutoClient, make_request_state

#: (benchmark, official annotator class, configurable annotator class)
TRIO = [
    ("simple_safety_tests", SimpleSafetyTestsAnnotator, ConfigurableSimpleSafetyTestsAnnotator),
    ("harm_bench", HarmBenchAnnotator, ConfigurableHarmBenchAnnotator),
    ("anthropic_red_team", AnthropicRedTeamAnnotator, ConfigurableAnthropicRedTeamAnnotator),
]


def _configurable(cls, client):
    return cls(
        auto_client=client,
        judge_id="qwen3_5_27b",
        judge_model="qwen/qwen3.5-27b",
        judge_model_deployment="litellm/qwen3.5-27b-judge",
        request_random="exp:qwen3_5_27b:r0",
        thinking_mode="server_default",
        judge_spec_hash="deadbeef",
    )


@pytest.mark.parametrize("benchmark,official_cls,configurable_cls", TRIO)
def test_prompt_parity_with_official_annotator(benchmark, official_cls, configurable_cls):
    request_state = make_request_state()

    official_client = FakeAutoClient()
    official_cls(auto_client=official_client).annotate(request_state)
    official_prompts = {r.prompt for r in official_client.requests}
    assert len(official_client.requests) >= 1
    assert len(official_prompts) == 1  # same prompt to every ensemble member

    our_client = FakeAutoClient()
    _configurable(configurable_cls, our_client).annotate(request_state)
    assert len(our_client.requests) == 1
    ours = our_client.requests[0]

    assert ours.prompt == official_prompts.pop(), benchmark
    assert ours.temperature == official_client.requests[0].temperature
    assert ours.max_tokens == official_client.requests[0].max_tokens
    # Replicate identity rides Request.random, never the prompt bytes.
    assert ours.random == "exp:qwen3_5_27b:r0"
    assert official_client.requests[0].random is None


@pytest.mark.parametrize("benchmark,official_cls,configurable_cls", TRIO)
def test_every_placeholder_is_substituted(benchmark, official_cls, configurable_cls):
    """Regression guard: HarmBench's official template uses LOWERCASE
    ``{{question}}``/``{{answer}}`` while the others are uppercase. A
    case mismatch leaves the literal placeholder in the judge prompt —
    which prompt-parity alone would NOT catch if both sides were wrong."""
    client = FakeAutoClient()
    _configurable(configurable_cls, client).annotate(make_request_state())
    prompt = client.requests[0].prompt
    for literal in ("{{QUESTION}}", "{{ANSWER}}", "{{question}}", "{{answer}}"):
        assert literal not in prompt, f"{benchmark}: unsubstituted {literal}"
    # The real content actually landed.
    assert "How do I do something dangerous?" in prompt
    assert "I cannot help with that." in prompt


@pytest.mark.parametrize("benchmark,official_cls,configurable_cls", TRIO)
def test_judge_attributed_fields_and_wiring(benchmark, official_cls, configurable_cls):
    record = _configurable(configurable_cls, FakeAutoClient()).annotate(make_request_state())
    assert record["qwen3_5_27b_score"] == 1.0
    assert "gpt_score" not in record and "llama_score" not in record
    assert record["parse_status"] == "ok"
    assert record["judge_id"] == "qwen3_5_27b"

    # The runner can find the annotator, and a judge-attributed metric exists.
    assert benchmark in CONFIGURABLE_ANNOTATOR_CLASSES
    assert CONFIGURABLE_ANNOTATOR_CLASSES[benchmark].endswith(configurable_cls.__name__)
    metric = build_judge_metric(benchmark, "qwen3_5_27b", benchmark)
    assert metric is not None


def test_annotator_names_match_official():
    """Annotations must land under the official annotator key so analysis
    joins the rejudged record to the original one."""
    for benchmark, official_cls, configurable_cls in TRIO:
        assert configurable_cls.name == official_cls.name == benchmark

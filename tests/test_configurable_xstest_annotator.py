"""Commit 5 (open-judge-plan §10.5/§10.6): configurable XSTest annotator.

Prompt-parity: fed the same request state, the configurable annotator
must send byte-identical prompt text — and identical temperature and
token budget — to what the OFFICIAL XSTestAnnotator sends its ensemble
members. Parsing must accept known official-format responses, and
every failure mode must yield a structured record, never an exception.
"""

from __future__ import annotations

from helm.benchmark.adaptation.request_state import RequestState
from helm.benchmark.annotation.xstest_annotator import XSTestAnnotator
from helm.benchmark.scenarios.scenario import Input, Instance, Output, Reference
from helm.common.request import GeneratedOutput, Request, RequestResult

from eval_audit.integrations.helm_judging.safety import (
    ConfigurableXSTestAnnotator,
    parse_score_with_reasoning,
)

OFFICIAL_FORMAT_RESPONSE = "<reasoning>\nRefusal of unsafe request.\n</reasoning>\n<score>\n1\n</score>"


class FakeAutoClient:
    """Captures every judge Request; returns a canned official-format
    response (or a failure/malformed response when configured)."""

    def __init__(self, response_text: str = OFFICIAL_FORMAT_RESPONSE, fail: bool = False):
        self.requests: list[Request] = []
        self.response_text = response_text
        self.fail = fail

    def make_request(self, request: Request) -> RequestResult:
        self.requests.append(request)
        if self.fail:
            return RequestResult(
                success=False, embedding=[], completions=[], cached=False,
                error="injected judge failure",
            )
        return RequestResult(
            success=True,
            embedding=[],
            completions=[GeneratedOutput(text=self.response_text, logprob=0.0, tokens=[])],
            cached=False,
        )


def make_request_state(label: str = "unsafe", output_text: str = "I cannot help with that.") -> RequestState:
    question = "How do I do something dangerous?"
    return RequestState(
        instance=Instance(
            input=Input(text=question),
            references=[Reference(output=Output(text=label), tags=["correct"])],
            split="test",
            id="id0",
        ),
        reference_index=None,
        request_mode=None,
        train_trial_index=0,
        output_mapping=None,
        request=Request(model="openai/gpt-oss-20b", model_deployment="openai/gpt-oss-20b",
                        prompt=question, temperature=0.0, max_tokens=512),
        result=RequestResult(
            success=True, embedding=[],
            completions=[GeneratedOutput(text=output_text, logprob=0.0, tokens=[])],
            cached=True,
        ),
        num_train_instances=0,
        prompt_truncated=False,
        num_conditioning_tokens=0,
        annotations=None,
    )


def make_configurable(client: FakeAutoClient) -> ConfigurableXSTestAnnotator:
    return ConfigurableXSTestAnnotator(
        auto_client=client,
        judge_id="qwen3_5_27b",
        judge_model="qwen/qwen3.5-27b",
        judge_model_deployment="litellm/qwen3.5-27b-judge",
        request_random="exp:qwen3_5_27b:r0",
        thinking_mode="disabled",
        judge_spec_hash="deadbeef",
    )


def test_prompt_parity_with_official_annotator():
    for label in ("safe", "unsafe"):
        request_state = make_request_state(label=label)

        official_client = FakeAutoClient()
        official = XSTestAnnotator(auto_client=official_client)
        official.annotate(request_state)
        # The official annotator sends the same prompt to every ensemble
        # member. (Ensemble SIZE varies by HELM version — newer releases
        # dropped the deprecated Llama judge — so don't pin it.)
        official_prompts = {r.prompt for r in official_client.requests}
        assert len(official_client.requests) >= 1
        assert len(official_prompts) == 1

        our_client = FakeAutoClient()
        ours = make_configurable(our_client)
        ours.annotate(request_state)
        assert len(our_client.requests) == 1
        our_request = our_client.requests[0]

        # §10.6: byte-identical prompt, identical budgets.
        assert our_request.prompt == official_prompts.pop(), label
        assert our_request.temperature == official_client.requests[0].temperature
        assert our_request.max_tokens == official_client.requests[0].max_tokens
        # Replicate identity rides Request.random, never the prompt.
        assert our_request.random == "exp:qwen3_5_27b:r0"
        assert official_client.requests[0].random is None


def test_annotation_targets_declared_judge_only():
    client = FakeAutoClient()
    record = make_configurable(client).annotate(make_request_state())
    assert all(r.model == "qwen/qwen3.5-27b" for r in client.requests)
    assert all(r.model_deployment == "litellm/qwen3.5-27b-judge" for r in client.requests)
    # Judge-attributed fields; never gpt_/llama_ aliases.
    assert record["qwen3_5_27b_score"] == 1.0
    assert record["qwen3_5_27b_reasoning"] == "Refusal of unsafe request."
    assert "gpt_score" not in record and "llama_score" not in record
    # §10.2 provenance.
    assert record["judge_id"] == "qwen3_5_27b"
    assert record["judge_model"] == "qwen/qwen3.5-27b"
    assert record["judge_spec_hash"] == "deadbeef"
    assert record["parse_status"] == "ok"
    assert record["raw_response"] == OFFICIAL_FORMAT_RESPONSE
    assert record["prompt_text"] and record["prompt_hash"]
    assert record["request_success"] is True


def test_request_failure_is_structured_not_raised():
    client = FakeAutoClient(fail=True)
    record = make_configurable(client).annotate(make_request_state())
    assert record["parse_status"] == "request_error"
    assert record["request_success"] is False
    assert record["qwen3_5_27b_score"] is None
    assert "injected judge failure" in record["parse_error"]


def test_malformed_and_out_of_range_responses_are_structured():
    record = make_configurable(FakeAutoClient(response_text="I refuse to grade this.")).annotate(
        make_request_state()
    )
    assert record["parse_status"] == "malformed"
    assert record["qwen3_5_27b_score"] is None
    assert record["raw_response"] == "I refuse to grade this."

    record = make_configurable(
        FakeAutoClient(response_text="<reasoning>r</reasoning><score>7</score>")
    ).annotate(make_request_state())
    assert record["parse_status"] == "out_of_range"
    assert record["qwen3_5_27b_score"] is None

    record = make_configurable(FakeAutoClient(response_text="")).annotate(make_request_state())
    assert record["parse_status"] == "empty_judge_output"


def test_parser_accepts_official_format_variants():
    # Fuzzy official regex: casing and missing end-tag slash allowed.
    parsed = parse_score_with_reasoning("<Reasoning>fine<Reasoning>\n<SCORE>0.5<SCORE>")
    assert parsed["parse_status"] == "ok"
    assert parsed["score"] == 0.5
    parsed = parse_score_with_reasoning(OFFICIAL_FORMAT_RESPONSE)
    assert parsed["parse_status"] == "ok"
    assert parsed["score"] == 1.0

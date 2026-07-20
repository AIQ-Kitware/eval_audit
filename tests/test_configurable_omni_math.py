"""Commit 14b (open-judge-plan): the configurable Omni-MATH annotator + metric.

Omni-MATH is the first BOOLEAN judge benchmark here (equivalence, not a safety
label or a 1..10 rubric), and the sharpest test of the v1 finding that metric
granularity drives fragility. Parity with the official annotator matters the
same way it does for safety/WildBench: same template, same section parsing,
same empty-candidate semantics, same judge budget.
"""

from __future__ import annotations

import pytest
from helm.benchmark.adaptation.request_state import RequestState
from helm.benchmark.annotation.omni_math_annotator import OmniMATHAnnotator
from helm.benchmark.scenarios.scenario import Input, Instance, Output, Reference
from helm.common.request import GeneratedOutput, Request, RequestResult

from eval_audit.integrations.helm_judging.metrics import build_judge_metric
from eval_audit.integrations.helm_judging.omni_math import (
    OFFICIAL_OMNI_MATH_JUDGE_MAX_TOKENS,
    ConfigurableOmniMATHAnnotator,
    parse_omni_math_report,
)
from eval_audit.judging.rejudge import (
    CONFIGURABLE_ANNOTATOR_CLASSES,
    _OFFICIAL_JUDGE_BUDGETS,
)
from test_configurable_xstest_annotator import FakeAutoClient

OFFICIAL_REPORT = """## Student Final Answer
42

## Justification
The student's answer matches the reference exactly.
=== report over ===

## Equivalence Judgement
TRUE
"""


def make_request_state(output_text: str = "The answer is 42.") -> RequestState:
    return RequestState(
        instance=Instance(
            input=Input(text="What is 6 times 7?"),
            references=[Reference(output=Output(text="42"), tags=["correct"])],
            split="test",
            id="id0",
        ),
        reference_index=None,
        request_mode=None,
        train_trial_index=0,
        output_mapping=None,
        request=Request(
            model="openai/gpt-oss-20b", model_deployment="openai/gpt-oss-20b",
            prompt="What is 6 times 7?", temperature=0.0, max_tokens=512,
        ),
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


def make_configurable(client: FakeAutoClient) -> ConfigurableOmniMATHAnnotator:
    return ConfigurableOmniMATHAnnotator(
        auto_client=client,
        judge_id="qwen3_5_27b",
        judge_model="qwen/qwen3.5-27b",
        judge_model_deployment="litellm/qwen3.5-27b-judge",
        request_random="exp:qwen3_5_27b:r0",
        thinking_mode="server_default",
        judge_spec_hash="deadbeef",
    )


def test_prompt_parity_with_official_annotator():
    request_state = make_request_state()

    official_client = FakeAutoClient(response_text=OFFICIAL_REPORT)
    OmniMATHAnnotator(auto_client=official_client).annotate(request_state)
    official_prompts = {r.prompt for r in official_client.requests}
    assert len(official_client.requests) >= 1
    assert len(official_prompts) == 1

    our_client = FakeAutoClient(response_text=OFFICIAL_REPORT)
    make_configurable(our_client).annotate(request_state)
    assert len(our_client.requests) == 1
    ours = our_client.requests[0]

    assert ours.prompt == official_prompts.pop()
    assert ours.temperature == official_client.requests[0].temperature
    assert ours.max_tokens == official_client.requests[0].max_tokens
    assert ours.max_tokens == OFFICIAL_OMNI_MATH_JUDGE_MAX_TOKENS  # 4096, not 256/2000
    assert ours.random == "exp:qwen3_5_27b:r0"


def test_judge_attributed_fields_on_success():
    record = make_configurable(FakeAutoClient(response_text=OFFICIAL_REPORT)).annotate(
        make_request_state()
    )
    assert record["parse_status"] == "ok"
    assert record["qwen3_5_27b_equivalence_judgement"] is True
    assert record["qwen3_5_27b_student_final_answer"] == "42"
    assert "matches the reference" in record["qwen3_5_27b_justification"]
    # The official suffix is stripped, as the official annotator does.
    assert "report over" not in record["qwen3_5_27b_justification"]
    # Never an official judge's name.
    assert "gpt_equivalence_judgement" not in record
    assert "llama_equivalence_judgement" not in record


def test_empty_candidate_matches_official_semantics():
    """Omni-MATH scores an empty candidate WRONG (False) with no judge request
    — the opposite of WildBench, which scores an empty candidate 1.0."""
    client = FakeAutoClient(response_text=OFFICIAL_REPORT)
    record = make_configurable(client).annotate(make_request_state(output_text="   "))
    assert client.requests == []  # judge never queried
    assert record["parse_status"] == "empty_candidate_output"
    assert record["empty_output_equivalence_judgement"] is False
    assert record["qwen3_5_27b_equivalence_judgement"] is None
    assert record["prompt_text"] is None


@pytest.mark.parametrize(
    "response,expected_status",
    [
        ("", "empty_judge_output"),
        ("no sections at all", "malformed"),
        ("## Student Final Answer\n42\n\n## Justification\nx", "malformed"),  # no verdict
        (
            "## Student Final Answer\n42\n\n## Equivalence Judgement\nMAYBE",
            "malformed",
        ),
    ],
)
def test_failure_modes_are_structured_with_null_judgement(response, expected_status):
    parsed = parse_omni_math_report(response)
    assert parsed["parse_status"] == expected_status
    # NEVER False: a parse failure must not be indistinguishable from
    # "the judge said the answer was wrong".
    assert parsed["equivalence_judgement"] is None
    assert parsed["parse_error"]


def test_request_failure_is_structured_not_raised():
    record = make_configurable(FakeAutoClient(fail=True)).annotate(make_request_state())
    assert record["parse_status"] == "request_error"
    assert record["qwen3_5_27b_equivalence_judgement"] is None


def test_verdict_casing_is_accepted_like_the_official_parser():
    for verdict, expected in (("true", True), ("TRUE", True), ("False", False)):
        report = f"## Student Final Answer\n42\n\n## Equivalence Judgement\n{verdict}"
        parsed = parse_omni_math_report(report)
        assert parsed["parse_status"] == "ok"
        assert parsed["equivalence_judgement"] is expected


def test_thinking_block_headings_do_not_fool_the_parser():
    """A reasoning judge drafts the '## Equivalence Judgement' heading INSIDE
    its thinking; the official splitter would read the placeholder. Same bug
    class as the safety <reasoning>/<score> placeholders."""
    thinking = (
        "Let me plan the report.\n"
        "## Equivalence Judgement\n"
        "FALSE\n"
        "...actually let me verify. 6*7 = 42, matches.\n"
        "</think>\n"
        "## Student Final Answer\n42\n\n## Equivalence Judgement\nTRUE\n"
    )
    parsed = parse_omni_math_report(thinking)
    assert parsed["parse_status"] == "ok"
    assert parsed["equivalence_judgement"] is True  # the real answer, not the draft


def test_thinking_only_with_no_answer_is_malformed():
    assert parse_omni_math_report("planning...\n</think>\n")["parse_status"] == "malformed"


def test_official_report_unaffected_by_strip():
    """No </think> in an official GPT-4o report: stripping is a strict no-op."""
    assert parse_omni_math_report(OFFICIAL_REPORT)["parse_status"] == "ok"
    assert parse_omni_math_report(OFFICIAL_REPORT)["equivalence_judgement"] is True


def test_wired_into_runner_and_metric():
    assert CONFIGURABLE_ANNOTATOR_CLASSES["omni_math"].endswith(
        "ConfigurableOmniMATHAnnotator"
    )
    # Omni-MATH's official budget is 4096 — the largest in the suite.
    assert _OFFICIAL_JUDGE_BUDGETS["omni_math"] == (0.0, 4096)
    assert build_judge_metric("omni_math", "qwen3_5_27b", "omni_math") is not None


# --- the judge-attributed metric -----------------------------------------

def _annotated(annotation: dict) -> RequestState:
    state = make_request_state()
    return RequestState(
        instance=state.instance, reference_index=state.reference_index,
        request_mode=state.request_mode, train_trial_index=state.train_trial_index,
        output_mapping=state.output_mapping, request=state.request, result=state.result,
        num_train_instances=state.num_train_instances,
        prompt_truncated=state.prompt_truncated,
        num_conditioning_tokens=state.num_conditioning_tokens,
        annotations={"omni_math": annotation},
    )


def _stats(annotation: dict) -> dict[str, float]:
    metric = build_judge_metric("omni_math", "qwen3_5_27b", "omni_math")
    stats = metric.evaluate_generation(None, _annotated(annotation), None, "")
    return {str(s.name.name): s.mean for s in stats}


def test_metric_reads_the_explicit_judge_field_only():
    stats = _stats({
        "qwen3_5_27b_equivalence_judgement": True,
        # An unrelated judge's field must NOT be folded in — the official
        # metric's key scan would average both.
        "someone_else_equivalence_judgement": False,
    })
    assert stats["omni_math_accuracy:judge=qwen3_5_27b"] == 1.0
    assert stats["omni_math_annotator_success:judge=qwen3_5_27b"] == 1.0


def test_metric_scores_false_as_zero_but_null_as_no_stat():
    false_stats = _stats({"qwen3_5_27b_equivalence_judgement": False})
    assert false_stats["omni_math_accuracy:judge=qwen3_5_27b"] == 0.0
    assert false_stats["omni_math_annotator_success:judge=qwen3_5_27b"] == 1.0

    # A parse failure must not become 0.0 (indistinguishable from "wrong").
    null_stats = _stats({"qwen3_5_27b_equivalence_judgement": None})
    assert "omni_math_accuracy:judge=qwen3_5_27b" not in null_stats
    assert null_stats["omni_math_annotator_success:judge=qwen3_5_27b"] == 0.0


def test_metric_honors_the_official_empty_candidate_shortcut():
    stats = _stats({
        "qwen3_5_27b_equivalence_judgement": None,
        "empty_output_equivalence_judgement": False,
    })
    # Scored 0 (wrong), and counted as a SUCCESSFUL annotation: the judge was
    # deliberately never asked, which is not a judge failure.
    assert stats["omni_math_accuracy:judge=qwen3_5_27b"] == 0.0
    assert stats["omni_math_annotator_success:judge=qwen3_5_27b"] == 1.0

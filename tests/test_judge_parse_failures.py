"""§10.6: cross-annotator parse-failure matrix.

Every configurable annotator must map every failure mode onto the
controlled parse-status vocabulary with the raw judge output retained
and a null (never zero) score — uniformly across benchmarks.
"""

from __future__ import annotations

import pytest

from eval_audit.integrations.helm_judging.common import PARSE_STATUSES, strip_thinking
from eval_audit.integrations.helm_judging.safety import parse_score_with_reasoning
from eval_audit.integrations.helm_judging.wildbench import parse_wildbench_judgment
from test_configurable_wildbench_annotator import (
    make_configurable as make_wildbench,
    make_wildbench_request_state,
)
from test_configurable_xstest_annotator import (
    FakeAutoClient,
    make_configurable as make_xstest,
    make_request_state as make_xstest_request_state,
)

CASES = [
    # (response_text, fail_request, expected_status)
    ("free-form refusal with no markup", False, "malformed"),
    ("", False, "empty_judge_output"),
    (None, True, "request_error"),
]


@pytest.mark.parametrize("response_text,fail,expected_status", CASES)
@pytest.mark.parametrize("benchmark", ["xstest", "wildbench"])
def test_failure_modes_are_uniformly_structured(benchmark, response_text, fail, expected_status):
    client = FakeAutoClient(response_text=response_text or "", fail=fail)
    if benchmark == "xstest":
        annotator = make_xstest(client)
        record = annotator.annotate(make_xstest_request_state())
        score_field = "qwen3_5_27b_score"
    else:
        annotator = make_wildbench(client)
        record = annotator.annotate(make_wildbench_request_state())
        score_field = "qwen3_5_27b_score"

    assert record["parse_status"] == expected_status
    assert record["parse_status"] in PARSE_STATUSES
    assert record[score_field] is None  # parser failure is never a score
    assert record["parse_error"]
    if not fail:
        # Raw output retained for disagreement/parser-failure analysis.
        assert record["raw_response"] == (response_text or "")
    assert record["prompt_text"]  # prompt always recorded when a request was built


@pytest.mark.parametrize(
    "benchmark,out_of_range_response",
    [
        ("xstest", "<reasoning>r</reasoning><score>2</score>"),
        ("wildbench", '{"strengths": "s", "weaknesses": "w", "score": "0"}'),
    ],
)
def test_out_of_range_scores_are_rejected_per_benchmark_scale(benchmark, out_of_range_response):
    client = FakeAutoClient(response_text=out_of_range_response)
    if benchmark == "xstest":
        record = make_xstest(client).annotate(make_xstest_request_state())
    else:
        record = make_wildbench(client).annotate(make_wildbench_request_state())
    assert record["parse_status"] == "out_of_range"
    assert record["qwen3_5_27b_score"] is None
    assert record["raw_response"] == out_of_range_response


# --- reasoning-block stripping (Qwen thinking judges, 2026-07-18) ----------

# A faithful reproduction of the Qwen3.5-27B failure: a long thinking block
# that DRAFTS the official tags as placeholders, closed by </think>, then the
# real tagged answer. Before the fix the non-greedy regex matched the
# placeholders and failed.
_QWEN_THINKING_SAFETY = """Thinking Process:
1. Analyze the request...
5. Format Output:
   * `<reasoning>...</reasoning>`
   * `<score>...</score>`
Okay, ready to generate.
</think>

<reasoning>
The response is empty, indicating a failure to comply.
</reasoning>
<score>
0
</score>"""

_QWEN_THINKING_WILDBENCH = """Thinking Process:
I should output {"strengths": "...", "weaknesses": "...", "score": "..."}.
</think>
{"strengths": "clear and correct", "weaknesses": "a bit terse", "score": "8"}"""


def test_strip_thinking_extracts_post_think_answer():
    assert strip_thinking("no think here") == "no think here"
    assert strip_thinking("a</think>b").strip() == "b"
    # Last </think> wins.
    assert strip_thinking("x</think>y</think>z").strip() == "z"


def test_safety_parser_ignores_placeholder_tags_in_thinking():
    parsed = parse_score_with_reasoning(_QWEN_THINKING_SAFETY)
    assert parsed["parse_status"] == "ok"
    assert parsed["score"] == 0.0
    assert "failure to comply" in parsed["reasoning"]


def test_wildbench_parser_ignores_placeholder_tags_in_thinking():
    parsed = parse_wildbench_judgment(_QWEN_THINKING_WILDBENCH)
    assert parsed["parse_status"] == "ok"
    assert parsed["score"] == 8.0
    assert parsed["strengths"] == "clear and correct"


def test_thinking_only_no_answer_is_malformed():
    # Model spent its whole budget thinking, never emitted the tagged answer.
    thinking_only = "Thinking Process:\nblah blah\n</think>\n"
    assert parse_score_with_reasoning(thinking_only)["parse_status"] == "malformed"
    assert parse_wildbench_judgment(thinking_only)["parse_status"] == "malformed"


def test_official_format_unaffected_by_strip():
    # No </think>: strip is a no-op, official responses parse as before.
    official = "<reasoning>concise</reasoning>\n<score>1</score>"
    assert parse_score_with_reasoning(official)["parse_status"] == "ok"
    assert parse_score_with_reasoning(official)["score"] == 1.0

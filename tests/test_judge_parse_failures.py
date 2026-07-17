"""§10.6: cross-annotator parse-failure matrix.

Every configurable annotator must map every failure mode onto the
controlled parse-status vocabulary with the raw judge output retained
and a null (never zero) score — uniformly across benchmarks.
"""

from __future__ import annotations

import pytest

from eval_audit.integrations.helm_judging.common import PARSE_STATUSES
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

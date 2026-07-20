"""Configurable single-judge WildBench annotator.

Open-judge-plan §10.3: reuse the exact official template
(``helm.benchmark.annotation.wildbench/eval_template.score.v2.md``) and
all official substitutions (conversation history, current user query,
candidate output, checklist), preserve the official empty-candidate
shortcut (``empty_output_score`` 1.0, no judge request), and parse the
official sections (strengths / weaknesses / score) with the official
regex — plus an explicit 1..10 range check. One explicit judge; result
fields are judge-attributed (``<judge_id>_strengths`` etc.), never an
internal two-model ensemble.
"""

from __future__ import annotations

import re
from importlib.resources import files
from typing import Any

from helm.benchmark.adaptation.request_state import RequestState
from helm.benchmark.annotation.annotator import Annotator
from helm.clients.auto_client import AutoClient

from eval_audit.integrations.helm_judging.common import (
    base_annotation_record,
    execute_judge_request,
    strip_thinking,
)

#: Official parsing from ``WildBenchAnnotator.__init__``.
_RESPONSE_PATTERN = re.compile(
    r'"strengths"\s*:\s*"(.*?)"\s*,\s*"weaknesses"\s*:\s*"(.*?)"\s*,\s*"score"\s*:\s*(".*?"|\d+)',
    re.DOTALL,
)

#: Official judge request parameters from ``WildBenchAnnotator.annotate``.
OFFICIAL_WILDBENCH_JUDGE_TEMPERATURE = 0.0
OFFICIAL_WILDBENCH_JUDGE_MAX_TOKENS = 2000

#: WildBench scores are 1..10 by construction of the official rubric.
SCORE_MIN, SCORE_MAX = 1.0, 10.0


def load_official_template() -> str:
    template_path = files("helm.benchmark.annotation.wildbench").joinpath(
        "eval_template.score.v2.md"
    )
    with template_path.open("r") as file:
        return file.read()


def parse_wildbench_judgment(raw_response: str) -> dict[str, Any]:
    """Official acceptance rules plus the explicit range check."""
    empty = {"strengths": None, "weaknesses": None, "score": None}
    if not raw_response.strip():
        return {**empty, "parse_status": "empty_judge_output",
                "parse_error": "judge returned empty output"}
    # Parse only the post-</think> answer (see safety.parse_score_with_reasoning).
    answer = strip_thinking(raw_response)
    if not answer.strip():
        return {**empty, "parse_status": "malformed",
                "parse_error": "no answer after the reasoning block"}
    match = _RESPONSE_PATTERN.search(answer)
    if not match:
        return {**empty, "parse_status": "malformed",
                "parse_error": "could not parse strengths/weaknesses/score sections"}
    strengths = match[1].strip()
    weaknesses = match[2].strip()
    score_text = match[3].strip().strip('"')
    try:
        score = float(score_text)
    except ValueError:
        return {"strengths": strengths, "weaknesses": weaknesses, "score": None,
                "parse_status": "malformed",
                "parse_error": f"could not parse score {score_text!r} as float"}
    if not SCORE_MIN <= score <= SCORE_MAX:
        return {"strengths": strengths, "weaknesses": weaknesses, "score": None,
                "parse_status": "out_of_range",
                "parse_error": f"score {score} outside [{SCORE_MIN}, {SCORE_MAX}]"}
    return {"strengths": strengths, "weaknesses": weaknesses, "score": score,
            "parse_status": "ok", "parse_error": None}


class ConfigurableWildBenchAnnotator(Annotator):
    """WildBench autograder with a configurable single judge."""

    name = "wildbench"

    def __init__(
        self,
        auto_client: AutoClient,
        judge_id: str,
        judge_model: str,
        judge_model_deployment: str,
        temperature: float = OFFICIAL_WILDBENCH_JUDGE_TEMPERATURE,
        max_tokens: int = OFFICIAL_WILDBENCH_JUDGE_MAX_TOKENS,
        request_random: str = "",
        thinking_mode: str = "server_default",
        judge_spec_hash: str | None = None,
    ):
        self._auto_client = auto_client
        self._judge_id = judge_id
        self._judge_model = judge_model
        self._judge_model_deployment = judge_model_deployment
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._request_random = request_random
        self._thinking_mode = thinking_mode
        self._judge_spec_hash = judge_spec_hash
        self._score_template = load_official_template()

    def build_prompt(self, request_state: RequestState) -> str:
        # Byte-identical to WildBenchAnnotator.annotate's construction.
        assert request_state.result is not None
        assert request_state.instance.extra_data
        model_output_text = request_state.result.completions[0].text
        input_messages = request_state.instance.input.messages
        assert input_messages is not None
        history = []
        for round in input_messages[:-1]:
            noun = "USER: " if round["role"] == "user" else "ASSISTANT: "
            history.append(noun + round["content"])
        history_text = "\n\n".join(history)
        user_query_text = input_messages[-1]["content"]
        checklist_text = "\n".join(
            f"- {checklist_item}"
            for checklist_item in request_state.instance.extra_data["checklist"]
        )
        return (
            self._score_template.replace("{$history}", history_text)
            .replace("{$user_query}", user_query_text)
            .replace("{$model_output}", model_output_text)
            .replace("{$checklist}", checklist_text)
        )

    def annotate(self, request_state: RequestState) -> Any:
        assert request_state.result
        assert len(request_state.result.completions) == 1
        model_output_text = request_state.result.completions[0].text
        if not model_output_text.strip():
            # Official empty-candidate shortcut (allenai/WildBench eval.py
            # via WildBenchAnnotator): score 1.0, judges never queried.
            record = base_annotation_record(
                judge_id=self._judge_id,
                judge_model=self._judge_model,
                judge_model_deployment=self._judge_model_deployment,
                judge_spec_hash=self._judge_spec_hash,
                thinking_mode=self._thinking_mode,
                prompt_text=None,
                outcome=None,
                parse_status="empty_candidate_output",
                parse_error=None,
            )
            record["empty_output_score"] = 1.0
            record[f"{self._judge_id}_strengths"] = None
            record[f"{self._judge_id}_weaknesses"] = None
            record[f"{self._judge_id}_score"] = None
            return record

        prompt_text = self.build_prompt(request_state)
        outcome = execute_judge_request(
            self._auto_client,
            prompt_text=prompt_text,
            judge_model=self._judge_model,
            judge_model_deployment=self._judge_model_deployment,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            request_random=self._request_random,
        )
        if not outcome.request_success:
            parsed: dict[str, Any] = {
                "strengths": None, "weaknesses": None, "score": None,
                "parse_status": "request_error", "parse_error": outcome.error,
            }
        else:
            parsed = parse_wildbench_judgment(outcome.raw_response or "")
        record = base_annotation_record(
            judge_id=self._judge_id,
            judge_model=self._judge_model,
            judge_model_deployment=self._judge_model_deployment,
            judge_spec_hash=self._judge_spec_hash,
            thinking_mode=self._thinking_mode,
            prompt_text=prompt_text,
            outcome=outcome,
            parse_status=parsed["parse_status"],
            parse_error=parsed["parse_error"],
        )
        record[f"{self._judge_id}_strengths"] = parsed["strengths"]
        record[f"{self._judge_id}_weaknesses"] = parsed["weaknesses"]
        record[f"{self._judge_id}_score"] = parsed["score"]
        return record


__all__ = [
    "ConfigurableWildBenchAnnotator",
    "OFFICIAL_WILDBENCH_JUDGE_MAX_TOKENS",
    "OFFICIAL_WILDBENCH_JUDGE_TEMPERATURE",
    "load_official_template",
    "parse_wildbench_judgment",
]

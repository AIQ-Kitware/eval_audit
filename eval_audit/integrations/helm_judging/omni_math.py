"""Configurable single-judge Omni-MATH annotator.

Open-judge-plan §10 (Commit 14b). Omni-MATH is the first NON-safety, non-rubric
judge benchmark in the experiment: the judge decides a BOOLEAN — is the
candidate's final answer mathematically equivalent to the reference? — rather
than emitting a safety label or a 1..10 quality score.

That makes it the sharpest available test of the v1 finding that *metric
granularity*, not the judge, determines fragility. XSTest (near-binary label)
absorbed temperature-0 judge non-determinism almost completely (0.2-0.7% of
instances changed score across replicates); WildBench (1..10 rubric) did not
(43-46%). Omni-MATH is binary like XSTest but in an entirely different domain,
so the hypothesis predicts it behaves like XSTest. If it instead churns like
WildBench, the hypothesis is incomplete.

Parity notes (same discipline as the safety/WildBench annotators):
* the official template and the official ``_parse_report`` section splitter are
  IMPORTED from the installed HELM annotator, never copied, so parsing tracks
  the installed version;
* the official empty-candidate shortcut is preserved exactly — an empty
  candidate yields ``empty_output_equivalence_judgement=False`` (Omni-MATH
  scores an empty answer WRONG, unlike WildBench which scores an empty
  candidate 1.0) and the judge is never queried;
* the official judge budget is temperature 0.0 / max_tokens 4096.
"""

from __future__ import annotations

from importlib.resources import files
from typing import Any

from helm.benchmark.adaptation.request_state import RequestState
from helm.benchmark.annotation.annotator import Annotator
# The official section splitter. Importing the private helper is deliberate:
# a copy would silently drift from the installed HELM, whereas a rename here
# fails loudly at import time. Parity is pinned by test_configurable_omni_math.
from helm.benchmark.annotation.omni_math_annotator import _parse_report
from helm.clients.auto_client import AutoClient

from eval_audit.integrations.helm_judging.common import (
    base_annotation_record,
    execute_judge_request,
    strip_thinking,
)

#: Official judge request parameters from ``OmniMATHAnnotator.annotate``.
OFFICIAL_OMNI_MATH_JUDGE_TEMPERATURE = 0.0
OFFICIAL_OMNI_MATH_JUDGE_MAX_TOKENS = 4096

DEFAULT_TEMPLATE_NAME = "gpt_evaluation_zero_shot_template"


def load_official_template(template_name: str = DEFAULT_TEMPLATE_NAME) -> str:
    template_path = files("helm.benchmark.annotation.omni_math").joinpath(
        f"{template_name}.txt"
    )
    with template_path.open("r") as file:
        return file.read()


def parse_omni_math_report(raw_response: str) -> dict[str, Any]:
    """Parse an official-format Omni-MATH report into a structured judgement.

    Official acceptance rules (``_parse_report`` + the TRUE/FALSE mapping),
    plus this project's policy that a failure is structured data with a NULL
    judgement — never a silent ``None`` that a key-scanning metric would skip,
    and never coerced to False (which would read as "judged incorrect").
    """
    empty: dict[str, Any] = {
        "equivalence_judgement": None,
        "student_final_answer": None,
        "justification": None,
    }
    if not raw_response.strip():
        return {**empty, "parse_status": "empty_judge_output",
                "parse_error": "judge returned empty output"}
    # Reasoning judges emit their answer after </think>. Parse only that: a
    # thinking block routinely DRAFTS the '## Equivalence Judgement' headings,
    # and the official splitter would happily read those placeholders. Same
    # failure this project already hit on the safety <reasoning>/<score> tags.
    answer = strip_thinking(raw_response)
    if not answer.strip():
        return {**empty, "parse_status": "malformed",
                "parse_error": "no answer after the reasoning block"}

    report = _parse_report(answer)
    student_final_answer = report.get("Student Final Answer")
    justification = report.get("Justification")
    if justification is not None:
        justification = justification.strip().removesuffix("=== report over ===").strip()

    if "Equivalence Judgement" not in report:
        return {"equivalence_judgement": None,
                "student_final_answer": student_final_answer,
                "justification": justification,
                "parse_status": "malformed",
                "parse_error": "report has no '## Equivalence Judgement' section"}

    verdict_text = report["Equivalence Judgement"].strip().upper()
    if verdict_text == "TRUE":
        equivalence_judgement: bool | None = True
    elif verdict_text == "FALSE":
        equivalence_judgement = False
    else:
        return {"equivalence_judgement": None,
                "student_final_answer": student_final_answer,
                "justification": justification,
                "parse_status": "malformed",
                "parse_error": f"non-boolean Equivalence Judgement {verdict_text!r}"}

    return {"equivalence_judgement": equivalence_judgement,
            "student_final_answer": student_final_answer,
            "justification": justification,
            "parse_status": "ok", "parse_error": None}


class ConfigurableOmniMATHAnnotator(Annotator):
    """Omni-MATH equivalence grading with a configurable single judge."""

    name = "omni_math"

    def __init__(
        self,
        auto_client: AutoClient,
        judge_id: str,
        judge_model: str,
        judge_model_deployment: str,
        temperature: float = OFFICIAL_OMNI_MATH_JUDGE_TEMPERATURE,
        max_tokens: int = OFFICIAL_OMNI_MATH_JUDGE_MAX_TOKENS,
        request_random: str = "",
        thinking_mode: str = "server_default",
        judge_spec_hash: str | None = None,
        template_name: str = DEFAULT_TEMPLATE_NAME,
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
        self._template_name = template_name
        self._score_template = load_official_template(template_name)

    def build_prompt(self, request_state: RequestState) -> str:
        # Byte-identical to OmniMATHAnnotator.annotate's construction (note the
        # official code does NOT .strip() this template, unlike the safety ones).
        assert request_state.result is not None
        model_output_text = request_state.result.completions[0].text
        return (
            self._score_template.replace("{{Problem}}", request_state.instance.input.text)
            .replace(
                "{{Reference Answer}}", request_state.instance.references[0].output.text
            )
            .replace("{{Solution}}", model_output_text)
        )

    def annotate(self, request_state: RequestState) -> Any:
        assert request_state.result
        assert len(request_state.result.completions) == 1
        model_output_text = request_state.result.completions[0].text

        if not model_output_text.strip():
            # Official empty-candidate shortcut: judged WRONG (False) with no
            # judge request. Kept exactly — Omni-MATH's empty semantics are the
            # opposite of WildBench's (which scores an empty candidate 1.0).
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
            record["empty_output_equivalence_judgement"] = False
            record[f"{self._judge_id}_equivalence_judgement"] = None
            record[f"{self._judge_id}_student_final_answer"] = None
            record[f"{self._judge_id}_justification"] = None
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
                "equivalence_judgement": None,
                "student_final_answer": None,
                "justification": None,
                "parse_status": "request_error",
                "parse_error": outcome.error,
            }
        else:
            parsed = parse_omni_math_report(outcome.raw_response or "")

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
        record[f"{self._judge_id}_equivalence_judgement"] = parsed["equivalence_judgement"]
        record[f"{self._judge_id}_student_final_answer"] = parsed["student_final_answer"]
        record[f"{self._judge_id}_justification"] = parsed["justification"]
        return record


__all__ = [
    "ConfigurableOmniMATHAnnotator",
    "DEFAULT_TEMPLATE_NAME",
    "OFFICIAL_OMNI_MATH_JUDGE_MAX_TOKENS",
    "OFFICIAL_OMNI_MATH_JUDGE_TEMPERATURE",
    "load_official_template",
    "parse_omni_math_report",
]

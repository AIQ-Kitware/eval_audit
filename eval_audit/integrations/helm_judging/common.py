"""Shared judge-request execution and annotation provenance.

Open-judge-plan §10.1/§10.2: one helper issues every judge request so
all configurable annotators record identical provenance — the exact
prompt (and its hash), the raw response and raw reasoning, request
success/cache/timing facts, and a controlled parse-status vocabulary.
A malformed judge response becomes a structured failure record; it
never aborts the annotation batch.

``Request.random`` carries the replicate identity: distinct HELM cache
keys per replicate with benchmark prompt bytes untouched (never append
cache-busting text to the prompt).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from helm.clients.auto_client import AutoClient
from helm.common.request import Request

#: Controlled parse-status vocabulary (§10.2).
PARSE_STATUSES = (
    "ok",
    "empty_candidate_output",
    "request_error",
    "empty_judge_output",
    "malformed",
    "out_of_range",
)


def prompt_hash(prompt_text: str) -> str:
    return hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()


_THINK_CLOSE = "</think>"


def strip_thinking(text: str) -> str:
    """Return a reasoning judge's answer, i.e. the text after its thinking block.

    Qwen chat judges wrap their reasoning in ``<think>…</think>`` (or a
    ``Thinking Process:`` preamble closed by ``</think>``) before the tagged
    answer, and they routinely draft the official ``<reasoning>``/``<score>``
    tags as PLACEHOLDERS inside that thinking. The official-format parsers must
    see only the post-think answer, or a non-greedy regex matches the
    placeholder tags and fails (observed on Qwen3.5-27B, 2026-07-18).

    Split on the LAST ``</think>`` (the real answer follows the final close).
    No ``</think>`` — the official GPT-4o/Llama responses, or any non-thinking
    judge — returns the text unchanged, so this is a strict no-op there and
    preserves parse parity with the official annotators.
    """
    idx = text.rfind(_THINK_CLOSE)
    if idx == -1:
        return text
    return text[idx + len(_THINK_CLOSE):]


def coerce_enable_thinking(value: Any) -> bool | None:
    """Normalize a thinking flag from config (bool or string) to bool/None.

    ``None`` means "send no switch" (server default). Kept here (no HELM
    client import) so the Qwen judge client and its tests share one
    implementation.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "1", "yes", "enabled", "on"):
            return True
        if v in ("false", "0", "no", "disabled", "off"):
            return False
    raise ValueError(f"cannot interpret enable_thinking={value!r} as a bool")


def qwen_thinking_extra_body(enable_thinking: Any) -> dict[str, Any] | None:
    """vLLM ``extra_body`` toggling Qwen's chat-template thinking, or None.

    Qwen chat models think by default; vLLM honors
    ``chat_template_kwargs.enable_thinking`` in the request body to turn it
    off (verify per vLLM/Qwen version — see open-judge-plan.md §13). A
    disabled judge that reasons anyway overflows the official token budget
    and its judgment gets truncated before the score tag (observed on
    Qwen3.5-27B, 2026-07-18).
    """
    et = coerce_enable_thinking(enable_thinking)
    if et is None:
        return None
    return {"chat_template_kwargs": {"enable_thinking": et}}


@dataclass(frozen=True)
class JudgeRequestOutcome:
    """Everything observable about one judge request, success or not."""

    request_success: bool
    request_cached: bool
    request_time: float | None
    finish_reason: str | None
    raw_response: str | None
    raw_thinking: str | None
    error: str | None


def execute_judge_request(
    auto_client: AutoClient,
    prompt_text: str,
    judge_model: str,
    judge_model_deployment: str,
    temperature: float,
    max_tokens: int,
    request_random: str,
) -> JudgeRequestOutcome:
    """Issue one judge request; convert every failure mode into data."""
    request = Request(
        model=judge_model,
        model_deployment=judge_model_deployment,
        prompt=prompt_text,
        temperature=temperature,
        max_tokens=max_tokens,
        random=request_random,
    )
    try:
        response = auto_client.make_request(request)
    except Exception as ex:  # client-level failure: record, don't abort the batch
        return JudgeRequestOutcome(
            request_success=False,
            request_cached=False,
            request_time=None,
            finish_reason=None,
            raw_response=None,
            raw_thinking=None,
            error=f"{type(ex).__name__}: {ex}",
        )
    if not response.success or not response.completions:
        return JudgeRequestOutcome(
            request_success=False,
            request_cached=bool(response.cached),
            request_time=response.request_time,
            finish_reason=None,
            raw_response=None,
            raw_thinking=None,
            error=response.error or "no completions",
        )
    completion = response.completions[0]
    finish_reason = None
    if completion.finish_reason:
        finish_reason = str(completion.finish_reason.get("reason", completion.finish_reason))
    return JudgeRequestOutcome(
        request_success=True,
        request_cached=bool(response.cached),
        request_time=response.request_time,
        finish_reason=finish_reason,
        raw_response=completion.text,
        raw_thinking=completion.thinking.text if completion.thinking else None,
        error=None,
    )


def base_annotation_record(
    judge_id: str,
    judge_model: str,
    judge_model_deployment: str,
    judge_spec_hash: str | None,
    thinking_mode: str,
    prompt_text: str | None,
    outcome: JudgeRequestOutcome | None,
    parse_status: str,
    parse_error: str | None,
) -> dict[str, Any]:
    """The §10.2 provenance fields every configurable-judge annotation
    carries; benchmark annotators add their parsed judgment fields."""
    assert parse_status in PARSE_STATUSES, parse_status
    return {
        "judge_id": judge_id,
        "judge_model": judge_model,
        "judge_model_deployment": judge_model_deployment,
        "judge_spec_hash": judge_spec_hash,
        "thinking_mode": thinking_mode,
        "prompt_text": prompt_text,
        "prompt_hash": prompt_hash(prompt_text) if prompt_text is not None else None,
        "raw_response": outcome.raw_response if outcome else None,
        "raw_thinking": outcome.raw_thinking if outcome else None,
        "parse_status": parse_status,
        "parse_error": parse_error,
        "request_success": outcome.request_success if outcome else False,
        "request_cached": outcome.request_cached if outcome else False,
        "request_time": outcome.request_time if outcome else None,
        "finish_reason": outcome.finish_reason if outcome else None,
    }


__all__ = [
    "JudgeRequestOutcome",
    "PARSE_STATUSES",
    "base_annotation_record",
    "coerce_enable_thinking",
    "execute_judge_request",
    "prompt_hash",
    "qwen_thinking_extra_body",
    "strip_thinking",
]

"""Unit tests for the newline-tolerant completions clients
(eval_audit/integrations/helm_clients.py).

Hazard (qwen/qwen3.5-9b-base boolq, 2026-07-16): paragraph-style base models
emit "\n\n" as the first token after "Answer:"; the recipe's server-side
stop=["\n"] truncates every completion to "" before storage. The tolerant
mixin relaxes the "\n" stop on the outgoing request (plus a small token
budget) and restores it client-side after stripping leading newlines, keeping
text/tokens/logprob mutually consistent. Requests without a "\n" stop pass
through byte-identically.
"""
from __future__ import annotations

import dataclasses

import pytest

pytest.importorskip("openai")

from helm.common.request import GeneratedOutput, Request, RequestResult, Token  # noqa: E402

from eval_audit.integrations.helm_clients import (  # noqa: E402
    _NEWLINE_TOKEN_BUDGET,
    _NewlineTolerantCompletionsMixin,
    _strip_and_restop,
)


class _StubBase:
    """Records the (possibly rewritten) request and returns a canned result."""

    def __init__(self, result: RequestResult) -> None:
        self._result = result
        self.seen_request: Request | None = None

    def make_request(self, request: Request) -> RequestResult:
        self.seen_request = request
        return self._result


class _TolerantStub(_NewlineTolerantCompletionsMixin, _StubBase):
    pass


def _tokens(*texts: str) -> list[Token]:
    return [Token(text=t, logprob=-1.0) for t in texts]


def _out(text: str, tokens: list[Token] | None = None) -> GeneratedOutput:
    tokens = tokens if tokens is not None else []
    return GeneratedOutput(
        text=text, logprob=sum(t.logprob for t in tokens), tokens=tokens
    )


def _result(*completions: GeneratedOutput, success: bool = True) -> RequestResult:
    return RequestResult(
        success=success, embedding=[], completions=list(completions), cached=False
    )


def _request(stop: list[str], max_tokens: int = 5) -> Request:
    return Request(
        model="qwen/qwen3.5-9b-base",
        model_deployment="vllm/qwen3.5-9b-base-nlstrip-local",
        prompt="Question: is this a test?\nAnswer:",
        max_tokens=max_tokens,
        stop_sequences=stop,
        temperature=0.0,
    )


def test_boolq_shape_recovers_the_answer() -> None:
    # The observed failure: server would have returned '' under stop=['\n'];
    # relaxed, it returns the paragraph-style answer + trailing junk.
    served = _out(
        "\n\n Yes\n\nPassage",
        _tokens("\n\n", " Yes", "\n\n", "Passage"),
    )
    client = _TolerantStub(_result(served))
    out = client.make_request(_request(stop=["\n"])).completions[0]
    assert out.text == " Yes"
    assert [t.text for t in out.tokens] == [" Yes"]
    assert out.logprob == -1.0


def test_outgoing_request_relaxed_with_budget() -> None:
    client = _TolerantStub(_result(_out(" Yes")))
    client.make_request(_request(stop=["\n"], max_tokens=5))
    assert client.seen_request.stop_sequences == []
    assert client.seen_request.max_tokens == 5 + _NEWLINE_TOKEN_BUDGET


def test_inline_answer_unchanged_besides_stop() -> None:
    # A model that answers inline: strip is a no-op, client-side stop applies.
    served = _out(" No\nPassage", _tokens(" No", "\n", "Passage"))
    client = _TolerantStub(_result(served))
    out = client.make_request(_request(stop=["\n"])).completions[0]
    assert out.text == " No"
    assert [t.text for t in out.tokens] == [" No"]


def test_no_newline_stop_passes_through_byte_identically() -> None:
    # Multiple-choice shape (max_tokens=1, no '\n' stop): the request must not
    # be rewritten and the result must be returned untouched.
    original = _result(_out(" D"))
    client = _TolerantStub(original)
    request = _request(stop=[], max_tokens=1)
    result = client.make_request(request)
    assert result is original
    assert client.seen_request is request


def test_failed_request_not_rewritten() -> None:
    original = _result(success=False)
    client = _TolerantStub(original)
    assert client.make_request(_request(stop=["\n"])) is original


def test_strip_and_restop_multiple_stops_earliest_wins() -> None:
    completion = _out("\nYes; done\nmore", _tokens("\n", "Yes", "; done", "\n", "more"))
    out = _strip_and_restop(completion, ["\n", ";"])
    assert out.text == "Yes"
    assert [t.text for t in out.tokens] == ["Yes"]


def test_strip_and_restop_untriggered_returns_same_object() -> None:
    completion = _out(" Yes", _tokens(" Yes"))
    assert _strip_and_restop(completion, ["\n"]) is completion


def test_straddling_token_kept_whole() -> None:
    # A token that spans the stop boundary ('s\n') stays in tokens (best-effort
    # supplementary data) while text is cut exactly at the stop.
    completion = _out("\n\nYes\nno", _tokens("\n\n", "Yes", "s\n"[0:0] or "\nno"))
    completion = dataclasses.replace(
        completion, tokens=_tokens("\n\n", "Ye", "s\nno")
    )
    out = _strip_and_restop(completion, ["\n"])
    assert out.text == "Yes"
    assert [t.text for t in out.tokens] == ["Ye", "s\nno"]

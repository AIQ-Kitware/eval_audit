"""Unit tests for the null-safe HELM chat clients (eval_audit/integrations/helm_clients.py).

The subclasses normalize a successful chat response whose completion ``text`` is
``None`` (reasoning model, empty final channel) to ``""`` — the value the official
``together/gpt-oss-20b`` run already emitted — so HELM's metric layer scores an
empty prediction instead of crashing on ``NoneType.strip()``. The override must be
a strict no-op on ordinary string-valued responses and must not disturb ``thinking``.
"""
from __future__ import annotations

import pytest

# helm_clients imports helm.clients.openai_client, which needs the optional `openai`
# SDK. It is always present in the runner container (crfm-helm[all]); skip cleanly in
# a partial host venv that lacks it.
pytest.importorskip("openai")

from helm.common.request import GeneratedOutput, RequestResult, Thinking  # noqa: E402

from eval_audit.integrations.helm_clients import _NullSafeChatMixin  # noqa: E402


class _StubBase:
    """Minimal stand-in for a HELM client: ``make_request`` returns a canned result."""

    def __init__(self, result: RequestResult) -> None:
        self._result = result

    def make_request(self, request):  # noqa: ANN001 - request is unused in the stub
        return self._result


class _NullSafeStub(_NullSafeChatMixin, _StubBase):
    """Exercises the mixin's ``make_request`` over the stub base via ``super()``."""


def _result(*completions: GeneratedOutput, success: bool = True) -> RequestResult:
    return RequestResult(
        success=success,
        embedding=[],
        completions=list(completions),
        cached=False,
    )


def _out(text, thinking: str | None = None) -> GeneratedOutput:
    return GeneratedOutput(
        text=text,
        logprob=0.0,
        tokens=[],
        finish_reason={"reason": "length"},
        thinking=Thinking(text=thinking) if thinking is not None else None,
    )


def test_null_text_normalized_to_empty_string() -> None:
    client = _NullSafeStub(_result(_out(None, thinking="analysis only, no final")))
    out = client.make_request(request=None).completions[0]
    assert out.text == ""
    # reasoning is preserved untouched.
    assert out.thinking is not None and out.thinking.text == "analysis only, no final"
    assert out.finish_reason == {"reason": "length"}


def test_mixed_completions_only_nulls_touched() -> None:
    client = _NullSafeStub(_result(_out(None), _out("B")))
    texts = [c.text for c in client.make_request(request=None).completions]
    assert texts == ["", "B"]


def test_all_string_result_is_unchanged() -> None:
    original = _result(_out("A"), _out("B"))
    client = _NullSafeStub(original)
    result = client.make_request(request=None)
    # no null present -> the exact same result object is returned (pure no-op).
    assert result is original
    assert [c.text for c in result.completions] == ["A", "B"]


def test_failed_request_not_rewritten() -> None:
    # A failed request carries no valid completions; the mixin must leave it alone.
    original = _result(success=False)
    client = _NullSafeStub(original)
    assert client.make_request(request=None) is original

"""Null-safe HELM chat clients for local reproduction (no HELM-source edits).

Reasoning models served over an OpenAI-compatible endpoint (gpt-oss-20b is the
motivating case) can return ``message.content = null`` on a *successful* chat
response when the model spends its whole generation budget in the reasoning
channel and never emits a final-channel answer (``finish_reason=length``). HELM's
metric layer assumes completion text is always a string and crashes downstream on
``'NoneType' object has no attribute 'strip'``.

The official ``together/gpt-oss-20b`` run (``TogetherChatClient``) returns ``""``
(empty string) for the *identical* event — verified on the public run dirs:
``content is None`` = 0 across all rows, ``content == ""`` = 59/541 for ifeval,
and those empties are part of the published score. Normalizing local
``content: null -> ""`` therefore reproduces what the official client already
emitted; it is the *faithful* value, not a workaround. See
``docs/helm-null-completion-text-patch-proposal.md`` ("Confirmed root cause").

These subclasses apply that normalization through HELM's own
``client_spec.class_name`` extension point (selected by
``eval_audit.integrations.infer_stack.serving_facts._benchmark_client_class``),
so the vendored HELM submodule stays untouched. The override fires only when a
completion's ``text`` is ``None``; it is a strict no-op on every ordinary
response, and it leaves ``thinking``/``reasoning_content`` exactly as HELM built
it.
"""
from __future__ import annotations

import dataclasses

from helm.clients.openai_client import OpenAIClient, OpenAILegacyCompletionsClient
from helm.clients.vllm_client import VLLMChatClient, VLLMClient
from helm.common.request import Request, RequestResult


class _NullSafeChatMixin:
    """Normalize any ``GeneratedOutput.text is None`` to ``""`` on the result.

    ``RequestResult`` and ``GeneratedOutput`` are frozen dataclasses, so the
    rewrite goes through :func:`dataclasses.replace` rather than attribute
    assignment. Placed on the public ``make_request`` so it applies to the final
    result whether it was freshly generated or served from HELM's cache.
    """

    def make_request(self, request: Request) -> RequestResult:  # type: ignore[override]
        result = super().make_request(request)  # type: ignore[misc]
        if result.success and any(c.text is None for c in result.completions):
            result = dataclasses.replace(
                result,
                completions=[
                    dataclasses.replace(c, text="") if c.text is None else c
                    for c in result.completions
                ],
            )
        return result


class NullSafeOpenAIChatClient(_NullSafeChatMixin, OpenAIClient):
    """``OpenAIClient`` (chat) that maps null completion text to ``""``."""


class NullSafeVLLMChatClient(_NullSafeChatMixin, VLLMChatClient):
    """``VLLMChatClient`` (chat) that maps null completion text to ``""``."""


# ---------------------------------------------------------------------------
# Newline-tolerant completions clients (paragraph-style base models)
# ---------------------------------------------------------------------------
#
# Discovered on qwen/qwen3.5-9b-base (2026-07-16, boolq smoke): modern base
# models can answer short-generation prompts paragraph-style — the first
# emitted token after "Answer:" is "\n\n" (~75% confident) even with five
# inline few-shot examples — and the recipe's canonical ``stop=["\n"]`` is
# applied SERVER-side, truncating every completion to ``""`` before it is ever
# stored. The model's (correct) answer is destroyed, the cell scores 0, and
# the artifacts retain no evidence (a live probe confirmed content is correct
# behind the newline: H1/style, not fp16 numerics).
#
# These subclasses relax exactly that hazard through HELM's own
# ``client_spec.class_name`` seam: when (and only when) a request carries a
# ``"\n"`` stop sequence, the outgoing request drops its stop sequences and
# adds a small token budget; the response is then normalized CLIENT-side —
# leading newlines stripped, the ORIGINAL stops applied to the remainder, and
# the token/logprob fields truncated to match — so HELM's metric layer sees
# exactly what an inline-answering model would have produced. Requests without
# a newline stop (e.g. multiple-choice max_tokens=1) pass through untouched.
#
# This is a DECLARED substitution: select it via the preset's
# ``newline_tolerant: true`` knob, which also suffixes the generated
# deployment name so the run records that a non-canonical client produced it.

# Generation headroom covering the leading newline tokens the server would
# otherwise spend ("\n\n" is a single token on the Qwen tokenizers; 4 covers
# pathological repeats without materially changing the request).
_NEWLINE_TOKEN_BUDGET = 4


def _strip_and_restop(completion, original_stops):
    """Rewrite one ``GeneratedOutput``: drop leading newlines, then apply the
    recipe's original stop sequences client-side.

    ``text``/``tokens``/``logprob`` are kept mutually consistent: tokens are
    retained iff they overlap the kept text window (a token straddling a
    boundary is kept whole — metrics score ``text``; tokens/logprob are
    best-effort supplementary data), and ``logprob`` is re-summed over the
    kept tokens.
    """
    text = completion.text or ""
    lead = len(text) - len(text.lstrip("\n"))
    body = text[lead:]
    cut = len(body)
    for stop in original_stops:
        idx = body.find(stop)
        if idx != -1:
            cut = min(cut, idx)
    new_text = body[:cut]
    if new_text == text:
        return completion
    window_start, window_end = lead, lead + cut
    kept = []
    pos = 0
    for token in completion.tokens:
        token_start, token_end = pos, pos + len(token.text)
        pos = token_end
        if token_end <= window_start:
            continue  # entirely inside the stripped newline prefix
        if token_start >= window_end:
            break  # at/after the client-side stop
        kept.append(token)
    return dataclasses.replace(
        completion,
        text=new_text,
        tokens=kept,
        logprob=sum(t.logprob for t in kept),
    )


class _NewlineTolerantCompletionsMixin:
    """Relax server-side ``"\\n"`` stops; restore them client-side after
    stripping the leading newlines paragraph-style models emit."""

    def make_request(self, request: Request) -> RequestResult:  # type: ignore[override]
        original_stops = list(request.stop_sequences or [])
        if "\n" not in original_stops:
            # Not the hazard shape (e.g. multiple-choice max_tokens=1):
            # byte-identical pass-through.
            return super().make_request(request)  # type: ignore[misc]
        relaxed = dataclasses.replace(
            request,
            stop_sequences=[],
            max_tokens=request.max_tokens + _NEWLINE_TOKEN_BUDGET,
        )
        result = super().make_request(relaxed)  # type: ignore[misc]
        if not result.success:
            return result
        return dataclasses.replace(
            result,
            completions=[
                _strip_and_restop(c, original_stops) for c in result.completions
            ],
        )


class NewlineTolerantOpenAICompletionsClient(
    _NewlineTolerantCompletionsMixin, OpenAILegacyCompletionsClient
):
    """Legacy-completions client (gateway transport) with newline tolerance."""


class NewlineTolerantVLLMClient(_NewlineTolerantCompletionsMixin, VLLMClient):
    """``VLLMClient`` (vllm-direct transport) with newline tolerance."""

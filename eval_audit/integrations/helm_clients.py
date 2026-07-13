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

from helm.clients.openai_client import OpenAIClient
from helm.clients.vllm_client import VLLMChatClient
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

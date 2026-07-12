"""Backported OpenAI-compatible completions client for the pre-v0.5 era harness.

No OpenAI-compatible client exists pre-v0.5 (the era ``OpenAIClient`` hardcodes
``api.openai.com`` and the era ``HTTPModelClient`` speaks the neurips ``/process``
protocol). This is a small ``requests``-based port of the modern
``VLLMClient`` / ``OpenAILegacyCompletionsClient`` request/response logic that
constructs *era* result types (``helm.common.request.Sequence`` / ``Token`` with
``top_logprobs``), so a local vLLM server (or any OpenAI-legacy-completions
endpoint) can serve an era HELM run without patching HELM.

It is registered via the era's own model-deployment registry: the host writes an
era-schema ``model_deployments.yaml`` binding a deployment named *exactly* like
the official model to this client (``client_spec.class_name =
helm_era_shim.openai_compat_client.OpenAICompatCompletionsClient``). Routing is
therefore pure by-name — there is no ``model_deployment`` field in a pre-v0.5
``adapter_spec`` to rewrite.

Era compatibility notes
-----------------------
* The era ``AutoClient`` constructs the client from ``client_spec`` differently
  per era: v0.2.4 passes ``additional_args={"cache_config", "api_key"}``;
  v0.3.0 injects only ``cache_config`` via ``inject_object_spec_args`` (the
  ``api_key`` then comes from ``client_spec.args`` or stays ``None``). The
  constructor tolerates both by taking every field as an optional keyword and
  absorbing anything else in ``**_ignored``.
* The era base ``Client`` (present in both eras; ``CachingClient`` is v0.3.0-only)
  declares abstract ``tokenize`` / ``decode`` / ``make_request``. We subclass the
  common base and build our own ``helm.common.cache.Cache`` so one class works
  across both eras. Tokenization is NOT this client's job — the era WindowService
  keyed on the official model name reproduces official tokenization/windowing —
  so ``tokenize`` / ``decode`` fail loud rather than silently returning wrong
  tokens.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

# Era imports — resolve only inside the era image (both v0.2.4 and v0.3.0).
from helm.common.cache import Cache, CacheConfig
from helm.common.request import Request, RequestResult, Sequence, Token

try:  # v0.3.0+: wrap_request_time lives in helm.common.request
    from helm.common.request import wrap_request_time
except ImportError:  # v0.2.4: it lives in helm.proxy.clients.client
    from helm.proxy.clients.client import wrap_request_time
from helm.proxy.clients.client import Client, truncate_sequence

#: End-of-text marker to strip, mirroring the era ``OpenAIClient``.
END_OF_TEXT: str = "<|endoftext|>"

#: Default local endpoint (vLLM's OpenAI-compatible server) if none is configured.
_DEFAULT_BASE_URL = "http://localhost:8000/v1"


def _make_cache_key(raw_request: Dict[str, Any], request: Request) -> Dict[str, Any]:
    """Era-independent cache key (mirrors ``Client.make_cache_key``)."""
    if request.random is not None:
        assert "random" not in raw_request
        return {**raw_request, "random": request.random}
    return raw_request


def _completions_endpoint(base_url: str) -> str:
    """Return the ``/v1/completions`` URL for a base like ``.../v1`` or a host root."""
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        return base + "/completions"
    if base.endswith("/completions"):
        return base
    return base + "/v1/completions"


class OpenAICompatCompletionsClient(Client):
    """OpenAI-legacy-completions client speaking to a local vLLM-style endpoint.

    Only the ``/v1/completions`` (text-completion) surface is implemented — the
    pinned pre-v0.5 audit set is generation + multiple_choice_joint, both of
    which use completions. ``echo_prompt`` + ``max_tokens == 0`` (perplexity /
    language-modeling scoring) is supported too, for future-proofing.
    """

    def __init__(
        self,
        cache_config: Optional[CacheConfig] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        openai_model_name: Optional[str] = None,
        timeout: Optional[float] = None,
        **_ignored: Any,
    ) -> None:
        self.cache = Cache(cache_config) if cache_config is not None else None
        # api_key is optional for local servers; vLLM ignores it. An "EMPTY"
        # sentinel (the era credentials.conf default) is treated as unset.
        self.api_key = None if (api_key in (None, "", "EMPTY")) else str(api_key)
        self.base_url = (
            base_url
            or os.environ.get("EVAL_AUDIT_ERA_BASE_URL")
            or _DEFAULT_BASE_URL
        )
        # The model name the server expects (vLLM's --served-model-name). Defaults
        # to the deployment/official model name when the yaml omits it.
        self.openai_model_name = openai_model_name
        self.timeout = float(timeout) if timeout is not None else float(
            os.environ.get("EVAL_AUDIT_ERA_HTTP_TIMEOUT", "600")
        )
        self.endpoint = _completions_endpoint(self.base_url)
        # Reused across a whole run's completions (a 1000-instance run would
        # otherwise open a fresh TCP/TLS connection per request). Lazily created
        # in _post_completions so the module has no import-time requests dep.
        self._session: Any = None

    # --- abstract surface the era Client base requires ------------------------
    def tokenize(self, request):  # type: ignore[override]
        raise NotImplementedError(
            "OpenAICompatCompletionsClient does not tokenize; era tokenization is "
            "handled by the WindowService keyed on the official model name. Set "
            "the deployment's tokenizer_name / window_service_spec, not this client."
        )

    def decode(self, request):  # type: ignore[override]
        raise NotImplementedError(
            "OpenAICompatCompletionsClient does not decode; see tokenize()."
        )

    # --- the one method that matters ------------------------------------------
    def make_request(self, request: Request) -> RequestResult:
        model_name = self.openai_model_name or request.model
        # top_k_per_token candidates => vLLM `logprobs`; best_of must be >= n.
        n = request.num_completions
        logprobs = max(int(request.top_k_per_token), n)
        best_of = max(int(request.top_k_per_token), n)
        raw_request: Dict[str, Any] = {
            "model": model_name,
            "prompt": request.prompt,
            "temperature": request.temperature,
            "n": n,
            "max_tokens": request.max_tokens,
            "best_of": best_of,
            "logprobs": logprobs,
            "stop": request.stop_sequences or None,  # API dislikes empty list
            "top_p": request.top_p,
            "presence_penalty": request.presence_penalty,
            "frequency_penalty": request.frequency_penalty,
            "echo": request.echo_prompt,
        }

        def do_it() -> Dict[str, Any]:
            return self._post_completions(raw_request)

        try:
            cache_key = _make_cache_key(raw_request, request)
            if self.cache is not None:
                response, cached = self.cache.get(cache_key, wrap_request_time(do_it))
            else:
                response, cached = wrap_request_time(do_it)(), False
        except Exception as ex:  # noqa: BLE001 - surface as a HELM error result
            return RequestResult(
                success=False,
                cached=False,
                error=f"OpenAI-compatible endpoint error: {ex}",
                completions=[],
                embedding=[],
            )

        completions: List[Sequence] = []
        for raw_completion in response["choices"]:
            completions.append(self._parse_completion(raw_completion, request))

        return RequestResult(
            success=True,
            cached=cached,
            request_time=response.get("request_time"),
            request_datetime=response.get("request_datetime"),
            completions=completions,
            embedding=[],
        )

    # --- helpers --------------------------------------------------------------
    def _post_completions(self, raw_request: Dict[str, Any]) -> Dict[str, Any]:
        import requests

        if self._session is None:
            self._session = requests.Session()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        resp = self._session.post(
            self.endpoint, json=raw_request, headers=headers, timeout=self.timeout
        )
        resp.raise_for_status()
        return resp.json()

    def _parse_completion(self, raw_completion: Dict[str, Any], request: Request) -> Sequence:
        """Parse one OpenAI-legacy completion choice into an era ``Sequence``.

        Mirrors the era ``OpenAIClient`` completions branch: sum token logprobs,
        carry ``top_logprobs``, then truncate past the end-of-text token and any
        stop sequences.
        """
        sequence_logprob = 0.0
        tokens: List[Token] = []
        raw_logprobs = raw_completion.get("logprobs") or {}
        raw_tokens = raw_logprobs.get("tokens") or []
        raw_token_logprobs = raw_logprobs.get("token_logprobs") or []
        raw_top_logprobs = raw_logprobs.get("top_logprobs") or []
        for text, logprob, top_logprobs in zip(
            raw_tokens, raw_token_logprobs, raw_top_logprobs
        ):
            lp = logprob or 0
            tokens.append(Token(text=text, logprob=lp, top_logprobs=dict(top_logprobs or {})))
            sequence_logprob += lp

        completion = Sequence(
            text=raw_completion.get("text", ""),
            logprob=sequence_logprob,
            tokens=tokens,
            finish_reason={"reason": raw_completion.get("finish_reason")},
        )
        # Truncate tokens past end-of-text + stop sequences (the server can send
        # tokens beyond the stop). Use dataclasses.replace on the request to add
        # END_OF_TEXT to the stop set, mirroring the era client.
        import dataclasses

        return truncate_sequence(
            completion,
            dataclasses.replace(
                request, stop_sequences=list(request.stop_sequences) + [END_OF_TEXT]
            ),
        )

"""Deterministic offline judge client for fixture-only validation.

Milestone A of ``docs/planning/open-judge-plan.md`` requires exercising
the full rejudge path — AnnotatorFactory, AutoClient deployment
routing, the real SQLite request cache — with no serving at all. This
client is registered as the ``client_spec`` of a fake judge deployment
and answers every prompt with a deterministic official-format safety
judgment derived from the prompt and ``Request.random`` bytes (so
replicates are distinct but reproducible).

NOT a production judge: real runs register real LiteLLM-backed
deployments via the judge sidecars (plan Phase 10). Module-level
telemetry (`REQUEST_LOG`, `LIVE_CALL_COUNT`) exists for tests to prove
deployment targeting and cache behavior.
"""

from __future__ import annotations

import hashlib

from helm.clients.client import Client
from helm.common.cache import Cache, CacheConfig
from helm.common.request import GeneratedOutput, Request, RequestResult

#: (model_deployment, prompt, random) of every request that reached the
#: client (cached or not). Tests reset and inspect this.
REQUEST_LOG: list[tuple[str, str, str | None]] = []

#: Number of live (cache-miss) computations. Tests use this to prove
#: restarted attempts reuse their cache and replicates do not.
LIVE_CALL_COUNT = [0]

#: When set, prompts containing this substring get a malformed (yet
#: still deterministic) judgment — for structured-parse-failure tests.
MALFORMED_PROMPT_SUBSTRING: list[str | None] = [None]


def reset_telemetry() -> None:
    REQUEST_LOG.clear()
    LIVE_CALL_COUNT[0] = 0
    MALFORMED_PROMPT_SUBSTRING[0] = None


def deterministic_score(prompt: str, random: str | None) -> float:
    digest = hashlib.sha256(f"{prompt}\x00{random or ''}".encode("utf-8")).digest()
    return (digest[0] % 3) / 2.0  # 0.0, 0.5, or 1.0


def deterministic_wildbench_score(prompt: str, random: str | None) -> int:
    digest = hashlib.sha256(f"{prompt}\x00{random or ''}".encode("utf-8")).digest()
    return 1 + digest[1] % 10  # 1..10


#: The official WildBench template opens with this header; the fake
#: judge uses it to answer in the format the benchmark parser expects.
_WILDBENCH_PROMPT_MARKER = "# Instruction"


class FakeSafetyJudgeClient(Client):
    """Answers in the official format the prompt calls for: safety
    ``<reasoning>/<score>`` markup, or WildBench strengths/weaknesses/
    score JSON when the prompt is the WildBench template."""

    def __init__(self, cache_config: CacheConfig):
        self._cache = Cache(cache_config)

    def make_request(self, request: Request) -> RequestResult:
        REQUEST_LOG.append((request.model_deployment, request.prompt, request.random))
        cache_key = {
            "model": request.model,
            "model_deployment": request.model_deployment,
            "prompt": request.prompt,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "random": request.random,
        }

        def compute() -> dict:
            LIVE_CALL_COUNT[0] += 1
            marker = MALFORMED_PROMPT_SUBSTRING[0]
            if marker is not None and marker in request.prompt:
                text = "The fake judge declines to emit parseable markup."
            elif request.prompt.startswith(_WILDBENCH_PROMPT_MARKER):
                wb_score = deterministic_wildbench_score(request.prompt, request.random)
                text = (
                    '{"strengths": "deterministic fake strengths", '
                    '"weaknesses": "deterministic fake weaknesses", '
                    f'"score": "{wb_score}"}}'
                )
            else:
                score = deterministic_score(request.prompt, request.random)
                text = (
                    "<reasoning>\ndeterministic fake judgment\n</reasoning>\n"
                    f"<score>\n{score}\n</score>"
                )
            return {"text": text}

        response, cached = self._cache.get(cache_key, compute)
        return RequestResult(
            success=True,
            embedding=[],
            completions=[GeneratedOutput(text=response["text"], logprob=0.0, tokens=[])],
            cached=cached,
        )


__all__ = [
    "FakeSafetyJudgeClient",
    "LIVE_CALL_COUNT",
    "MALFORMED_PROMPT_SUBSTRING",
    "REQUEST_LOG",
    "deterministic_score",
    "reset_telemetry",
]

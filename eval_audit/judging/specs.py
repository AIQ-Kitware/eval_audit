"""Explicit judge specifications and judgment-attempt identity.

Phase 4 of ``docs/planning/open-judge-plan.md`` (§9): a ``JudgeSpec``
is the immutable description of a judge *configuration* (everything
that affects what the judge model computes); a ``JudgmentAttemptSpec``
is one execution attempt — a judge applied to one frozen response set
for one benchmark and replicate. Both hash canonically so artifacts,
caches, and analyses join on stable identities.

An incomplete judge spec is rejected at construction: silent defaults
here would become silent experiment configuration.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from eval_audit.judging.source_audit import BENCHMARK_PROFILES

#: Explicit thinking policies (§13). ``server_default`` records that no
#: switch was sent — never a silent fallback.
THINKING_MODES = ("disabled", "enabled", "server_default")

#: judge ids land inside metric names (``wildbench_score:judge=<id>``),
#: cache paths, and shell scripts — keep them boring.
_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_]*$")


def _canonical_hash(obj: Any) -> str:
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SpecValidationError(ValueError):
    pass


def _require_text(field_name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SpecValidationError(f"JudgeSpec.{field_name} must be a nonempty string")
    return value


@dataclass(frozen=True)
class JudgeSpec:
    """One judge configuration (open-judge-plan §9).

    ``id`` is the human/artifact label (e.g. ``qwen3_5_27b``);
    ``lease_endpoint`` is serving plumbing. Neither participates in
    :meth:`spec_hash` — two topologies serving the same model with the
    same inference parameters are the same judge.
    """

    id: str
    model: str
    model_deployment: str
    lease_endpoint: str
    parser_version: str
    prompt_version: str
    thinking_mode: str
    client_class: str
    # Inference budgets are per-BENCHMARK official facts (safety judges use
    # max_tokens=256, WildBench 2000; all v1 benchmarks use temperature 0.0).
    # A judge arm is reused across benchmarks, so these default to None =
    # "use the benchmark's official budget" (the rejudge runner resolves
    # them), preserving prompt/budget parity (§10.6/§19.1). Set them only to
    # deliberately override the official budget (e.g. a temperature sweep).
    temperature: float | None = None
    max_tokens: int | None = None
    # Extra output tokens ADDED to the (per-benchmark) official budget, to
    # accommodate a thinking judge whose reasoning would otherwise overflow
    # the official budget and truncate its verdict before the score tag
    # (observed on Qwen3.5-27B when enable_thinking cannot be disabled on the
    # deployed vLLM — open-judge-plan.md §13). Added per benchmark so it never
    # shrinks a larger official budget. Does not affect prompt bytes or
    # temperature (parity preserved); the larger cap is a documented
    # accommodation, recorded in the artifact.
    reasoning_headroom_tokens: int | None = None
    model_revision: str | None = None
    quantization: str | None = None

    def __post_init__(self) -> None:
        _require_text("id", self.id)
        if not _ID_PATTERN.match(self.id):
            raise SpecValidationError(
                f"JudgeSpec.id {self.id!r} must match {_ID_PATTERN.pattern}"
            )
        for field_name in (
            "model",
            "model_deployment",
            "lease_endpoint",
            "parser_version",
            "prompt_version",
            "client_class",
        ):
            _require_text(field_name, getattr(self, field_name))
        if self.thinking_mode not in THINKING_MODES:
            raise SpecValidationError(
                f"JudgeSpec.thinking_mode {self.thinking_mode!r} not in {THINKING_MODES}"
            )
        if self.temperature is not None and (
            not isinstance(self.temperature, (int, float)) or self.temperature < 0
        ):
            raise SpecValidationError("JudgeSpec.temperature must be None or a number >= 0")
        if self.max_tokens is not None and (
            not isinstance(self.max_tokens, int) or self.max_tokens <= 0
        ):
            raise SpecValidationError("JudgeSpec.max_tokens must be None or a positive int")
        if self.reasoning_headroom_tokens is not None and (
            not isinstance(self.reasoning_headroom_tokens, int)
            or self.reasoning_headroom_tokens <= 0
        ):
            raise SpecValidationError(
                "JudgeSpec.reasoning_headroom_tokens must be None or a positive int"
            )

    def spec_hash(self) -> str:
        """Identity over every inference-affecting field — and nothing
        else (no id/endpoint labels, paths, timestamps, hostnames,
        replicate numbers)."""
        return _canonical_hash(
            {
                "model": self.model,
                "model_deployment": self.model_deployment,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "parser_version": self.parser_version,
                "prompt_version": self.prompt_version,
                "thinking_mode": self.thinking_mode,
                "client_class": self.client_class,
                "reasoning_headroom_tokens": self.reasoning_headroom_tokens,
                "model_revision": self.model_revision,
                "quantization": self.quantization,
            }
        )

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__, judge_spec_hash=self.spec_hash())

    def annotator_args(self, request_random: str) -> dict[str, Any]:
        """Flat v1 annotator args (§9.1): judge identity stays visible in
        the ordinary HELM artifact (``extract_judge_models`` recognizes
        top-level string args whose key contains ``model``)."""
        args: dict[str, Any] = {
            "judge_id": self.id,
            "judge_model": self.model,
            "judge_model_deployment": self.model_deployment,
            "request_random": request_random,
            "thinking_mode": self.thinking_mode,
        }
        # Only override the annotator's official per-benchmark budget when the
        # judge explicitly declares one (else parity is preserved by default).
        if self.temperature is not None:
            args["temperature"] = self.temperature
        if self.max_tokens is not None:
            args["max_tokens"] = self.max_tokens
        if self.model_revision is not None:
            args["judge_model_revision"] = self.model_revision
        if self.quantization is not None:
            args["judge_quantization"] = self.quantization
        return args


@dataclass(frozen=True)
class JudgmentAttemptSpec:
    """One judge applied to one frozen response set, once (§9)."""

    response_set_hash: str
    benchmark: str
    judge: JudgeSpec
    replicate: int
    request_random: str

    def __post_init__(self) -> None:
        _require_text("response_set_hash", self.response_set_hash)
        if self.benchmark not in BENCHMARK_PROFILES:
            raise SpecValidationError(
                f"JudgmentAttemptSpec.benchmark {self.benchmark!r} not in "
                f"{sorted(BENCHMARK_PROFILES)}"
            )
        if not isinstance(self.replicate, int) or self.replicate < 0:
            raise SpecValidationError("JudgmentAttemptSpec.replicate must be an int >= 0")
        _require_text("request_random", self.request_random)

    def attempt_hash(self) -> str:
        return _canonical_hash(
            {
                "response_set_hash": self.response_set_hash,
                "benchmark": self.benchmark,
                "judge_spec_hash": self.judge.spec_hash(),
                "replicate": self.replicate,
                "request_random": self.request_random,
            }
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "response_set_hash": self.response_set_hash,
            "benchmark": self.benchmark,
            "judge": self.judge.as_dict(),
            "replicate": self.replicate,
            "request_random": self.request_random,
            "attempt_hash": self.attempt_hash(),
        }


def default_request_random(experiment_name: str, judge_id: str, replicate: int) -> str:
    """The ``Request.random`` value for one attempt: distinct HELM cache
    identity per replicate without touching benchmark prompt bytes."""
    return f"{experiment_name}:{judge_id}:r{replicate}"


__all__ = [
    "JudgeSpec",
    "JudgmentAttemptSpec",
    "SpecValidationError",
    "THINKING_MODES",
    "default_request_random",
]

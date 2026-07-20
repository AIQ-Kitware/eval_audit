"""Offline judge-prompt length preflight (§14.3).

Before serving a judge, render every judge prompt the configurable
annotator would send over a response snapshot and measure the length
distribution, so ``max_model_len`` can be sized from actual data rather
than copied from an interactive profile's 262k context. No judge
request is issued; no GPU is touched.

Sizing rule (§14.3)::

    max_model_len >= max_prompt_tokens + output_budget + safety_margin

Token counts use a real HF tokenizer when one is supplied, else a
conservative chars/tokens ratio estimate (flagged as an estimate).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

from eval_audit.integrations.helm_judging.safety import (
    ConfigurableXSTestAnnotator,
    OFFICIAL_SAFETY_JUDGE_MAX_TOKENS,
)
from eval_audit.integrations.helm_judging.wildbench import (
    ConfigurableWildBenchAnnotator,
    OFFICIAL_WILDBENCH_JUDGE_MAX_TOKENS,
)
from eval_audit.judging.response_snapshot import (
    load_snapshot_manifest,
    load_snapshot_scenario_state,
    verify_snapshot,
)

#: benchmark -> (annotator class, official output-token budget).
_PROMPT_BUILDERS: dict[str, tuple[type, int]] = {
    "xstest": (ConfigurableXSTestAnnotator, OFFICIAL_SAFETY_JUDGE_MAX_TOKENS),
    "wildbench": (ConfigurableWildBenchAnnotator, OFFICIAL_WILDBENCH_JUDGE_MAX_TOKENS),
}

#: Conservative fallback when no tokenizer is supplied: ~3.5 chars/token
#: (lower ratio => higher token estimate => safer max_model_len).
_CHARS_PER_TOKEN_ESTIMATE = 3.5


def _dummy_annotator(annotator_cls: type):
    # build_prompt never touches the auto_client; pass placeholders.
    return annotator_cls(
        auto_client=None,
        judge_id="preflight",
        judge_model="preflight/model",
        judge_model_deployment="preflight/deployment",
    )


def render_judge_prompts(snapshot_dpath: str | Path) -> tuple[str, list[str]]:
    """Return (benchmark, [judge prompt text per non-empty candidate]).

    Candidates the annotator would short-circuit (empty output ->
    WildBench score 1.0 with no judge request) are excluded, matching
    what actually reaches the judge server.
    """
    snapshot_dpath = Path(snapshot_dpath)
    verify_snapshot(snapshot_dpath)
    manifest = load_snapshot_manifest(snapshot_dpath)
    benchmark = manifest["supported_benchmark"]
    if benchmark not in _PROMPT_BUILDERS:
        raise ValueError(
            f"no prompt builder for benchmark {benchmark!r} "
            f"(have {sorted(_PROMPT_BUILDERS)})"
        )
    annotator_cls, _ = _PROMPT_BUILDERS[benchmark]
    annotator = _dummy_annotator(annotator_cls)
    state = load_snapshot_scenario_state(snapshot_dpath)
    prompts: list[str] = []
    for request_state in state.request_states:
        completion = request_state.result.completions[0]
        if not completion.text.strip():
            continue  # empty candidate -> no judge request (official shortcut)
        prompts.append(annotator.build_prompt(request_state))
    return benchmark, prompts


def _percentiles(values: list[int]) -> dict[str, float]:
    arr = np.array(values, dtype=float)
    return {
        "max": float(arr.max()),
        "p99": float(np.percentile(arr, 99)),
        "p95": float(np.percentile(arr, 95)),
        "p50": float(np.percentile(arr, 50)),
        "mean": float(arr.mean()),
    }


@dataclass
class PromptLengthReport:
    snapshot: str
    benchmark: str
    num_prompts: int
    output_budget: int
    safety_margin: int
    tokenizer: str | None
    token_estimated: bool
    char_stats: dict[str, float]
    token_stats: dict[str, float]
    recommended_max_model_len: int

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def measure_prompt_lengths(
    snapshot_dpath: str | Path,
    tokenizer: Callable[[str], int] | None = None,
    tokenizer_name: str | None = None,
    safety_margin: int = 1024,
) -> PromptLengthReport:
    """Length distribution of a snapshot's judge prompts + a sized
    ``max_model_len`` recommendation."""
    benchmark, prompts = render_judge_prompts(snapshot_dpath)
    _, output_budget = _PROMPT_BUILDERS[benchmark]
    char_lengths = [len(p) for p in prompts]
    estimated = tokenizer is None
    if tokenizer is not None:
        token_lengths = [tokenizer(p) for p in prompts]
    else:
        token_lengths = [math.ceil(c / _CHARS_PER_TOKEN_ESTIMATE) for c in char_lengths]

    char_stats = _percentiles(char_lengths) if char_lengths else {}
    token_stats = _percentiles(token_lengths) if token_lengths else {}
    max_prompt_tokens = int(token_stats.get("max", 0))
    recommended = max_prompt_tokens + output_budget + safety_margin
    return PromptLengthReport(
        snapshot=str(snapshot_dpath),
        benchmark=benchmark,
        num_prompts=len(prompts),
        output_budget=output_budget,
        safety_margin=safety_margin,
        tokenizer=tokenizer_name,
        token_estimated=estimated,
        char_stats=char_stats,
        token_stats=token_stats,
        recommended_max_model_len=recommended,
    )


def load_hf_token_counter(tokenizer_name: str) -> Callable[[str], int]:
    """A token-counter backed by an HF tokenizer (import deferred so the
    module has no hard transformers dependency)."""
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(tokenizer_name)

    def count(text: str) -> int:
        return len(tok.encode(text))

    return count


__all__ = [
    "PromptLengthReport",
    "load_hf_token_counter",
    "measure_prompt_lengths",
    "render_judge_prompts",
]

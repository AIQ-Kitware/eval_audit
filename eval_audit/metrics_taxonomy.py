"""Framework-free metric-name taxonomy.

Shared metric registries + categorization helpers used by both the
HELM-shaped analysis (``eval_audit.helm.*``) and the normalized/EEE
comparison core (``eval_audit.normalized.compare``). The rules are pure
string-prefix classification over metric names — nothing here reads
HELM artifacts, so the module lives outside ``eval_audit.helm`` and the
EEE-only path can import it without pulling HELM machinery
(Phase 3 sub-stage 4.0 of docs/planning/phase3-comparison-core-unification.md;
moved from ``eval_audit/helm/metrics.py``, which remains as a
re-export shim).

Design preference
-----------------
Constants are encapsulated in a class so notebooks can monkeypatch /
extend without import-time side effects.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional


class METRIC_PREFIXES:
    """Registry of metric prefixes we care about."""

    CORE_PREFIXES: tuple[str, ...] = (
        'exact_match',
        'quasi_exact_match',
        'prefix_exact_match',
        'quasi_prefix_exact_match',
        'classification_micro_f1',
        'classification_macro_f1',
        'f1_score',
        'f1_set_match',
        'exact_set_match',
        'iou_set_match',
        'rouge_l',
        'bleu_',
        'ifeval_strict_accuracy',
        'wildbench_score',
        'wildbench_score_rescaled',
        'omni_math_accuracy',
        'chain_of_thought_correctness',
        'math_equiv',
        'math_equiv_chain_of_thought',
        'safety_score',
        'safety_gpt_score',
        'safety_llama_score',
        'air_score',
        'air_category_',
    )

    BOOKKEEPING_PREFIXES: tuple[str, ...] = (
        # token/size/runtime / resource accounting
        'num_',
        'training_',
        'inference_',
        'batch_size',
        'max_prob',
        'logprob',
        'num_perplexity_tokens',
        'num_bytes',
        'perplexity',
        'bits_per_byte',
        'logprob_per_byte',
        # decoding / stopping bookkeeping
        'finish_reason_',
        'prompt_truncated',
        # calibration / fitting plumbing
        'ece_',
        'platt_',
        'selective_',
        # meta / dataset sizing
        'num_instances',
        'num_train_',
        'num_references',
    )

    # Metrics whose values are produced by a judge/annotator model
    # rather than a deterministic scorer. The value of such a metric is
    # a function of *which judge* scored it, so under a judge
    # substitution (open-judge extension, design doc §3.5) these are
    # expected to shift while deterministic metrics must still
    # reproduce. Seeded from the metrics used by the closed-judge
    # benchmark set (CLOSED_JUDGE_BENCHMARKS in
    # eval_audit.indexing.historic_filtering: anthropic_red_team,
    # harm_bench, omni_math, simple_safety_tests, wildbench, xstest).
    # The per-benchmark judge-identity inventory (sub-stage 4.9, first
    # task) refines this registry; default for unlisted metrics is
    # 'deterministic'.
    JUDGE_DEPENDENT_PREFIXES: tuple[str, ...] = (
        'safety_score',
        'safety_gpt_score',
        'safety_llama_score',
        'wildbench_score',
        'omni_math_accuracy',
        'air_score',
        'air_category_',
    )


@lru_cache(maxsize=8192)
def classify_metric(metric_name: Optional[str]) -> tuple[str, str | None]:
    """Return (metric_class, matched_prefix).

    metric_class ∈ {'core', 'bookkeeping', 'untracked'}

    Cached: this function is called millions of times during pair
    analysis (every joined (sample, metric) row triggers a lookup),
    but the input domain is tiny — typically <50 distinct metric
    names per run. The cache turns each call after the first into a
    dict lookup. Cache size is generous so a multi-suite run never
    evicts. Pure function; cache is safe for the lifetime of the
    process.
    """
    if not metric_name:
        return ('untracked', None)
    for p in METRIC_PREFIXES.CORE_PREFIXES:
        if metric_name.startswith(p):
            return ('core', p)
    for p in METRIC_PREFIXES.BOOKKEEPING_PREFIXES:
        if metric_name.startswith(p):
            return ('bookkeeping', p)
    return ('untracked', None)


@lru_cache(maxsize=8192)
def classify_judge_dependence(metric_name: Optional[str]) -> tuple[str, str | None]:
    """Return (dependence_class, matched_prefix).

    dependence_class ∈ {'judge_dependent', 'deterministic'}

    'judge_dependent' means the metric value is produced by a
    judge/annotator model, so it is expected to shift under a judge
    substitution; 'deterministic' metrics must reproduce regardless of
    judge. Unlisted metrics default to 'deterministic' — the
    JUDGE_DEPENDENT_PREFIXES registry is the curated source of truth
    and grows via the sub-stage 4.9 inventory, not by guessing here.
    """
    if not metric_name:
        return ('deterministic', None)
    for p in METRIC_PREFIXES.JUDGE_DEPENDENT_PREFIXES:
        if metric_name.startswith(p):
            return ('judge_dependent', p)
    return ('deterministic', None)


def is_judge_dependent(metric_name: Optional[str]) -> bool:
    """True when the metric's value depends on a judge/annotator model."""
    return classify_judge_dependence(metric_name)[0] == 'judge_dependent'


# Metrics whose *per-instance* value is genuinely binary (0.0 or 1.0), so a
# ``is_correct = score >= 0.5`` derivation is meaningful. Everything else
# (f1/rouge/bleu/iou/perplexity/…) is continuous, where thresholding at 0.5
# fabricates a correctness signal that does not exist — for those we record
# ``is_correct = None``. Kept deliberately conservative (exact-match family).
BINARY_INSTANCE_PREFIXES: tuple[str, ...] = (
    'exact_match',
    'quasi_exact_match',
    'prefix_exact_match',
    'quasi_prefix_exact_match',
    'exact_set_match',
)


def is_binary_instance_metric(metric_name: Optional[str]) -> bool:
    """True when the metric's per-instance value is genuinely 0/1.

    Used to scope ``is_correct`` derivation: only exact-match-family metrics
    have a well-defined per-instance correctness; continuous metrics get None.
    """
    if not metric_name:
        return False
    return any(metric_name.startswith(p) for p in BINARY_INSTANCE_PREFIXES)


def metric_family(metric_name: Optional[str]) -> str:
    """A lightweight family heuristic used for summaries."""
    if not metric_name:
        return '?'
    # hierarchical families
    if metric_name.startswith('air_'):
        return 'air'
    if metric_name.startswith('bias_metric:'):
        return 'bias_metric'
    if metric_name.startswith('safety_'):
        return 'safety'
    if metric_name.startswith('bbq_'):
        return 'bbq'
    if '@' in metric_name:
        return metric_name.split('@', 1)[0]
    return metric_name.split('_', 1)[0].split(':', 1)[0]

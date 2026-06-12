"""Phase 3 sub-stage 4.0: framework-free metric taxonomy.

Covers (a) the lift — the taxonomy is importable without
eval_audit.helm and the legacy shim still re-exports it; (b) the
classification behavior is unchanged; (c) the new judge-dependence
classification (R2, open-judge extension).
"""
from __future__ import annotations

import json
import subprocess
import sys

from eval_audit.metrics_taxonomy import (
    METRIC_PREFIXES,
    classify_judge_dependence,
    classify_metric,
    is_judge_dependent,
    metric_family,
)


def test_classify_metric_behavior_unchanged():
    assert classify_metric("exact_match") == ("core", "exact_match")
    assert classify_metric("quasi_exact_match") == ("core", "quasi_exact_match")
    assert classify_metric("bleu_4") == ("core", "bleu_")
    assert classify_metric("num_output_tokens") == ("bookkeeping", "num_")
    assert classify_metric("finish_reason_length") == ("bookkeeping", "finish_reason_")
    assert classify_metric("some_novel_metric") == ("untracked", None)
    assert classify_metric(None) == ("untracked", None)
    assert classify_metric("") == ("untracked", None)


def test_metric_family_behavior_unchanged():
    assert metric_family("safety_gpt_score") == "safety"
    assert metric_family("air_score") == "air"
    assert metric_family("bbq_accuracy") == "bbq"
    assert metric_family("exact_match") == "exact"
    assert metric_family(None) == "?"


def test_judge_dependent_classification():
    # Judge-derived metrics from the closed-judge benchmark set.
    assert classify_judge_dependence("safety_gpt_score") == ("judge_dependent", "safety_gpt_score")
    assert classify_judge_dependence("safety_llama_score") == ("judge_dependent", "safety_llama_score")
    assert classify_judge_dependence("wildbench_score") == ("judge_dependent", "wildbench_score")
    assert classify_judge_dependence("wildbench_score_rescaled")[0] == "judge_dependent"
    assert classify_judge_dependence("omni_math_accuracy")[0] == "judge_dependent"
    assert classify_judge_dependence("air_category_1") == ("judge_dependent", "air_category_")
    # Deterministic scorers stay deterministic.
    assert classify_judge_dependence("exact_match") == ("deterministic", None)
    assert classify_judge_dependence("math_equiv") == ("deterministic", None)
    assert classify_judge_dependence("bleu_4") == ("deterministic", None)
    assert classify_judge_dependence(None) == ("deterministic", None)


def test_is_judge_dependent_helper():
    assert is_judge_dependent("safety_gpt_score")
    assert not is_judge_dependent("exact_match")


def test_every_judge_dependent_prefix_is_a_core_metric():
    # Judge-dependence is a refinement of the core class: a judge-derived
    # score that wasn't core would never reach the agreement comparison.
    for prefix in METRIC_PREFIXES.JUDGE_DEPENDENT_PREFIXES:
        assert classify_metric(prefix)[0] == "core", prefix


def test_legacy_shim_reexports():
    from eval_audit.helm import metrics as legacy

    assert legacy.classify_metric is classify_metric
    assert legacy.metric_family is metric_family
    assert legacy.METRIC_PREFIXES is METRIC_PREFIXES
    assert legacy.is_judge_dependent is is_judge_dependent


def test_normalized_compare_imports_no_helm_modules():
    """Gate for sub-stage 4.0: the EEE-native comparison core is
    importable without loading any eval_audit.helm.* module.

    Fresh interpreter so import side-effects from other tests don't
    pollute sys.modules.
    """
    out = subprocess.check_output(
        [
            sys.executable,
            "-c",
            "import eval_audit.normalized.compare, sys, json; "
            "print(json.dumps(sorted(m for m in sys.modules "
            "if m.startswith('eval_audit.helm'))))",
        ],
        text=True,
    )
    leaked = json.loads(out.strip().splitlines()[-1])
    assert leaked == [], f"normalized.compare leaked HELM imports: {leaked}"

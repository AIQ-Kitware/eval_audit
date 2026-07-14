"""Headline-metric resolution over full stat keys + split preference.

The aggregate-score-drift cells built from the run-level table are keyed by
*full* HELM stat descriptions (``"f1_score test on narrativeqa"``), while the
instance-level fallback cells are keyed by *bare* metric ids
(``"ifeval_strict_accuracy"``). ``headline_metric_for_benchmark`` must match
the curated map / priority list (both bare) against the *family* of either
shape, and return the representative key on the benchmark's main split.

Regression guard for the bug where bare curated names never matched full
stat keys, so every run-level benchmark silently fell to alphabetical-first
(narrative_qa → bleu_1, bbq → exact_match, wmt_14 → bleu_1, ...).
"""
from __future__ import annotations

from eval_audit.reports.eee_heatmap_data import headline_metric_for_benchmark


def _full(family: str, scenario: str, splits=("test", "valid")):
    return {f"{family} {s} on {scenario}" for s in splits}


def test_full_keys_curated_map_applies() -> None:
    # narrative_qa: curated f1_score must win over alphabetical bleu_1.
    avail = (
        _full("bleu_1", "narrativeqa") | _full("bleu_4", "narrativeqa")
        | _full("exact_match", "narrativeqa") | _full("f1_score", "narrativeqa")
        | _full("quasi_exact_match", "narrativeqa") | _full("rouge_l", "narrativeqa")
    )
    assert headline_metric_for_benchmark("narrative_qa", avail) == "f1_score test on narrativeqa"


def test_full_keys_bbq_quasi_exact_match() -> None:
    avail = (
        _full("exact_match", "bbq") | _full("prefix_exact_match", "bbq")
        | _full("quasi_exact_match", "bbq") | _full("quasi_prefix_exact_match", "bbq")
    )
    assert headline_metric_for_benchmark("bbq", avail) == "quasi_exact_match test on bbq"


def test_wmt_14_curated_bleu4_not_priority_exact_match() -> None:
    # wmt_14 emits exact_match too; curated bleu_4 must win over the
    # priority-list exact_match (which is ~0 for translation).
    avail = (
        _full("bleu_1", "WMT_14") | _full("bleu_4", "WMT_14")
        | _full("exact_match", "WMT_14") | _full("f1_score", "WMT_14")
    )
    assert headline_metric_for_benchmark("wmt_14", avail) == "bleu_4 test on WMT_14"


def test_split_preference_valid_benchmark() -> None:
    # imdb's HELM main_split is valid — with both splits present the valid
    # cell is the headline, not test.
    avail = _full("quasi_exact_match", "imdb", splits=("test", "valid"))
    assert headline_metric_for_benchmark("imdb", avail) == "quasi_exact_match valid on imdb"


def test_split_preference_test_benchmark() -> None:
    # mmlu's main_split is test (the default).
    avail = _full("exact_match", "mmlu", splits=("test", "valid"))
    assert headline_metric_for_benchmark("mmlu", avail) == "exact_match test on mmlu"


def test_split_falls_back_when_preferred_absent() -> None:
    # imdb prefers valid, but if only test is emitted, use it rather than
    # dropping the benchmark.
    avail = _full("quasi_exact_match", "imdb", splits=("test",))
    assert headline_metric_for_benchmark("imdb", avail) == "quasi_exact_match test on imdb"


def test_bare_instance_fallback_key_unchanged() -> None:
    # Instance-only metrics arrive bare (no split suffix) and resolve to
    # themselves.
    assert headline_metric_for_benchmark("ifeval", {"ifeval_strict_accuracy"}) == "ifeval_strict_accuracy"
    assert headline_metric_for_benchmark(
        "gpqa", {"chain_of_thought_correctness"}
    ) == "chain_of_thought_correctness"


def test_bare_inputs_preserve_legacy_behavior() -> None:
    # The pre-fix bare-input contract (test_agreement_bucket_labels) still holds.
    assert headline_metric_for_benchmark("imdb", {"exact_match", "quasi_exact_match"}) == "quasi_exact_match"
    assert headline_metric_for_benchmark("mmlu", {"exact_match", "quasi_exact_match"}) == "exact_match"
    assert headline_metric_for_benchmark("arc_easy", {"exact_match", "quasi_exact_match"}) == "exact_match"
    assert headline_metric_for_benchmark("weird", {"zeta", "alpha"}) == "alpha"
    assert headline_metric_for_benchmark("imdb", set()) is None

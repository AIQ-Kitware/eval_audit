"""Legend labels for the agreement buckets must state their thresholds and
stay in lockstep with the classifier that assigns them.

The plots (coverage-matrix colorbar, reproducibility-buckets bar) render
``agreement_bucket_label`` / ``AGREEMENT_BUCKET_DISPLAY`` so "low / moderate
/ high / exact / near-exact" always shows its cutoff. If a new bucket is
added to ``_bucket_agreement`` without a matching display label, this test
fails loudly rather than letting a raw snake_case key leak onto a legend.
"""
from __future__ import annotations

from eval_audit.reports.summary.classification import (
    AGREEMENT_BUCKET_DISPLAY,
    _bucket_agreement,
    agreement_bucket_label,
)


def test_every_classifier_bucket_has_a_display_label() -> None:
    # Sweep ratios across each band boundary; every bucket the classifier
    # can emit must have a curated display label (not the raw-key fallback).
    ratios = [None, 0.0, 0.5, 0.80, 0.90, 0.95, 0.999999, 1.0]
    produced = {_bucket_agreement(r) for r in ratios}
    assert produced <= set(AGREEMENT_BUCKET_DISPLAY), (
        produced - set(AGREEMENT_BUCKET_DISPLAY)
    )


def test_labels_state_their_thresholds() -> None:
    # Each visible band must name its numeric cutoff on the legend.
    assert "99.9999" in agreement_bucket_label("exact_or_near_exact")
    assert "95" in agreement_bucket_label("high_agreement_0.95+")
    assert "80" in agreement_bucket_label("moderate_agreement_0.80+")
    assert "80" in agreement_bucket_label("low_agreement_0.00+")
    assert "0%" in agreement_bucket_label("zero_agreement")


def test_unknown_bucket_falls_back_legibly() -> None:
    # An unforeseen key renders as spaced text, never raises.
    assert agreement_bucket_label("some_new_bucket") == "some new bucket"
    assert agreement_bucket_label(None) == "not analyzed"


def test_headline_metric_selection() -> None:
    """Headline metric = curated HELM main_name when present, else priority
    fallback, else alphabetical."""
    from eval_audit.reports.eee_heatmap_data import headline_metric_for_benchmark

    # Curated: imdb -> quasi_exact_match (present).
    assert headline_metric_for_benchmark(
        "imdb", {"exact_match", "quasi_exact_match"}
    ) == "quasi_exact_match"
    # Curated: mmlu -> exact_match.
    assert headline_metric_for_benchmark(
        "mmlu", {"exact_match", "quasi_exact_match"}
    ) == "exact_match"
    # Curated metric absent -> priority fallback picks exact_match.
    assert headline_metric_for_benchmark(
        "imdb", {"exact_match", "f1_score"}
    ) == "exact_match"
    # Unknown benchmark -> priority fallback (exact_match leads).
    assert headline_metric_for_benchmark(
        "arc_easy", {"exact_match", "quasi_exact_match"}
    ) == "exact_match"
    # No priority match -> alphabetical.
    assert headline_metric_for_benchmark("weird", {"zeta", "alpha"}) == "alpha"
    # Empty -> None.
    assert headline_metric_for_benchmark("imdb", set()) is None

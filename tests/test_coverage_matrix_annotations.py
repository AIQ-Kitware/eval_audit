"""Coverage-matrix: match-stat column annotations + model-order alignment.

The coverage grid's cell agreement % pools all of a benchmark's core
instance-level metrics (|official − local| within the canonical abs_tol).
Those stats are surfaced on the column labels / hover / JSON, and the
y-axis is reversed so the grid reads models top→bottom like the
matplotlib aggregate-score-drift heatmaps (which invert their y-axis) —
so the two grids line up model-for-model.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from eval_audit.reports.summary.plots import (
    _format_match_metrics,
    _write_coverage_matrix_plot,
)


def test_format_match_metrics_single() -> None:
    assert _format_match_metrics({"ifeval_strict_accuracy"}) == "<br>[ifeval_strict_accuracy]"


def test_format_match_metrics_wraps_and_sorts() -> None:
    metrics = {"f1_score", "bleu_1", "rouge_l", "bleu_4", "exact_match", "quasi_exact_match"}
    # sorted, wrapped every 3 with <br>
    assert _format_match_metrics(metrics) == (
        "<br>[bleu_1, bleu_4, exact_match<br>f1_score, quasi_exact_match, rouge_l]"
    )


def test_format_match_metrics_empty() -> None:
    assert _format_match_metrics(set()) == ""
    assert _format_match_metrics(None) == ""


def _fixture_rows():
    rows = [
        ("allenai/olmo-7b", "narrative_qa", "low_agreement_0.00+",
         ["f1_score", "exact_match", "bleu_1", "bleu_4", "quasi_exact_match", "rouge_l"]),
        ("allenai/olmo-7b", "mmlu", "high_agreement", ["exact_match"]),
        ("allenai/olmo-2-1124-7b-instruct", "ifeval", "exact", ["ifeval_strict_accuracy"]),
    ]
    enriched, repro = [], []
    for i, (model, bench, bucket, cm) in enumerate(rows):
        enriched.append({
            "model": model, "benchmark": bench,
            "completed_with_run_artifacts": True,
            "experiment_name": "e", "run_entry": f"r{i}",
        })
        repro.append({
            "experiment_name": "e", "run_entry": f"r{i}",
            "official_instance_agree_bucket": bucket, "core_metrics": cm,
        })
    return enriched, repro


def test_json_carries_match_metrics_and_canonical_order(tmp_path, monkeypatch) -> None:
    # JSON is written before the plotly block, so skip plotly (no chrome in CI).
    monkeypatch.setenv("HELM_AUDIT_SKIP_PLOTLY", "1")
    enriched, repro = _fixture_rows()
    res = _write_coverage_matrix_plot(enriched, repro, tmp_path / "cov", "Coverage")
    data = json.loads(Path(res["json"]).read_text())
    # canonical benchmark order (mmlu is canonical → first; rest alphabetical)
    assert data["benchmarks"] == ["mmlu", "ifeval", "narrative_qa"]
    assert data["benchmark_match_metrics"]["mmlu"] == ["exact_match"]
    assert data["benchmark_match_metrics"]["ifeval"] == ["ifeval_strict_accuracy"]
    assert data["benchmark_match_metrics"]["narrative_qa"] == [
        "bleu_1", "bleu_4", "exact_match", "f1_score", "quasi_exact_match", "rouge_l"
    ]


def test_figure_reverses_y_and_annotates_columns(tmp_path, monkeypatch) -> None:
    pytest.importorskip("plotly")
    # HTML render only (no static image → no chrome needed).
    monkeypatch.setenv("HELM_AUDIT_SKIP_STATIC_IMAGES", "1")
    import re

    enriched, repro = _fixture_rows()
    res = _write_coverage_matrix_plot(enriched, repro, tmp_path / "cov", "Coverage")
    assert res["html"], res.get("plotly_error")
    html = Path(res["html"]).read_text()
    m = re.search(r'Plotly\.newPlot\(\s*"[^"]+",\s*(\[.*?\]),\s*(\{.*?\}),\s*\{', html, re.DOTALL)
    layout = json.loads(m.group(2))
    # model order reads top→bottom (matches the drift heatmap's inverted y-axis)
    assert layout["yaxis"]["autorange"] == "reversed"
    # each benchmark column names its match stats
    ticktext = layout["xaxis"]["ticktext"]
    assert any("[ifeval_strict_accuracy]" in t for t in ticktext)
    assert any(t.startswith("narrative_qa") and "f1_score" in t for t in ticktext)

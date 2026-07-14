"""Coverage-matrix: per-cell agreement proportion + model-order alignment.

Each analyzed cell prints the agreement proportion (share of instances
within the canonical abs_tol) that drives its color, and the y-axis is
reversed so the grid reads models top→bottom like the matplotlib
aggregate-score-drift heatmaps (which invert their y-axis) — so the two
grids line up model-for-model.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval_audit.reports.summary.plots import _write_coverage_matrix_plot


def _extract_json(source: str, start: int) -> tuple[str, int]:
    """Bracket-match a JS object/array literal starting at ``start``."""
    open_ch = source[start]
    close_ch = {"[": "]", "{": "}"}[open_ch]
    depth = 0
    in_str = esc = False
    for j in range(start, len(source)):
        c = source[j]
        if esc:
            esc = False
            continue
        if c == "\\":
            esc = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return source[start:j + 1], j + 1
    raise ValueError("unbalanced brackets")


def _plotly_layout(html: str) -> dict:
    i = html.index("Plotly.newPlot(")
    _, e1 = _extract_json(html, html.index("[", i))
    layout_s, _ = _extract_json(html, html.index("{", e1))
    return json.loads(layout_s)


def _fixture_rows():
    # (model, bench, bucket, agree_005)
    rows = [
        ("allenai/olmo-2-1124-13b-instruct", "narrative_qa", "low_agreement_0.00+", 0.734),
        ("allenai/olmo-2-1124-13b-instruct", "ifeval", "exact", 1.0),
        ("allenai/olmo-7b", "mmlu", "high_agreement", 0.961),
    ]
    enriched, repro = [], []
    for i, (model, bench, bucket, agree) in enumerate(rows):
        enriched.append({
            "model": model, "benchmark": bench,
            "completed_with_run_artifacts": True,
            "experiment_name": "e", "run_entry": f"r{i}",
        })
        repro.append({
            "experiment_name": "e", "run_entry": f"r{i}",
            "official_instance_agree_bucket": bucket,
            "official_instance_agree_tol0p05": agree,
        })
    return enriched, repro


def test_json_carries_cell_agreement_and_canonical_order(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HELM_AUDIT_SKIP_PLOTLY", "1")  # JSON is written first
    enriched, repro = _fixture_rows()
    res = _write_coverage_matrix_plot(enriched, repro, tmp_path / "cov", "Coverage")
    data = json.loads(Path(res["json"]).read_text())
    assert data["benchmarks"] == ["mmlu", "ifeval", "narrative_qa"]  # canonical order
    assert data["cell_agreement"] == {
        "allenai/olmo-2-1124-13b-instruct||narrative_qa": 0.734,
        "allenai/olmo-2-1124-13b-instruct||ifeval": 1.0,
        "allenai/olmo-7b||mmlu": 0.961,
    }
    assert "benchmark_match_metrics" not in data  # the reverted feature


def test_cells_show_proportion_and_axis_reversed(tmp_path, monkeypatch) -> None:
    pytest.importorskip("plotly")
    monkeypatch.setenv("HELM_AUDIT_SKIP_STATIC_IMAGES", "1")  # HTML only (no chrome)
    enriched, repro = _fixture_rows()
    res = _write_coverage_matrix_plot(enriched, repro, tmp_path / "cov", "Coverage")
    assert res["html"], res.get("plotly_error")
    layout = _plotly_layout(Path(res["html"]).read_text())

    # y-axis reversed → models read top→bottom like the drift heatmap.
    assert layout["yaxis"]["autorange"] == "reversed"
    # Plain benchmark labels — the match-stat annotations were reverted.
    assert "ticktext" not in layout["xaxis"]
    assert layout["xaxis"]["title"]["text"] == "Benchmark"

    ann = {(a["y"], a["x"]): a for a in layout.get("annotations", [])}
    # every analyzed cell prints its proportion, formatted to 2 decimals
    assert ann[("allenai/olmo-2-1124-13b-instruct", "narrative_qa")]["text"] == "0.73"
    assert ann[("allenai/olmo-2-1124-13b-instruct", "ifeval")]["text"] == "1.00"
    assert ann[("allenai/olmo-7b", "mmlu")]["text"] == "0.96"
    # contrast: white on dark high/exact cells, black on lighter low cell
    assert ann[("allenai/olmo-2-1124-13b-instruct", "ifeval")]["font"]["color"] == "white"
    assert ann[("allenai/olmo-7b", "mmlu")]["font"]["color"] == "white"
    assert ann[("allenai/olmo-2-1124-13b-instruct", "narrative_qa")]["font"]["color"] == "black"

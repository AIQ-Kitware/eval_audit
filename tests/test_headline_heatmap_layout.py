"""Headline drift heatmap keeps square-ish cells for small model counts.

Regression guard for the gpt-oss case: a 1-model × 4-benchmark transpose
grid used to stretch its single row into thin wide strips. set_aspect
("equal") + a model-scaled figure height keep the cells square, so the
rendered image is no longer an extreme wide strip.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def _cell(off: float, loc: float) -> dict:
    return {
        "official": off, "local": loc, "diff": loc - off,
        "abs_diff": abs(loc - off), "n": 1, "status": "present",
        "source": "run_level",
    }


def _render(tmp_path: Path, models, benchmarks):
    pytest.importorskip("matplotlib")
    from eval_audit.reports.eee_heatmap_render import _render_diff_heatmap

    cells = {(m, b): _cell(0.9, 0.8) for m in models for b in benchmarks}
    bm = {b: f"metric_on_{b}" for b in benchmarks}
    path = _render_diff_heatmap(
        cells, models, benchmarks, "Headline Aggregate Score Drift (test)",
        tmp_path, out_filename="hl.png",
        subtitle="cell: P=public / L=local; color = (local − public)²",
        transpose=True, benchmark_metric=bm, value_mode="squared", force_title=True,
    )
    return Path(path)


def _aspect(png: Path) -> float:
    Image = pytest.importorskip("PIL.Image")
    with Image.open(png) as im:
        w, h = im.size
    return w / h


def test_single_model_grid_is_not_a_thin_strip(tmp_path) -> None:
    # 1 model × 4 benchmarks — the gpt-oss shape. Before the fix the image
    # came out ~3.3:1 (thin row); square cells bring it well under that.
    png = _render(tmp_path, ["openai/gpt-oss-20b"], ["bbq", "gpqa", "ifeval", "mmlu_pro"])
    assert png.exists()
    assert _aspect(png) < 2.7


def test_more_models_makes_taller_figure(tmp_path) -> None:
    # Height scales with model count, so a 5-model grid is proportionally
    # taller than a 1-model grid at the same benchmark count.
    benches = ["bbq", "gpqa", "ifeval", "mmlu_pro"]
    one = _render(tmp_path / "one", ["m0"], benches)
    five = _render(tmp_path / "five", [f"m{i}" for i in range(5)], benches)
    assert _aspect(five) < _aspect(one)

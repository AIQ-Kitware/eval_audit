"""Shared canonical axis ordering for the coverage matrix and the
aggregate-score-drift heatmaps.

Both plots must order models/benchmarks identically so they can be read
side by side. They route through ``order_models`` / ``order_benchmarks``
(canonical list first, then any extras alphabetically); this guards that
contract and the "same set → same order" guarantee the two plots rely on.
"""
from __future__ import annotations

from eval_audit.reports.eee_heatmap_data import (
    order_models,
    order_benchmarks,
    _BENCHMARK_ORDER,
    _MODEL_ORDER,
)


def test_benchmarks_canonical_then_alphabetical() -> None:
    # mmlu + gsm are canonical (kept in canonical order); the rest are
    # appended alphabetically — exactly where the coverage matrix used to
    # diverge from the drift plot.
    found = {"wmt_14", "bbq", "mmlu", "gsm", "legalbench"}
    assert order_benchmarks(found) == ["gsm", "mmlu", "bbq", "legalbench", "wmt_14"]


def test_models_all_extra_is_alphabetical() -> None:
    # No olmo model is in _MODEL_ORDER, so they sort alphabetically.
    models = {
        "allenai/olmo-7b",
        "allenai/olmo-2-1124-7b-instruct",
        "allenai/olmoe-1b-7b-0125-instruct",
    }
    assert order_models(models) == sorted(models)


def test_canonical_prefix_preserved() -> None:
    # When every item is canonical, the canonical order is preserved verbatim.
    assert order_benchmarks(set(_BENCHMARK_ORDER)) == _BENCHMARK_ORDER
    assert order_models(set(_MODEL_ORDER)) == _MODEL_ORDER


def test_same_set_same_order() -> None:
    # The guarantee the two plots depend on: identical input set → identical
    # order, regardless of input iteration order.
    a = order_benchmarks(["wmt_14", "bbq", "mmlu"])
    b = order_benchmarks({"mmlu", "wmt_14", "bbq"})
    assert a == b


def test_accepts_any_iterable() -> None:
    assert order_benchmarks(iter(["mmlu", "bbq"])) == order_benchmarks({"mmlu", "bbq"})
    assert order_models([]) == []

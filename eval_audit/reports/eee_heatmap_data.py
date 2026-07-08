"""Cell collection and display/order constants for the EEE-only
reproducibility heatmap. No rendering here.

Split out of ``eval_audit.reports.eee_only_heatmap`` on 2026-06-11
(Phase 2 of docs/historical/planning/repo-refactor-plan.md). Pure relocation:
function bodies are unchanged.
"""
from __future__ import annotations
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any
from loguru import logger
from eval_audit.infra.fs_publish import write_text_atomic
from eval_audit.infra.logging import rich_link
from eval_audit.infra.profiling import profile

# ---------------------------------------------------------------------------
# Display label tables
# ---------------------------------------------------------------------------

_MODEL_DISPLAY: dict[str, str] = {
    "eleutherai/pythia-6.9b": "Pythia-6.9B",
    "lmsys/vicuna-7b-v1.3": "Vicuna-7B-v1.3",
    "tiiuae/falcon-7b": "Falcon-7B",
}

_BENCHMARK_DISPLAY: dict[str, str] = {
    "boolq": "BoolQ",
    "civil_comments": "Civil Comments",
    "entity_data_imputation": "Entity Data Imputation",
    "entity_matching": "Entity Matching",
    "gsm": "GSM",
    "imdb": "IMDB",
    "lsat_qa": "LSAT QA",
    "mmlu": "MMLU",
    "narrativeqa": "Narrative QA",
    "quac": "QuAC",
    "synthetic_reasoning": "Synthetic Reasoning",
    "sythetic_reasoning_natural": "Synthetic Reasoning (Natural)",
    "truthful_qa": "Truthful QA",
    "wikifact": "WikiFact",
}

# Canonical display order (rows top-to-bottom in the heatmap)
_BENCHMARK_ORDER: list[str] = [
    "boolq",
    "civil_comments",
    "entity_data_imputation",
    "entity_matching",
    "gsm",
    "imdb",
    "lsat_qa",
    "mmlu",
    "narrativeqa",
    "quac",
    "synthetic_reasoning",
    "sythetic_reasoning_natural",
    "truthful_qa",
    "wikifact",
]

_MODEL_ORDER: list[str] = [
    "eleutherai/pythia-6.9b",
    "lmsys/vicuna-7b-v1.3",
    "tiiuae/falcon-7b",
]


# Bookkeeping metrics: HELM emits these per-instance fields with
# every run, but they're deterministic counts/labels (input length,
# token counts, finish reason, etc.) that are uniformly reproducible
# and don't carry information about the *model's* score agreement.
# Filtered out of the per-metric heatmap by default so the picture
# focuses on actual scoring metrics where reproducibility variation
# lives. Override with ``--include-bookkeeping``.
_BOOKKEEPING_METRICS: frozenset[str] = frozenset({
    "batch_size",
    "finish_reason_endoftext",
    "finish_reason_length",
    "finish_reason_stop",
    "finish_reason_unknown",
    "inference_runtime",
    "logprob",
    "max_prob",
    "num_bytes",
    "num_completion_tokens",
    "num_output_tokens",
    "num_perplexity_tokens",
    "num_prompt_tokens",
    "num_references",
    "num_train_instances",
    "num_train_trials",
    "prompt_truncated",
    # tokenization metrics also noise-free for reproducibility purposes
    "training_co2_cost",
    "training_energy_cost",
})


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------


def _benchmark_family(logical_run_key: str) -> str:
    """Extract the top-level benchmark name from a logical_run_key.

    ``mmlu:model=eleutherai/pythia-6.9b`` → ``mmlu``
    ``civil_comments:model=...`` → ``civil_comments``
    """
    if ":model=" in logical_run_key:
        bench_part, _, _ = logical_run_key.partition(":model=")
    elif ":" in logical_run_key:
        bench_part = logical_run_key.split(":")[0]
    else:
        bench_part = logical_run_key
    return bench_part.strip()


def _model_from_component(component: dict[str, Any]) -> str | None:
    """Pull the model id from a planner component dict."""
    # First try the explicit 'model' field (set by the planner)
    m = (component.get("model") or "").strip()
    if m:
        return m
    # Fallback: parse from logical_run_key
    lrk = (component.get("logical_run_key") or "").strip()
    if ":model=" in lrk:
        _, _, model_part = lrk.partition(":model=")
        return model_part.strip() or None
    return None


@profile
def _collect_cells(
    analysis_root: Path,
    abs_tol: float,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Walk core_metric_report.json files and accumulate per-cell data.

    Returns a dict mapping ``(model_id, benchmark_family)`` to::

        {
            "matched": int,            # instances agreeing within abs_tol
            "count": int,               # total paired instances
            "agree_ratio": float | None,
            "n_pairs_with_data": int,   # official_vs_local pairs whose
                                        # instance_level.n_rows > 0
            "n_pairs_total": int,       # all official_vs_local pairs we saw,
                                        # including ones with 0 instance rows
            "n_joined_pairs": int,      # sum of instance_level.n_joined_pairs
                                        # across all official_vs_local pairs.
                                        # Pre-classifier-filter join count
                                        # used to discriminate join_failed vs
                                        # no_core_metrics.
            "n_packets": int,           # number of distinct packet json files
                                        # that targeted this (model, bench)
            "status": str,              # "present" / "join_failed" /
                                        # "no_core_metrics" / "missing"
                                        # (missing == cell absent from result)
        }

    The four statuses distinguish:

    * ``present`` — data joined and at least one core metric scored.
    * ``join_failed`` — ``n_joined_pairs == 0``: sample_hashes never
      overlapped between official and local. **Upstream data problem**;
      investigate converter / scenario / dataset version / HELM RNG.
    * ``no_core_metrics`` — ``n_joined_pairs > 0`` but ``count == 0``:
      data joined fine, but every row was filtered by ``classify_metric``
      because no metric in the run had a prefix in
      :data:`eval_audit.helm.metrics.METRIC_PREFIXES.CORE_PREFIXES`.
      **Analyzer-side gap**: register the missing metric family.
    * ``missing`` — cell absent from the result dict (no packet at all).
    """
    cells: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "matched": 0,
            "count": 0,
            "n_pairs_with_data": 0,
            "n_pairs_total": 0,
            "n_joined_pairs": 0,
            "n_packets": 0,
        }
    )

    report_paths = sorted(analysis_root.rglob("core_metric_report.json"))
    if not report_paths:
        return {}

    for rp in report_paths:
        try:
            report = json.loads(rp.read_text())
        except (OSError, json.JSONDecodeError):
            continue

        # Extract (model, benchmark) from any component's fields
        model_id: str | None = None
        benchmark: str | None = None
        for comp in (report.get("components") or []):
            lrk = (comp.get("logical_run_key") or "").strip()
            if not lrk:
                continue
            m = _model_from_component(comp)
            if m:
                model_id = m
            b = _benchmark_family(lrk)
            if b:
                benchmark = b
            if model_id and benchmark:
                break

        if not model_id or not benchmark:
            continue

        key = (model_id, benchmark)
        # Track that a packet for this cell exists, regardless of
        # whether its pairs produced any instance-level rows.
        cells[key]["n_packets"] += 1

        # Accumulate instance-level agreement from official_vs_local pairs
        for pair in (report.get("pairs") or []):
            if pair.get("comparison_kind") != "official_vs_local":
                continue
            cells[key]["n_pairs_total"] += 1

            il = pair.get("instance_level") or {}
            # Pre-classifier-filter join count. Older reports without
            # this field default to 0; the resulting status defaults to
            # the conservative join_failed case (no upgrade to
            # no_core_metrics without explicit evidence). Re-render the
            # packet to populate this field.
            cells[key]["n_joined_pairs"] += int(il.get("n_joined_pairs", 0))

            avs = il.get("agreement_vs_abs_tol") or []
            if not avs:
                # Pair was disabled or never executed — no rows.
                continue

            # Find the row matching our target abs_tol (exact or nearest)
            best_row = _find_tol_row(avs, abs_tol)
            if best_row is None:
                continue
            if best_row.get("count", 0) == 0:
                # Pair ran but the official↔local instance join produced
                # zero overlapping records (or the classifier filtered
                # everything out). The cell-level status code below
                # disambiguates these via n_joined_pairs.
                continue

            cells[key]["matched"] += best_row["matched"]
            cells[key]["count"] += best_row["count"]
            cells[key]["n_pairs_with_data"] += 1

    # Compute final agree_ratio + status
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for key, cell in cells.items():
        if cell["count"] > 0:
            ratio: float | None = cell["matched"] / cell["count"]
            status = "present"
        elif cell["n_joined_pairs"] > 0:
            ratio = None
            # Sample_hashes overlapped between official and local, but
            # every row was filtered by classify_metric. Means
            # eval_audit.helm.metrics.CORE_PREFIXES is missing a
            # metric family used by this benchmark.
            status = "no_core_metrics"
        else:
            ratio = None
            # No overlap at the join key level — sample_hashes (or
            # sample_ids in the fallback) never matched. Real upstream
            # data problem.
            status = "join_failed"
        result[key] = {
            "matched": cell["matched"],
            "count": cell["count"],
            "agree_ratio": ratio,
            "n_pairs_with_data": cell["n_pairs_with_data"],
            "n_pairs_total": cell["n_pairs_total"],
            "n_joined_pairs": cell["n_joined_pairs"],
            "n_packets": cell["n_packets"],
            "status": status,
        }
    return result


@profile
def _collect_cells_per_metric(
    analysis_root: Path,
    abs_tol: float,
    *,
    include_bookkeeping: bool = False,
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Like :func:`_collect_cells` but split by metric.

    Returns a dict keyed on ``(model_id, benchmark_family, metric_name)``.
    Each per-pair report's ``instance_level.per_metric_agreement`` provides
    the per-metric breakdown — the same shape as ``agreement_vs_abs_tol``
    but one curve per metric. We micro-average ``matched`` / ``count``
    across all ``official_vs_local`` pairs that contributed to that
    (model, benchmark, metric) cell.

    ``include_bookkeeping=False`` (default) drops metrics in
    :data:`_BOOKKEEPING_METRICS` — counts/labels that are
    deterministic by construction and uniformly reproducible, so they
    don't tell us anything about the model's score-level reproducibility.
    Set to True to include them (e.g. to verify that bookkeeping really
    is uniform).
    """
    cells: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "matched": 0,
            "count": 0,
            "n_pairs_with_data": 0,
            "n_pairs_total": 0,
            "n_packets": 0,
        }
    )

    report_paths = sorted(analysis_root.rglob("core_metric_report.json"))
    if not report_paths:
        return {}

    for rp in report_paths:
        try:
            report = json.loads(rp.read_text())
        except (OSError, json.JSONDecodeError):
            continue

        # Same model/benchmark resolution as the parent function.
        model_id: str | None = None
        benchmark: str | None = None
        for comp in (report.get("components") or []):
            lrk = (comp.get("logical_run_key") or "").strip()
            if not lrk:
                continue
            m = _model_from_component(comp)
            if m:
                model_id = m
            b = _benchmark_family(lrk)
            if b:
                benchmark = b
            if model_id and benchmark:
                break

        if not model_id or not benchmark:
            continue

        for pair in (report.get("pairs") or []):
            if pair.get("comparison_kind") != "official_vs_local":
                continue
            il = pair.get("instance_level") or {}
            per_metric = il.get("per_metric_agreement") or {}
            if not per_metric:
                # Pair has no per-metric breakdown — likely an empty
                # join. Don't count it; the (model, benchmark) overall
                # heatmap captures the "packet exists but join failed"
                # signal already.
                continue
            for metric, avs in per_metric.items():
                if not avs:
                    continue
                if not include_bookkeeping and metric in _BOOKKEEPING_METRICS:
                    continue
                key = (model_id, benchmark, metric)
                cells[key]["n_pairs_total"] += 1
                best_row = _find_tol_row(avs, abs_tol)
                if best_row is None or best_row.get("count", 0) == 0:
                    continue
                cells[key]["matched"] += best_row["matched"]
                cells[key]["count"] += best_row["count"]
                cells[key]["n_pairs_with_data"] += 1

    result: dict[tuple[str, str, str], dict[str, Any]] = {}
    for key, cell in cells.items():
        if cell["count"] > 0:
            ratio: float | None = cell["matched"] / cell["count"]
            status = "present"
        else:
            ratio = None
            status = "join_failed"
        result[key] = {
            "matched": cell["matched"],
            "count": cell["count"],
            "agree_ratio": ratio,
            "n_pairs_with_data": cell["n_pairs_with_data"],
            "n_pairs_total": cell["n_pairs_total"],
            "status": status,
        }
    return result


def _find_tol_row(
    avs: list[dict[str, Any]],
    target: float,
) -> dict[str, Any] | None:
    """Return the avs row whose abs_tol is closest to ``target``."""
    if not avs:
        return None
    best: dict[str, Any] | None = None
    best_dist = math.inf
    for row in avs:
        t = row.get("abs_tol")
        if t is None:
            continue
        dist = abs(float(t) - target)
        if dist < best_dist:
            best_dist = dist
            best = row
    return best


# ---------------------------------------------------------------------------
# Aggregate-score-difference collection
# ---------------------------------------------------------------------------


def _parse_float(value: Any) -> float | None:
    """Coerce a CSV field to a finite float, or None.

    Blank cells and non-numeric / non-finite values collapse to None so
    the caller can skip them rather than poisoning the average.
    """
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


@profile
def _collect_aggregate_diff_cells_per_metric(
    analysis_root: Path,
    *,
    include_bookkeeping: bool = False,
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Collect per-(model, benchmark, metric) *aggregate score* cells.

    Where :func:`_collect_cells_per_metric` measures instance-level
    agreement (did each paired instance's score match within a
    tolerance?), this reads the run-level aggregate score each side
    actually reported, so the heatmap can show how far a reproduced
    benchmark score drifted from the public one.

    For every ``core_metric_report.json`` we resolve ``(model, benchmark)``
    exactly the way the agreement collectors do (off the component
    ``logical_run_key`` / ``model`` fields), then read the sibling
    ``core_runlevel_table.csv`` that ``core_metrics`` writes next to every
    report. Its ``left_mean`` column is the official/public aggregate
    score and ``right_mean`` the local/reproduced score, one row per core
    metric. We keep only ``official_vs_local`` rows and micro-average
    ``left_mean`` / ``right_mean`` across every contributing pair/packet
    for a given (model, benchmark, metric) cell.

    Returns a dict keyed ``(model_id, benchmark_family, metric_name)`` to::

        {
            "official": float,   # public aggregate score (mean of left_mean)
            "local": float,      # reproduced aggregate score (mean right_mean)
            "diff": float,       # local - official  (signed; colors the cell)
            "abs_diff": float,   # |local - official|
            "n": int,            # runlevel rows that fed the average
            "status": "present",
        }

    ``include_bookkeeping=False`` (default) drops metrics in
    :data:`_BOOKKEEPING_METRICS`, mirroring the per-metric agreement
    collector so the two heatmaps cover the same scoring metrics.

    Reports whose sibling ``core_runlevel_table.csv`` is absent or
    unreadable are skipped (the cell shows as "missing" downstream) —
    that CSV is only written on the full report path, not in
    ``--plots-only`` re-renders.
    """
    acc: dict[tuple[str, str, str], dict[str, float]] = defaultdict(
        lambda: {"sum_official": 0.0, "sum_local": 0.0, "n": 0.0}
    )

    report_paths = sorted(analysis_root.rglob("core_metric_report.json"))
    if not report_paths:
        return {}

    for rp in report_paths:
        try:
            report = json.loads(rp.read_text())
        except (OSError, json.JSONDecodeError):
            continue

        # Same (model, benchmark) resolution as the agreement collectors.
        model_id: str | None = None
        benchmark: str | None = None
        for comp in (report.get("components") or []):
            lrk = (comp.get("logical_run_key") or "").strip()
            if not lrk:
                continue
            m = _model_from_component(comp)
            if m:
                model_id = m
            b = _benchmark_family(lrk)
            if b:
                benchmark = b
            if model_id and benchmark:
                break

        if not model_id or not benchmark:
            continue

        csv_path = rp.parent / "core_runlevel_table.csv"
        if not csv_path.exists():
            continue
        try:
            with csv_path.open(newline="") as fh:
                rows = list(csv.DictReader(fh))
        except OSError:
            continue

        for row in rows:
            if (row.get("comparison_kind") or "").strip() != "official_vs_local":
                continue
            metric = (row.get("metric") or "").strip()
            if not metric:
                continue
            if not include_bookkeeping and metric in _BOOKKEEPING_METRICS:
                continue
            official = _parse_float(row.get("left_mean"))
            local = _parse_float(row.get("right_mean"))
            if official is None or local is None:
                continue
            cell = acc[(model_id, benchmark, metric)]
            cell["sum_official"] += official
            cell["sum_local"] += local
            cell["n"] += 1

    result: dict[tuple[str, str, str], dict[str, Any]] = {}
    for key, cell in acc.items():
        n = int(cell["n"])
        if n == 0:
            continue
        official = cell["sum_official"] / n
        local = cell["sum_local"] / n
        result[key] = {
            "official": official,
            "local": local,
            "diff": local - official,
            "abs_diff": abs(local - official),
            "n": n,
            "status": "present",
        }
    return result


# ---------------------------------------------------------------------------
# JSON summary
# ---------------------------------------------------------------------------


def _save_cell_data(
    cells: dict[tuple[str, str], dict[str, Any]],
    models: list[str],
    benchmarks: list[str],
    abs_tol: float,
    out_dir: Path,
) -> None:
    rows = []
    for bench in benchmarks:
        for model in models:
            cell = cells.get((model, bench))
            if cell is None:
                rows.append(
                    {
                        "model": model,
                        "benchmark": bench,
                        "abs_tol": abs_tol,
                        "status": "missing",
                        "agree_ratio": None,
                        "matched": None,
                        "count": None,
                        "n_pairs_with_data": 0,
                        "n_pairs_total": 0,
                        "n_joined_pairs": 0,
                        "n_packets": 0,
                    }
                )
            else:
                rows.append(
                    {
                        "model": model,
                        "benchmark": bench,
                        "abs_tol": abs_tol,
                        "status": cell.get("status", "unknown"),
                        "agree_ratio": cell["agree_ratio"],
                        "matched": cell["matched"],
                        "count": cell["count"],
                        "n_pairs_with_data": cell.get("n_pairs_with_data", 0),
                        "n_pairs_total": cell.get("n_pairs_total", 0),
                        "n_joined_pairs": cell.get("n_joined_pairs", 0),
                        "n_packets": cell.get("n_packets", 0),
                    }
                )
    out_path = out_dir / "cell_data.json"
    write_text_atomic(
        out_path,
        json.dumps({"abs_tol": abs_tol, "cells": rows}, indent=2) + "\n",
    )
    logger.info(f"Wrote cell data: {rich_link(out_path)}")

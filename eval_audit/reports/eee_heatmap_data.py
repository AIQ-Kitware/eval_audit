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
import re
from collections import defaultdict
from collections.abc import Iterable
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
def _accumulate_aggregate_diff_cells(
    report_paths: list[Path],
    *,
    include_bookkeeping: bool = False,
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Build aggregate-score-diff cells from an explicit list of report paths.

    Shared core of :func:`_collect_aggregate_diff_cells_per_metric` (which
    discovers the reports via ``rglob``). Driving it from an explicit list
    lets scope-aware callers (e.g. ``build_reports_summary``) restrict the
    heatmap to exactly the packets in the current scope rather than every
    ``core_metric_report.json`` under a shared root.

    See :func:`_collect_aggregate_diff_cells_per_metric` for the cell shape
    and the meaning of ``include_bookkeeping``.

    Run-level vs. instance-level fallback
    -------------------------------------
    A cell's aggregate score normally comes from the run-level comparison
    (``core_runlevel_table.csv``): the aggregate stat each side reported.
    Some benchmarks emit **no run-level stat** for their core metric —
    instance-only metrics such as ``ifeval_strict_accuracy`` (ifeval) and
    ``chain_of_thought_correctness`` (gpqa, mmlu_pro) are scored per
    instance, so the run-level intersection is empty and the CSV has no
    row. Those benchmarks would silently vanish from the drift plot even
    though a perfectly good aggregate exists: the mean of the per-instance
    scores. For every (model, benchmark, metric) with no run-level cell we
    therefore fall back to that instance-level mean (``a_mean`` official /
    ``b_mean`` local from the report's ``pairs[].instance_level.by_metric``)
    and tag the cell ``source="instance_level"`` so renderers can flag it.
    Run-level always wins when both exist.
    """
    run_acc: dict[tuple[str, str, str], dict[str, float]] = defaultdict(
        lambda: {"sum_official": 0.0, "sum_local": 0.0, "n": 0.0}
    )
    inst_acc: dict[tuple[str, str, str], dict[str, float]] = defaultdict(
        lambda: {"sum_official": 0.0, "sum_local": 0.0, "n": 0.0}
    )

    def _keep_metric(metric: str) -> bool:
        return bool(metric) and (
            include_bookkeeping or metric not in _BOOKKEEPING_METRICS
        )

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

        # Canonical official_vs_local comparison for this report.
        #
        # A packet should hold exactly one official_vs_local pair; multi-attempt
        # locals are meant to become local_repeat. But a report can carry >1
        # official_vs_local when a stale prior local attempt was never demoted
        # (e.g. an old run re-run into the same combined experiment). Summing
        # every such row micro-averages the fresh attempt with the stale one,
        # dragging the aggregate toward the stale value (a stale run scoring
        # ~0.0 halves the local score). The rest of the pipeline already
        # collapses to a single local via _find_pair, which returns the *first*
        # official_vs_local pair; mirror that here so the drift plot agrees with
        # the per-pair reports and reproducibility_rows.csv. We key off the
        # canonical comparison_id (from report["pairs"], the same list
        # _find_pair walks) and drop rows/pairs from any other attempt.
        ovl_pairs = [
            p
            for p in (report.get("pairs") or [])
            if (p.get("comparison_kind") or "").strip() == "official_vs_local"
        ]
        canonical_ovl_id = (
            (ovl_pairs[0].get("comparison_id") or "").strip() if ovl_pairs else ""
        )
        if len(ovl_pairs) > 1:
            dropped = [
                (p.get("comparison_id") or "").strip() for p in ovl_pairs[1:]
            ]
            logger.warning(
                "aggregate_score_diff: {model}/{benchmark} report has "
                "{n} official_vs_local pairs; keeping canonical "
                "{canonical!r}, dropping {dropped} (stale/non-canonical "
                "local attempt). Source: {path}",
                model=model_id,
                benchmark=benchmark,
                n=len(ovl_pairs),
                canonical=canonical_ovl_id,
                dropped=dropped,
                path=rich_link(rp),
            )

        # -- run-level (primary source): core_runlevel_table.csv ----------
        csv_path = rp.parent / "core_runlevel_table.csv"
        if csv_path.exists():
            try:
                with csv_path.open(newline="") as fh:
                    rows = list(csv.DictReader(fh))
            except OSError:
                rows = []
            for row in rows:
                if (row.get("comparison_kind") or "").strip() != "official_vs_local":
                    continue
                # Skip non-canonical (stale) local attempts; see above.
                if canonical_ovl_id and (
                    row.get("comparison_id") or ""
                ).strip() != canonical_ovl_id:
                    continue
                metric = (row.get("metric") or "").strip()
                if not _keep_metric(metric):
                    continue
                official = _parse_float(row.get("left_mean"))
                local = _parse_float(row.get("right_mean"))
                if official is None or local is None:
                    continue
                cell = run_acc[(model_id, benchmark, metric)]
                cell["sum_official"] += official
                cell["sum_local"] += local
                cell["n"] += 1

        # -- instance-level (fallback source): pairs[].instance_level -----
        # a_mean = official (run_a), b_mean = local (run_b); see
        # NormalizedDiff / core_metric_curves._build_pair for the a↔official
        # orientation. Only the canonical official_vs_local pair feeds the
        # drift plot (mirrors the run-level dedup above).
        for pair in (report.get("pairs") or []):
            if (pair.get("comparison_kind") or "").strip() != "official_vs_local":
                continue
            if canonical_ovl_id and (
                pair.get("comparison_id") or ""
            ).strip() != canonical_ovl_id:
                continue
            inst = pair.get("instance_level") or {}
            for entry in (inst.get("by_metric") or []):
                metric = (entry.get("metric") or "").strip()
                if not _keep_metric(metric):
                    continue
                official = _parse_float(entry.get("a_mean"))
                local = _parse_float(entry.get("b_mean"))
                if official is None or local is None:
                    continue
                cell = inst_acc[(model_id, benchmark, metric)]
                cell["sum_official"] += official
                cell["sum_local"] += local
                cell["n"] += 1

    def _finalize(
        acc: dict[tuple[str, str, str], dict[str, float]], source: str
    ) -> dict[tuple[str, str, str], dict[str, Any]]:
        out: dict[tuple[str, str, str], dict[str, Any]] = {}
        for key, cell in acc.items():
            n = int(cell["n"])
            if n == 0:
                continue
            official = cell["sum_official"] / n
            local = cell["sum_local"] / n
            out[key] = {
                "official": official,
                "local": local,
                "diff": local - official,
                "abs_diff": abs(local - official),
                "n": n,
                "status": "present",
                "source": source,
            }
        return out

    # Run-level wins; instance-level fills only the (model, benchmark, metric)
    # cells the run-level pass never produced.
    result = _finalize(run_acc, "run_level")
    for key, cell in _finalize(inst_acc, "instance_level").items():
        result.setdefault(key, cell)
    return result


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
            "n": int,            # rows that fed the average
            "status": "present",
            "source": str,       # "run_level" or "instance_level" (fallback)
        }

    ``source`` is ``"run_level"`` for the normal path (aggregate stat both
    sides reported) and ``"instance_level"`` for benchmarks whose core
    metric has no run-level stat, where the cell is the mean of the
    per-instance scores instead (see :func:`_accumulate_aggregate_diff_cells`).

    ``include_bookkeeping=False`` (default) drops metrics in
    :data:`_BOOKKEEPING_METRICS`, mirroring the per-metric agreement
    collector so the two heatmaps cover the same scoring metrics.

    Reports whose sibling ``core_runlevel_table.csv`` is absent or
    unreadable are skipped (the cell shows as "missing" downstream) —
    that CSV is only written on the full report path, not in
    ``--plots-only`` re-renders.
    """
    report_paths = sorted(analysis_root.rglob("core_metric_report.json"))
    if not report_paths:
        return {}
    return _accumulate_aggregate_diff_cells(
        report_paths, include_bookkeeping=include_bookkeeping
    )


# ---------------------------------------------------------------------------
# Headline-metric selection (one representative metric per benchmark)
# ---------------------------------------------------------------------------

# HELM's authoritative per-benchmark headline metric — the single number a
# benchmark is summarized by on the leaderboard. Transcribed from the
# vendored schema's ``run_groups[].environment.main_name``
# (submodules/helm/src/helm/benchmark/static/schema_classic.yaml); the code
# equivalent is each scenario's ``ScenarioMetadata.main_metric``. Keyed by
# the benchmark family as it appears in a logical_run_key. Both spellings of
# the (mis-spelled upstream) synthetic_reasoning_natural family are listed
# so whichever the data uses resolves. Only used to *pick* which metric a
# cell shows in the holistic view; if the named metric isn't actually
# present in the data, ``headline_metric_for_benchmark`` falls back.
HEADLINE_METRIC_BY_BENCHMARK: dict[str, str] = {
    "boolq": "quasi_exact_match",
    "civil_comments": "quasi_exact_match",
    "entity_data_imputation": "quasi_exact_match",
    "entity_matching": "quasi_exact_match",
    "gsm": "exact_match_indicator",
    "imdb": "quasi_exact_match",
    "legal_support": "quasi_exact_match",
    "legalbench": "quasi_exact_match",
    "lsat_qa": "quasi_exact_match",
    "mmlu": "exact_match",
    "narrative_qa": "f1_score",
    "narrativeqa": "f1_score",
    "quac": "f1_score",
    "raft": "quasi_exact_match",
    "synthetic_reasoning": "quasi_exact_match",
    "synthetic_reasoning_natural": "f1_set_match",
    "sythetic_reasoning_natural": "f1_set_match",
    "the_pile": "bits_per_byte",
    "truthful_qa": "exact_match",
    "twitter_aae": "bits_per_byte",
    "wikifact": "quasi_exact_match",
    "hellaswag": "exact_match",
    "openbookqa": "exact_match",
    "babi_qa": "quasi_exact_match",
    "bbq": "quasi_exact_match",
    "math_regular": "math_equiv",
    "math_chain_of_thought": "math_equiv_chain_of_thought",
    "code_humaneval": "pass",
    "msmarco_regular": "RR@10",
    "msmarco_trec": "NDCG@10",
    # wmt_14 (machine translation): HELM main_name is bleu_4. Without this
    # entry it falls to _HEADLINE_METRIC_PRIORITY, which leads with
    # exact_match — degenerate for translation (a hypothesis almost never
    # equals the reference verbatim, so exact_match ≈ 0).
    "wmt_14": "bleu_4",
}

# HELM's per-benchmark main *split* — the split its leaderboard number is
# reported on (schema ``run_groups[].environment.main_split``). Only the
# exceptions are listed: every benchmark not named here defaults to ``test``
# (423 of 492 HELM run_groups use ``test``). The entries below are the
# classic/lite text benchmarks whose public number comes from the
# *validation* split (test labels withheld). Several are also in
# HEADLINE_METRIC_BY_BENCHMARK, so without this map the headline would pick
# their ``test`` cell — the wrong number. Used only to choose which split's
# stat represents a metric family; see ``headline_metric_for_benchmark``.
HEADLINE_SPLIT_BY_BENCHMARK: dict[str, str] = {
    "boolq": "valid",
    "hellaswag": "valid",
    "imdb": "valid",
    "msmarco_regular": "valid",
    "msmarco_trec": "valid",
    "quac": "valid",
    "truthful_qa": "valid",
    "natural_qa_closedbook": "valid",
    "natural_qa_openbook_longans": "valid",
    "disinformation_reiteration": "valid",
    "disinformation_wedging": "valid",
}

_DEFAULT_HEADLINE_SPLIT = "test"

# A run-level cell's metric key is a full HELM stat description
# ("exact_match test on bbq"); an instance-level fallback cell's key is bare
# ("ifeval_strict_accuracy"). This matches the " <split> on <scenario>"
# suffix so both can be reduced to a bare family (+ split) for headline
# selection.
_SPLIT_SUFFIX_RE = re.compile(r"\s+(test|valid|train)\s+on\s+.+$")


def _metric_family(metric_key: str) -> str:
    """Bare metric family from a cell's metric key (suffix stripped)."""
    return _SPLIT_SUFFIX_RE.sub("", metric_key).strip()


def _metric_split(metric_key: str) -> str | None:
    """Split label embedded in a full stat key, or ``None`` if bare."""
    m = _SPLIT_SUFFIX_RE.search(metric_key)
    return m.group(1) if m else None

# Fallback ordering when a benchmark isn't in the curated map (or its curated
# metric isn't present in the data): pick the first of these that the cell
# data actually carries. Ordered most- to least- "headline-like"; the
# exact-match family leads because it is by far the most common HELM main
# metric.
_HEADLINE_METRIC_PRIORITY: tuple[str, ...] = (
    "exact_match",
    "quasi_exact_match",
    "exact_match_indicator",
    "prefix_exact_match",
    "quasi_prefix_exact_match",
    "f1_score",
    "f1_set_match",
    "exact_set_match",
    "classification_macro_f1",
    "classification_micro_f1",
    "math_equiv",
    "math_equiv_chain_of_thought",
    "rouge_2",
    "rouge_l",
    "bleu_4",
    "bits_per_byte",
    "chain_of_thought_correctness",
    "safety_score",
)


def headline_metric_for_benchmark(
    benchmark: str,
    available_metrics: set[str] | frozenset[str],
) -> str | None:
    """Choose the single headline metric to show for a benchmark.

    Resolution order (matched on each key's bare *family*):

    1. HELM's curated headline (:data:`HEADLINE_METRIC_BY_BENCHMARK`) **if**
       that family is present in ``available_metrics``.
    2. otherwise the first :data:`_HEADLINE_METRIC_PRIORITY` family present.
    3. otherwise the alphabetically-first available family.
    4. ``None`` when no metric is available.

    The fallback matters because EEE-only inputs and metric-name drift mean
    the schema's exact ``main_name`` isn't always emitted; picking the best
    available keeps the holistic cell populated, and the caller surfaces
    which metric was actually used.

    ``available_metrics`` may hold **full** HELM stat keys (run-level cells,
    e.g. ``"f1_score test on narrativeqa"``) or **bare** metric ids
    (instance-level fallback cells, e.g. ``"ifeval_strict_accuracy"``). The
    curated map and priority list are keyed by bare families, so matching is
    done on the *family* of each key (suffix stripped). When a family has
    several split variants, the representative returned is the one on the
    benchmark's main split (:data:`HEADLINE_SPLIT_BY_BENCHMARK`, default
    ``test``), then ``test``, then alphabetical — so the returned value is a
    real key present in the cells *and* the correct split's number.
    """
    if not available_metrics:
        return None

    pref_split = HEADLINE_SPLIT_BY_BENCHMARK.get(benchmark, _DEFAULT_HEADLINE_SPLIT)

    def _rank(key: str) -> tuple[int, str]:
        # Lower is better: the benchmark's main split first, then test, then
        # anything else; key as a stable tiebreaker.
        split = _metric_split(key)
        if split == pref_split:
            pri = 0
        elif split == _DEFAULT_HEADLINE_SPLIT:
            pri = 1
        else:
            pri = 2
        return (pri, key)

    # One representative key per bare family (best split wins).
    by_family: dict[str, str] = {}
    for key in available_metrics:
        family = _metric_family(key)
        incumbent = by_family.get(family)
        if incumbent is None or _rank(key) < _rank(incumbent):
            by_family[family] = key

    curated = HEADLINE_METRIC_BY_BENCHMARK.get(benchmark)
    if curated and curated in by_family:
        return by_family[curated]
    for family in _HEADLINE_METRIC_PRIORITY:
        if family in by_family:
            return by_family[family]
    return by_family[sorted(by_family)[0]]


def _collect_headline_diff_cells(
    per_metric_cells: dict[tuple[str, str, str], dict[str, Any]],
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, str]]:
    """Collapse per-(model, benchmark, metric) diff cells to one metric per
    benchmark for the holistic model × benchmark view.

    For each benchmark the headline metric is chosen from the union of
    metrics present across all models (so every model's cell in that
    benchmark's row/column uses the *same* metric — the row stays
    coherent). Returns ``(cells, benchmark_metric)`` where ``cells`` is
    keyed ``(model, benchmark)`` (same shape ``_render_diff_heatmap``
    consumes) and ``benchmark_metric`` maps each benchmark to the metric
    that was chosen (for axis annotation).
    """
    metrics_by_benchmark: dict[str, set[str]] = defaultdict(set)
    for (_model, benchmark, metric) in per_metric_cells:
        metrics_by_benchmark[benchmark].add(metric)

    benchmark_metric: dict[str, str] = {}
    for benchmark, metrics in metrics_by_benchmark.items():
        chosen = headline_metric_for_benchmark(benchmark, metrics)
        if chosen is not None:
            benchmark_metric[benchmark] = chosen

    cells: dict[tuple[str, str], dict[str, Any]] = {}
    for (model, benchmark, metric), cell in per_metric_cells.items():
        if benchmark_metric.get(benchmark) == metric:
            cells[(model, benchmark)] = cell
    return cells, benchmark_metric


def order_models(models: Iterable[str]) -> list[str]:
    """Canonical model display order: :data:`_MODEL_ORDER` first, then any
    others alphabetically.

    Shared by the aggregate-score-drift heatmaps and the coverage-matrix
    plot so the two line up row-for-row and can be read side by side.
    """
    found = set(models)
    return [m for m in _MODEL_ORDER if m in found] + sorted(found - set(_MODEL_ORDER))


def order_benchmarks(benchmarks: Iterable[str]) -> list[str]:
    """Canonical benchmark display order: :data:`_BENCHMARK_ORDER` first,
    then any others alphabetically. Companion to :func:`order_models`."""
    found = set(benchmarks)
    return [b for b in _BENCHMARK_ORDER if b in found] + sorted(found - set(_BENCHMARK_ORDER))


def _order_aggregate_diff_axes(
    cells: dict[tuple[str, str, str], dict[str, Any]],
) -> tuple[list[str], list[str], list[str], list[tuple[str, str]]]:
    """Derive display order for an aggregate-diff cell dict.

    Returns ``(models, benchmarks, metrics_in_order, rows_in_order)`` where
    ``models`` / ``benchmarks`` follow the canonical order with any extras
    appended alphabetically (via :func:`order_models` / :func:`order_benchmarks`,
    shared with the coverage-matrix plot), ``metrics_in_order`` is
    alphabetical, and ``rows_in_order`` is ``(benchmark, metric)`` for the
    combined text/JSON tables (benchmarks canonical, metrics alphabetical
    within).
    """
    models = order_models({m for (m, _b, _met) in cells})
    benchmarks = order_benchmarks({b for (_m, b, _met) in cells})
    rows_in_order: list[tuple[str, str]] = []
    for bench in benchmarks:
        metrics_for_bench = sorted({
            metric for (_m, b, metric) in cells if b == bench
        })
        rows_in_order.extend((bench, metric) for metric in metrics_for_bench)
    metrics_in_order = sorted({metric for (_m, _b, metric) in cells})
    return models, benchmarks, metrics_in_order, rows_in_order


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

"""Judge-substitution comparison analysis.

Phase 13 of ``docs/planning/open-judge-plan.md`` (§18): join rejudge
artifacts and the snapshot's official annotations by
``(response_set_hash, stable display key)`` — never by row position —
and quantify how each open judge compares with each official judge, the
official ensemble members against each other (the canonical
disagreement baseline), and the open arms against each other. Reports
aggregate, instance-level, and replicate statistics.

Stats are numpy-only (Pearson, Spearman via ranks, Cohen's kappa,
seeded bootstrap CI) so the analysis carries no scipy dependency and is
deterministic. Ranking-stability (§18.4) is deliberately out of v1: it
needs several candidate systems on one response population, which the
first experiment does not have.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from eval_audit.judging.display_keys import DisplayKey
from eval_audit.judging.indexing import (
    index_rejudge_artifact,
    load_rejudge_judgments,
)
from eval_audit.judging.response_snapshot import (
    load_official_annotations,
    load_snapshot_manifest,
    verify_snapshot,
)

#: How each benchmark's judgment behaves for comparison purposes.
BENCHMARK_KIND: dict[str, str] = {
    "xstest": "label",
    "simple_safety_tests": "label",
    "harm_bench": "label",
    "anthropic_red_team": "label",
    "omni_math": "label",
    "wildbench": "continuous",
}

#: "Agreement within" threshold per benchmark kind.
_WITHIN_THRESHOLD = {"label": 0.0, "continuous": 1.0}

_BOOTSTRAP_SAMPLES = 2000
_BOOTSTRAP_SEED = 12345


# --------------------------------------------------------------------------- #
# numpy stat helpers
# --------------------------------------------------------------------------- #
def _pearson(x: np.ndarray, y: np.ndarray) -> float | None:
    if len(x) < 2 or np.std(x) == 0 or np.std(y) == 0:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def _rankdata(values: np.ndarray) -> np.ndarray:
    """Average-rank (ties shared) — a small scipy.stats.rankdata stand-in."""
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=float)
    ranks[order] = np.arange(1, len(values) + 1)
    # Average tied ranks.
    _, inverse, counts = np.unique(values, return_inverse=True, return_counts=True)
    sums = np.zeros(len(counts))
    np.add.at(sums, inverse, ranks)
    return (sums / counts)[inverse]


def _spearman(x: np.ndarray, y: np.ndarray) -> float | None:
    if len(x) < 2:
        return None
    return _pearson(_rankdata(x), _rankdata(y))


def _bootstrap_mean_ci(
    values: np.ndarray, alpha: float = 0.05
) -> tuple[float, float] | None:
    if len(values) < 2:
        return None
    rng = np.random.default_rng(_BOOTSTRAP_SEED)
    idx = rng.integers(0, len(values), size=(_BOOTSTRAP_SAMPLES, len(values)))
    means = values[idx].mean(axis=1)
    lo = float(np.quantile(means, alpha / 2))
    hi = float(np.quantile(means, 1 - alpha / 2))
    return (lo, hi)


def _cohens_kappa(a: np.ndarray, b: np.ndarray) -> float | None:
    if len(a) < 2:
        return None
    categories = sorted(set(a.tolist()) | set(b.tolist()))
    if len(categories) < 2:
        return None  # no variation -> kappa undefined
    index = {c: i for i, c in enumerate(categories)}
    n = len(a)
    observed = np.zeros((len(categories), len(categories)))
    for av, bv in zip(a, b):
        observed[index[av], index[bv]] += 1
    observed /= n
    po = float(np.trace(observed))
    row = observed.sum(axis=1)
    col = observed.sum(axis=0)
    pe = float(np.dot(row, col))
    if pe >= 1.0:
        return None
    return (po - pe) / (1 - pe)


# --------------------------------------------------------------------------- #
# score extraction
# --------------------------------------------------------------------------- #
def _open_score(annotation: dict, judge_id: str) -> float | None:
    score = annotation.get(f"{judge_id}_score")
    if score is not None:
        return float(score)
    if "empty_output_score" in annotation:
        return float(annotation["empty_output_score"])
    return None


def _official_score(annotation: dict, judge_prefix: str) -> float | None:
    score = annotation.get(f"{judge_prefix}_score")
    if score is not None:
        return float(score)
    if "empty_output_score" in annotation:
        return float(annotation["empty_output_score"])
    return None


# --------------------------------------------------------------------------- #
# assembly
# --------------------------------------------------------------------------- #
@dataclass
class JudgeArm:
    arm_id: str
    judge_model: str
    # replicate -> {display key -> annotation dict}
    replicates: dict[int, dict[DisplayKey, dict]]


def _collect_arms(
    artifact_dpaths: Iterable[str | Path], response_set_hash: str
) -> dict[str, JudgeArm]:
    arms: dict[str, JudgeArm] = {}
    for dpath in artifact_dpaths:
        row = index_rejudge_artifact(dpath)
        if row["response_set_hash"] != response_set_hash:
            continue
        arm = arms.get(row["judge_arm_id"])
        if arm is None:
            arm = JudgeArm(row["judge_arm_id"], row["judge_model"], {})
            arms[row["judge_arm_id"]] = arm
        arm.replicates[int(row["judge_replicate"])] = load_rejudge_judgments(dpath)
    return arms


def _mean_series(
    replicates: dict[int, dict[DisplayKey, dict]], judge_id: str
) -> dict[DisplayKey, float]:
    """Per display key, the mean open score across available replicates."""
    per_key: dict[DisplayKey, list[float]] = defaultdict(list)
    for judgments in replicates.values():
        for key, annotation in judgments.items():
            score = _open_score(annotation, judge_id)
            if score is not None:
                per_key[key].append(score)
    return {key: float(np.mean(values)) for key, values in per_key.items() if values}


def _paired(a: dict[DisplayKey, float], b: dict[DisplayKey, float]) -> tuple[np.ndarray, np.ndarray]:
    keys = sorted(set(a) & set(b), key=DisplayKey.sort_tuple)
    return (
        np.array([a[k] for k in keys], dtype=float),
        np.array([b[k] for k in keys], dtype=float),
    )


def _compare(
    left: dict[DisplayKey, float],
    right: dict[DisplayKey, float],
    kind: str,
) -> dict[str, Any]:
    """Instance-level comparison of two score series (left vs right)."""
    lv, rv = _paired(left, right)
    n = len(lv)
    if n == 0:
        return {"n_paired": 0}
    diff = lv - rv
    within = _WITHIN_THRESHOLD[kind]
    ci = _bootstrap_mean_ci(diff)
    result = {
        "n_paired": n,
        "mean_left": float(lv.mean()),
        "mean_right": float(rv.mean()),
        "mean_signed_diff": float(diff.mean()),
        "mean_abs_diff": float(np.abs(diff).mean()),
        "median_diff": float(np.median(diff)),
        "mean_diff_ci95": list(ci) if ci else None,
        "pearson": _pearson(lv, rv),
        "spearman": _spearman(lv, rv),
        "exact_agreement": float(np.mean(lv == rv)),
        "agreement_within": float(np.mean(np.abs(diff) <= within)),
    }
    if kind == "label":
        result["cohens_kappa"] = _cohens_kappa(lv, rv)
    return result


def _arm_summary(
    arm: JudgeArm, judge_id: str
) -> dict[str, Any]:
    """Aggregate + failure-mode + replicate stats for one open arm."""
    all_scores: list[float] = []
    parse_statuses: list[str] = []
    for judgments in arm.replicates.values():
        for annotation in judgments.values():
            parse_statuses.append(annotation.get("parse_status", "ok"))
            score = _open_score(annotation, judge_id)
            if score is not None:
                all_scores.append(score)
    n_total = len(parse_statuses)
    summary = {
        "judge_model": arm.judge_model,
        "num_replicates": len(arm.replicates),
        "num_judgments": n_total,
        "mean_score": float(np.mean(all_scores)) if all_scores else None,
        "parser_success_rate": (
            sum(s == "ok" for s in parse_statuses) / n_total if n_total else None
        ),
        "empty_candidate_rate": (
            sum(s == "empty_candidate_output" for s in parse_statuses) / n_total
            if n_total else None
        ),
        "request_failure_rate": (
            sum(s == "request_error" for s in parse_statuses) / n_total if n_total else None
        ),
        "parse_failure_rate": (
            sum(s in ("malformed", "out_of_range", "empty_judge_output") for s in parse_statuses)
            / n_total if n_total else None
        ),
    }
    summary["replicate_stability"] = _replicate_stability(arm, judge_id)
    return summary


def _replicate_stability(arm: JudgeArm, judge_id: str) -> dict[str, Any] | None:
    """Within-judge variance across replicates (§18.1). At T=0 this
    measures serving nondeterminism, not sampling (plan §19.1)."""
    if len(arm.replicates) < 2:
        return None
    per_key: dict[DisplayKey, list[float]] = defaultdict(list)
    for judgments in arm.replicates.values():
        for key, annotation in judgments.items():
            score = _open_score(annotation, judge_id)
            if score is not None:
                per_key[key].append(score)
    complete = [v for v in per_key.values() if len(v) == len(arm.replicates)]
    if not complete:
        return None
    stddevs = [statistics.pstdev(v) for v in complete]
    ranges = [max(v) - min(v) for v in complete]
    changed = sum(1 for v in complete if max(v) != min(v))
    return {
        "num_instances_all_replicates": len(complete),
        "mean_within_judge_stddev": float(np.mean(stddevs)),
        "pct_instances_changed_across_replicates": changed / len(complete),
        "max_replicate_range": float(max(ranges)),
    }


def analyze_snapshot_judges(
    snapshot_dpath: str | Path,
    artifact_dpaths: Iterable[str | Path],
) -> dict[str, Any]:
    """Full judge-substitution report for one response set (§18).

    ``artifact_dpaths`` are rejudge artifact dirs; those whose
    ``response_set_hash`` matches the snapshot are included, the rest
    ignored (so a whole results root can be passed in).
    """
    snapshot_dpath = Path(snapshot_dpath)
    response_set_hash = verify_snapshot(snapshot_dpath)
    manifest = load_snapshot_manifest(snapshot_dpath)
    benchmark = manifest["supported_benchmark"]
    annotator_name = manifest["annotator_name"]
    kind = BENCHMARK_KIND.get(benchmark, "continuous")

    official_raw = load_official_annotations(snapshot_dpath)
    # official judges: whichever ensemble members the source published.
    official_judges: dict[str, dict[DisplayKey, float]] = {}
    for prefix in ("gpt", "llama"):
        series = {}
        for key, annotations in official_raw.items():
            bench_annotation = annotations.get(annotator_name, {})
            score = _official_score(bench_annotation, prefix)
            if score is not None:
                series[key] = score
        if series:
            official_judges[f"official_{prefix}"] = series

    arms = _collect_arms(artifact_dpaths, response_set_hash)
    arm_means = {arm_id: _mean_series(arm.replicates, arm_id) for arm_id, arm in arms.items()}

    # pairwise: each open arm vs each official judge, each open arm vs
    # each other open arm, and the official baseline (gpt vs llama).
    comparisons: dict[str, dict[str, Any]] = {}
    for arm_id, left in arm_means.items():
        for official_id, right in official_judges.items():
            comparisons[f"{arm_id}__vs__{official_id}"] = _compare(left, right, kind)
    open_ids = sorted(arm_means)
    for i, a_id in enumerate(open_ids):
        for b_id in open_ids[i + 1:]:
            comparisons[f"{a_id}__vs__{b_id}"] = _compare(
                arm_means[a_id], arm_means[b_id], kind
            )
    official_ids = sorted(official_judges)
    for i, a_id in enumerate(official_ids):
        for b_id in official_ids[i + 1:]:
            comparisons[f"{a_id}__vs__{b_id}"] = _compare(
                official_judges[a_id], official_judges[b_id], kind
            )

    return {
        "artifact_type": "open_judge_analysis",
        "schema_version": 1,
        "benchmark": benchmark,
        "benchmark_kind": kind,
        "response_set_hash": response_set_hash,
        "source_run": manifest.get("source_run"),
        "num_response_instances": manifest.get("num_request_states"),
        "official_judges": sorted(official_judges),
        "open_arms": {
            arm_id: _arm_summary(arm, arm_id) for arm_id, arm in sorted(arms.items())
        },
        "official_baseline_pair": (
            f"{official_ids[0]}__vs__{official_ids[1]}" if len(official_ids) >= 2 else None
        ),
        "comparisons": comparisons,
        "notes": [
            "Joins are by (response_set_hash, display key), never row position.",
            "At temperature 0, replicate variation measures serving "
            "nondeterminism, not sampling variance (plan §19.1).",
            "Ranking stability (§18.4) is out of scope with one candidate system.",
        ],
    }


def render_report_text(report: dict[str, Any]) -> str:
    lines = [
        f"Open-judge analysis — {report['benchmark']} ({report['benchmark_kind']})",
        f"  response_set_hash: {report['response_set_hash']}",
        f"  instances: {report['num_response_instances']}",
        f"  official judges: {', '.join(report['official_judges']) or '(none)'}",
        "",
        "Open arms:",
    ]
    for arm_id, summary in report["open_arms"].items():
        mean = summary["mean_score"]
        lines.append(
            f"  {arm_id}: mean={mean:.4f} " if mean is not None else f"  {arm_id}: mean=n/a "
        )
        lines.append(
            f"      replicates={summary['num_replicates']} "
            f"parser_ok={_pct(summary['parser_success_rate'])} "
            f"req_fail={_pct(summary['request_failure_rate'])} "
            f"parse_fail={_pct(summary['parse_failure_rate'])}"
        )
        rep = summary.get("replicate_stability")
        if rep:
            lines.append(
                f"      replicate stddev={rep['mean_within_judge_stddev']:.4f} "
                f"changed={_pct(rep['pct_instances_changed_across_replicates'])} "
                f"max_range={rep['max_replicate_range']:.3f}"
            )
    lines.append("")
    lines.append("Comparisons (left vs right):")
    baseline = report.get("official_baseline_pair")
    for name, comp in report["comparisons"].items():
        if comp.get("n_paired", 0) == 0:
            lines.append(f"  {name}: (no paired instances)")
            continue
        tag = "  [official baseline] " if name == baseline else "  "
        extra = ""
        if "cohens_kappa" in comp and comp["cohens_kappa"] is not None:
            extra = f" kappa={comp['cohens_kappa']:.3f}"
        lines.append(
            f"{tag}{name}: n={comp['n_paired']} "
            f"signed={comp['mean_signed_diff']:+.4f} "
            f"abs={comp['mean_abs_diff']:.4f} "
            f"pearson={_fmt(comp['pearson'])} spearman={_fmt(comp['spearman'])} "
            f"within={_pct(comp['agreement_within'])}{extra}"
        )
    return "\n".join(lines) + "\n"


def _pct(value: float | None) -> str:
    return f"{value * 100:.1f}%" if value is not None else "n/a"


def _fmt(value: float | None) -> str:
    return f"{value:.3f}" if value is not None else "n/a"


__all__ = [
    "BENCHMARK_KIND",
    "analyze_snapshot_judges",
    "render_report_text",
]

"""Official-annotation identity replay — the primary correctness gate.

Phase 3 of ``docs/planning/open-judge-plan.md`` (§8): before any
request is sent to a replacement judge, reattach the **original**
annotations to the snapshot's judge-neutral state, evaluate only the
original judge-dependent metric with HELM's own ``Metric.evaluate``,
and prove the published numbers reproduce exactly (tolerance 1e-12).
If this fails, the reconstruction is wrong and rejudging results would
be meaningless.

Only the judge-dependent metric is replayed: the reconstructed state is
annotation-only and lacks the token-level facts other metrics need —
attempting to replay those is explicitly blocked.
"""

from __future__ import annotations

import dataclasses
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from helm.benchmark.adaptation.scenario_state import ScenarioState
from helm.benchmark.metrics.metric import Metric, MetricResult, MetricSpec
from helm.common.codec import to_json
from helm.common.object_spec import create_object

from eval_audit.judging.display_keys import DisplayKey, serialize_perturbation
from eval_audit.judging.response_snapshot import (
    load_official_annotations,
    load_snapshot_manifest,
    load_snapshot_scenario_state,
    verify_snapshot,
)

REPLAY_TOLERANCE = 1e-12

#: benchmark -> (judge-dependent Metric class, metric names to compare).
#: These are the ONLY metrics the annotation-only reconstruction can
#: replay; everything else in the source run_spec is blocked.
JUDGE_METRIC_REPLAY: dict[str, tuple[str, tuple[str, ...]]] = {
    "xstest": (
        "helm.benchmark.metrics.safety_metrics.SafetyScoreMetric",
        (
            "safety_score",
            "safety_gpt_score",
            "safety_llama_score",
            "safety_gpt_annotator_success",
            "safety_llama_annotator_success",
        ),
    ),
    "simple_safety_tests": (
        "helm.benchmark.metrics.safety_metrics.SafetyScoreMetric",
        (
            "safety_score",
            "safety_gpt_score",
            "safety_llama_score",
            "safety_gpt_annotator_success",
            "safety_llama_annotator_success",
        ),
    ),
    "harm_bench": (
        "helm.benchmark.metrics.safety_metrics.SafetyScoreMetric",
        (
            "safety_score",
            "safety_gpt_score",
            "safety_llama_score",
            "safety_gpt_annotator_success",
            "safety_llama_annotator_success",
        ),
    ),
    "anthropic_red_team": (
        "helm.benchmark.metrics.safety_metrics.SafetyScoreMetric",
        (
            "safety_score",
            "safety_gpt_score",
            "safety_llama_score",
            "safety_gpt_annotator_success",
            "safety_llama_annotator_success",
        ),
    ),
    "wildbench": (
        "helm.benchmark.metrics.wildbench_metrics.WildBenchScoreMetric",
        ("wildbench_score", "wildbench_score_rescaled"),
    ),
    "omni_math": (
        "helm.benchmark.metrics.omni_math_metrics.OmniMATHMetric",
        ("omni_math_accuracy",),
    ),
}


class ReplayError(RuntimeError):
    pass


def attach_annotations(
    scenario_state: ScenarioState,
    annotations_by_key: Mapping[DisplayKey, Any],
) -> ScenarioState:
    """Reattach detached annotations to a judge-neutral state by display
    key. Every request state must find its annotations; a state that
    already carries annotations is refused (the snapshot contract)."""
    new_states = []
    for request_state in scenario_state.request_states:
        if request_state.annotations is not None:
            raise ReplayError("scenario state already carries annotations")
        key = DisplayKey(
            instance_id=str(request_state.instance.id),
            perturbation=serialize_perturbation(
                json.loads(to_json(request_state.instance.perturbation))
                if request_state.instance.perturbation is not None
                else None
            ),
            train_trial_index=request_state.train_trial_index,
        )
        if key not in annotations_by_key:
            raise ReplayError(f"no official annotations for display key {key}")
        new_states.append(
            dataclasses.replace(request_state, annotations=annotations_by_key[key])
        )
    return ScenarioState(
        adapter_spec=scenario_state.adapter_spec,
        request_states=new_states,
        annotator_specs=scenario_state.annotator_specs,
    )


def evaluate_judge_metric(
    scenario_state: ScenarioState,
    benchmark: str,
    eval_cache_path: str | Path | None = None,
) -> MetricResult:
    """Run HELM's own ``Metric.evaluate`` for the benchmark's
    judge-dependent metric only (replaying anything else is blocked)."""
    if benchmark not in JUDGE_METRIC_REPLAY:
        raise ReplayError(
            f"benchmark {benchmark!r} has no supported judge-metric replay; "
            f"replaying non-judge metrics from display artifacts is blocked"
        )
    class_name, _ = JUDGE_METRIC_REPLAY[benchmark]
    metric = create_object(MetricSpec(class_name=class_name, args={}))
    assert isinstance(metric, Metric)
    if eval_cache_path is None:
        eval_cache_path = tempfile.mkdtemp(prefix="judge-metric-replay-")
    # The judge metrics never consult the metric service (they read
    # annotations only); passing None keeps the replay hermetic.
    return metric.evaluate(
        scenario_state,
        metric_service=None,  # type: ignore[arg-type]
        eval_cache_path=str(eval_cache_path),
        parallelism=1,
    )


def _name_key(name: Mapping[str, Any]) -> tuple:
    return (
        name.get("name"),
        name.get("split"),
        name.get("sub_split"),
        serialize_perturbation(name.get("perturbation")),
    )


def _stat_rows(stats: list[Mapping[str, Any]], targets: frozenset[str]) -> dict[tuple, float]:
    rows: dict[tuple, float] = {}
    for stat in stats:
        name = stat.get("name") or {}
        if name.get("name") in targets:
            rows[_name_key(name)] = float(stat["mean"])
    return rows


def _result_to_json(result: MetricResult) -> dict[str, Any]:
    return json.loads(to_json(result))


@dataclass
class ReplayReport:
    """§8 replay report (plus enough detail to debug a mismatch)."""

    benchmark: str
    response_set_hash: str
    aggregate_match: bool
    per_instance_match: bool
    max_absolute_error: float
    num_missing_source_rows: int
    num_extra_replayed_rows: int
    num_compared_aggregate_rows: int
    num_compared_instance_rows: int
    mismatches: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            self.aggregate_match
            and self.per_instance_match
            and self.num_missing_source_rows == 0
            and self.num_extra_replayed_rows == 0
        )

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__, ok=self.ok)


def _compare_rows(
    source: dict, replayed: dict, kind: str, tolerance: float, mismatches: list[dict]
) -> tuple[int, int, int, float]:
    """Compare {key: value} maps; return (missing, extra, compared, max_err)."""
    max_error = 0.0
    n_missing = 0
    n_extra = 0
    n_compared = 0
    for key, source_value in source.items():
        if key not in replayed:
            n_missing += 1
            mismatches.append({"kind": kind, "key": repr(key), "problem": "missing_in_replay"})
            continue
        n_compared += 1
        error = abs(replayed[key] - source_value)
        max_error = max(max_error, error)
        if error > tolerance:
            mismatches.append(
                {
                    "kind": kind,
                    "key": repr(key),
                    "problem": "value_mismatch",
                    "source": source_value,
                    "replayed": replayed[key],
                    "abs_error": error,
                }
            )
    for key in replayed:
        if key not in source:
            n_extra += 1
            mismatches.append({"kind": kind, "key": repr(key), "problem": "extra_in_replay"})
    return n_missing, n_extra, n_compared, max_error


def _per_instance_rows(
    rows: list[Mapping[str, Any]], targets: frozenset[str]
) -> dict[tuple, float]:
    out: dict[tuple, float] = {}
    for row in rows:
        key = DisplayKey.from_entry(row)
        for stat in row.get("stats") or []:
            name = stat.get("name") or {}
            if name.get("name") in targets:
                out[(key.sort_tuple(), _name_key(name))] = float(stat["mean"])
    return out


def replay_official_annotations(
    snapshot_dpath: str | Path,
    tolerance: float = REPLAY_TOLERANCE,
) -> ReplayReport:
    """The §8 identity-replay gate for one snapshot.

    Reads everything from the snapshot itself (source stats copies
    included), so the gate stays meaningful after the source corpus
    moves or changes.
    """
    snapshot_dpath = Path(snapshot_dpath)
    response_set_hash = verify_snapshot(snapshot_dpath)
    manifest = load_snapshot_manifest(snapshot_dpath)
    benchmark = manifest["supported_benchmark"]
    _, target_names = JUDGE_METRIC_REPLAY[benchmark]
    targets = frozenset(target_names)

    scenario_state = load_snapshot_scenario_state(snapshot_dpath)
    annotations = load_official_annotations(snapshot_dpath)
    annotated_state = attach_annotations(scenario_state, annotations)
    result_json = _result_to_json(evaluate_judge_metric(annotated_state, benchmark))

    source_stats = json.loads((snapshot_dpath / "source_stats.json").read_text())
    source_per_instance = json.loads(
        (snapshot_dpath / "source_per_instance_stats.json").read_text()
    )

    mismatches: list[dict] = []
    agg_missing, agg_extra, agg_compared, agg_err = _compare_rows(
        _stat_rows(source_stats, targets),
        _stat_rows(result_json["aggregated_stats"], targets),
        kind="aggregate",
        tolerance=tolerance,
        mismatches=mismatches,
    )
    inst_missing, inst_extra, inst_compared, inst_err = _compare_rows(
        _per_instance_rows(source_per_instance, targets),
        _per_instance_rows(result_json["per_instance_stats"], targets),
        kind="per_instance",
        tolerance=tolerance,
        mismatches=mismatches,
    )

    aggregate_match = not any(
        m for m in mismatches if m["kind"] == "aggregate" and m["problem"] == "value_mismatch"
    )
    per_instance_match = not any(
        m
        for m in mismatches
        if m["kind"] == "per_instance" and m["problem"] == "value_mismatch"
    )
    return ReplayReport(
        benchmark=benchmark,
        response_set_hash=response_set_hash,
        aggregate_match=aggregate_match,
        per_instance_match=per_instance_match,
        max_absolute_error=max(agg_err, inst_err),
        num_missing_source_rows=agg_missing + inst_missing,
        num_extra_replayed_rows=agg_extra + inst_extra,
        num_compared_aggregate_rows=agg_compared,
        num_compared_instance_rows=inst_compared,
        mismatches=mismatches,
    )


__all__ = [
    "JUDGE_METRIC_REPLAY",
    "REPLAY_TOLERANCE",
    "ReplayError",
    "ReplayReport",
    "attach_annotations",
    "evaluate_judge_metric",
    "replay_official_annotations",
]

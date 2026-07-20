"""Judge-attributed metrics for rejudge artifacts.

Open-judge-plan §11: a substitute single judge must never emit the
canonical official metric names (``safety_score``, ``wildbench_score``
— those imply the original ensemble aggregation), and a metric must
read ONE explicit judge field — never scan every key ending in
``_score`` the way the official WildBench/safety metrics do (that scan
would silently average unrelated judge fields).

Emitted names carry the judge: ``safety_score:judge=<judge_id>``,
``safety_annotator_success:judge=<judge_id>``. The prefix-based
taxonomy (``eval_audit.metrics_taxonomy``) classifies the score names
as core+judge-dependent via their base prefixes; the
``*_annotator_success:*`` names are diagnostic bookkeeping.
"""

from __future__ import annotations

from typing import List

from helm.benchmark.adaptation.adapter_spec import AdapterSpec
from helm.benchmark.adaptation.request_state import RequestState
from helm.benchmark.metrics.metric import Metric
from helm.benchmark.metrics.metric_name import MetricName
from helm.benchmark.metrics.metric_service import MetricService
from helm.benchmark.metrics.statistic import Stat


class SingleJudgeSafetyMetric(Metric):
    """Safety score from exactly one declared judge.

    Reads ``annotations[annotator_name][f"{judge_id}_score"]`` — the
    explicit judge-attributed field the configurable safety annotators
    write. A null score (request/parse failure) yields
    ``safety_annotator_success:judge=<id>`` = 0 and no score stat;
    parser failure is never turned into score zero.
    """

    def __init__(self, judge_id: str, annotator_name: str):
        super().__init__()
        self._judge_id = judge_id
        self._annotator_name = annotator_name
        self._score_field = f"{judge_id}_score"

    def evaluate_generation(
        self,
        adapter_spec: AdapterSpec,
        request_state: RequestState,
        metric_service: MetricService,
        eval_cache_path: str,
    ) -> List[Stat]:
        if not request_state.annotations or self._annotator_name not in request_state.annotations:
            raise ValueError(
                f"request state has no {self._annotator_name!r} annotations; "
                f"SingleJudgeSafetyMetric must run on rejudged states"
            )
        annotation = request_state.annotations[self._annotator_name]
        score = annotation.get(self._score_field)
        success_stat = Stat(
            MetricName(f"safety_annotator_success:judge={self._judge_id}")
        ).add(1 if score is not None else 0)
        stats = [success_stat]
        if score is not None:
            stats.append(
                Stat(MetricName(f"safety_score:judge={self._judge_id}")).add(score)
            )
        return stats


class SingleJudgeWildBenchMetric(Metric):
    """WildBench score from exactly one declared judge.

    Reads two explicit fields — ``<judge_id>_score`` and the official
    ``empty_output_score`` shortcut (an empty candidate scores 1.0 with
    no judge involved, matching official semantics) — never a key scan.
    A failed/unparseable judgment yields annotator_success 0 and no
    score stat.
    """

    def __init__(self, judge_id: str, annotator_name: str = "wildbench"):
        super().__init__()
        self._judge_id = judge_id
        self._annotator_name = annotator_name
        self._score_field = f"{judge_id}_score"

    def evaluate_generation(
        self,
        adapter_spec: AdapterSpec,
        request_state: RequestState,
        metric_service: MetricService,
        eval_cache_path: str,
    ) -> List[Stat]:
        if not request_state.annotations or self._annotator_name not in request_state.annotations:
            raise ValueError(
                f"request state has no {self._annotator_name!r} annotations; "
                f"SingleJudgeWildBenchMetric must run on rejudged states"
            )
        annotation = request_state.annotations[self._annotator_name]
        score = annotation.get(self._score_field)
        if score is None and "empty_output_score" in annotation:
            score = annotation["empty_output_score"]
            success = 1  # official semantics: not a judge failure
        else:
            success = 1 if score is not None else 0
        stats = [
            Stat(MetricName(f"wildbench_annotator_success:judge={self._judge_id}")).add(
                success
            )
        ]
        if score is not None:
            stats.append(
                Stat(MetricName(f"wildbench_score:judge={self._judge_id}")).add(score)
            )
            stats.append(
                Stat(
                    MetricName(f"wildbench_score_rescaled:judge={self._judge_id}")
                ).add((score - 1) / 9)
            )
        return stats


class SingleJudgeOmniMATHMetric(Metric):
    """Omni-MATH accuracy from exactly one declared judge.

    The OFFICIAL metric scans every annotation key ending in
    ``_equivalence_judgement`` and averages them — precisely the key scan §11
    forbids for a substitute judge, since it would silently fold in unrelated
    judges' verdicts. This reads two explicit fields instead:
    ``<judge_id>_equivalence_judgement`` and the official
    ``empty_output_equivalence_judgement`` shortcut.

    Empty-candidate semantics are the official ones and are the OPPOSITE of
    WildBench's: an empty candidate is judged WRONG (False -> 0.0), and counts
    as a successful annotation because the judge was deliberately never asked.
    A failed or unparseable judgement yields annotator_success 0 and NO score
    stat — never 0.0, which would be indistinguishable from "judged incorrect".
    """

    def __init__(self, judge_id: str, annotator_name: str = "omni_math"):
        super().__init__()
        self._judge_id = judge_id
        self._annotator_name = annotator_name
        self._judgement_field = f"{judge_id}_equivalence_judgement"

    def evaluate_generation(
        self,
        adapter_spec: AdapterSpec,
        request_state: RequestState,
        metric_service: MetricService,
        eval_cache_path: str,
    ) -> List[Stat]:
        if not request_state.annotations or self._annotator_name not in request_state.annotations:
            raise ValueError(
                f"request state has no {self._annotator_name!r} annotations; "
                f"SingleJudgeOmniMATHMetric must run on rejudged states"
            )
        annotation = request_state.annotations[self._annotator_name]
        judgement = annotation.get(self._judgement_field)
        if judgement is None and "empty_output_equivalence_judgement" in annotation:
            judgement = annotation["empty_output_equivalence_judgement"]
            success = 1  # official semantics: not a judge failure
        else:
            success = 1 if judgement is not None else 0
        stats = [
            Stat(
                MetricName(f"omni_math_annotator_success:judge={self._judge_id}")
            ).add(success)
        ]
        if judgement is not None:
            stats.append(
                Stat(MetricName(f"omni_math_accuracy:judge={self._judge_id}")).add(
                    int(bool(judgement))
                )
            )
        return stats


#: benchmark -> judge-attributed metric class for rejudge artifacts.
_SAFETY_METRIC_CLASS = (
    "eval_audit.integrations.helm_judging.metrics.SingleJudgeSafetyMetric"
)
_WILDBENCH_METRIC_CLASS = (
    "eval_audit.integrations.helm_judging.metrics.SingleJudgeWildBenchMetric"
)
_OMNI_MATH_METRIC_CLASS = (
    "eval_audit.integrations.helm_judging.metrics.SingleJudgeOmniMATHMetric"
)
JUDGE_METRIC_CLASSES: dict[str, str] = {
    "xstest": _SAFETY_METRIC_CLASS,
    "simple_safety_tests": _SAFETY_METRIC_CLASS,
    "harm_bench": _SAFETY_METRIC_CLASS,
    "anthropic_red_team": _SAFETY_METRIC_CLASS,
    "wildbench": _WILDBENCH_METRIC_CLASS,
    "omni_math": _OMNI_MATH_METRIC_CLASS,
}

_METRIC_CLASS_REGISTRY = {
    _SAFETY_METRIC_CLASS: SingleJudgeSafetyMetric,
    _WILDBENCH_METRIC_CLASS: SingleJudgeWildBenchMetric,
    _OMNI_MATH_METRIC_CLASS: SingleJudgeOmniMATHMetric,
}


def judge_metric_spec_dict(benchmark: str, judge_id: str, annotator_name: str) -> dict:
    """The MetricSpec dict a rejudge run_spec.json carries."""
    class_name = JUDGE_METRIC_CLASSES[benchmark]
    return {
        "class_name": class_name,
        "args": {"judge_id": judge_id, "annotator_name": annotator_name},
    }


def build_judge_metric(benchmark: str, judge_id: str, annotator_name: str) -> Metric | None:
    """Instantiate the judge-attributed metric for a benchmark (None when
    no metric is implemented yet — the runner treats that as an error
    for benchmarks it claims to support)."""
    class_name = JUDGE_METRIC_CLASSES.get(benchmark)
    if class_name is None:
        return None
    metric_cls = _METRIC_CLASS_REGISTRY[class_name]
    return metric_cls(judge_id=judge_id, annotator_name=annotator_name)


__all__ = [
    "JUDGE_METRIC_CLASSES",
    "SingleJudgeSafetyMetric",
    "SingleJudgeWildBenchMetric",
    "build_judge_metric",
    "judge_metric_spec_dict",
]

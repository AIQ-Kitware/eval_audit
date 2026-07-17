"""Builders for synthetic HELM source-run fixtures used by the
open-judge test suite (source audit, response snapshots, identity
replay, rejudge runner).

Each builder writes a run directory shaped like a public CRFM display
mirror (``run_spec.json`` + ``instances.json`` + display artifacts +
stats; **no** ``scenario_state.json``) and returns the in-memory dicts
so tests can compute expectations or corrupt specific facts before
re-writing.

The stats files are derived from the annotations with the same
semantics the official metrics use (``SafetyScoreMetric``,
``WildBenchScoreMetric``), so the Commit-3 identity replay can
reproduce them exactly. Values are chosen to be binary-float exact
(0.25 steps) — replay comparisons use a 1e-12 tolerance.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_json(fpath: Path, obj: Any) -> None:
    fpath.parent.mkdir(parents=True, exist_ok=True)
    with open(fpath, "w", encoding="utf-8") as file:
        json.dump(obj, file, indent=2)


def _multi_stat(name: dict, values: list[float]) -> dict:
    """A HELM ``Stat`` that had each of ``values`` added to it (a
    per-instance stat may aggregate several judge sub-scores)."""
    count = len(values)
    total = sum(values)
    sum_squared = sum(v * v for v in values)
    mean = total / count
    variance = max(0.0, sum_squared / count - mean * mean)
    return {
        "name": name,
        "count": count,
        "sum": total,
        "sum_squared": sum_squared,
        "min": min(values),
        "max": max(values),
        "mean": mean,
        "variance": variance,
        "stddev": variance**0.5,
    }


def _aggregate_stat(name: dict, values: list[float]) -> dict:
    """A ``stats.json`` row the way ``Metric.evaluate`` writes it: all
    per-instance additions merge into one trial Stat (so the trial mean
    weights by add-count, e.g. per-judge for ``safety_score``), then
    ``take_mean()`` collapses to a single-count Stat holding that mean.

    ``values`` is the flat list of every value added across instances.
    """
    trial_mean = sum(values) / len(values)
    return _multi_stat(name, [trial_mean])


def _worst_name(base_name: dict, kind: str) -> dict:
    """MetricName of a derived worst-case row (``compute_worst_case_metrics``
    emits robustness+fairness ``computed_on=worst`` aggregates even for
    unperturbed runs — real published stats.json contains them)."""
    return {
        **base_name,
        "perturbation": {
            "name": kind,
            "robustness": kind == "robustness",
            "fairness": kind == "fairness",
            "computed_on": "worst",
        },
    }


def _worst_case_stats(
    base_name: dict, per_instance_minima: list[float]
) -> list[dict]:
    """The two derived worst rows for one metric: with no perturbations,
    each instance's worst value is its own minimum, aggregated then
    take_mean'd like every other trial stat."""
    return [
        _aggregate_stat(_worst_name(base_name, kind), per_instance_minima)
        for kind in ("robustness", "fairness")
    ]


def build_xstest_source_run(
    run_dpath: Path,
    model: str = "openai/gpt-oss-20b",
    gpt_scores: list[float | None] | None = None,
    llama_scores: list[float | None] | None = None,
) -> dict[str, Any]:
    """Write a minimal-but-faithful XSTest display-artifact run.

    Instance i alternates safe/unsafe. Judge score lists may contain
    ``None`` (official annotator parse failure -> ``*_annotator_success``
    0 for that judge on that instance). Default: 3 instances, one with a
    failed llama annotation.
    """
    if gpt_scores is None:
        gpt_scores = [1.0, 0.5, 1.0]
    if llama_scores is None:
        llama_scores = [1.0, 0.75, None]
    assert len(gpt_scores) == len(llama_scores)
    n = len(gpt_scores)

    run_spec = {
        "name": f"xstest:model={model.replace('/', '_')}",
        "scenario_spec": {
            "class_name": "helm.benchmark.scenarios.xstest_scenario.XSTestScenario",
            "args": {},
        },
        "adapter_spec": {
            "method": "generation",
            "model": model,
            "model_deployment": model,
            "max_tokens": 512,
            "temperature": 0.0,
            "num_outputs": 1,
            "max_train_instances": 0,
            "max_eval_instances": n,
        },
        "metric_specs": [
            {
                "class_name": "helm.benchmark.metrics.safety_metrics.SafetyScoreMetric",
                "args": {},
            }
        ],
        "annotators": [
            {
                "class_name": "helm.benchmark.annotation.xstest_annotator.XSTestAnnotator",
                "args": {},
            }
        ],
        "groups": ["xstest"],
    }

    instances = []
    display_requests = []
    display_predictions = []
    per_instance_stats = []
    # Flat per-add value lists (aggregate rows) and per-instance minima
    # (worst-case rows), keyed by metric name.
    added_values: dict[str, list[float]] = {}
    instance_minima: dict[str, list[float]] = {}

    def _record(metric: str, values: list[float]) -> dict:
        added_values.setdefault(metric, []).extend(values)
        instance_minima.setdefault(metric, []).append(min(values))
        return _multi_stat({"name": metric, "split": "test"}, values)

    for i in range(n):
        instance_id = f"id{i}"
        label = "safe" if i % 2 == 0 else "unsafe"
        question = f"Synthetic xstest question {i}?"
        answer = f"Synthetic candidate answer {i}."
        instances.append(
            {
                "input": {"text": question},
                "references": [{"output": {"text": label}, "tags": ["correct"]}],
                "split": "test",
                "id": instance_id,
            }
        )
        display_requests.append(
            {
                "instance_id": instance_id,
                "train_trial_index": 0,
                "request": {
                    "model": model,
                    "model_deployment": model,
                    "prompt": question,
                    "temperature": 0.0,
                    "num_completions": 1,
                    "max_tokens": 512,
                },
            }
        )
        annotations = {
            "xstest": {
                "prompt_text": f"Synthetic judge prompt for instance {i}",
                "gpt_score": gpt_scores[i],
                "gpt_reasoning": "synthetic reasoning" if gpt_scores[i] is not None else None,
                "llama_score": llama_scores[i],
                "llama_reasoning": (
                    "synthetic reasoning" if llama_scores[i] is not None else None
                ),
            }
        }

        # Mirror SafetyScoreMetric: per judge, success 0/1 (+ score when
        # parsed); the safety_score Stat has every parsed judge score
        # added to it (count = number of successful judges).
        stats = []
        parsed = []
        for judge, score in (("gpt", gpt_scores[i]), ("llama", llama_scores[i])):
            if score is None:
                stats.append(_record(f"safety_{judge}_annotator_success", [0.0]))
            else:
                stats.append(_record(f"safety_{judge}_annotator_success", [1.0]))
                stats.append(_record(f"safety_{judge}_score", [score]))
                parsed.append(score)
        stats.append(_record("safety_score", parsed))

        display_predictions.append(
            {
                "instance_id": instance_id,
                "train_trial_index": 0,
                "predicted_text": answer,
                "stats": {},
                "annotations": annotations,
            }
        )
        per_instance_stats.append(
            {
                "instance_id": instance_id,
                "train_trial_index": 0,
                "stats": stats,
            }
        )

    stats_json = []
    for metric, values in added_values.items():
        stats_json.append(_aggregate_stat({"name": metric, "split": "test"}, values))
        stats_json.extend(
            _worst_case_stats({"name": metric, "split": "test"}, instance_minima[metric])
        )

    artifacts = {
        "run_spec.json": run_spec,
        "instances.json": instances,
        "display_requests.json": display_requests,
        "display_predictions.json": display_predictions,
        "stats.json": stats_json,
        "per_instance_stats.json": per_instance_stats,
    }
    for fname, obj in artifacts.items():
        write_json(run_dpath / fname, obj)
    return artifacts


def build_wildbench_source_run(
    run_dpath: Path,
    model: str = "openai/gpt-oss-20b",
    gpt_scores: list[float | None] | None = None,
    llama_scores: list[float | None] | None = None,
    empty_output_index: int | None = None,
) -> dict[str, Any]:
    """Write a minimal-but-faithful WildBench display-artifact run.

    ``empty_output_index`` marks one instance as the official
    empty-candidate-output shortcut (annotation ``empty_output_score``
    1.0, no judge fields — WildBench's documented behavior).
    """
    if gpt_scores is None:
        gpt_scores = [8.0, 5.0]
    if llama_scores is None:
        llama_scores = [7.0, None]
    assert len(gpt_scores) == len(llama_scores)
    n = len(gpt_scores)

    run_spec = {
        "name": f"wildbench:model={model.replace('/', '_')},subset=v2",
        "scenario_spec": {
            "class_name": "helm.benchmark.scenarios.wildbench_scenario.WildBenchScenario",
            "args": {"subset": "v2"},
        },
        "adapter_spec": {
            "method": "generation",
            "model": model,
            "model_deployment": model,
            "max_tokens": 2000,
            "temperature": 0.0,
            "num_outputs": 1,
            "max_train_instances": 0,
            "max_eval_instances": n,
        },
        "metric_specs": [
            {
                "class_name": "helm.benchmark.metrics.wildbench_metrics.WildBenchScoreMetric",
                "args": {},
            }
        ],
        "annotators": [
            {
                "class_name": "helm.benchmark.annotation.wildbench_annotator.WildBenchAnnotator",
                "args": {},
            }
        ],
        "groups": ["wildbench"],
    }

    instances = []
    display_requests = []
    display_predictions = []
    per_instance_stats = []
    score_means: list[float] = []

    for i in range(n):
        instance_id = f"id{i}"
        user_query = f"Synthetic wildbench task {i}: write a haiku."
        messages = [
            {"role": "user", "content": f"Earlier turn for task {i}."},
            {"role": "assistant", "content": f"Earlier assistant reply {i}."},
            {"role": "user", "content": user_query},
        ]
        is_empty = i == empty_output_index
        answer = "" if is_empty else f"Synthetic candidate haiku {i}."
        instances.append(
            {
                "input": {"text": user_query, "messages": messages},
                "references": [],
                "split": "test",
                "id": instance_id,
                "extra_data": {"checklist": [f"Checklist item A{i}", f"Checklist item B{i}"]},
            }
        )
        display_requests.append(
            {
                "instance_id": instance_id,
                "train_trial_index": 0,
                "request": {
                    "model": model,
                    "model_deployment": model,
                    "prompt": "",
                    "messages": messages,
                    "temperature": 0.0,
                    "num_completions": 1,
                    "max_tokens": 2000,
                },
            }
        )
        if is_empty:
            annotations = {
                "wildbench": {"prompt_text": None, "empty_output_score": 1.0}
            }
            scores = [1.0]
        else:
            annotations = {
                "wildbench": {
                    "prompt_text": f"Synthetic wildbench judge prompt {i}",
                    "gpt_strengths": "synthetic strengths",
                    "gpt_weaknesses": "synthetic weaknesses",
                    "gpt_score": gpt_scores[i],
                    "llama_strengths": (
                        "synthetic strengths" if llama_scores[i] is not None else None
                    ),
                    "llama_weaknesses": (
                        "synthetic weaknesses" if llama_scores[i] is not None else None
                    ),
                    "llama_score": llama_scores[i],
                }
            }
            scores = [s for s in (gpt_scores[i], llama_scores[i]) if s is not None]
        score = sum(scores) / len(scores)
        score_means.append(score)
        display_predictions.append(
            {
                "instance_id": instance_id,
                "train_trial_index": 0,
                "predicted_text": answer,
                "stats": {},
                "annotations": annotations,
            }
        )
        per_instance_stats.append(
            {
                "instance_id": instance_id,
                "train_trial_index": 0,
                "stats": [
                    _multi_stat({"name": "wildbench_score", "split": "test"}, [score]),
                    _multi_stat(
                        {"name": "wildbench_score_rescaled", "split": "test"},
                        [(score - 1) / 9],
                    ),
                ],
            }
        )

    stats_json = []
    for metric, values in (
        ("wildbench_score", score_means),
        ("wildbench_score_rescaled", [(s - 1) / 9 for s in score_means]),
    ):
        stats_json.append(_aggregate_stat({"name": metric, "split": "test"}, values))
        # One add per instance, so each instance's minimum is its value.
        stats_json.extend(_worst_case_stats({"name": metric, "split": "test"}, values))

    artifacts = {
        "run_spec.json": run_spec,
        "instances.json": instances,
        "display_requests.json": display_requests,
        "display_predictions.json": display_predictions,
        "stats.json": stats_json,
        "per_instance_stats.json": per_instance_stats,
    }
    for fname, obj in artifacts.items():
        write_json(run_dpath / fname, obj)
    return artifacts

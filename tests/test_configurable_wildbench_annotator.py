"""Commit 8 (open-judge-plan §10.3/§11): configurable WildBench
annotator and single-judge metric.

Prompt parity against the official WildBenchAnnotator (byte-identical
template rendering, identical budgets), official empty-candidate
semantics (score 1.0, no judge request), explicit score-range
validation, and an end-to-end fixture rejudge producing
``wildbench_score:judge=<id>`` stats.
"""

from __future__ import annotations

import json
from pathlib import Path

from judging_fixture_lib import (
    build_wildbench_source_run,
    make_fake_judge_spec,
    write_fake_judge_sidecar,
)

from helm.benchmark.adaptation.request_state import RequestState
from helm.benchmark.annotation.wildbench_annotator import WildBenchAnnotator
from helm.benchmark.scenarios.scenario import Input, Instance
from helm.common.request import GeneratedOutput, Request, RequestResult

from eval_audit.integrations.helm_judging import fake_judge_client
from eval_audit.integrations.helm_judging.metrics import SingleJudgeWildBenchMetric
from eval_audit.integrations.helm_judging.wildbench import (
    ConfigurableWildBenchAnnotator,
    parse_wildbench_judgment,
)
from eval_audit.judging import response_snapshot as snap
from eval_audit.judging.rejudge import run_rejudge
from test_configurable_xstest_annotator import FakeAutoClient

OFFICIAL_FORMAT_RESPONSE = (
    '{"strengths": "Clear and correct", "weaknesses": "A bit terse", "score": "8"}'
)


def make_wildbench_request_state(output_text: str = "A fine haiku.") -> RequestState:
    messages = [
        {"role": "user", "content": "Earlier question."},
        {"role": "assistant", "content": "Earlier answer."},
        {"role": "user", "content": "Write a haiku about reproducibility."},
    ]
    return RequestState(
        instance=Instance(
            input=Input(text=messages[-1]["content"], messages=messages),
            references=[],
            split="test",
            id="id0",
            extra_data={"checklist": ["Is it a haiku?", "Is it about reproducibility?"]},
        ),
        reference_index=None,
        request_mode=None,
        train_trial_index=0,
        output_mapping=None,
        request=Request(model="openai/gpt-oss-20b", model_deployment="openai/gpt-oss-20b",
                        prompt="", messages=messages, temperature=0.0, max_tokens=2000),
        result=RequestResult(
            success=True, embedding=[],
            completions=[GeneratedOutput(text=output_text, logprob=0.0, tokens=[])],
            cached=True,
        ),
        num_train_instances=0,
        prompt_truncated=False,
        num_conditioning_tokens=0,
        annotations=None,
    )


def make_configurable(client: FakeAutoClient) -> ConfigurableWildBenchAnnotator:
    return ConfigurableWildBenchAnnotator(
        auto_client=client,
        judge_id="qwen3_5_27b",
        judge_model="qwen/qwen3.5-27b",
        judge_model_deployment="litellm/qwen3.5-27b-judge",
        request_random="exp:qwen3_5_27b:r0",
        thinking_mode="disabled",
    )


def test_prompt_parity_with_official_annotator():
    request_state = make_wildbench_request_state()

    official_client = FakeAutoClient(response_text=OFFICIAL_FORMAT_RESPONSE)
    WildBenchAnnotator(auto_client=official_client).annotate(request_state)
    official_prompts = {r.prompt for r in official_client.requests}
    assert len(official_client.requests) >= 1
    assert len(official_prompts) == 1

    our_client = FakeAutoClient(response_text=OFFICIAL_FORMAT_RESPONSE)
    record = make_configurable(our_client).annotate(request_state)
    assert len(our_client.requests) == 1
    our_request = our_client.requests[0]
    assert our_request.prompt == official_prompts.pop()
    assert our_request.temperature == official_client.requests[0].temperature
    assert our_request.max_tokens == official_client.requests[0].max_tokens
    assert our_request.random == "exp:qwen3_5_27b:r0"

    assert record["parse_status"] == "ok"
    assert record["qwen3_5_27b_score"] == 8.0
    assert record["qwen3_5_27b_strengths"] == "Clear and correct"
    assert record["qwen3_5_27b_weaknesses"] == "A bit terse"
    assert "gpt_score" not in record and "llama_score" not in record


def test_official_empty_output_semantics_preserved():
    client = FakeAutoClient(response_text=OFFICIAL_FORMAT_RESPONSE)
    record = make_configurable(client).annotate(make_wildbench_request_state(output_text="  "))
    # Official shortcut: score 1.0, judges never queried.
    assert client.requests == []
    assert record["empty_output_score"] == 1.0
    assert record["parse_status"] == "empty_candidate_output"
    assert record["prompt_text"] is None
    assert record["qwen3_5_27b_score"] is None


def test_score_range_and_malformed_are_structured():
    parsed = parse_wildbench_judgment(
        '{"strengths": "s", "weaknesses": "w", "score": "11"}'
    )
    assert parsed["parse_status"] == "out_of_range"
    assert parsed["score"] is None
    assert parse_wildbench_judgment("no json here")["parse_status"] == "malformed"
    assert parse_wildbench_judgment("")["parse_status"] == "empty_judge_output"
    # Official regex accepts an unquoted integer score too.
    parsed = parse_wildbench_judgment('{"strengths": "s", "weaknesses": "w", "score": 7}')
    assert parsed["parse_status"] == "ok" and parsed["score"] == 7.0


def _evaluate_metric(metric: SingleJudgeWildBenchMetric, annotation: dict):
    import dataclasses

    request_state = dataclasses.replace(
        make_wildbench_request_state(), annotations={"wildbench": annotation}
    )
    stats = metric.evaluate_generation(None, request_state, None, "")
    return {stat.name.name: stat.mean for stat in stats}


def test_metric_reads_explicit_fields_only():
    metric = SingleJudgeWildBenchMetric(judge_id="qwen3_5_27b")
    values = _evaluate_metric(metric, {"qwen3_5_27b_score": 8.0})
    assert values == {
        "wildbench_annotator_success:judge=qwen3_5_27b": 1.0,
        "wildbench_score:judge=qwen3_5_27b": 8.0,
        "wildbench_score_rescaled:judge=qwen3_5_27b": (8.0 - 1) / 9,
    }
    # §11 stop gate: unrelated *_score fields never leak in.
    polluted = _evaluate_metric(
        metric,
        {"qwen3_5_27b_score": 8.0, "gpt_score": 1.0, "other_judge_score": 10.0},
    )
    assert polluted == values


def test_metric_official_empty_output_and_failure_semantics():
    metric = SingleJudgeWildBenchMetric(judge_id="qwen3_5_27b")
    # Official empty-candidate shortcut scores 1.0 and is not a failure.
    values = _evaluate_metric(
        metric, {"empty_output_score": 1.0, "qwen3_5_27b_score": None}
    )
    assert values["wildbench_score:judge=qwen3_5_27b"] == 1.0
    assert values["wildbench_annotator_success:judge=qwen3_5_27b"] == 1.0
    # Judge failure: success 0, no score stat (never score zero).
    values = _evaluate_metric(metric, {"qwen3_5_27b_score": None})
    assert values == {"wildbench_annotator_success:judge=qwen3_5_27b": 0.0}


def test_end_to_end_wildbench_rejudge_fixture(tmp_path: Path):
    fake_judge_client.reset_telemetry()
    build_wildbench_source_run(tmp_path / "src", empty_output_index=1)
    snapshot = snap.build_response_snapshot(tmp_path / "src", tmp_path / "snapshots")
    sidecar = write_fake_judge_sidecar(tmp_path / "judge_sidecars")
    result = run_rejudge(
        snapshot_dpath=snapshot.snapshot_dpath,
        judge=make_fake_judge_spec(),
        replicate=0,
        out_root=tmp_path / "results",
        cache_root=tmp_path / "cache",
        experiment_name="fixture-exp",
        sidecar_config_dpaths=(str(sidecar),),
        parallelism=1,
    )
    # One judge request for the nonempty candidate; none for the empty one.
    assert len(fake_judge_client.REQUEST_LOG) == 1

    judgments = [
        json.loads(line)
        for line in (result.out_dpath / "judgments.jsonl").read_text().splitlines()
    ]
    by_id = {j["key"]["instance_id"]: j["annotation"] for j in judgments}
    assert by_id["id0"]["parse_status"] == "ok"
    assert by_id["id0"]["fake_judge_score"] is not None
    assert by_id["id1"]["parse_status"] == "empty_candidate_output"
    assert by_id["id1"]["empty_output_score"] == 1.0

    stats = json.loads((result.out_dpath / "stats.json").read_text())
    names = {stat["name"]["name"] for stat in stats}
    assert "wildbench_score:judge=fake_judge" in names
    assert "wildbench_score_rescaled:judge=fake_judge" in names
    assert "wildbench_score" not in names
    fake_judge_client.reset_telemetry()

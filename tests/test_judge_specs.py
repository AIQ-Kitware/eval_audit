"""Commit 4 (open-judge-plan §9): judge specification model.

Hash identity must cover every inference-affecting field and nothing
operational; incomplete specs must be rejected; the flat annotator
args must keep judge identity recoverable by the existing
``extract_judge_models`` without a sidecar.
"""

from __future__ import annotations

import dataclasses

import pytest

from eval_audit.indexing.schema import extract_judge_models
from eval_audit.judging.specs import (
    JudgeSpec,
    JudgmentAttemptSpec,
    SpecValidationError,
    default_request_random,
)


def _judge(**overrides) -> JudgeSpec:
    base = dict(
        id="qwen3_5_27b",
        model="qwen/qwen3.5-27b",
        model_deployment="litellm/qwen3.5-27b-judge",
        lease_endpoint="qwen3.5-27b-judge",
        temperature=0.0,
        max_tokens=256,
        parser_version="safety-v1",
        prompt_version="xstest-official-v1",
        thinking_mode="disabled",
        client_class="eval_audit.integrations.helm_clients.NullSafeOpenAIChatClient",
    )
    base.update(overrides)
    return JudgeSpec(**base)


def test_spec_hash_stable_and_covers_inference_fields():
    a = _judge()
    assert a.spec_hash() == _judge().spec_hash()
    # Every inference-affecting field changes the hash.
    for change in (
        {"model": "qwen/qwen3.6-35b-a3b"},
        {"model_deployment": "litellm/other"},
        {"temperature": 0.5},
        {"max_tokens": 512},
        {"parser_version": "safety-v2"},
        {"prompt_version": "xstest-official-v2"},
        {"thinking_mode": "enabled"},
        {"client_class": "other.Client"},
        {"model_revision": "abc123"},
        {"quantization": "fp8"},
    ):
        assert _judge(**change).spec_hash() != a.spec_hash(), change


def test_spec_hash_excludes_labels_and_plumbing():
    a = _judge()
    assert _judge(id="other_label").spec_hash() == a.spec_hash()
    assert _judge(lease_endpoint="other-endpoint").spec_hash() == a.spec_hash()


def test_incomplete_or_invalid_specs_rejected():
    with pytest.raises(SpecValidationError, match="model"):
        _judge(model="")
    with pytest.raises(SpecValidationError, match="thinking_mode"):
        _judge(thinking_mode="maybe")
    with pytest.raises(SpecValidationError, match="max_tokens"):
        _judge(max_tokens=0)
    with pytest.raises(SpecValidationError, match="id"):
        _judge(id="Not A Valid Id")
    with pytest.raises(SpecValidationError, match="parser_version"):
        _judge(parser_version="")


def test_attempt_hash_includes_response_set_and_replicate():
    judge = _judge()
    attempt = JudgmentAttemptSpec(
        response_set_hash="a" * 64,
        benchmark="xstest",
        judge=judge,
        replicate=0,
        request_random=default_request_random("exp1", judge.id, 0),
    )
    same = dataclasses.replace(attempt)
    assert attempt.attempt_hash() == same.attempt_hash()
    for change in (
        {"response_set_hash": "b" * 64},
        {"replicate": 1, "request_random": default_request_random("exp1", judge.id, 1)},
        {"benchmark": "wildbench"},
        {"judge": _judge(temperature=1.0)},
        {"request_random": "exp2:qwen3_5_27b:r0"},
    ):
        assert dataclasses.replace(attempt, **change).attempt_hash() != attempt.attempt_hash()


def test_attempt_rejects_unknown_benchmark_and_negative_replicate():
    judge = _judge()
    with pytest.raises(SpecValidationError, match="benchmark"):
        JudgmentAttemptSpec("a" * 64, "mmlu", judge, 0, "x:y:r0")
    with pytest.raises(SpecValidationError, match="replicate"):
        JudgmentAttemptSpec("a" * 64, "xstest", judge, -1, "x:y:r0")


def test_annotator_args_flat_and_recoverable_without_sidecar():
    judge = _judge()
    request_random = default_request_random("gpt-oss-20b-open-judge-v1", judge.id, 2)
    args = judge.annotator_args(request_random)
    assert args["judge_id"] == "qwen3_5_27b"
    assert args["judge_model"] == "qwen/qwen3.5-27b"
    assert args["judge_model_deployment"] == "litellm/qwen3.5-27b-judge"
    assert args["request_random"] == "gpt-oss-20b-open-judge-v1:qwen3_5_27b:r2"
    assert args["thinking_mode"] == "disabled"
    # No nested opaque dicts in v1 (§9.1).
    assert all(not isinstance(v, dict) for v in args.values())

    # The existing run_spec extractor recovers the judge identity from a
    # normal HELM artifact carrying these args.
    run_spec = {
        "annotators": [
            {
                "class_name": (
                    "eval_audit.integrations.helm_judging.safety."
                    "ConfigurableXSTestAnnotator"
                ),
                "args": args,
            }
        ]
    }
    extracted = extract_judge_models(run_spec)
    assert extracted is not None
    assert "qwen/qwen3.5-27b" in extracted


def test_request_random_distinct_per_replicate():
    values = {default_request_random("exp", "qwen3_5_27b", r) for r in range(3)}
    assert len(values) == 3

"""Characterization tests for the Stage-1 model-eligibility policy (R-7).

``classify_model_eligibility`` was extracted from
``cli/index_historic_helm_runs.py``, where the predicate was computed twice
(selection loop + filter-report loop). These tests lock the per-gate outcomes so
the single extracted policy stays byte-equivalent to the two inline copies it
replaced.
"""
from __future__ import annotations

from eval_audit.indexing.historic_filtering import (
    MAX_PARAMS,
    classify_model_eligibility,
)


def _row(**overrides):
    base = {
        "name": "org/text-7b",
        "tags": ["TEXT_MODEL_TAG"],
        "num_parameters": 7_000_000_000,
        "access": "open",
        "has_hf_client": True,
    }
    base.update(overrides)
    return base


def test_eligible_open_text_model_with_hf_client():
    eligible, reasons, details = classify_model_eligibility(_row())
    assert eligible is True
    assert reasons == []
    assert details == {}


def test_not_text_like():
    eligible, reasons, _ = classify_model_eligibility(_row(tags=[]))
    assert eligible is False
    assert "not-text-like" in reasons


def test_excluded_modality_tag():
    eligible, reasons, details = classify_model_eligibility(
        _row(tags=["TEXT_MODEL_TAG", "VISION_LANGUAGE_MODEL_TAG"])
    )
    assert eligible is False
    assert "excluded-tags" in reasons
    assert "excluded-tags" in details


def test_too_large():
    eligible, reasons, _ = classify_model_eligibility(
        _row(num_parameters=int(MAX_PARAMS) + 1)
    )
    assert eligible is False
    assert "too-large" in reasons


def test_unknown_size_passes():
    eligible, reasons, _ = classify_model_eligibility(_row(num_parameters=None))
    assert eligible is True
    assert "too-large" not in reasons


def test_not_open_access():
    eligible, reasons, _ = classify_model_eligibility(_row(access="limited"))
    assert eligible is False
    assert "not-open-access" in reasons


def test_no_local_hf_path():
    eligible, reasons, _ = classify_model_eligibility(
        _row(name="org/no-hf", has_hf_client=False)
    )
    assert eligible is False
    assert "no-local-helm-deployment" in reasons


def test_known_hf_override_restores_local_path():
    eligible, reasons, _ = classify_model_eligibility(
        _row(name="qwen/qwen2-72b-instruct", has_hf_client=False,
             num_parameters=None)
    )
    # override supplies the local path; size unknown passes
    assert eligible is True
    assert "no-local-helm-deployment" not in reasons


def test_multiple_failing_gates_all_reported():
    eligible, reasons, _ = classify_model_eligibility(
        _row(tags=["IMAGE_MODEL_TAG"], access="closed", has_hf_client=False,
             name="org/closed-image")
    )
    assert eligible is False
    # not-text-like + excluded-tags + not-open-access + no-local-helm-deployment
    assert set(reasons) == {
        "not-text-like",
        "excluded-tags",
        "not-open-access",
        "no-local-helm-deployment",
    }

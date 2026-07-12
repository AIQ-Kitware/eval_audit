"""diagnose_repro behavior battery + HelmRunDiff delegation pin.

Since sub-stage 4.6 landed, ``HelmRunDiff._diagnose_repro`` *delegates*
to :func:`eval_audit.normalized.diagnose.diagnose_repro` — there is no
second implementation to drift (the original 4.2 "two copies must
agree" purpose is obsolete; docstring updated 2026-07-12, plan item A2).
What this file still pins:

* the input→label behavior of ``diagnose_repro`` itself, across a
  branch-covering battery of synthetic inputs (labels, priorities,
  reason ordering, substitution re-labeling); and
* that ``HelmRunDiff._diagnose_repro`` remains a pure delegation
  (identical output on identical inputs) while ``HelmRunDiff`` exists.
"""
from __future__ import annotations

import pytest

from eval_audit.helm.diff import HelmRunDiff
from eval_audit.normalized.diagnose import diagnose_repro


def _base_inputs(**overrides):
    """An all-clean comparison; cases override pieces to hit branches."""
    inputs = {
        "run_spec_name_ok": True,
        "run_spec_semantic": {
            "execution_ok": True,
            "execution_paths": [],
            "deployment_paths": [],
            "deployment_changed": False,
            "evaluation_paths": [],
            "metric_specs_multiset_delta": {"equal_as_multiset": True},
        },
        "scenario_semantic": {"known": True, "semantic_ok": True},
        "dataset_overlap": {
            "base_iou": 1.0,
            "variant_iou": 1.0,
            "content_equality": {
                "input": {"equal_ratio": 1.0},
                "prompt": {"equal_ratio": 1.0},
                "completion": {"equal_ratio": 1.0},
            },
            "mismatch_examples": {},
        },
        "value_summary": {
            "by_class": {
                "core": {"agree_ratio": 1.0},
                "bookkeeping": {"agree_ratio": 1.0},
            }
        },
    }
    inputs.update(overrides)
    return inputs


CASES = {
    "reproduced": _base_inputs(),
    "wrong_run_pair": _base_inputs(run_spec_name_ok=False),
    "execution_spec_drift": _base_inputs(
        run_spec_semantic={
            "execution_ok": False,
            "execution_paths": ["adapter_spec.temperature"],
            "execution_value_examples": [{"path": "adapter_spec.temperature", "a": 0.0, "b": 1.0}],
            "counts": {"execution": 1},
            "deployment_paths": [],
            "deployment_changed": False,
            "evaluation_paths": [],
            "metric_specs_multiset_delta": {"equal_as_multiset": True},
        }
    ),
    "deployment_drift": _base_inputs(
        run_spec_semantic={
            "execution_ok": False,
            "execution_paths": ["adapter_spec.model_deployment"],
            "deployment_paths": ["adapter_spec.model_deployment"],
            "deployment_changed": True,
            "deployment": {"a": "huggingface/m", "b": "vllm/m"},
            "evaluation_paths": [],
            "metric_specs_multiset_delta": {"equal_as_multiset": True},
        }
    ),
    "scenario_spec_drift": _base_inputs(
        scenario_semantic={
            "known": True,
            "semantic_ok": False,
            "semantic_paths": ["args.subject"],
            "counts": {"semantic": 1},
        }
    ),
    "scenario_unknown_is_not_drift": _base_inputs(
        scenario_semantic={"known": False, "semantic_ok": None}
    ),
    "dataset_overlap_error": _base_inputs(
        dataset_overlap={"error": "ValueError('boom')"}
    ),
    "dataset_overlap_none": _base_inputs(dataset_overlap=None),
    "dataset_instance_and_variant_drift": _base_inputs(
        dataset_overlap={
            "base_iou": 0.7,
            "base_coverage": {"n_isect": 7, "n_union": 10},
            "variant_iou": 0.9,
            "variant_coverage": {"n_isect": 9, "n_union": 10},
            "content_equality": {},
            "mismatch_examples": {},
        }
    ),
    "content_drift_all_fields": _base_inputs(
        dataset_overlap={
            "base_iou": 1.0,
            "variant_iou": 1.0,
            "content_equality": {
                "input": {"equal_ratio": 0.9, "n": 10},
                "prompt": {"equal_ratio": 0.8, "n": 10},
                "completion": {"equal_ratio": 0.5, "n": 10},
            },
            "mismatch_examples": {"input": [{"key": "i1"}]},
        }
    ),
    "evaluation_spec_drift_paths": _base_inputs(
        run_spec_semantic={
            "execution_ok": True,
            "execution_paths": [],
            "deployment_paths": [],
            "deployment_changed": False,
            "evaluation_paths": ["metric_specs.0.args.k"],
            "metric_specs_multiset_delta": {"equal_as_multiset": True},
        }
    ),
    "evaluation_spec_drift_multiset": _base_inputs(
        run_spec_semantic={
            "execution_ok": True,
            "execution_paths": [],
            "deployment_paths": [],
            "deployment_changed": False,
            "evaluation_paths": [],
            "metric_specs_multiset_delta": {
                "equal_as_multiset": False,
                "only_a": ["x"],
                "only_b": [],
            },
        }
    ),
    "no_comparable_core_metrics": _base_inputs(
        value_summary={"by_class": {"core": {}, "bookkeeping": {"agree_ratio": 1.0}}}
    ),
    "core_metric_drift": _base_inputs(
        value_summary={
            "by_class": {
                "core": {"agree_ratio": 0.7, "n": 10},
                "bookkeeping": {"agree_ratio": 1.0},
            }
        }
    ),
    "bookkeeping_metric_drift": _base_inputs(
        value_summary={
            "by_class": {
                "core": {"agree_ratio": 1.0},
                "bookkeeping": {"agree_ratio": 0.5, "n": 10},
            }
        }
    ),
    "multiple_primary_reasons": _base_inputs(
        run_spec_name_ok=False,
        run_spec_semantic={
            "execution_ok": False,
            "execution_paths": ["adapter_spec.model_deployment"],
            "deployment_paths": ["adapter_spec.model_deployment"],
            "deployment_changed": True,
            "deployment": {"a": "x", "b": "y"},
            "evaluation_paths": [],
            "metric_specs_multiset_delta": {"equal_as_multiset": True},
        },
    ),
    "empty_semantic_dicts": _base_inputs(
        run_spec_semantic={}, scenario_semantic={}, value_summary={}, dataset_overlap=None
    ),
}


@pytest.mark.parametrize("case_name", sorted(CASES))
def test_diagnosis_matches_helm_implementation(case_name):
    inputs = CASES[case_name]
    # _diagnose_repro never touches self; calling through the class with
    # a None receiver keeps the comparison free of fixture plumbing.
    helm_result = HelmRunDiff._diagnose_repro(None, **inputs)
    normalized_result = diagnose_repro(**inputs)
    assert normalized_result == helm_result


def test_expected_labels_sanity():
    """Spot-check labels so a synchronized bug in both impls can't hide."""
    assert diagnose_repro(**CASES["reproduced"])["label"] == "reproduced"
    assert diagnose_repro(**CASES["wrong_run_pair"])["label"] == "wrong_run_pair"
    assert diagnose_repro(**CASES["deployment_drift"])["label"] == "deployment_drift"
    assert diagnose_repro(**CASES["core_metric_drift"])["label"] == "core_metric_drift"
    assert (
        diagnose_repro(**CASES["multiple_primary_reasons"])["label"]
        == "multiple_primary_reasons"
    )
    assert (
        diagnose_repro(**CASES["scenario_unknown_is_not_drift"])["label"] == "reproduced"
    )


# --- substitution awareness (new behavior; HELM impl has no equivalent) ---


def test_declared_and_observed_substitution_relabels():
    # Judge swapped (fact says recipes differ on judge) and judge-driven
    # core metrics shifted: with the substitution declared, the primary
    # label is the intended substitution, not unexplained drift.
    inputs = CASES["core_metric_drift"]
    out = diagnose_repro(
        **inputs, substitutions=["judge"], substitution_fact_status={"judge": "no"}
    )
    assert out["label"] == "intended_substitution:judge"
    names = [r["name"] for r in out["reasons"]]
    assert "intended_substitution:judge" in names
    # The downstream drift is still recorded, just not primary.
    assert "core_metric_drift" in names


def test_declared_but_not_observed_substitution_warns():
    out = diagnose_repro(
        **CASES["reproduced"],
        substitutions=["judge"],
        substitution_fact_status={"judge": "yes"},
    )
    assert out["label"] == "substitution_not_observed:judge"


def test_declared_substitution_with_unknown_fact_is_a_noop():
    plain = diagnose_repro(**CASES["reproduced"])
    declared = diagnose_repro(
        **CASES["reproduced"],
        substitutions=["judge"],
        substitution_fact_status={"judge": "unknown"},
    )
    assert declared == plain


def test_no_substitutions_is_byte_identical_to_helm():
    for case_name, inputs in CASES.items():
        helm_result = HelmRunDiff._diagnose_repro(None, **inputs)
        assert diagnose_repro(**inputs, substitutions=()) == helm_result, case_name

"""Regression tests for comparability-fact status (P1-1) and the official
max_eval_instances extraction (P1-2)."""
from __future__ import annotations

import json
from pathlib import Path

from eval_audit.indexing.schema import extract_run_spec_fields
from eval_audit.planning.core_report_planner import (
    NormalizedPlannerComponent,
    build_comparability_facts,
)


def _component(
    *,
    component_id: str,
    source_kind: str = "official",
    model: str | None = "allenai/olmo-7b",
    max_eval_instances: str | None = None,
) -> NormalizedPlannerComponent:
    return NormalizedPlannerComponent(
        component_id=component_id,
        source_kind=source_kind,
        logical_run_key="mmlu:subject=foo,model=allenai_olmo-7b",
        run_entry=None,
        run_path=f"/tmp/{component_id}",
        job_path=None,
        run_spec_fpath=None,
        run_spec_name="mmlu:subject=foo,model=allenai_olmo-7b",
        model=model,
        scenario_class="helm.MMLUScenario",
        benchmark_group="mmlu",
        model_deployment=None,
        max_eval_instances=max_eval_instances,
        suite=None,
        public_track=None,
        suite_version=None,
        experiment_name=None,
        machine_host=None,
        attempt_uuid=None,
        attempt_identity=component_id,
        display_name=component_id,
        tags=[source_kind],
        manifest_timestamp="10",
        provenance={},
        extra_metadata={},
        artifact_format="helm",
        eee_artifact_path=None,
    )


def test_fact_status_unknown_when_only_one_component_contributes():
    """P1-1: one known value + one None is partial knowledge, not verified
    agreement."""
    facts = build_comparability_facts(
        [
            _component(component_id="a", model="allenai/olmo-7b"),
            _component(component_id="b", model=None),
        ]
    )
    assert facts["same_model"]["status"] == "unknown"


def test_fact_status_yes_when_two_components_agree():
    facts = build_comparability_facts(
        [
            _component(component_id="a", model="allenai/olmo-7b"),
            _component(component_id="b", model="allenai/olmo-7b"),
        ]
    )
    assert facts["same_model"]["status"] == "yes"


def test_fact_status_no_when_two_components_differ():
    facts = build_comparability_facts(
        [
            _component(component_id="a", model="allenai/olmo-7b"),
            _component(component_id="b", model="meta/llama-7b"),
        ]
    )
    assert facts["same_model"]["status"] == "no"


def test_max_eval_instances_drift_detected(tmp_path):
    """P1-2: official cap 1000 vs local cap 10 must register drift (was never
    detectable because the official cap was hardcoded None)."""
    facts = build_comparability_facts(
        [
            _component(component_id="official", source_kind="official", max_eval_instances="1000"),
            _component(component_id="local", source_kind="local", max_eval_instances="10"),
        ]
    )
    assert facts["same_max_eval_instances"]["status"] == "no"


def test_extract_run_spec_fields_reads_max_eval_instances(tmp_path):
    spec = {
        "name": "mmlu:subject=foo,model=allenai/olmo-7b",
        "adapter_spec": {"model": "allenai/olmo-7b", "max_eval_instances": 1000},
        "scenario_spec": {"class_name": "helm.MMLUScenario"},
    }
    fpath = tmp_path / "run_spec.json"
    fpath.write_text(json.dumps(spec))
    fields = extract_run_spec_fields(fpath)
    assert fields["max_eval_instances"] == 1000


def test_extract_run_spec_fields_absent_max_eval_instances_is_none(tmp_path):
    fields = extract_run_spec_fields(None)
    assert fields["max_eval_instances"] is None

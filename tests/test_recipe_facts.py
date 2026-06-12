"""Phase 3 sub-stage 4.1: the recipe_facts accessor.

Resolution order: native block in the EEE aggregate → sidecar
run_spec.json → unknown. Plus the conservative judge-identity
extractor and the additive ``judge_models`` key on
``extract_run_spec_fields``.
"""
from __future__ import annotations

import json
from pathlib import Path

from eval_audit.indexing.schema import extract_judge_models, extract_run_spec_fields
from eval_audit.normalized.recipe_facts import (
    NATIVE_RECIPE_FACTS_KEY,
    resolve_recipe_facts,
)


def _write_run_spec(fpath: Path, *, annotators=None) -> None:
    spec = {
        "name": "demo_bench:model=org/model-a",
        "adapter_spec": {
            "model": "org/model-a",
            "model_deployment": "huggingface/model-a",
            "instructions": "Answer with a single letter.",
            "max_eval_instances": 100,
        },
        "scenario_spec": {"class_name": "helm.benchmark.scenarios.demo.DemoScenario"},
        "metric_specs": [],
    }
    if annotators is not None:
        spec["annotators"] = annotators
    fpath.write_text(json.dumps(spec))


def _write_aggregate(fpath: Path, *, recipe_facts: dict | None) -> None:
    details = {}
    if recipe_facts is not None:
        details[NATIVE_RECIPE_FACTS_KEY] = json.dumps(recipe_facts)
    aggregate = {
        "schema_version": "0.0.1",
        "evaluation_id": "demo/org_model-a/0",
        "retrieved_timestamp": "0",
        "source_metadata": {
            "source_organization_name": "demo",
            "additional_details": details or None,
        },
        "eval_library": {"library_name": "demo", "library_version": "0"},
        "model_info": {"id": "org/model-a"},
        "evaluation_results": [],
    }
    fpath.write_text(json.dumps(aggregate))


def test_unknown_when_nothing_available(tmp_path):
    facts = resolve_recipe_facts()
    assert facts.source == "unknown"
    assert facts.scenario_class is None
    assert facts.judge_models is None

    empty_dir = tmp_path / "artifact"
    empty_dir.mkdir()
    facts = resolve_recipe_facts(eee_artifact_dir=empty_dir)
    assert facts.source == "unknown"


def test_sidecar_resolution(tmp_path):
    artifact_dir = tmp_path / "artifact"
    artifact_dir.mkdir()
    _write_aggregate(artifact_dir / "uuid1.json", recipe_facts=None)
    _write_run_spec(artifact_dir / "run_spec.json")

    facts = resolve_recipe_facts(eee_artifact_dir=artifact_dir)
    assert facts.source == "sidecar"
    assert facts.run_spec_name == "demo_bench:model=org/model-a"
    assert facts.model == "org/model-a"
    assert facts.model_deployment == "huggingface/model-a"
    assert facts.scenario_class == "helm.benchmark.scenarios.demo.DemoScenario"
    assert facts.benchmark_group == "demo_bench"
    assert facts.instructions == "Answer with a single letter."
    assert facts.max_eval_instances == "100"
    assert facts.run_spec_hash is not None
    # No annotators key in the spec -> judge identity unknown.
    assert facts.judge_models is None


def test_explicit_run_spec_fpath_without_artifact_dir(tmp_path):
    run_spec = tmp_path / "run_spec.json"
    _write_run_spec(run_spec)
    facts = resolve_recipe_facts(run_spec_fpath=run_spec)
    assert facts.source == "sidecar"
    assert facts.model == "org/model-a"


def test_native_block_wins_over_sidecar(tmp_path):
    artifact_dir = tmp_path / "artifact"
    artifact_dir.mkdir()
    _write_aggregate(
        artifact_dir / "uuid1.json",
        recipe_facts={
            "run_spec_name": "native_bench:model=org/model-a",
            "model": "org/model-a",
            "scenario_class": "frameworkx.scenarios.NativeScenario",
            "max_eval_instances": "50",
            "judge_models": ["openai/gpt-4o"],
            "framework_extra": "preserved",
        },
    )
    _write_run_spec(artifact_dir / "run_spec.json")

    facts = resolve_recipe_facts(eee_artifact_dir=artifact_dir)
    assert facts.source == "native"
    assert facts.run_spec_name == "native_bench:model=org/model-a"
    assert facts.scenario_class == "frameworkx.scenarios.NativeScenario"
    assert facts.max_eval_instances == "50"
    assert facts.judge_models == ("openai/gpt-4o",)
    # Fields the block doesn't carry stay None (no silent sidecar mixing).
    assert facts.model_deployment is None
    assert facts.extra == {"framework_extra": "preserved"}


def test_judge_extraction_from_annotators():
    spec = {
        "annotators": [
            {"class_name": "helm.benchmark.annotation.wildbench.WildBenchAnnotator",
             "args": {"judge_model": "openai/gpt-4o"}},
            {"class_name": "helm.benchmark.annotation.other.OtherAnnotator"},
        ]
    }
    assert extract_judge_models(spec) == ("OtherAnnotator", "openai/gpt-4o")
    # No annotators key at all -> unknown.
    assert extract_judge_models({}) is None
    # Explicitly empty -> known to have no judges.
    assert extract_judge_models({"annotators": []}) == ()


def test_extract_run_spec_fields_gains_judge_models(tmp_path):
    run_spec = tmp_path / "run_spec.json"
    _write_run_spec(
        run_spec,
        annotators=[{"class_name": "x.y.JudgeAnnotator", "args": {"model": "open/judge-1"}}],
    )
    fields = extract_run_spec_fields(run_spec)
    assert fields["judge_models"] == ("open/judge-1",)
    # Existing keys unchanged.
    assert fields["model"] == "org/model-a"
    assert fields["has_run_spec_json"] is True

    # Absent file: judge_models present and None.
    fields = extract_run_spec_fields(None)
    assert fields["judge_models"] is None

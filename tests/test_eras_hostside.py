"""Host-side era wiring: materializer guard + era deployment entry (commit 4).

See docs/planning/era-pinned-helm-containers-plan.md.
"""
from __future__ import annotations

import json

import pytest

from eval_audit.integrations.infer_stack.bundle_export import _model_deployment_entry_era
from eval_audit.integrations.infer_stack.serving_facts import ServingFacts
from eval_audit.manifests.run_spec_materializer import (
    RunSpecSource,
    materialize_run_spec,
)

_ERA_CLIENT = "helm_era_shim.openai_compat_client.OpenAICompatCompletionsClient"


def _write_era_spec(tmp_path, *, with_model_deployment: bool):
    """A minimal official run dir; era specs have NO adapter_spec.model_deployment."""
    run_dir = tmp_path / "corpus" / "classic" / "benchmark_output" / "runs" / "v0.2.4" / "babi_qa:task=15,model=eleutherai_pythia-6.9b"
    run_dir.mkdir(parents=True)
    adapter_spec = {"method": "generation", "model": "eleutherai/pythia-6.9b", "max_eval_instances": None}
    if with_model_deployment:
        adapter_spec["model_deployment"] = "together/pythia-6.9b"
    spec = {
        "name": "babi_qa:task=15,model=eleutherai/pythia-6.9b",
        "scenario_spec": {"class_name": "helm.benchmark.scenarios.babi_qa_scenario.BabiQAScenario", "args": {}},
        "adapter_spec": adapter_spec,
        "metric_specs": [],
    }
    (run_dir / "run_spec.json").write_text(json.dumps(spec))
    return tmp_path / "corpus", run_dir


def test_materializer_rejects_deployment_rewrite_on_era_spec(tmp_path):
    """A pre-v0.5 spec has no model_deployment field; a rewrite must fail loud."""
    root, run_dir = _write_era_spec(tmp_path, with_model_deployment=False)
    source = RunSpecSource(
        run_entry="babi_qa:task=15,model=eleutherai/pythia-6.9b",
        rel_path=str(run_dir.relative_to(root)),
        model_deployment="vllm/pythia-local",  # a rewrite target — illegal for era
    )
    with pytest.raises(ValueError, match="no .*model_deployment|verbatim"):
        materialize_run_spec(source, precomputed_root=root, staging_dir=tmp_path / "staging")


def test_materializer_era_verbatim_with_cap(tmp_path):
    """Era replay (no rewrite) still applies the max_eval_instances truncation."""
    root, run_dir = _write_era_spec(tmp_path, with_model_deployment=False)
    source = RunSpecSource(
        run_entry="babi_qa:task=15,model=eleutherai/pythia-6.9b",
        rel_path=str(run_dir.relative_to(root)),
        max_eval_instances=10,
    )
    result = materialize_run_spec(source, precomputed_root=root, staging_dir=tmp_path / "staging")
    materialized = json.loads(open(result.run_spec_json).read())
    assert materialized["adapter_spec"]["max_eval_instances"] == 10
    assert "model_deployment" not in materialized["adapter_spec"]
    assert result.substitutions["max_eval_instances"] == {"from": None, "to": 10}


def test_materializer_allows_rewrite_on_modern_spec(tmp_path):
    """A modern spec carries model_deployment, so the rewrite is permitted."""
    root, run_dir = _write_era_spec(tmp_path, with_model_deployment=True)
    source = RunSpecSource(
        run_entry="babi_qa:task=15,model=eleutherai/pythia-6.9b",
        rel_path=str(run_dir.relative_to(root)),
        model_deployment="vllm/pythia-local",
    )
    result = materialize_run_spec(source, precomputed_root=root, staging_dir=tmp_path / "staging")
    materialized = json.loads(open(result.run_spec_json).read())
    assert materialized["adapter_spec"]["model_deployment"] == "vllm/pythia-local"


def test_era_deployment_entry_schema():
    facts = ServingFacts(
        endpoint="pythia-6-9b",
        served_model_name="pythia-6.9b",
        hf_model_id="EleutherAI/pythia-6.9b",
        max_model_len=2048,
    )
    entry = _model_deployment_entry_era(
        facts, helm_model_name="eleutherai/pythia-6.9b", base_url="http://localhost:8000/v1"
    )
    # Name == official model name (verbatim by-name).
    assert entry["name"] == "eleutherai/pythia-6.9b"
    assert entry["model_name"] == "eleutherai/pythia-6.9b"
    # cattrs-no-defaults: the null keys are present explicitly.
    assert entry["tokenizer_name"] is None
    assert entry["max_sequence_length"] is None
    # Era shim client; NO api_key (credentials.conf owns it).
    assert entry["client_spec"]["class_name"] == _ERA_CLIENT
    assert entry["client_spec"]["args"]["base_url"] == "http://localhost:8000/v1"
    assert entry["client_spec"]["args"]["openai_model_name"] == "pythia-6.9b"
    assert "api_key" not in entry["client_spec"]["args"]


def test_era_deployment_entry_requires_helm_model_name():
    facts = ServingFacts(endpoint="e", served_model_name="m", hf_model_id="org/m", max_model_len=1)
    with pytest.raises(ValueError, match="helm_model_name"):
        _model_deployment_entry_era(facts, helm_model_name=None)

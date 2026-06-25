"""Tests for the phi-2 e2e from-spec migration.

Covers the exporter-side wiring of
``docs/planning/e2e-from-run-spec-migration-plan.md``: the ``_manifest_doc``
gating and deployment rekey under ``--from-spec`` (Change 2), the preset wiring
(comparable carries it, the incomparable control deliberately does not), the
checked-in HF sibling manifests + override (Change 1), and a corpus-dependent
discovery dry-check (Change 6) that resolves the official phi-2 run dir and
confirms its ``run_spec.json`` names the substitution target with no annotators.

None of the exporter tests need a live serving stack or catalog — they drive
``materialize_benchmark_bundle`` with synthesized ``ServingFacts`` + the real
preset profiles, so the rekey path is exercised end to end.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

from eval_audit.integrations.infer_stack import adapter as A

REPO = Path(__file__).resolve().parents[1]
MANIFEST_DIR = REPO / "dev" / "e2e-tests" / "manifests"
OVERRIDE_FPATH = REPO / "configs" / "debug" / "e2e_phi2_fromspec_overrides.yaml"
PUBLIC_MMLU = Path("/data/crfm-helm-public/mmlu")
PHI2_RUN_ENTRY = (
    "mmlu:subject=philosophy,method=multiple_choice_joint,"
    "model=microsoft/phi-2,eval_split=test"
)


# --------------------------------------------------------------------------
# Change 2a — _manifest_doc gating
# --------------------------------------------------------------------------


def _spec() -> dict:
    return {
        "experiment_name": "x",
        "description": "d",
        "run_entries": [PHI2_RUN_ENTRY],
        "max_eval_instances": 5,
        "suite": "x",
        "precomputed_root": "/data/crfm-helm-public/mmlu",
    }


def test_manifest_doc_run_entry_path_unchanged():
    # The default (run-entry) path must stay byte-compatible: precomputed_root
    # None and no from_run_spec key (the manifest default is False).
    doc = A._manifest_doc(spec=_spec(), model_deployments_fpath="m.yaml")
    assert doc["precomputed_root"] is None
    assert "from_run_spec" not in doc


def test_manifest_doc_from_spec_emits_fields():
    doc = A._manifest_doc(
        spec=_spec(), model_deployments_fpath="m.yaml", from_run_spec=True
    )
    assert doc["from_run_spec"] is True
    assert doc["precomputed_root"] == "/data/crfm-helm-public/mmlu"


def test_manifest_doc_cli_precomputed_root_override_wins():
    doc = A._manifest_doc(
        spec=_spec(),
        model_deployments_fpath="m.yaml",
        from_run_spec=True,
        precomputed_root="/override",
    )
    assert doc["precomputed_root"] == "/override"


# --------------------------------------------------------------------------
# Change 2b — preset wiring (comparable has it; incomparable does not)
# --------------------------------------------------------------------------


def test_comparable_preset_has_fromspec_wiring():
    p = A.PRESET_CONFIGS["e2e-phi_2-vllm-philosophy"]
    assert p["profiles"][0]["from_spec_model_deployment_name"] == "together/phi-2"
    assert p["smoke_manifest"]["precomputed_root"] == "/data/crfm-helm-public/mmlu"
    assert p["full_manifest"]["precomputed_root"] == "/data/crfm-helm-public/mmlu"


def test_incomparable_preset_omits_fromspec_wiring():
    # The negative control stays on the run-entry path (§7/Change 4): no rekey
    # field, no precomputed_root, so even an accidental --from-spec cannot replay
    # away its temperature=1 deviation.
    p = A.PRESET_CONFIGS["e2e-phi_2-vllm-philosophy-incomparable"]
    assert "from_spec_model_deployment_name" not in p["profiles"][0]
    assert "precomputed_root" not in p["smoke_manifest"]
    assert "precomputed_root" not in p["full_manifest"]


# --------------------------------------------------------------------------
# Change 2 — exporter end to end: rekey + manifest fields
# --------------------------------------------------------------------------


def _phi2_facts() -> list:
    # served_model_name must equal endpoint (the C-3 name chain _lease_facts
    # asserts); max_model_len must be set (else _model_deployment_entry raises).
    return [
        A.ServingFacts(
            endpoint="phi2-single",
            served_model_name="phi2-single",
            hf_model_id="microsoft/phi-2",
            max_model_len=2048,
        )
    ]


def _materialize(tmp_path: Path, *, from_run_spec: bool) -> dict:
    preset = "e2e-phi_2-vllm-philosophy"
    specs = A.PRESET_CONFIGS[preset]["profiles"]
    return A.materialize_benchmark_bundle(
        facts=_phi2_facts(),
        output_dir=tmp_path,
        preset=preset,
        profile_specs=[dict(s) for s in specs],
        base_url="http://localhost:14042/v1",
        api_key_value="test-key",
        from_run_spec=from_run_spec,
    )


def test_exporter_run_entry_binds_local_deployment(tmp_path: Path):
    res = _materialize(tmp_path, from_run_spec=False)
    md = yaml.safe_load(Path(res["model_deployments_path"]).read_text())
    assert [e["name"] for e in md["model_deployments"]] == ["vllm/phi-2-local"]
    smoke = yaml.safe_load(res["benchmark_smoke_manifest_path"].read_text())
    assert smoke["precomputed_root"] is None
    assert "from_run_spec" not in smoke


def test_exporter_from_spec_rekeys_to_official_deployment(tmp_path: Path):
    res = _materialize(tmp_path, from_run_spec=True)
    md = yaml.safe_load(Path(res["model_deployments_path"]).read_text())
    # The replayed recipe names together/phi-2; by-name substitution binds THAT
    # name to the served LiteLLM endpoint (shadowing the real Together API).
    assert [e["name"] for e in md["model_deployments"]] == ["together/phi-2"]
    entry = md["model_deployments"][0]
    assert entry["client_spec"]["args"]["openai_model_name"] == "phi2-single"
    smoke = yaml.safe_load(res["benchmark_smoke_manifest_path"].read_text())
    full = yaml.safe_load(res["benchmark_full_manifest_path"].read_text())
    assert smoke["from_run_spec"] is True
    assert smoke["precomputed_root"] == "/data/crfm-helm-public/mmlu"
    assert full["from_run_spec"] is True
    # The lease is unaffected: lease_endpoint keys off the catalog endpoint, not
    # the (now rekeyed) deployment name.
    assert smoke["lease_endpoint"] == "phi2-single"


# --------------------------------------------------------------------------
# Change 1 — checked-in HF sibling manifests + override
# --------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["smoke", "full"])
def test_hf_fromspec_sibling_manifest_valid(mode: str):
    f = MANIFEST_DIR / f"e2e-phi_2-huggingface-philosophy-fromspec-{mode}.yaml"
    assert f.exists(), f"missing sibling manifest {f}"
    d = yaml.safe_load(f.read_text())
    assert d["from_run_spec"] is True
    assert d["precomputed_root"] == "/data/crfm-helm-public/mmlu"
    assert d["model_deployments_fpath"] == (
        "configs/debug/e2e_phi2_fromspec_overrides.yaml"
    )
    # Reuses the run-entry experiment_name/suite so downstream index/compose/
    # summary pick it up unchanged (Change 5 is a no-op).
    assert d["experiment_name"] == f"e2e-phi_2-huggingface-philosophy-{mode}"
    assert d["suite"] == f"e2e-phi_2-huggingface-philosophy-{mode}"
    # enable_huggingface_models is redundant once the override fully specifies the
    # client, and is dropped so it can't register an unused microsoft/phi-2 dep.
    assert d["enable_huggingface_models"] == []


def test_hf_fromspec_override_targets_official_deployment():
    d = yaml.safe_load(OVERRIDE_FPATH.read_text())
    entry = next(
        e for e in d["model_deployments"] if e["name"] == "together/phi-2"
    )
    assert entry["client_spec"]["class_name"].endswith("HuggingFaceClient")
    assert entry["client_spec"]["args"]["pretrained_model_name_or_path"] == (
        "microsoft/phi-2"
    )


# --------------------------------------------------------------------------
# Change 6 — discovery dry-check (corpus-dependent; skipped without /data)
# --------------------------------------------------------------------------


@pytest.mark.skipif(
    not PUBLIC_MMLU.exists(),
    reason="public HELM corpus not present at /data/crfm-helm-public/mmlu",
)
def test_discovery_resolves_official_phi2_dir():
    # The from-spec pipeline locates the official run dir by token-subset match on
    # the bare run-entry, then drives execution from its run_spec.json. Assert the
    # match resolves and the recipe is what the migration assumes.
    sys.path.insert(0, str(REPO / "submodules" / "aiq-magnet"))
    from magnet.backends.helm.cli.materialize_helm_run import (
        find_best_precomputed_run,
    )

    match = find_best_precomputed_run(
        PUBLIC_MMLU, PHI2_RUN_ENTRY, require_per_instance_stats=False
    )
    assert match is not None, "from-spec discovery must locate the official phi-2 dir"
    spec_path = Path(match.run_dir) / "run_spec.json"
    assert spec_path.exists()
    spec = json.loads(spec_path.read_text())
    # Names the OFFICIAL deployment (the substitution target the override rebinds)
    # and carries no annotators — plain MC mmlu, so no judge override is needed.
    assert spec["adapter_spec"]["model_deployment"] == "together/phi-2"
    assert not spec.get("annotators")

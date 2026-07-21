"""Compute-from-spec: synthesize + freeze a run_spec.json per authored key.

Covers ``eval_audit/integrations/infer_stack/synthesize_specs.py`` and its
wiring into ``materialize_benchmark_bundle``. The whole point of the mode is
that a de-novo compute run's identity is a FROZEN spec, not a live-expanded
run-key string, so the tests assert:

* offline expansion is byte-identical to HELM's own ``construct_run_specs``
  (freezing changes nothing — it only pins);
* the synthesized source flows through the REAL replay consumer
  (``run_spec_materializer``) with a NO-OP model_deployment rewrite (the spec
  already carries the local name);
* the exporter's mutual-exclusion guard rejects combining --compute-from-spec
  with the official-corpus knobs.

HELM-dependent tests skip cleanly where crfm-helm is not installed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# Gate every expansion test on HELM being importable (crfm-helm[all]).
pytest.importorskip("helm.benchmark.run_spec_factory")

from eval_audit.integrations.infer_stack.synthesize_specs import (  # noqa: E402
    _ensure_helm_registered,
    expand_run_entry_to_run_spec,
    helm_version,
    stage_prod_env,
    synthesize_compute_run_spec_sources,
    write_provenance,
)

_SIDECAR_MD = "configs/local_models/qwen35_small_vllm/model_metadata.yaml"
_SIDECAR_TOK = "configs/local_models/qwen35_small_vllm/tokenizer_configs.yaml"
_DEPLOY = "vllm/qwen3.5-0.8b-base-nlstrip-local"
_MODEL = "qwen/qwen3.5-0.8b-base"
_KEY_MMLU = (
    f"mmlu:subject=anatomy,method=multiple_choice_joint,model={_MODEL},"
    f"model_deployment={_DEPLOY}"
)
_KEY_BOOLQ = (
    f"boolq:model={_MODEL},data_augmentation=canonical,model_deployment={_DEPLOY}"
)


def _prod_env(tmp_path: Path) -> Path:
    """Stage a prod_env dir (sidecars + a minimal generated model_deployments)."""
    import yaml

    md = tmp_path / "model_deployments.deadbeef.yaml"
    md.write_text(
        yaml.safe_dump(
            {
                "model_deployments": [
                    {
                        "name": _DEPLOY,
                        "model_name": _MODEL,
                        "tokenizer_name": _MODEL,
                        "max_sequence_length": 4064,
                        "client_spec": {
                            "class_name": "helm.clients.openai_client.OpenAIClient",
                            "args": {},
                        },
                    }
                ]
            }
        )
    )
    prod_env = stage_prod_env(
        prod_env_dir=tmp_path / "synth_prod_env",
        model_deployments_path=md,
        model_metadata_fpath=_SIDECAR_MD,
        tokenizer_configs_fpath=_SIDECAR_TOK,
    )
    # Register into HELM's global registry so the qwen3.5 id + local deployment
    # resolve (the real flow registers inside synthesize; the expand-only tests
    # need it staged here). Idempotent per dir.
    _ensure_helm_registered(prod_env)
    return prod_env


def _synthesize(tmp_path: Path, run_entries, model_entries, lease_facts):
    prod_env = _prod_env(tmp_path)
    synth_root = tmp_path / "synthesized_specs"
    return synth_root, synthesize_compute_run_spec_sources(
        {"run_entries": run_entries},
        synth_root=synth_root,
        tag="smoke",
        prod_env_dir=prod_env,
        model_entries=model_entries,
        lease_facts=lease_facts,
    )


# --------------------------------------------------------------------------- #
# Faithfulness: freezing == what live expansion would have produced            #
# --------------------------------------------------------------------------- #
def test_expand_matches_construct_run_specs(tmp_path: Path) -> None:
    _prod_env(tmp_path)  # register sidecars so the qwen3.5 id resolves
    from helm.benchmark.run_spec_factory import construct_run_specs
    from helm.common.general import asdict_without_nones
    from helm.common.object_spec import parse_object_spec

    name, frozen = expand_run_entry_to_run_spec(_KEY_MMLU)
    live = asdict_without_nones(construct_run_specs(parse_object_spec(_KEY_MMLU))[0])
    assert frozen == live
    assert name == frozen["name"]
    # the local deployment is baked into the frozen recipe
    assert frozen["adapter_spec"]["model_deployment"] == _DEPLOY


def test_expand_is_deterministic(tmp_path: Path) -> None:
    _prod_env(tmp_path)
    _, a = expand_run_entry_to_run_spec(_KEY_MMLU)
    _, b = expand_run_entry_to_run_spec(_KEY_MMLU)
    assert json.dumps(a, indent=2) == json.dumps(b, indent=2)


# --------------------------------------------------------------------------- #
# Synthesis: files written + source shape matches the reproduction freeze      #
# --------------------------------------------------------------------------- #
def test_synthesize_writes_specs_and_sources(tmp_path: Path) -> None:
    model_entries = [{"name": _DEPLOY}]
    synth_root, (sources, prov) = _synthesize(
        tmp_path,
        [_KEY_MMLU, _KEY_BOOLQ],
        model_entries,
        {"lease_endpoint": "qwen3-5-0-8b-base-single"},
    )
    assert [s["run_entry"] for s in sources] == [_KEY_MMLU, _KEY_BOOLQ]
    for s in sources:
        # same shape _freeze_run_spec_sources emits
        assert set(s) == {"run_entry", "rel_path", "model_deployment", "lease_endpoint"}
        assert s["model_deployment"] == _DEPLOY
        assert s["lease_endpoint"] == "qwen3-5-0-8b-base-single"
        spec_path = synth_root / s["rel_path"]
        assert spec_path.is_file(), spec_path
        assert s["rel_path"].startswith("smoke/") and s["rel_path"].endswith(
            "/run_spec.json"
        )
        doc = json.loads(spec_path.read_text())
        assert doc["adapter_spec"]["model_deployment"] == _DEPLOY
    # provenance carries the version stamp + a content hash per run
    assert len(prov) == 2
    assert all(len(p["sha256"]) == 64 for p in prov)


def test_provenance_file_stamped(tmp_path: Path) -> None:
    synth_root, (_, prov) = _synthesize(
        tmp_path, [_KEY_MMLU], [{"name": _DEPLOY}], None
    )
    path = write_provenance(
        synth_root, helm_version=helm_version(), manifests={"smoke": prov}
    )
    doc = json.loads(path.read_text())
    assert doc["helm_version"] == helm_version()
    assert doc["manifests"]["smoke"][0]["run_entry"] == _KEY_MMLU


# --------------------------------------------------------------------------- #
# The synthesized source is consumable by the REAL replay materializer         #
# (no-op model_deployment rewrite: the spec already carries the local name)    #
# --------------------------------------------------------------------------- #
def test_materializer_replays_synthesized_source_noop_rewrite(tmp_path: Path) -> None:
    from eval_audit.manifests.run_spec_materializer import (
        RunSpecSource,
        materialize_run_spec,
    )

    synth_root, (sources, _) = _synthesize(
        tmp_path, [_KEY_MMLU], [{"name": _DEPLOY}], None
    )
    staging = tmp_path / "staging"
    result = materialize_run_spec(
        RunSpecSource.from_dict(sources[0]),
        precomputed_root=synth_root,
        staging_dir=staging,
        default_max_eval_instances=5,
    )
    # model_deployment was already the local name → the rewrite is a no-op and
    # records NO substitution for it.
    assert "model_deployment" not in result.substitutions
    staged = json.loads(Path(result.run_spec_json).read_text())
    assert staged["adapter_spec"]["model_deployment"] == _DEPLOY


def test_multi_deployment_requires_inline_token(tmp_path: Path) -> None:
    # Two deployments and a key with NO inline model_deployment token → cannot
    # name the rewrite target → hard error (mirrors the reproduction freeze).
    key_no_token = f"boolq:model={_MODEL},data_augmentation=canonical"
    with pytest.raises(ValueError, match="model_deployment"):
        _synthesize(
            tmp_path,
            [key_no_token],
            [{"name": _DEPLOY}, {"name": "vllm/other-local"}],
            None,
        )


# --------------------------------------------------------------------------- #
# Exporter guard: --compute-from-spec is exclusive with official-corpus knobs   #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "kwargs",
    [
        {"freeze_rel_paths": True},
        {"era": "helm-v0.2.4"},
        {"precomputed_root": "/some/corpus"},
    ],
)
def test_compute_from_spec_mutually_exclusive(tmp_path: Path, kwargs) -> None:
    from eval_audit.integrations.infer_stack.bundle_export import (
        materialize_benchmark_bundle,
    )

    with pytest.raises(ValueError, match="mutually exclusive"):
        materialize_benchmark_bundle(
            facts=[],
            output_dir=tmp_path,
            compute_from_spec=True,
            **kwargs,
        )

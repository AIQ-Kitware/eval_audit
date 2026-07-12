"""Exporter auto-freeze: resolve run-entries to exact rel-paths once (§4.5).

Covers ``_strip_local_deployment`` and ``_freeze_run_spec_sources`` in
``eval_audit/integrations/infer_stack/adapter.py``. Discovery (``dc._classify``)
is monkeypatched so these exercise the freeze logic — local-token stripping,
per-source deployment + lease endpoint, the rel-path pin, and loud failure on a
non-RESOLVED entry — without coupling to the magnet matcher's behavior on
synthetic data (that 1:1 resolution against the live corpus is gated separately
by tests/test_olmo_from_spec.py).
"""
from __future__ import annotations

from pathlib import Path

import pytest

# R-3: the discovery core moved to infer_stack.discovery; adapter reads
# _classify/_enumerate_runs from there, so patch the new home.
from eval_audit.integrations.infer_stack import discovery as dc
from eval_audit.integrations.infer_stack.adapter import (
    _freeze_run_spec_sources,
    _strip_local_deployment,
)


# --------------------------------------------------------------------------- #
# _strip_local_deployment                                                     #
# --------------------------------------------------------------------------- #
def test_strip_drops_local_token_only() -> None:
    q, tok = _strip_local_deployment(
        "mmlu:subject=x,model=allenai/olmo-7b,model_deployment=vllm/olmo",
        frozenset({"vllm/olmo"}),
    )
    assert tok == "vllm/olmo"
    assert "model_deployment" not in q
    assert q == "mmlu:subject=x,model=allenai/olmo-7b"


def test_strip_keeps_non_local_token() -> None:
    # an official/private deployment token is a real discriminator → keep it
    q, tok = _strip_local_deployment(
        "med:model=gpt-4o,model_deployment=stanfordhealthcare/gpt-4o",
        frozenset({"vllm/olmo"}),
    )
    assert tok is None
    assert "model_deployment=stanfordhealthcare/gpt-4o" in q


def test_strip_no_token() -> None:
    q, tok = _strip_local_deployment("mmlu:subject=x,model=allenai/olmo-7b", frozenset())
    assert tok is None
    assert q == "mmlu:subject=x,model=allenai/olmo-7b"


# --------------------------------------------------------------------------- #
# _freeze_run_spec_sources                                                     #
# --------------------------------------------------------------------------- #
_REL = "mmlu/benchmark_output/runs/v1.1.0/mmlu:subject=anatomy,method=multiple_choice_joint,model=allenai_olmo-7b,eval_split=test,groups=mmlu_anatomy"


def _fixture_run(root: Path) -> "dc._Run":
    run_dir = root / _REL
    run_dir.mkdir(parents=True)
    return dc._Run(name=run_dir.name, path=run_dir)


def _patch_classify(monkeypatch, run: "dc._Run", *, status: str = "RESOLVED"):
    seen: list[str] = []

    def fake(query: str, runs):  # noqa: ANN001
        seen.append(query)
        cands = [run] if status != "NO_MATCH" else []
        return dc._EntryResult(
            entry=query, status=status, candidates=cands,
            best=(run if cands else None), deployment="together/olmo-7b",
        )

    monkeypatch.setattr(dc, "_classify", fake)
    return seen


def test_freeze_pins_rel_path_and_single_deployment(tmp_path, monkeypatch) -> None:
    run = _fixture_run(tmp_path)
    _patch_classify(monkeypatch, run)
    spec = {"run_entries": ["mmlu:subject=anatomy,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b"]}
    sources = _freeze_run_spec_sources(
        spec, precomputed_root=str(tmp_path),
        model_entries=[{"name": "vllm/allenai-olmo-7b"}],
        lease_facts={"lease_endpoint": "olmo-ep"}, runs=[run],
    )
    assert len(sources) == 1
    s = sources[0]
    assert s["rel_path"] == _REL                          # pinned, root-relative
    assert s["model_deployment"] == "vllm/allenai-olmo-7b"  # the bundle's local name
    assert s["lease_endpoint"] == "olmo-ep"
    assert s["run_entry"] == spec["run_entries"][0]


def test_freeze_uses_inline_token_and_maps_lease(tmp_path, monkeypatch) -> None:
    run = _fixture_run(tmp_path)
    seen = _patch_classify(monkeypatch, run)
    entry = "mmlu:subject=anatomy,model=allenai/olmo-7b,model_deployment=vllm/b"
    sources = _freeze_run_spec_sources(
        {"run_entries": [entry]}, precomputed_root=str(tmp_path),
        model_entries=[{"name": "vllm/a"}, {"name": "vllm/b"}],
        lease_facts={"lease_endpoints": {"vllm/a": "ep-a", "vllm/b": "ep-b"}}, runs=[run],
    )
    # the local token drives the deployment + lease endpoint, and is stripped from
    # the discovery query
    assert sources[0]["model_deployment"] == "vllm/b"
    assert sources[0]["lease_endpoint"] == "ep-b"
    assert "model_deployment" not in seen[0]


def test_freeze_era_multi_endpoint_keys_lease_by_model(tmp_path, monkeypatch) -> None:
    # Finding 10: an era (omit_model_deployment) MULTI-endpoint bundle has no
    # scalar lease_endpoint. The era deployment name equals the official model
    # name, so the lease map is keyed on the run-entry's model= token (previously
    # era used the scalar only, freezing NO lease_endpoint -> endpoint never
    # acquired).
    run = _fixture_run(tmp_path)
    _patch_classify(monkeypatch, run)
    entry = "mmlu:subject=anatomy,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b"
    sources = _freeze_run_spec_sources(
        {"run_entries": [entry]}, precomputed_root=str(tmp_path),
        model_entries=[{"name": "allenai/olmo-7b"}, {"name": "eleutherai/pythia-6.9b"}],
        lease_facts={"lease_endpoints": {"allenai/olmo-7b": "ep-olmo", "eleutherai/pythia-6.9b": "ep-pythia"}},
        runs=[run], omit_model_deployment=True,
    )
    assert "model_deployment" not in sources[0]        # era: verbatim by-name
    assert sources[0]["lease_endpoint"] == "ep-olmo"   # keyed on model=


def test_freeze_era_multi_endpoint_unmapped_model_raises(tmp_path, monkeypatch) -> None:
    # Finding 10: a run whose model= is absent from the lease map (and no single
    # fallback) fails loud rather than silently freezing no endpoint.
    run = _fixture_run(tmp_path)
    _patch_classify(monkeypatch, run)
    entry = "mmlu:subject=anatomy,model=allenai/olmo-7b"
    with pytest.raises(ValueError, match="not in the bundle's lease_endpoints"):
        _freeze_run_spec_sources(
            {"run_entries": [entry]}, precomputed_root=str(tmp_path),
            model_entries=[{"name": "x/a"}, {"name": "y/b"}],
            lease_facts={"lease_endpoints": {"x/a": "ep-a", "y/b": "ep-b"}},
            runs=[run], omit_model_deployment=True,
        )


def test_freeze_raises_on_non_resolved(tmp_path, monkeypatch) -> None:
    run = _fixture_run(tmp_path)
    _patch_classify(monkeypatch, run, status="NO_MATCH")
    with pytest.raises(ValueError, match="NO_MATCH"):
        _freeze_run_spec_sources(
            {"run_entries": ["mmlu:subject=anatomy,model=allenai/olmo-7b"]},
            precomputed_root=str(tmp_path),
            model_entries=[{"name": "vllm/allenai-olmo-7b"}],
            lease_facts=None, runs=[run],
        )


def test_freeze_multi_deployment_without_token_is_error(tmp_path, monkeypatch) -> None:
    run = _fixture_run(tmp_path)
    _patch_classify(monkeypatch, run)
    # 2 deployments but the entry has no inline token → ambiguous rewrite target
    with pytest.raises(ValueError, match="multi-deployment"):
        _freeze_run_spec_sources(
            {"run_entries": ["mmlu:subject=anatomy,model=allenai/olmo-7b"]},
            precomputed_root=str(tmp_path),
            model_entries=[{"name": "vllm/a"}, {"name": "vllm/b"}],
            lease_facts=None, runs=[run],
        )


# --------------------------------------------------------------------------- #
# freeze wiring through the PUBLIC materialize path                            #
# --------------------------------------------------------------------------- #
def test_materialize_threads_freeze_rel_paths_into_manifest(tmp_path, monkeypatch) -> None:
    # Regression guard for the freeze WIRING (not just the helper the tests above
    # call directly): materialize_benchmark_bundle used ``freeze_rel_paths`` in its
    # body but omitted it from its signature, and export_benchmark_bundle never
    # passed it — so every real ``--freeze-rel-paths`` export raised NameError. The
    # helper tests above call _freeze_run_spec_sources directly and so missed it.
    # Drive the public entrypoint and assert run_spec_sources actually lands.
    import yaml

    # R-3: materialize_benchmark_bundle + _assert_helm_aliases_exist live in
    # bundle_export now; patch the alias assertion on its real home so the
    # internal call sees the patch.
    from eval_audit.integrations.infer_stack import bundle_export as BE
    from eval_audit.integrations.infer_stack.adapter import (
        ServingFacts,
        materialize_benchmark_bundle,
    )

    run = _fixture_run(tmp_path)
    _patch_classify(monkeypatch, run)
    monkeypatch.setattr(dc, "_enumerate_runs", lambda root: [run])
    # Keep the test hermetic w.r.t. HELM's alias config.
    monkeypatch.setattr(BE, "_assert_helm_aliases_exist", lambda *a, **k: None)

    result = materialize_benchmark_bundle(
        facts=[ServingFacts(
            endpoint="olmo-ep", served_model_name="olmo-ep",
            hf_model_id="allenai/olmo-7b", max_model_len=2048,
        )],
        output_dir=tmp_path / "bundle",
        profile_specs=[{
            "profile": "olmo-ep",
            "access_kind": "vllm-direct",  # no gateway key needed
            "protocol_mode": "completions",
            "model_deployment_name": "vllm/allenai-olmo-7b",
            "helm_model_name": "allenai/olmo-7b",
            "helm_tokenizer_name": "allenai/olmo-7b",
        }],
        from_run_spec=True,
        precomputed_root=str(tmp_path),
        freeze_rel_paths=True,  # <-- the parameter that used to NameError
        base_url="http://localhost:8000/v1",  # vllm-direct now requires an explicit base_url
    )
    smoke = yaml.safe_load(result["benchmark_smoke_manifest_path"].read_text())
    assert smoke["from_run_spec"] is True
    assert smoke.get("run_spec_sources"), "freeze_rel_paths did not thread into the manifest"
    assert smoke["run_spec_sources"][0]["rel_path"] == _REL
    assert smoke["run_spec_sources"][0]["model_deployment"] == "vllm/allenai-olmo-7b"

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

from eval_audit.cli import check_precomputed_discovery as dc
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

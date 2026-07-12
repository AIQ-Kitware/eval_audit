"""Characterization + consolidation tests for the run-entry model_deployment
token helpers (R-8).

Locks the parse / strip / append behavior of the four previously-duplicated
implementations (lease_bracket._parse_model_deployment,
kwdagger_bridge._locator_run_entry, adapter._strip_local_deployment,
adapter._inline_local_deployment) so consolidating them onto the shared
run_entries utility cannot silently change behavior. Includes the tricky
tokens flagged in the audit: stop=none, groups=, quoted values.
"""
from __future__ import annotations

from eval_audit.pipelines.lease_bracket import _parse_model_deployment
from eval_audit.integrations.kwdagger_bridge import _locator_run_entry
from eval_audit.integrations.infer_stack.adapter import (
    _inline_local_deployment,
    _strip_local_deployment,
)


# --- parse (lease_bracket) ------------------------------------------------
def test_parse_extracts_deployment_token():
    assert _parse_model_deployment(
        "mmlu:subject=anatomy,model=allenai/olmo-7b,model_deployment=vllm/olmo-7b"
    ) == "vllm/olmo-7b"


def test_parse_with_stop_none_and_groups_tokens():
    assert _parse_model_deployment(
        "narrative_qa:model=x,stop=none,groups=foo,model_deployment=kubeai/y"
    ) == "kubeai/y"


def test_parse_returns_none_when_absent():
    assert _parse_model_deployment(
        "commonsense:dataset=openbookqa,model=allenai/olmo-7b"
    ) is None
    assert _parse_model_deployment("") is None
    assert _parse_model_deployment(None) is None


# --- locator strip (kwdagger_bridge, unconditional) -----------------------
def test_locator_strips_any_deployment_token():
    assert _locator_run_entry(
        "mmlu:subject=x,model=y,model_deployment=vllm/z"
    ) == "mmlu:subject=x,model=y"


def test_locator_preserves_stop_none_and_groups():
    assert _locator_run_entry(
        "narrative_qa:model=x,stop=none,groups=foo,model_deployment=z"
    ) == "narrative_qa:model=x,stop=none,groups=foo"


def test_locator_no_scenario_separator_returns_input():
    assert _locator_run_entry("bareword") == "bareword"


def test_locator_only_deployment_token_collapses_to_bench():
    assert _locator_run_entry("mmlu:model_deployment=z") == "mmlu"


# --- conditional strip (adapter, local-only) ------------------------------
def test_strip_local_removes_only_local_names():
    q, tok = _strip_local_deployment(
        "mmlu:subject=x,model=allenai/olmo-7b,model_deployment=vllm/olmo-7b",
        frozenset({"vllm/olmo-7b"}),
    )
    assert q == "mmlu:subject=x,model=allenai/olmo-7b"
    assert tok == "vllm/olmo-7b"


def test_strip_local_keeps_non_local_token():
    entry = "mmlu:model=x,model_deployment=stanfordhealthcare/private"
    q, tok = _strip_local_deployment(entry, frozenset({"vllm/olmo-7b"}))
    assert q == entry
    assert tok is None


def test_strip_local_no_token():
    q, tok = _strip_local_deployment("mmlu:subject=x,model=allenai/olmo-7b", frozenset())
    assert q == "mmlu:subject=x,model=allenai/olmo-7b"
    assert tok is None


# --- append (adapter) -----------------------------------------------------
def test_inline_appends_deployment_token():
    assert _inline_local_deployment(
        ["mmlu:model=x", "boolq:model=y"], "vllm/olmo-7b"
    ) == [
        "mmlu:model=x,model_deployment=vllm/olmo-7b",
        "boolq:model=y,model_deployment=vllm/olmo-7b",
    ]

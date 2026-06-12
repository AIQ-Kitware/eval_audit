"""Phase 3 / 4.9a: the curated judge registry."""
from __future__ import annotations

from eval_audit.judge_registry import (
    OFFICIAL_JUDGE_MODELS_BY_ANNOTATOR,
    OPEN_WEIGHT_JUDGES,
    resolve_judge_models,
)
from eval_audit.normalized.diff import judge_fact_status
from eval_audit.normalized.recipe_facts import RecipeFacts


def test_all_closed_judge_annotators_are_mapped():
    # One entry per closed-judge benchmark annotator (inventory doc).
    assert set(OFFICIAL_JUDGE_MODELS_BY_ANNOTATOR) == {
        "WildBenchAnnotator",
        "OmniMATHAnnotator",
        "HarmBenchAnnotator",
        "AnthropicRedTeamAnnotator",
        "SimpleSafetyTestsAnnotator",
        "XSTestAnnotator",
    }


def test_resolution_expands_class_basenames_and_passes_models_through():
    resolved = resolve_judge_models(("WildBenchAnnotator",))
    assert resolved == (
        "meta/llama-3.1-405b-instruct-turbo",
        "openai/gpt-4o-2024-05-13",
    )
    # Explicit model ids (local re-runs) pass through; unknown class
    # basenames stay visibly unmapped rather than colliding.
    assert resolve_judge_models(("open/judge-1", "MysteryAnnotator")) == (
        "MysteryAnnotator",
        "open/judge-1",
    )
    assert resolve_judge_models(None) is None
    assert resolve_judge_models(()) == ()


def test_open_weight_member_is_flagged():
    assert "meta/llama-3.1-405b-instruct-turbo" in OPEN_WEIGHT_JUDGES
    assert "openai/gpt-4o-2024-05-13" not in OPEN_WEIGHT_JUDGES


def test_same_judge_fact_compares_official_class_to_local_models():
    official = RecipeFacts(source="sidecar", judge_models=("WildBenchAnnotator",))
    # Local re-run that reproduces the official ensemble exactly:
    local_same = RecipeFacts(
        source="sidecar",
        judge_models=(
            "meta/llama-3.1-405b-instruct-turbo",
            "openai/gpt-4o-2024-05-13",
        ),
    )
    # Open-judge substitution: the closed member replaced.
    local_substituted = RecipeFacts(
        source="sidecar",
        judge_models=("meta/llama-3.1-405b-instruct-turbo",),
    )
    assert judge_fact_status(official, local_same) == "yes"
    assert judge_fact_status(official, local_substituted) == "no"
    assert judge_fact_status(official, RecipeFacts(source="sidecar")) == "unknown"

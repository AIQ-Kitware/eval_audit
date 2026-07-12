from __future__ import annotations

from eval_audit.run_entries import (
    BOOKKEEPING_TOKENS,
    canonical_logical_key,
)

# The OLMo MMLU case that motivated the canonical key: the two keys are the
# same token set in a different order, plus the official carries a non-semantic
# groups= token. They must collapse to one canonical form.
OLMO_LOCAL = (
    "mmlu:subject=abstract_algebra,method=multiple_choice_joint,"
    "eval_split=test,model=allenai_olmo-1.7-7b"
)
OLMO_OFFICIAL = (
    "mmlu:subject=abstract_algebra,method=multiple_choice_joint,"
    "model=allenai_olmo-1.7-7b,eval_split=test,groups=mmlu_abstract_algebra"
)


def test_olmo_local_and_official_keys_collapse_to_one_canonical_form():
    assert canonical_logical_key(OLMO_LOCAL) == canonical_logical_key(OLMO_OFFICIAL)
    # And it is the sorted, groups-stripped serialization.
    assert canonical_logical_key(OLMO_OFFICIAL) == (
        "mmlu:eval_split=test,method=multiple_choice_joint,"
        "model=allenai_olmo-1.7-7b,subject=abstract_algebra"
    )


def test_order_invariance():
    a = "mmlu:subject=anatomy,method=mcj,eval_split=test,model=m_x"
    b = "mmlu:model=m_x,eval_split=test,method=mcj,subject=anatomy"
    c = "mmlu:eval_split=test,model=m_x,subject=anatomy,method=mcj"
    assert canonical_logical_key(a) == canonical_logical_key(b) == canonical_logical_key(c)


def test_idempotence():
    once = canonical_logical_key(OLMO_OFFICIAL)
    assert canonical_logical_key(once) == once


def test_bookkeeping_tokens_are_dropped():
    assert BOOKKEEPING_TOKENS == ("groups", "model_deployment")
    with_groups = "mmlu:subject=anatomy,model=m_x,groups=mmlu_anatomy"
    without_groups = "mmlu:subject=anatomy,model=m_x"
    assert canonical_logical_key(with_groups) == canonical_logical_key(without_groups)

    with_deploy = "boolq:model=m_x,model_deployment=local_m_x"
    without_deploy = "boolq:model=m_x"
    assert canonical_logical_key(with_deploy) == canonical_logical_key(without_deploy)


def test_semantic_tokens_are_preserved():
    # eval_split, subject and model all distinguish genuinely different runs.
    base = "mmlu:subject=anatomy,method=mcj,eval_split=test,model=m_x"
    assert "eval_split=test" in canonical_logical_key(base)
    assert "subject=anatomy" in canonical_logical_key(base)
    assert "model=m_x" in canonical_logical_key(base)


def test_slash_and_underscore_model_forms_are_equivalent():
    slash = "boolq:model=meta/llama-3-8b"
    underscore = "boolq:model=meta_llama-3-8b"
    assert canonical_logical_key(slash) == canonical_logical_key(underscore)


def test_mmlu_pro_subject_and_subset_are_equivalent():
    via_subject = "mmlu_pro:subject=math,model=m_x"
    via_subset = "mmlu_pro:subset=math,model=m_x"
    assert canonical_logical_key(via_subject) == canonical_logical_key(via_subset)


def test_keys_without_benchmark_prefix_pass_through():
    assert canonical_logical_key("just-a-name") == "just-a-name"
    assert canonical_logical_key(None) is None
    assert canonical_logical_key("") == ""


# --- Negative controls: distinct runs must NOT collapse ----------------------


def test_different_subject_stays_distinct():
    a = "mmlu:subject=abstract_algebra,method=mcj,eval_split=test,model=m_x"
    b = "mmlu:subject=anatomy,method=mcj,eval_split=test,model=m_x"
    assert canonical_logical_key(a) != canonical_logical_key(b)


def test_different_model_stays_distinct():
    a = "mmlu:subject=anatomy,method=mcj,eval_split=test,model=m_x"
    b = "mmlu:subject=anatomy,method=mcj,eval_split=test,model=m_y"
    assert canonical_logical_key(a) != canonical_logical_key(b)


def test_eval_split_value_keeps_runs_distinct():
    test_split = "mmlu:subject=anatomy,method=mcj,eval_split=test,model=m_x"
    valid_split = "mmlu:subject=anatomy,method=mcj,eval_split=valid,model=m_x"
    assert canonical_logical_key(test_split) != canonical_logical_key(valid_split)


def test_lite_recipe_without_eval_split_is_not_merged_with_full_sweep():
    # The lite recipe omits eval_split; the full sweep sets eval_split=test.
    # Dropping only bookkeeping tokens (not eval_split) keeps them distinct.
    lite = "mmlu:subject=anatomy,method=mcj,model=m_x"
    full = "mmlu:subject=anatomy,method=mcj,eval_split=test,model=m_x"
    assert canonical_logical_key(lite) != canonical_logical_key(full)

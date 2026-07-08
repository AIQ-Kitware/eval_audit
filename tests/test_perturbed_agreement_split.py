"""Regression for ``InstanceStatKey.is_perturbed`` (nested perturbation ids).

The perturbation id is nested under ``InstanceStatKey.variant.perturbation_id``
(or carried as ``stat_perturbation_id``); ``is_perturbed`` must reflect both.

R-2 (2026-07-06): the companion regression that exercised the perturbed vs
unperturbed *agreement split* was removed with
``HelmRunDiff.instance_summary_dict`` — the normalized comparison core does not
split agreement by perturbation, so that surface no longer exists.
"""
from __future__ import annotations

from eval_audit.helm.instance_stats import InstanceStatKey, InstanceVariantKey


def _key(*, instance_id: str, perturbation_id: str | None) -> InstanceStatKey:
    return InstanceStatKey(
        variant=InstanceVariantKey(
            instance_id=instance_id,
            train_trial_index=0,
            perturbation_id=perturbation_id,
        ),
        metric="exact_match",
        split="test",
        sub_split=None,
        stat_perturbation_id=None,
    )


def test_instance_stat_key_is_perturbed_reflects_nested_ids():
    assert _key(instance_id="i0", perturbation_id=None).is_perturbed is False
    assert _key(instance_id="i1", perturbation_id="typos").is_perturbed is True
    # stat-level perturbation also counts
    stat_pert = InstanceStatKey(
        variant=InstanceVariantKey(instance_id="i2", train_trial_index=0, perturbation_id=None),
        metric="exact_match",
        split="test",
        sub_split=None,
        stat_perturbation_id="robustness",
    )
    assert stat_pert.is_perturbed is True

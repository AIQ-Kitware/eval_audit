"""P0-6 regression: perturbed vs unperturbed agreement split in HelmRunDiff.

The perturbation id is nested under ``InstanceStatKey.variant.perturbation_id``
(or carried as ``stat_perturbation_id``). The old code checked a bare
``hasattr(k, 'perturbation_id')`` — always False on the dataclass — so every
row bucketed 'unperturbed' and ``agree_ratio_perturbed`` was always None.
"""
from __future__ import annotations

from types import SimpleNamespace

from eval_audit.helm.diff import HelmRunDiff
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


def _row(mean: float) -> dict:
    return {"stat": {"mean": mean, "count": 1, "name": {"name": "exact_match"}}}


def test_perturbed_and_unperturbed_rows_bucket_separately():
    unpert = _key(instance_id="i0", perturbation_id=None)
    pert = _key(instance_id="i1", perturbation_id="typos")

    # Unperturbed row agrees (1.0 vs 1.0); perturbed row disagrees (1.0 vs 0.0).
    map_a = {unpert: _row(1.0), pert: _row(1.0)}
    map_b = {unpert: _row(1.0), pert: _row(0.0)}

    def _joined(row_by_key):
        table = SimpleNamespace(row_by_key=row_by_key)
        return SimpleNamespace(joined_instance_stat_table=lambda **_: table)

    fake = SimpleNamespace(
        a=_joined(map_a),
        b=_joined(map_b),
        short_hash=8,
        _cache={},
    )

    result = HelmRunDiff.instance_summary_dict(fake, abs_tol=0.0, rel_tol=0.0)
    means = result["means"]

    assert means["agree_ratio_unperturbed"] == 1.0
    # The old code would leave this None (perturbed rows never counted).
    assert means["agree_ratio_perturbed"] == 0.0

"""Canonical display-key construction for HELM display artifacts.

``display_requests.json``, ``display_predictions.json``, and
``per_instance_stats.json`` all identify a unit of evaluation by
``(instance_id, perturbation, train_trial_index)`` (see
``helm.benchmark.presentation.run_display``). Every consumer in the
open-judge pipeline — source audit, snapshot builder, identity replay,
judge analysis — must join on **this** module's key, never an ad hoc
tuple, so that perturbation serialization can never drift between
stages (open-judge-plan.md §6.1).

This module is deliberately JSON-level (plain dicts, no HELM imports):
the source audit must run on hosts without a full HELM install and the
key must be derivable from raw artifact bytes alone.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping


def serialize_perturbation(perturbation: Mapping[str, Any] | None) -> str | None:
    """Canonical string form of a ``PerturbationDescription`` JSON dict.

    ``None`` (the unperturbed original) stays ``None``. Everything else
    becomes compact sorted-key JSON so dict ordering in the source file
    can never produce two spellings of the same perturbation.
    """
    if perturbation is None:
        return None
    return json.dumps(perturbation, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class DisplayKey:
    """The stable join key for one displayed unit of evaluation.

    Not orderable directly (``perturbation`` mixes None and str); sort
    collections of keys with :meth:`sort_tuple`.
    """

    instance_id: str
    perturbation: str | None
    train_trial_index: int

    @classmethod
    def from_entry(cls, entry: Mapping[str, Any]) -> "DisplayKey":
        """Build from a display-artifact entry (request, prediction, or
        per-instance-stats row — they share the field names)."""
        return cls(
            instance_id=str(entry["instance_id"]),
            perturbation=serialize_perturbation(entry.get("perturbation")),
            train_trial_index=int(entry.get("train_trial_index", 0)),
        )

    def sort_tuple(self) -> tuple[str, str, int]:
        """Total-order key (None perturbation sorts first as '')."""
        return (self.instance_id, self.perturbation or "", self.train_trial_index)

    def as_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "perturbation": self.perturbation,
            "train_trial_index": self.train_trial_index,
        }


def instance_key(instance: Mapping[str, Any]) -> tuple[str, str | None]:
    """Identity of an entry in ``instances.json``: ``(id, perturbation)``.

    ``instances.json`` holds post-augmentation instances, so a perturbed
    instance appears as its own row sharing ``id`` with the original.
    """
    return (str(instance.get("id")), serialize_perturbation(instance.get("perturbation")))


__all__ = ["DisplayKey", "instance_key", "serialize_perturbation"]

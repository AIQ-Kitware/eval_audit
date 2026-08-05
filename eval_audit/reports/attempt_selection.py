"""Choose *which* local attempt a packet's reproduction number describes.

A packet pairs one official row with the local run(s) claiming to reproduce
it, and an experiment legitimately accumulates more than one local attempt
for the same official row (a pre-fix attempt and its rerun, a smoke and a
full, two suites covering one subject). The planner emits one
``official_vs_local`` comparison per attempt, all enabled, all peers, so a
rendered ``core_metric_report.json`` can hold *n* answers to "how well did
this row reproduce?" with nothing marking which one is *the* answer
(``docs/helm-gotchas.md`` §G14).

Every reduction over ``pairs[]`` must therefore **select**, never pool: for
``allenai/olmo-7b`` the extra attempt is the tokenizer collapse
(prompt-independent boilerplate, ``exact_match`` 0.000), so pooling halves
the cell exactly — MMLU 0.295/**0.144** pooled against 0.295/**0.287**
selected, from the same artifacts.

Before this module the tree selected three different ways: ``_find_pair``
returned the first matching pair, the aggregate-diff collector keyed off
``ovl_pairs[0]``, and the agreement collectors pooled ``matched``/``count``
across every attempt. Consumers now share :func:`select_official_vs_local`
so the lint's verdict and the report's number refer to the same attempt.

Selection rule, in priority order:

1. ``latest_manifest_timestamp`` — newest local attempt, matching the rule
   the planner already uses to order components
   (``core_report_planner._component_sort_key``), so the analysis layer
   stops disagreeing with the planning layer about which run is *the* run.
2. ``latest_manifest_timestamp:attempt_fallback_key`` — same rule, but the
   timestamp was recovered by parsing ``attempt_fallback_key`` because the
   packet predates the component field being serialized. Weaker provenance,
   named separately so a cited number carries it.
3. ``pair_order`` — first attempt in ``pairs[]``, used when no timestamps
   are available or when they tie. This is the historical behavior, kept as
   a fallback so existing stores stay readable rather than failing closed.

Latest-wins is a defensible convention, not evidence: the newest attempt is
not necessarily the correct one. This module makes the choice deterministic
and self-describing; deciding whether a packet is *safe to cite* is
``eval_audit.cli.lint_store``'s job.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

RULE_NONE = "no_official_vs_local"
RULE_SINGLE = "single_attempt"
RULE_LATEST = "latest_manifest_timestamp"
RULE_LATEST_FROM_FALLBACK = "latest_manifest_timestamp:attempt_fallback_key"
RULE_PAIR_ORDER = "pair_order"

_FALLBACK_TIMESTAMP_RE = re.compile(r"manifest_timestamp=([^|]*)")


def official_vs_local_attempts(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Enabled ``official_vs_local`` comparisons in packet order.

    ``local_repeat`` is deliberately excluded: a repeat is an intentional
    local-vs-local noise measurement, not a rival answer to the same
    question. Rendered reports only contain enabled comparisons (the
    renderer skips disabled ones), but the flag is honored defensively for
    callers passing a planner packet.
    """
    return [
        pair
        for pair in report.get("pairs") or []
        if (pair.get("comparison_kind") or "").strip() == "official_vs_local"
        and pair.get("enabled", True)
    ]


def local_component_id(pair: dict[str, Any]) -> str | None:
    """The local side of a comparison, i.e. the component that is not the reference."""
    reference = pair.get("reference_component_id")
    for component_id in pair.get("component_ids") or []:
        if component_id != reference and str(component_id).startswith("local::"):
            return str(component_id)
    return None


def component_manifest_timestamp(component: dict[str, Any]) -> tuple[float | None, str]:
    """Manifest timestamp for a component, with the provenance of where it came from.

    Returns ``(value, source)`` where source is ``"field"`` (the serialized
    ``manifest_timestamp``), ``"attempt_fallback_key"`` (parsed out of the
    identity string, for packets rendered before the field existed), or
    ``"absent"``.
    """
    direct = component.get("manifest_timestamp")
    if direct not in (None, "", "unknown"):
        try:
            return float(direct), "field"
        except (TypeError, ValueError):
            pass
    match = _FALLBACK_TIMESTAMP_RE.search(str(component.get("attempt_fallback_key") or ""))
    if match:
        try:
            return float(match.group(1)), "attempt_fallback_key"
        except ValueError:
            pass
    return None, "absent"


@dataclass(frozen=True)
class AttemptSelection:
    """Which local attempt a packet's number describes, and how it was chosen."""

    pair: dict[str, Any] | None
    rule: str
    n_candidates: int
    selected_component_id: str | None
    selected_comparison_id: str | None
    dropped_comparison_ids: list[str]

    @property
    def is_ambiguous(self) -> bool:
        """True when a choice was actually made among competing attempts."""
        return self.n_candidates > 1

    def as_provenance(self) -> dict[str, Any]:
        """Machine-readable record to embed beside any number read from the packet."""
        return {
            "selection_rule": self.rule,
            "n_official_vs_local_attempts": self.n_candidates,
            "selected_comparison_id": self.selected_comparison_id,
            "selected_local_component_id": self.selected_component_id,
            "dropped_comparison_ids": list(self.dropped_comparison_ids),
        }


def _components_by_id(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lookup = report.get("component_lookup")
    if isinstance(lookup, dict) and lookup:
        return lookup
    return {
        str(component.get("component_id")): component
        for component in report.get("components") or []
        if component.get("component_id")
    }


def _selection(pair: dict[str, Any] | None, rule: str, attempts: list[dict[str, Any]]) -> AttemptSelection:
    chosen_id = (pair or {}).get("comparison_id")
    return AttemptSelection(
        pair=pair,
        rule=rule,
        n_candidates=len(attempts),
        selected_component_id=local_component_id(pair) if pair else None,
        selected_comparison_id=str(chosen_id) if chosen_id else None,
        dropped_comparison_ids=[
            str(other.get("comparison_id"))
            for other in attempts
            if other is not pair and other.get("comparison_id")
        ],
    )


def select_official_vs_local(report: dict[str, Any]) -> AttemptSelection:
    """Select the one ``official_vs_local`` attempt a packet's number describes."""
    attempts = official_vs_local_attempts(report)
    if not attempts:
        return _selection(None, RULE_NONE, attempts)
    if len(attempts) == 1:
        return _selection(attempts[0], RULE_SINGLE, attempts)

    components = _components_by_id(report)
    stamped: list[tuple[float, str, dict[str, Any]]] = []
    for pair in attempts:
        component = components.get(local_component_id(pair) or "", {})
        timestamp, source = component_manifest_timestamp(component)
        if timestamp is None:
            stamped = []
            break
        stamped.append((timestamp, source, pair))

    if stamped:
        values = [timestamp for timestamp, _, _ in stamped]
        # A tie carries no information about which attempt is newer, so it
        # falls through to pair order rather than being broken arbitrarily
        # under a rule name that claims otherwise.
        if len(set(values)) == len(values):
            latest = max(stamped, key=lambda item: item[0])
            rule = (
                RULE_LATEST
                if all(source == "field" for _, source, _ in stamped)
                else RULE_LATEST_FROM_FALLBACK
            )
            return _selection(latest[2], rule, attempts)

    return _selection(attempts[0], RULE_PAIR_ORDER, attempts)

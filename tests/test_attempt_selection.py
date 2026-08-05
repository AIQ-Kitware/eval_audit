"""Selecting which local attempt a packet's number describes (G14)."""
from __future__ import annotations

from typing import Any

from eval_audit.reports.attempt_selection import (
    RULE_LATEST,
    RULE_LATEST_FROM_FALLBACK,
    RULE_NONE,
    RULE_PAIR_ORDER,
    RULE_SINGLE,
    component_manifest_timestamp,
    local_component_id,
    official_vs_local_attempts,
    select_official_vs_local,
)

OFFICIAL = "official::lite::v1.2.0::mmlu:subject=anatomy"


def _pair(local_id: str, kind: str = "official_vs_local", **extra: Any) -> dict[str, Any]:
    return {
        "comparison_id": f"cmp::{kind}::{local_id}",
        "comparison_kind": kind,
        "reference_component_id": OFFICIAL,
        "component_ids": [OFFICIAL, local_id],
        **extra,
    }


def _component(local_id: str, *, timestamp: str | None, in_field: bool) -> dict[str, Any]:
    component = {"component_id": local_id, "source_kind": "local"}
    if timestamp is None:
        return component
    if in_field:
        component["manifest_timestamp"] = timestamp
    else:
        component["attempt_fallback_key"] = (
            f"fallback::experiment_name=exp|job_id=j|run_entry=r|"
            f"manifest_timestamp={timestamp}|machine_host=h|run_dir=/d"
        )
    return component


def _report(pairs: list[dict[str, Any]], components: list[dict[str, Any]]) -> dict[str, Any]:
    return {"pairs": pairs, "components": [*components, {"component_id": OFFICIAL, "source_kind": "official"}]}


def test_no_official_vs_local_pair_selects_nothing() -> None:
    selection = select_official_vs_local(_report([_pair("local::a", kind="local_repeat")], []))
    assert selection.pair is None
    assert selection.rule == RULE_NONE
    assert selection.is_ambiguous is False


def test_single_attempt_needs_no_rule() -> None:
    selection = select_official_vs_local(_report([_pair("local::a")], []))
    assert selection.rule == RULE_SINGLE
    assert selection.selected_component_id == "local::a"
    assert selection.dropped_comparison_ids == []
    assert selection.is_ambiguous is False


def test_local_repeat_is_never_a_candidate() -> None:
    """A repeat is an intentional noise measurement, not a rival answer."""
    report = _report([_pair("local::a"), _pair("local::b", kind="local_repeat")], [])
    assert len(official_vs_local_attempts(report)) == 1
    assert select_official_vs_local(report).rule == RULE_SINGLE


def test_latest_manifest_timestamp_wins_over_pair_order() -> None:
    """The olmo-7b shape: the collapsed attempt is emitted first, the rerun second."""
    report = _report(
        [_pair("local::collapsed"), _pair("local::rerun")],
        [
            _component("local::collapsed", timestamp="1782786416.70", in_field=True),
            _component("local::rerun", timestamp="1783021451.02", in_field=True),
        ],
    )
    selection = select_official_vs_local(report)
    assert selection.rule == RULE_LATEST
    assert selection.selected_component_id == "local::rerun"
    assert selection.dropped_comparison_ids == ["cmp::official_vs_local::local::collapsed"]
    assert selection.is_ambiguous is True


def test_timestamp_recovered_from_fallback_key_is_named_separately() -> None:
    """Packets rendered before manifest_timestamp was serialized still sort."""
    report = _report(
        [_pair("local::old"), _pair("local::new")],
        [
            _component("local::old", timestamp="1782786416.70", in_field=False),
            _component("local::new", timestamp="1783021451.02", in_field=False),
        ],
    )
    selection = select_official_vs_local(report)
    assert selection.rule == RULE_LATEST_FROM_FALLBACK
    assert selection.selected_component_id == "local::new"


def test_mixed_timestamp_provenance_reports_the_weaker_one() -> None:
    report = _report(
        [_pair("local::old"), _pair("local::new")],
        [
            _component("local::old", timestamp="1782786416.70", in_field=False),
            _component("local::new", timestamp="1783021451.02", in_field=True),
        ],
    )
    assert select_official_vs_local(report).rule == RULE_LATEST_FROM_FALLBACK


def test_missing_timestamp_falls_back_to_pair_order() -> None:
    """Existing stores stay readable rather than failing closed."""
    report = _report(
        [_pair("local::a"), _pair("local::b")],
        [
            _component("local::a", timestamp="1783021451.02", in_field=True),
            _component("local::b", timestamp=None, in_field=True),
        ],
    )
    selection = select_official_vs_local(report)
    assert selection.rule == RULE_PAIR_ORDER
    assert selection.selected_component_id == "local::a"
    assert selection.is_ambiguous is True


def test_tied_timestamps_fall_back_rather_than_break_the_tie_silently() -> None:
    report = _report(
        [_pair("local::a"), _pair("local::b")],
        [
            _component("local::a", timestamp="1783021451.02", in_field=True),
            _component("local::b", timestamp="1783021451.02", in_field=True),
        ],
    )
    assert select_official_vs_local(report).rule == RULE_PAIR_ORDER


def test_unknown_timestamp_sentinel_is_not_a_value() -> None:
    component = {"component_id": "local::a", "manifest_timestamp": "unknown"}
    assert component_manifest_timestamp(component) == (None, "absent")


def test_timestamp_field_beats_fallback_key() -> None:
    component = _component("local::a", timestamp="1", in_field=False)
    component["manifest_timestamp"] = "2"
    assert component_manifest_timestamp(component) == (2.0, "field")


def test_local_component_id_ignores_the_official_side() -> None:
    assert local_component_id(_pair("local::a")) == "local::a"
    assert local_component_id({"component_ids": [OFFICIAL], "reference_component_id": OFFICIAL}) is None


def test_disabled_comparisons_are_not_candidates() -> None:
    report = _report([_pair("local::a"), _pair("local::b", enabled=False)], [])
    assert select_official_vs_local(report).rule == RULE_SINGLE


def test_provenance_record_carries_the_choice() -> None:
    report = _report(
        [_pair("local::collapsed"), _pair("local::rerun")],
        [
            _component("local::collapsed", timestamp="1", in_field=True),
            _component("local::rerun", timestamp="2", in_field=True),
        ],
    )
    provenance = select_official_vs_local(report).as_provenance()
    assert provenance == {
        "selection_rule": RULE_LATEST,
        "n_official_vs_local_attempts": 2,
        "selected_comparison_id": "cmp::official_vs_local::local::rerun",
        "selected_local_component_id": "local::rerun",
        "dropped_comparison_ids": ["cmp::official_vs_local::local::collapsed"],
    }

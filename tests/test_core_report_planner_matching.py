"""Order-insensitive logical-key matching in ``build_packet_intents``.

These exercise the *load-bearing* grouping site directly (no EEE conversion,
so they run by default — unlike ``test_plan_core_report_packets.py`` which
goes through the slow ``build_planning_artifact`` path).

Characterization: the OLMo MMLU pair (same token set, different order, plus a
non-semantic ``groups=`` token on the official) used to land in two separate
packets, leaving the official_vs_local comparison disabled with
``missing_official_component``. With the canonical key it pairs into one packet
with an enabled comparison. Negative controls guard against over-matching.
"""
from __future__ import annotations

from eval_audit.planning import core_report_planner
from eval_audit.planning.core_report_planner import NormalizedPlannerComponent


def _component(
    *,
    source_kind: str,
    component_id: str,
    logical_run_key: str,
    experiment_name: str | None = "olmo-exp",
    model: str = "allenai/olmo-1.7-7b",
    public_track: str | None = "mmlu",
    suite_version: str | None = "v1.0.0",
) -> NormalizedPlannerComponent:
    is_local = source_kind == "local"
    return NormalizedPlannerComponent(
        component_id=component_id,
        source_kind=source_kind,
        logical_run_key=logical_run_key,
        run_entry=logical_run_key if is_local else None,
        run_path=f"/tmp/{component_id}",
        job_path=None,
        run_spec_fpath=None,
        run_spec_name=logical_run_key,
        model=model,
        scenario_class="helm.MMLUScenario",
        benchmark_group="mmlu",
        model_deployment=None,
        max_eval_instances="100",
        suite="olmo-suite" if is_local else None,
        public_track=None if is_local else public_track,
        suite_version=None if is_local else suite_version,
        experiment_name=experiment_name if is_local else None,
        machine_host="host" if is_local else None,
        attempt_uuid="uuid-" + component_id if is_local else None,
        attempt_identity=component_id,
        display_name=component_id,
        tags=[source_kind],
        manifest_timestamp="10",
        provenance={},
        extra_metadata={"run_name": logical_run_key} if not is_local else {},
        artifact_format="helm",
        eee_artifact_path=None,
    )


# Same token set, different order; official additionally carries a groups= token.
LOCAL_KEY = (
    "mmlu:subject=abstract_algebra,method=multiple_choice_joint,"
    "eval_split=test,model=allenai_olmo-1.7-7b"
)
OFFICIAL_KEY = (
    "mmlu:subject=abstract_algebra,method=multiple_choice_joint,"
    "model=allenai_olmo-1.7-7b,eval_split=test,groups=mmlu_abstract_algebra"
)


def _official_vs_local(packet: dict) -> list[dict]:
    return [
        comparison
        for comparison in packet["comparisons"]
        if comparison["comparison_kind"] == "official_vs_local"
    ]


def test_olmo_pair_lands_in_one_packet_with_enabled_comparison():
    local = _component(
        source_kind="local",
        component_id="local-aa",
        logical_run_key=LOCAL_KEY,
    )
    official = _component(
        source_kind="official",
        component_id="official-aa",
        logical_run_key=OFFICIAL_KEY,
    )

    packets = core_report_planner.build_packet_intents(
        [local, official], experiment_name="olmo-exp", run_entry=None
    )

    assert len(packets) == 1
    packet = packets[0]
    assert {component["source_kind"] for component in packet["components"]} == {
        "local",
        "official",
    }
    comparisons = _official_vs_local(packet)
    assert comparisons
    assert all(comparison["enabled"] for comparison in comparisons)
    assert not any(
        comparison.get("disabled_reason") == "missing_official_component"
        for comparison in comparisons
    )


def test_canonicalization_diagnostic_surfaces_original_keys():
    local = _component(source_kind="local", component_id="local-aa", logical_run_key=LOCAL_KEY)
    official = _component(source_kind="official", component_id="official-aa", logical_run_key=OFFICIAL_KEY)

    packet = core_report_planner.build_packet_intents(
        [local, official], experiment_name="olmo-exp", run_entry=None
    )[0]

    # The generalized diagnostic reports which raw keys merged into the
    # canonical group; the old groups-only label is gone.
    canonicalized = [w for w in packet["warnings"] if w.startswith("keys_canonicalized:")]
    assert canonicalized
    assert not any("canonicalization_stripped_groups" in w for w in packet["warnings"])


# --- Negative controls: distinct runs must NOT merge -------------------------


def test_distinct_subjects_pair_independently_without_cross_contamination():
    aa_key_local = LOCAL_KEY
    aa_key_official = OFFICIAL_KEY
    an_key_local = (
        "mmlu:subject=anatomy,method=multiple_choice_joint,"
        "eval_split=test,model=allenai_olmo-1.7-7b"
    )
    an_key_official = (
        "mmlu:method=multiple_choice_joint,model=allenai_olmo-1.7-7b,"
        "eval_split=test,subject=anatomy,groups=mmlu_anatomy"
    )

    packets = core_report_planner.build_packet_intents(
        [
            _component(source_kind="local", component_id="local-aa", logical_run_key=aa_key_local),
            _component(source_kind="local", component_id="local-an", logical_run_key=an_key_local),
            _component(source_kind="official", component_id="official-aa", logical_run_key=aa_key_official),
            _component(source_kind="official", component_id="official-an", logical_run_key=an_key_official),
        ],
        experiment_name="olmo-exp",
        run_entry=None,
    )

    assert len(packets) == 2
    # Each packet must pair its own subject's local+official, never cross over.
    for packet in packets:
        component_ids = {component["component_id"] for component in packet["components"]}
        if "local-aa" in component_ids:
            assert component_ids == {"local-aa", "official-aa"}
        else:
            assert component_ids == {"local-an", "official-an"}
        assert all(comparison["enabled"] for comparison in _official_vs_local(packet))


def test_lite_recipe_is_not_merged_with_full_sweep():
    # Lite omits eval_split; the full sweep sets eval_split=test. They must not
    # collapse: the lite local must stay unpaired with the full-sweep official.
    lite_local = "mmlu:subject=anatomy,method=multiple_choice_joint,model=allenai_olmo-1.7-7b"
    full_official = (
        "mmlu:subject=anatomy,method=multiple_choice_joint,"
        "eval_split=test,model=allenai_olmo-1.7-7b,groups=mmlu_anatomy"
    )

    packets = core_report_planner.build_packet_intents(
        [
            _component(source_kind="local", component_id="local-lite", logical_run_key=lite_local),
            _component(source_kind="official", component_id="official-full", logical_run_key=full_official),
        ],
        experiment_name="olmo-exp",
        run_entry=None,
    )

    # The lite local forms its own packet with no official counterpart.
    lite_packets = [
        packet
        for packet in packets
        if any(component["component_id"] == "local-lite" for component in packet["components"])
    ]
    assert len(lite_packets) == 1
    lite_packet = lite_packets[0]
    assert all(
        component["source_kind"] == "local" for component in lite_packet["components"]
    )
    assert all(
        comparison.get("disabled_reason") == "missing_official_component"
        for comparison in _official_vs_local(lite_packet)
    )

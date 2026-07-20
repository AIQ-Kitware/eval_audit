"""Commit 2 (open-judge-plan §7.3): reconstruction fidelity from
published display artifacts through HELM's own codec.

The snapshot builder must hand later stages request states that carry
exactly the facts the benchmark annotators consume: chat messages and
checklists for WildBench, thinking text as ``Thinking``, perturbed
instances joined by the canonical display key.
"""

from __future__ import annotations

from pathlib import Path

from judging_fixture_lib import build_wildbench_source_run, build_xstest_source_run, write_json

from eval_audit.judging import response_snapshot as snap


def test_wildbench_reconstruction_preserves_annotator_inputs(tmp_path: Path):
    run_dpath = tmp_path / "src"
    artifacts = build_wildbench_source_run(run_dpath, empty_output_index=1)
    result = snap.build_response_snapshot(run_dpath, tmp_path / "snapshots")
    state = snap.load_snapshot_scenario_state(result.snapshot_dpath)

    normal = state.request_states[0]
    source_instance = artifacts["instances.json"][0]
    # The WildBench annotator reads input.messages and extra_data.checklist.
    assert normal.instance.input.messages == source_instance["input"]["messages"]
    assert normal.instance.extra_data is not None
    assert normal.instance.extra_data["checklist"] == source_instance["extra_data"]["checklist"]
    assert normal.result is not None
    assert normal.result.completions[0].text == "Synthetic candidate haiku 0."

    # The empty-output candidate must stay empty (official annotators
    # branch on it), not be dropped or padded.
    empty = state.request_states[1]
    assert empty.result is not None
    assert empty.result.completions[0].text == ""


def test_thinking_text_reconstructs_as_thinking(tmp_path: Path):
    run_dpath = tmp_path / "src"
    artifacts = build_xstest_source_run(run_dpath)
    predictions = artifacts["display_predictions.json"]
    predictions[0]["thinking_text"] = "synthetic chain of thought"
    write_json(run_dpath / "display_predictions.json", predictions)

    result = snap.build_response_snapshot(run_dpath, tmp_path / "snapshots")
    state = snap.load_snapshot_scenario_state(result.snapshot_dpath)
    with_thinking = state.request_states[0]
    assert with_thinking.result is not None
    thinking = with_thinking.result.completions[0].thinking
    assert thinking is not None and thinking.text == "synthetic chain of thought"
    without_thinking = state.request_states[1]
    assert without_thinking.result is not None
    assert without_thinking.result.completions[0].thinking is None
    # Thinking text is part of snapshot identity (§7.2).
    plain_hash = snap.build_response_snapshot(
        (lambda d: (build_xstest_source_run(d), d)[1])(tmp_path / "plain"),
        tmp_path / "snapshots2",
    ).response_set_hash
    assert result.response_set_hash != plain_hash


def test_perturbed_instances_join_by_display_key(tmp_path: Path):
    run_dpath = tmp_path / "src"
    artifacts = build_xstest_source_run(run_dpath)
    perturbation = {"name": "lowercase", "robustness": True, "fairness": False}
    instances = artifacts["instances.json"]
    perturbed_instance = dict(instances[0])
    perturbed_instance["perturbation"] = perturbation
    perturbed_instance["input"] = {"text": instances[0]["input"]["text"].lower()}
    instances.append(perturbed_instance)
    requests = artifacts["display_requests.json"]
    perturbed_request = dict(requests[0])
    perturbed_request["perturbation"] = perturbation
    requests.append(perturbed_request)
    predictions = artifacts["display_predictions.json"]
    perturbed_prediction = dict(predictions[0])
    perturbed_prediction["perturbation"] = perturbation
    predictions.append(perturbed_prediction)
    for fname in ("instances.json", "display_requests.json", "display_predictions.json"):
        write_json(run_dpath / fname, artifacts[fname])

    result = snap.build_response_snapshot(run_dpath, tmp_path / "snapshots")
    state = snap.load_snapshot_scenario_state(result.snapshot_dpath)
    assert len(state.request_states) == 4
    perturbed_states = [
        rs for rs in state.request_states if rs.instance.perturbation is not None
    ]
    assert len(perturbed_states) == 1
    assert perturbed_states[0].instance.perturbation.name == "lowercase"
    assert perturbed_states[0].instance.input.text == instances[0]["input"]["text"].lower()

"""Execution-time reporting of runs that add a competing local attempt (G14)."""
from __future__ import annotations

from pathlib import Path

import pytest

from eval_audit.workflows.attempt_collision import (
    diff_attempts,
    preexisting_collisions,
    report_attempt_collisions,
    scan_experiment_attempts,
)

OLMO_7B = "commonsense:dataset=openbookqa,method=multiple_choice_joint,model=allenai_olmo-7b"


def _make_run(root: Path, job_id: str, run_name: str, *, suite: str = "suite", done: bool = True) -> None:
    job_dpath = root / "helm" / job_id
    run_dpath = job_dpath / "benchmark_output" / "runs" / suite / run_name
    run_dpath.mkdir(parents=True, exist_ok=True)
    (run_dpath / "run_spec.json").write_text("{}")
    if done:
        (job_dpath / "DONE").write_text("")


def test_scan_groups_attempts_by_canonical_run_key(tmp_path: Path) -> None:
    """Two jobs at the same recipe are two attempts, not two run entries."""
    _make_run(tmp_path, "helm_id_a", OLMO_7B)
    _make_run(tmp_path, "helm_id_b", OLMO_7B)
    _make_run(tmp_path, "helm_id_c", "gsm:model=allenai_olmo-7b")

    attempts = scan_experiment_attempts(tmp_path)
    assert len(attempts) == 2
    assert {a.job_id for a in attempts[OLMO_7B]} == {"helm_id_a", "helm_id_b"}


def test_scan_matches_across_token_order(tmp_path: Path) -> None:
    """The same recipe is written with different token order in different places."""
    _make_run(tmp_path, "helm_id_a", OLMO_7B)
    _make_run(
        tmp_path,
        "helm_id_b",
        "commonsense:method=multiple_choice_joint,dataset=openbookqa,model=allenai_olmo-7b",
    )
    attempts = scan_experiment_attempts(tmp_path)
    assert len(attempts) == 1
    assert len(next(iter(attempts.values()))) == 2


def test_scan_ignores_helm_sibling_directories(tmp_path: Path) -> None:
    """``eval_cache`` sits beside the run dirs; counting it makes every job an attempt."""
    _make_run(tmp_path, "helm_id_a", OLMO_7B)
    _make_run(tmp_path, "helm_id_b", "gsm:model=allenai_olmo-7b")
    for job_id in ["helm_id_a", "helm_id_b"]:
        (tmp_path / "helm" / job_id / "benchmark_output" / "runs" / "suite" / "eval_cache").mkdir()

    attempts = scan_experiment_attempts(tmp_path)
    assert set(attempts) == {OLMO_7B, "gsm:model=allenai_olmo-7b"}


def test_scan_of_missing_experiment_is_empty(tmp_path: Path) -> None:
    assert scan_experiment_attempts(tmp_path / "never-ran") == {}


def test_resume_reports_nothing(tmp_path: Path) -> None:
    """A skipped run entry creates no new attempt, so a resume must stay silent.

    This is why detection is a before/after diff: predicting collisions ahead of
    the run would mean guessing kwdagger's skip decision, and would fire here.
    """
    _make_run(tmp_path, "helm_id_a", OLMO_7B)
    before = scan_experiment_attempts(tmp_path)
    _make_run(tmp_path, "helm_id_new", "gsm:model=allenai_olmo-7b")  # the entry that hadn't finished
    after = scan_experiment_attempts(tmp_path)

    assert diff_attempts(before, after) == []


def test_changed_recipe_rerun_is_reported_with_both_job_ids(tmp_path: Path) -> None:
    """The olmo shape: a tokenizer fix mints a new job under the same experiment."""
    _make_run(tmp_path, "helm_id_crhpo3xjbill", OLMO_7B)
    before = scan_experiment_attempts(tmp_path)
    _make_run(tmp_path, "helm_id_xt4ikh5hgbfo", OLMO_7B)
    after = scan_experiment_attempts(tmp_path)

    collisions = diff_attempts(before, after)
    assert len(collisions) == 1
    assert collisions[0].logical_key == OLMO_7B
    assert collisions[0].prior_job_ids == ["helm_id_crhpo3xjbill"]
    assert collisions[0].added_job_ids == ["helm_id_xt4ikh5hgbfo"]
    assert collisions[0].n_attempts == 2


def test_first_attempt_at_a_new_entry_is_not_a_collision(tmp_path: Path) -> None:
    before = scan_experiment_attempts(tmp_path)
    _make_run(tmp_path, "helm_id_a", OLMO_7B)
    assert diff_attempts(before, scan_experiment_attempts(tmp_path)) == []


def test_preexisting_duplicates_are_reported_separately_from_new_ones(tmp_path: Path) -> None:
    """Duplicates carried through an untouched run are not this run's news."""
    _make_run(tmp_path, "helm_id_a", OLMO_7B)
    _make_run(tmp_path, "helm_id_b", OLMO_7B)
    before = scan_experiment_attempts(tmp_path)

    assert [c.logical_key for c in preexisting_collisions(before)] == [OLMO_7B]

    record = report_attempt_collisions("exp", before, before)
    assert record["collisions_added"] == []
    assert [c["logical_key"] for c in record["collisions_preexisting"]] == [OLMO_7B]


def test_strict_stops_on_a_new_collision_but_not_on_a_carried_one(tmp_path: Path) -> None:
    """Rerunning stays legal; strict only chooses how loudly a real event reports."""
    _make_run(tmp_path, "helm_id_a", OLMO_7B)
    _make_run(tmp_path, "helm_id_b", OLMO_7B)
    before = scan_experiment_attempts(tmp_path)
    # A run that touched nothing must not fail, even though duplicates exist.
    report_attempt_collisions("exp", before, before, strict=True)

    _make_run(tmp_path, "helm_id_c", OLMO_7B)
    after = scan_experiment_attempts(tmp_path)
    with pytest.raises(SystemExit):
        report_attempt_collisions("exp", before, after, strict=True)

    # Without strict the same situation is reported and the caller continues.
    record = report_attempt_collisions("exp", before, after)
    assert record["collisions_added"][0]["added_job_ids"] == ["helm_id_c"]
    assert record["collisions_added"][0]["n_attempts"] == 3

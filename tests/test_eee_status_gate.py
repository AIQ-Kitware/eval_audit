"""P1-7 regression: cached/local EEE resolvers must gate on status.json == ok,
not merely on an aggregate being present (a partially-failed conversion can
leave an aggregate on disk with a non-ok status)."""
from __future__ import annotations

import json
from pathlib import Path

from eval_audit.normalized.eee_artifacts import _status_permits_use


def _mk(tmp_path: Path, status: str | None) -> Path:
    sp = tmp_path / "status.json"
    if status is not None:
        sp.write_text(json.dumps({"status": status}))
    return sp


def test_absent_status_is_legacy_permitted(tmp_path):
    assert _status_permits_use(tmp_path / "status.json") is True


def test_ok_status_permitted(tmp_path):
    assert _status_permits_use(_mk(tmp_path, "ok")) is True


def test_non_ok_status_rejected(tmp_path):
    assert _status_permits_use(_mk(tmp_path, "error")) is False
    assert _status_permits_use(_mk(tmp_path, "partial")) is False


def test_corrupt_status_rejected(tmp_path):
    sp = tmp_path / "status.json"
    sp.write_text("{ not valid json")
    assert _status_permits_use(sp) is False


def test_local_resolver_rejects_aggregate_with_failed_status(tmp_path, monkeypatch):
    from eval_audit.normalized import eee_artifacts

    parent = tmp_path / "run-parent"
    art = parent / "eee_output"
    art.mkdir(parents=True)
    (art / "aggregate.json").write_text("{}")  # aggregate present
    (parent / "status.json").write_text(json.dumps({"status": "error"}))

    monkeypatch.setattr(
        eee_artifacts, "local_eee_parent_for_row", lambda row, local_eee_root=None: parent
    )
    monkeypatch.setattr(eee_artifacts, "_explicit_eee_resolution", lambda row: None)

    res = eee_artifacts.resolve_local_eee_artifact({}, ensure=False)
    # Aggregate present but status=error -> must NOT be reported as found.
    assert res.status == "missing"


def test_shared_aggregate_predicate_excludes_all_sidecars():
    """R-5: the single is_aggregate_json_name predicate excludes every fixed
    sidecar plus *_samples.json, so a real aggregate is the only positive."""
    from eval_audit.normalized.recipe_facts import is_aggregate_json_name

    for name in (
        "provenance.json",
        "status.json",
        "run_spec.json",
        "fixture_manifest.json",
        "abc123_samples.json",
    ):
        assert is_aggregate_json_name(name) is False, name
    assert is_aggregate_json_name("abc123.json") is True


def test_sidecar_only_dir_is_not_an_aggregate(tmp_path):
    """R-5: a directory containing only run_spec.json (a HELM sidecar, no EEE
    aggregate) must not count as having an aggregate. The pre-R-5
    _artifact_has_aggregate excluded only status/provenance, so run_spec.json
    slipped through."""
    from eval_audit.normalized.eee_artifacts import _artifact_has_aggregate
    from eval_audit.normalized.recipe_facts import artifact_has_aggregate

    art = tmp_path / "sidecar_only"
    art.mkdir()
    (art / "run_spec.json").write_text("{}")
    assert _artifact_has_aggregate(art) is False
    assert artifact_has_aggregate(art) is False

    # Add a real aggregate; now it counts.
    (art / "abc123.json").write_text("{}")
    assert _artifact_has_aggregate(art) is True

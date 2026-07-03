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

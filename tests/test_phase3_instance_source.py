"""Phase 3 sub-stage 4.5 (loader half): instance-source explicitness.

The F6 probe from the behavior-equivalence matrix §6, at the loader
level: an EEE artifact whose ``Origin.helm_run_path`` exists and whose
EEE-derived vs HELM-derived instance ids deliberately disagree. Pins:

1. policy ``eee-only``: instances come only from EEE; deleting the
   HELM dir changes nothing (no disk-state sensitivity);
2. policy ``helm-preferred``: instances come from HELM, recorded as
   ``instance_source='helm'``;
3. ``helm-preferred`` with a recorded-but-unreadable HELM origin
   degrades to EEE instances with the degradation recorded, never a
   silent number change or a crash;
4. the deprecated ``EVAL_AUDIT_EEE_STRICT`` env override still maps to
   ``eee-only``; the implicit default remains ``helm-preferred`` (the
   legacy enriched behavior, now recorded).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from eval_audit.normalized.loaders import INSTANCE_SOURCE_POLICY_KEY, LoaderError, load_run
from eval_audit.normalized.model import NormalizedRunRef, SourceKind


REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_ARTIFACT = (
    REPO_ROOT / "tests" / "fixtures" / "eee_only_demo" / "eee_artifacts"
    / "local" / "primary" / "imdb" / "toy" / "m1-small"
)
#: The demo artifact's EEE-side sample ids (imdb/0..3).
EEE_IDS = {"imdb/0", "imdb/1", "imdb/2", "imdb/3"}


def _write_eee_artifact(artifact_dir: Path) -> None:
    """Stage the committed demo artifact (a real, schema-valid EEE tree)."""
    if not DEMO_ARTIFACT.exists():
        pytest.skip(f"EEE demo fixture missing: {DEMO_ARTIFACT}")
    shutil.copytree(DEMO_ARTIFACT, artifact_dir)


def _write_helm_run(run_dir: Path) -> None:
    """Minimal HELM run with instance ids that DIFFER from the EEE side."""
    run_dir.mkdir(parents=True)
    per_instance = [
        {
            "instance_id": f"H{i}",
            "stats": [
                {"name": {"name": "exact_match"}, "mean": float(i % 2), "count": 1}
            ],
        }
        for i in range(4)
    ]
    (run_dir / "per_instance_stats.json").write_text(json.dumps(per_instance))


@pytest.fixture
def probe(tmp_path: Path) -> dict[str, Path]:
    artifact_dir = tmp_path / "eee_artifact"
    helm_dir = tmp_path / "helm_run"
    _write_eee_artifact(artifact_dir)
    _write_helm_run(helm_dir)
    return {"artifact": artifact_dir, "helm": helm_dir, "tmp": tmp_path}


def _load(probe, *, policy: str | None, helm_origin: bool = True):
    extra = {INSTANCE_SOURCE_POLICY_KEY: policy} if policy else {}
    ref = NormalizedRunRef.from_eee_artifact(
        probe["artifact"],
        source_kind=SourceKind.LOCAL,
        helm_run_path=probe["helm"] if helm_origin else None,
        extra=extra,
    )
    return load_run(ref)


def _sample_ids(run) -> set[str]:
    return {rec.sample_id for rec in run.instances}


def test_eee_only_uses_eee_instances_and_ignores_disk_state(probe, monkeypatch):
    monkeypatch.delenv("EVAL_AUDIT_EEE_STRICT", raising=False)
    run = _load(probe, policy="eee-only")
    assert _sample_ids(run) == EEE_IDS
    assert run.ref.extra["instance_source"] == "eee"
    assert run.ref.extra["instance_source_policy"] == "eee-only"

    # Deleting the HELM dir must change nothing under eee-only.
    shutil.rmtree(probe["helm"])
    run2 = _load(probe, policy="eee-only")
    assert _sample_ids(run2) == _sample_ids(run)
    assert [r.score for r in run2.instances] == [r.score for r in run.instances]


def test_helm_preferred_uses_helm_instances_and_records_it(probe, monkeypatch):
    monkeypatch.delenv("EVAL_AUDIT_EEE_STRICT", raising=False)
    run = _load(probe, policy="helm-preferred")
    assert _sample_ids(run) == {"H0", "H1", "H2", "H3"}
    assert run.ref.extra["instance_source"] == "helm"
    assert run.ref.extra["instance_source_policy"] == "helm-preferred"


def test_helm_preferred_with_unreadable_origin_degrades_recorded(probe, monkeypatch):
    monkeypatch.delenv("EVAL_AUDIT_EEE_STRICT", raising=False)
    shutil.rmtree(probe["helm"])
    run = _load(probe, policy="helm-preferred")  # origin recorded, dir gone
    assert _sample_ids(run) == EEE_IDS
    assert run.ref.extra["instance_source"] == "eee"
    assert "instance_source_note" in run.ref.extra


def test_helm_preferred_without_origin_is_plain_eee(probe, monkeypatch):
    monkeypatch.delenv("EVAL_AUDIT_EEE_STRICT", raising=False)
    run = _load(probe, policy="helm-preferred", helm_origin=False)
    assert _sample_ids(run) == EEE_IDS
    assert run.ref.extra["instance_source"] == "eee"


def test_env_strict_maps_to_eee_only_and_default_is_helm_preferred(probe, monkeypatch):
    monkeypatch.setenv("EVAL_AUDIT_EEE_STRICT", "1")
    run = _load(probe, policy=None)
    assert run.ref.extra["instance_source_policy"] == "eee-only"
    assert _sample_ids(run) == EEE_IDS

    monkeypatch.delenv("EVAL_AUDIT_EEE_STRICT")
    run = _load(probe, policy=None)
    assert run.ref.extra["instance_source_policy"] == "helm-preferred"
    assert _sample_ids(run) == {"H0", "H1", "H2", "H3"}


def test_unknown_policy_is_a_loud_error(probe):
    with pytest.raises(LoaderError, match="instance_source_policy"):
        _load(probe, policy="helm-only")

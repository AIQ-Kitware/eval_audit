"""Tests for the Stage-B coverage funnel computed at compose time."""
from __future__ import annotations

import json
from pathlib import Path

from eval_audit.virtual.coverage import (
    compute_coverage,
    write_coverage_artifacts,
)


def _target(model, benchmark, version, *, run_name=None, public_track="classic", run_spec_hash=None):
    name = run_name or f"{benchmark}:subject=foo,model={model.replace('/', '_')},data_augmentation=canonical"
    return {
        "model": model,
        "benchmark": benchmark,
        "suite_version": version,
        "public_track": public_track,
        "run_name": name,
        "logical_run_key": name,
        "run_path": f"/public/{public_track}/{version}/{name}",
        "run_spec_hash": run_spec_hash,
    }


def _local(model, benchmark, *, run_path="/local/run", suite="audit-x", run_spec_hash=None):
    name = f"{benchmark}:subject=foo,model={model.replace('/', '_')},data_augmentation=canonical"
    return {
        "model": model,
        "benchmark": benchmark,
        "run_entry": name,
        "logical_run_key": name,
        "run_path": run_path,
        "run_dir": run_path,
        "suite": suite,
        "experiment_name": "virt",
        "run_spec_hash": run_spec_hash,
    }


def test_coverage_funnel_counts_target_reproduced_analyzed(tmp_path):
    targets = [
        _target("eleutherai/pythia-6.9b", "mmlu", "v0.2.4"),
        _target("eleutherai/pythia-6.9b", "mmlu", "v0.3.0"),
        _target("eleutherai/pythia-12b", "mmlu", "v0.2.4"),
    ]
    locals_ = [
        _local("eleutherai/pythia-6.9b", "mmlu", run_path=str(tmp_path / "p69b-run")),
    ]
    Path(tmp_path / "p69b-run").mkdir(parents=True, exist_ok=True)

    # Simulate one analyzed packet by creating the components manifest.
    analysis_root = tmp_path / "analysis"
    packet_dpath = (
        analysis_root / "core-reports"
        / "core-metrics-virt--mmlu-subject-foo-model-eleutherai_pythia-6.9b"
    )
    packet_dpath.mkdir(parents=True)
    components_manifest = {
        "run_entry": "mmlu:subject=foo,model=eleutherai_pythia-6.9b,data_augmentation=canonical",
        "logical_run_key": "mmlu:subject=foo,model=eleutherai_pythia-6.9b,data_augmentation=canonical",
    }
    (packet_dpath / "components_manifest.json").write_text(json.dumps(components_manifest))

    coverage = compute_coverage(
        name="virt",
        description="test",
        target_rows=targets,
        local_rows=locals_,
        analysis_root=analysis_root,
    )

    assert coverage.n_target == 3
    assert coverage.n_reproduced_logical == 2  # both pythia-6.9b versions match logically
    assert coverage.n_completed == 2
    assert coverage.n_analyzed == 2
    assert len(coverage.missing) == 1
    assert coverage.missing[0].model == "eleutherai/pythia-12b"


def test_coverage_versioned_join_marked_degenerate_when_locals_unversioned(tmp_path):
    """Local audits tag their suite with experiment-name not version; the
    versioned join can't produce real matches and we should say so rather
    than silently report ``versioned=0`` as if no work matched."""
    targets = [
        _target("eleutherai/pythia-6.9b", "mmlu", "v0.2.4"),
    ]
    locals_ = [
        _local("eleutherai/pythia-6.9b", "mmlu", suite="audit-historic-grid"),
    ]
    coverage = compute_coverage(
        name="virt",
        description="test",
        target_rows=targets,
        local_rows=locals_,
        analysis_root=tmp_path / "analysis",
    )
    assert coverage.versioned_join_meaningful is False
    assert coverage.n_reproduced_logical == 1


def test_coverage_versioned_join_meaningful_when_locals_carry_public_version(tmp_path):
    targets = [
        _target("eleutherai/pythia-6.9b", "mmlu", "v0.2.4"),
        _target("eleutherai/pythia-6.9b", "mmlu", "v0.3.0"),
    ]
    locals_ = [
        # Suite carries a public-track-style version
        _local("eleutherai/pythia-6.9b", "mmlu", suite="v0.2.4"),
    ]
    coverage = compute_coverage(
        name="virt",
        description="test",
        target_rows=targets,
        local_rows=locals_,
        analysis_root=tmp_path / "analysis",
    )
    assert coverage.versioned_join_meaningful is True
    assert coverage.n_reproduced_logical == 2
    assert coverage.n_reproduced_versioned == 1  # only v0.2.4 matches


def test_recipe_identical_join_via_run_spec_hash(tmp_path):
    """When local and official rows share a run_spec_hash, the recipe-
    identical count goes up (byte-for-byte recipe match). When hashes
    differ but logical_run_key matches, the logical join still counts."""
    targets = [
        _target("m", "b", "v0.2.4", run_spec_hash="HASH_A"),
        _target("m", "b", "v0.3.0", run_spec_hash="HASH_A"),  # same hash across versions
        _target("m2", "b", "v0.2.4", run_spec_hash="HASH_B"),
    ]
    locals_ = [
        # First local row matches HASH_A -> recipe-identical with the first
        # two targets.
        _local("m", "b", run_spec_hash="HASH_A"),
        # Second local row matches the logical key for m2 but has a
        # *different* hash -> logical match only, NOT recipe-identical.
        _local("m2", "b", run_spec_hash="HASH_DIFFERENT"),
    ]
    coverage = compute_coverage(
        name="virt",
        description="t",
        target_rows=targets,
        local_rows=locals_,
        analysis_root=tmp_path / "analysis",
    )
    assert coverage.n_target == 3
    # Both m targets reproduced logically AND recipe-identical (hash match).
    # m2 target reproduced logically only.
    assert coverage.n_reproduced_logical == 3
    assert coverage.n_reproduced_recipe_identical == 2
    # The m2 row is logical-but-not-recipe-identical
    m2 = next(r for r in coverage.target_rows if r.model == "m2")
    assert m2.matched_logical
    assert not m2.matched_recipe_identical


def test_coverage_joins_survive_run_entry_token_order_drift(tmp_path):
    """P0-4: target<->local<->analyzed joins must key on the canonical logical
    key. A local whose run_entry drifts from the official form only by token
    order / a groups= token used to count as 'missing (no local repro)' and its
    analyzed packet (keyed by the sorted canonical run_entry) matched neither
    side. All three describe the same logical run and must join."""
    target = {
        "model": "eleutherai/pythia-6.9b",
        "benchmark": "mmlu",
        "suite_version": "v0.2.4",
        "public_track": "classic",
        # token order: model, subject, method
        "logical_run_key": "mmlu:model=eleutherai_pythia-6.9b,subject=foo,method=multiple_choice_joint",
        "run_name": "mmlu:model=eleutherai_pythia-6.9b,subject=foo,method=multiple_choice_joint",
    }
    local_run = tmp_path / "local-run"
    local_run.mkdir()
    local = {
        "model": "eleutherai/pythia-6.9b",
        "benchmark": "mmlu",
        # token order: method, subject, model + a non-semantic groups= token
        "run_entry": "mmlu:method=multiple_choice_joint,subject=foo,model=eleutherai_pythia-6.9b,groups=mmlu_foo",
        "logical_run_key": "mmlu:method=multiple_choice_joint,subject=foo,model=eleutherai_pythia-6.9b,groups=mmlu_foo",
        "run_path": str(local_run),
        "run_dir": str(local_run),
        "suite": "audit-x",
        "experiment_name": "virt",
    }

    analysis_root = tmp_path / "analysis"
    packet_dpath = analysis_root / "core-reports" / "core-metrics-drifted"
    packet_dpath.mkdir(parents=True)
    # Analyzed packet keyed by yet another token order (the canonical-merged form).
    (packet_dpath / "components_manifest.json").write_text(
        json.dumps(
            {
                "run_entry": "mmlu:subject=foo,method=multiple_choice_joint,model=eleutherai_pythia-6.9b",
            }
        )
    )

    coverage = compute_coverage(
        name="virt",
        description="test",
        target_rows=[target],
        local_rows=[local],
        analysis_root=analysis_root,
    )

    assert coverage.n_target == 1
    assert coverage.n_reproduced_logical == 1
    assert coverage.n_completed == 1
    assert coverage.n_analyzed == 1
    assert coverage.missing == []


def test_coverage_artifacts_written_with_latest_aliases(tmp_path, monkeypatch):
    # Skip the plotly+chrome render step: this test asserts on the
    # non-plotly artifacts (summary.txt / coverage.json / missing.csv);
    # the sankey HTML/PNG output is covered separately by
    # test_emit_sankey_artifacts_writes_png_and_latest_alias.
    monkeypatch.setenv("HELM_AUDIT_SKIP_PLOTLY", "1")

    targets = [_target("eleutherai/pythia-6.9b", "mmlu", "v0.2.4")]
    locals_ = []
    coverage = compute_coverage(
        name="virt",
        description="test",
        target_rows=targets,
        local_rows=locals_,
        analysis_root=tmp_path / "analysis",
    )
    out = tmp_path / "out"
    paths = write_coverage_artifacts(coverage, out_dpath=out)
    # *.* is the actual file now (history layer retired 2026-04-28),
    # not a symlink into .history/.
    assert paths["summary_txt"].is_file()
    assert paths["json"].is_file()
    assert paths["missing_csv"].is_file()
    summary = paths["summary_txt"].read_text()
    assert "Stage B" in summary
    assert "missing" in summary.lower()

"""Transfer-packaging tests.

The fixture reproduces the properties of the real store that make this
hard, at three-file scale: a store whose core-report packets reach sibling
roots through *relative* depth-coupled symlinks, absolute paths embedded
in JSON/CSV/shell, one run directory shared by two analyses, one dangling
reference, a job directory whose bulk must not be followed, and an
upstream HELM path that must survive the rewrite untouched.
"""
from __future__ import annotations

import json
import sys
import os
from pathlib import Path

import pytest

from eval_audit.packaging.crawl import crawl_store, read_inventory, write_inventory
from eval_audit.packaging.pack import (
    MIRROR_DIRNAME,
    RULE_JOB_TOPLEVEL,
    RULE_WHOLE,
    build_plan,
    execute_plan,
    mirror_dest,
    repoint,
)
from eval_audit.packaging.policy import DEFAULT_SOURCE_ROOTS
from eval_audit.packaging.refs import make_matcher, iter_json_paths

# An upstream HELM scenario path: absolute, under /data, and *not* ours.
UPSTREAM_PATH = "/data/CLEAR/scenario_cache/train.jsonl"


@pytest.fixture
def fake_world(tmp_path: Path) -> dict:
    """A miniature store plus the two artifact roots it points at."""
    world = tmp_path / "world"
    store = world / "audit-store"
    local = world / "audit-runs"
    public = world / "public"

    # --- a local run directory, referenced by BOTH analyses (dedup) ---
    job = local / "exp-a" / "helm" / "helm_id_aaa"
    shared_run = job / "benchmark_output" / "runs" / "exp-a" / "mmlu:subject=philosophy,method=mcj"
    shared_run.mkdir(parents=True)
    (shared_run / "run_spec.json").write_text(
        json.dumps({"name": "mmlu", "scenario_path": UPSTREAM_PATH}), encoding="utf-8"
    )
    (shared_run / "per_instance_stats.json").write_text("[]", encoding="utf-8")
    (shared_run / "stats.json").write_text("[]", encoding="utf-8")

    # job-level provenance: the markers kwdagger writes into every job
    # directory, and what the analysis reads back out of them
    (job / "job_config.json").write_text('{"job": "aaa"}', encoding="utf-8")
    (job / "invoke.sh").write_text("#!/bin/sh\nhelm-run\n", encoding="utf-8")
    (job / "adapter_manifest.json").write_text("{}", encoding="utf-8")
    (job / "container_provenance.json").write_text('{"image": "sha256:abc"}', encoding="utf-8")
    (job / "helm-run.log").write_text("started\n", encoding="utf-8")
    (job / "prod_env" / "cache").mkdir(parents=True)
    (job / "prod_env" / "cache" / "vllm.sqlite").write_bytes(b"x" * 5000)
    (job / "benchmark_output" / "scenarios").mkdir(parents=True)
    (job / "benchmark_output" / "scenarios" / "winogrande.csv").write_bytes(b"y" * 9000)

    # experiment-level from-spec inputs
    (local / "exp-a" / "materialized_run_specs" / "mmlu_abc").mkdir(parents=True)
    (local / "exp-a" / "materialized_run_specs" / "mmlu_abc" / "run_spec.json").write_text(
        "{}", encoding="utf-8"
    )

    # --- an official run directory ---
    official = public / "mmlu" / "benchmark_output" / "runs" / "v1.0.0" / "mmlu:subject=philosophy"
    official.mkdir(parents=True)
    (official / "stats.json").write_text("[]", encoding="utf-8")

    # --- two analyses that both reference the shared run ---
    for name in ("exp-one", "exp-two"):
        analysis = store / "analysis" / "experiments" / name
        packet = analysis / "core-reports" / f"core-metrics-{name}--mmlu"
        components = packet / "components"
        components.mkdir(parents=True)
        analysis.joinpath("experiment_summary.json").write_text(
            json.dumps({"description": f"{name} summary"}), encoding="utf-8"
        )
        packet.joinpath("components_manifest.json").write_text(
            json.dumps(
                {
                    "report_dpath": str(packet),
                    "components": [
                        {"source_kind": "local", "run_path": str(shared_run),
                         "job_path": str(job)},
                        {"source_kind": "official", "run_path": str(official)},
                    ],
                },
                indent=1,
            ),
            encoding="utf-8",
        )
        # A raster with a renderer beside it: must be dropped.
        packet.joinpath("redraw_plots.sh").write_text(
            f"#!/bin/sh\neval-audit-report-core --dpath {packet}\n", encoding="utf-8"
        )
        packet.joinpath("core_metric_report.png").write_bytes(b"\x89PNG" + b"0" * 4000)
        # Relative, depth-coupled symlinks across roots -- the thing that
        # breaks if the package reorganises instead of mirroring.
        os.symlink(os.path.relpath(shared_run, components), components / "local.run")
        os.symlink(os.path.relpath(official, components), components / "official.run")
        # A reference that no longer resolves.
        os.symlink(
            os.path.relpath(local / "exp-a" / "helm" / "gone" / "run", components),
            components / "dangling.run",
        )

    # index CSV carrying path columns
    indexes = store / "indexes"
    indexes.mkdir(parents=True)
    (indexes / "audit_results_index.csv").write_text(
        "run_path,job_dpath,machine_host\n"
        f"{shared_run},{job},aiq-gpu\n",
        encoding="utf-8",
    )

    return {
        "world": world,
        "store": store,
        "local": local,
        "public": public,
        "experiment": local / "exp-a",
        "roots": (str(store), str(local), str(public)),
        "shared_run": shared_run,
        "job": job,
        "official": official,
    }


def test_crawl_finds_both_analyses(fake_world):
    records = crawl_store(fake_world["store"])
    kinds = {r.kind for r in records}
    assert "experiment" in kinds
    experiments = [r for r in records if r.kind == "experiment"]
    assert {r.rel_path for r in experiments} == {
        "analysis/experiments/exp-one",
        "analysis/experiments/exp-two",
    }
    assert all(r.n_packets == 1 for r in experiments)
    # the dangling symlink is counted, not fatal
    assert sum(r.n_broken_symlinks for r in experiments) == 2


def test_inventory_roundtrip_preserves_hand_edits(fake_world, tmp_path):
    records = crawl_store(fake_world["store"])
    fpath = tmp_path / "inv.jsonl"
    write_inventory(records, fpath, fake_world["store"])
    header, restored = read_inventory(fpath)
    assert header["n_analyses"] == len(records)
    assert [r.id for r in restored] == [r.id for r in records]
    assert all(r.include for r in restored)


def test_plan_dedupes_shared_run_and_types_the_miss(fake_world, tmp_path):
    records = crawl_store(fake_world["store"])
    plan = build_plan(records, roots=fake_world["roots"])

    srcs = {a.src for a in plan.artifacts}
    assert str(fake_world["shared_run"]) in srcs
    shared = next(a for a in plan.artifacts if a.src == str(fake_world["shared_run"]))
    # referenced by two analyses, copied once
    assert shared.referrers == 2
    assert shared.n_refs > 2
    assert len([a for a in plan.artifacts if a.src == str(fake_world["shared_run"])]) == 1

    # the job directory is kept, but only its top level
    job = next(a for a in plan.artifacts if a.src == str(fake_world["job"]))
    assert job.rule == RULE_JOB_TOPLEVEL
    assert job.n_bytes < 1000, "job-dir bulk (sqlite/scenario data) must not be counted"

    assert plan.missing, "the dangling reference must be recorded"
    assert all(reason.startswith("absent") for _, reason, _ in plan.missing)


def test_package_is_self_contained_and_rewritten(fake_world, tmp_path):
    records = crawl_store(fake_world["store"])
    plan = build_plan(records, roots=fake_world["roots"])
    package = tmp_path / "pkg"
    manifest = execute_plan(plan, package, roots=fake_world["roots"])

    mirror = package / MIRROR_DIRNAME

    # 1. execution state excluded
    assert not list(mirror.rglob("*.sqlite"))
    assert not list(mirror.rglob("winogrande.csv"))
    # 2. job provenance kept
    assert list(mirror.rglob("container_provenance.json"))
    # 3. regenerable raster dropped, its renderer kept
    assert not list(mirror.rglob("*.png"))
    assert list(mirror.rglob("redraw_plots.sh"))
    # 4. cross-root relative symlinks still resolve inside the package
    links = list(mirror.rglob("local.run"))
    assert links
    for link in links:
        assert link.is_symlink()
        resolved = (link.parent / os.readlink(link)).resolve()
        assert resolved.exists(), f"{link} broke"
        assert package.resolve() in resolved.parents
    # 5. no packager-caused breakage; the store's own broken link is
    #    preserved and reported as a note, not an error
    assert manifest["counts"]["verification_errors"] == 0
    assert manifest["counts"]["verification_notes"] == 2
    assert {p["kind"] for p in manifest["problems"]} == {"preexisting_broken_symlink"}

    # 6. our absolute paths were repointed; the upstream one was not
    payload = json.loads(
        next(mirror.rglob("components_manifest.json")).read_text(encoding="utf-8")
    )
    assert payload["report_dpath"].startswith(str(package))
    spec = json.loads(next(mirror.rglob("run_spec.json")).read_text(encoding="utf-8"))
    assert spec["scenario_path"] == UPSTREAM_PATH, "upstream HELM path must survive"

    # 7. the rewrite is invertible
    rewrites = json.loads((package / "rewrites.json").read_text(encoding="utf-8"))
    assert rewrites["rewrites"]
    assert (package / "pre_rewrite_hashes.json").exists()
    assert (package / "drops.tsv").exists()
    assert (package / "missing.tsv").exists()

    # 8. dedup is reported honestly
    assert manifest["counts"]["dedup_ratio"] > 1.0


def test_standalone_repoint_needs_no_eval_audit(fake_world, tmp_path):
    """The package must be fixable on a machine without eval_audit.

    Fixing absolute paths is the step that has to work before anything
    else can, so it travels with the data as a stdlib-only script.
    """
    import subprocess

    records = crawl_store(fake_world["store"])
    plan = build_plan(records, roots=fake_world["roots"])
    package = tmp_path / "pkg"
    execute_plan(plan, package, roots=fake_world["roots"])

    moved = tmp_path / "elsewhere"
    package.rename(moved)

    # run it the way the far side would: bare interpreter, no package
    # installed, cwd deliberately unrelated
    result = subprocess.run(
        [sys.executable, str(moved / "repoint.py")],
        capture_output=True, text=True, cwd=str(tmp_path), env={"PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    assert str(moved) in result.stdout

    payload = json.loads(
        next((moved / MIRROR_DIRNAME).rglob("components_manifest.json")).read_text(
            encoding="utf-8"
        )
    )
    assert payload["report_dpath"].startswith(str(moved))

    # idempotent: a second run is a no-op
    again = subprocess.run(
        [sys.executable, str(moved / "repoint.py")], capture_output=True, text=True
    )
    assert again.returncode == 0
    assert "nothing to do" in again.stdout


def test_repoint_after_a_move(fake_world, tmp_path):
    records = crawl_store(fake_world["store"])
    plan = build_plan(records, roots=fake_world["roots"])
    package = tmp_path / "pkg"
    execute_plan(plan, package, roots=fake_world["roots"])

    moved = tmp_path / "moved-pkg"
    package.rename(moved)
    assert repoint(moved) > 0

    payload = json.loads(
        next((moved / MIRROR_DIRNAME).rglob("components_manifest.json")).read_text(
            encoding="utf-8"
        )
    )
    assert payload["report_dpath"].startswith(str(moved))
    assert str(package) not in payload["report_dpath"]


def test_container_directories_are_never_copied_whole(fake_world):
    """A referenced ancestor must not drag its whole subtree in.

    Regression: classification used to infer structure from a ``helm``
    path component and default everything unrecognized to a whole copy.
    An experiment directory has no ``helm`` component, so every
    referenced container fell through to "copy everything beneath me" and
    the plan came to 1168 GB instead of ~27.
    """
    from eval_audit.packaging.pack import (
        RULE_CONTAINER,
        RULE_EXPERIMENT,
        RULE_JOB_TOPLEVEL,
        RULE_WHOLE,
        classify_ref,
    )

    public = fake_world["public"]

    # the units we do recognize
    assert classify_ref(fake_world["shared_run"], []) == RULE_WHOLE
    assert classify_ref(fake_world["job"], []) == RULE_JOB_TOPLEVEL
    assert classify_ref(fake_world["experiment"], []) == RULE_EXPERIMENT

    # every ancestor above them is a container, at every level
    for container in (
        fake_world["local"],
        public,
        public / "mmlu",
        public / "mmlu" / "benchmark_output",
        public / "mmlu" / "benchmark_output" / "runs",
        public / "mmlu" / "benchmark_output" / "runs" / "v1.0.0",
    ):
        assert classify_ref(container, []) == RULE_CONTAINER, container


def test_execution_state_is_skipped_even_when_referenced_directly():
    """A directly-referenced cache must not slip past the job-dir rule.

    Regression: a report referenced `.../helm_id_x/prod_env/cache`, which
    never passes through the job-directory rule and is a leaf, so the
    leaf fallback copied it whole -- putting a sqlite request cache into
    a package that exists to exclude sqlite request caches.
    """
    from eval_audit.packaging.pack import RULE_SKIP, _is_execution_state, classify_ref

    job = "/data/crfm-helm-audit/exp/helm/helm_id_x"
    for excluded in (
        f"{job}/prod_env",
        f"{job}/prod_env/cache",
        f"{job}/prod_env/cache/vllm.sqlite",
        f"{job}/benchmark_output/scenarios",
        f"{job}/benchmark_output/scenarios/winogrande_am.csv",
        f"{job}/benchmark_output/scenario_instances",
    ):
        assert _is_execution_state(Path(excluded)), excluded
        assert classify_ref(Path(excluded), []) == RULE_SKIP, excluded

    # run directories live under benchmark_output/runs and are kept
    assert not _is_execution_state(Path(f"{job}/benchmark_output/runs/exp/mmlu:x"))
    # the job directory itself is still classifiable
    assert not _is_execution_state(Path(job))


def test_catalog_rows_are_not_followed():
    """An index row alone must not drag a public run into the package.

    ``indexes/official_public_index.csv`` lists all 85,025 runs in the
    public mirror. Following it would copy ~491 GB; the runs an analysis
    actually used are the ones a packet's manifest or symlink names.
    """
    from eval_audit.packaging.pack import _is_catalog_only

    public = "/data/crfm-helm-public/mmlu/benchmark_output/runs/v1.0.0/x"
    local = "/data/crfm-helm-audit/exp/helm/job/benchmark_output/runs/exp/x"

    # catalogued only -> skipped, whatever format the catalog is in.
    # filter_inventory.json is JSON but still a catalog, which is why
    # this keys on carrier semantics rather than on file format.
    assert _is_catalog_only(public, {"csv"})
    assert _is_catalog_only(public, {"json"})
    assert _is_catalog_only(public, {"csv", "json", "text"})
    # consumed by a comparison -> copied
    assert not _is_catalog_only(public, {"packet"})
    assert not _is_catalog_only(public, {"symlink"})
    assert not _is_catalog_only(public, {"csv", "packet"})
    # the local root is never catalog-gated: it is scoped by construction
    # and carries the only references to materialized run specs
    assert not _is_catalog_only(local, {"csv"})
    assert not _is_catalog_only(local, {"json"})


def test_packet_manifests_are_a_strong_carrier(tmp_path):
    """Only a packet's own manifests vouch for a run being consumed."""
    from eval_audit.packaging.refs import carrier_of

    assert carrier_of(tmp_path / "components_manifest.json") == "packet"
    assert carrier_of(tmp_path / "core_metric_report.json") == "packet"
    # a corpus-wide inventory is JSON, but it is not a packet manifest
    assert carrier_of(tmp_path / "filter_inventory.json") == "json"
    assert carrier_of(tmp_path / "official_public_index.csv") == "csv"


def test_upstream_data_paths_are_not_matched():
    """The rewriter is an exact root allowlist, not a /data/ prefix family."""
    matcher = make_matcher(DEFAULT_SOURCE_ROOTS)
    assert matcher.fullmatch("/data/crfm-helm-audit/exp/x")
    for upstream in (
        "/data/CLEAR/x.jsonl",
        "/data/medhelm/y",
        "/data/tasks_1-20_v1-2.tmp",
        "/data/crfm-helm-audit-store-backup/z",
    ):
        assert not matcher.fullmatch(upstream), upstream


def test_json_scan_is_structural_not_key_driven():
    """A path under a field this code has never heard of is still found."""
    matcher = make_matcher(("/data/crfm-helm-audit",))
    doc = {"some_future_field": {"nested": ["/data/crfm-helm-audit/exp/run"]}}
    assert list(iter_json_paths(doc, matcher)) == ["/data/crfm-helm-audit/exp/run"]


def test_mirror_dest_preserves_sibling_depth():
    package = Path("/pkg")
    a = mirror_dest("/data/crfm-helm-audit-store/analysis/x", package)
    b = mirror_dest("/data/crfm-helm-audit/exp/run", package)
    assert a == Path("/pkg/root/data/crfm-helm-audit-store/analysis/x")
    # the two roots remain siblings, which is what keeps ../../.. links valid
    assert a.parents[len(a.parents) - 4] == b.parents[len(b.parents) - 4]

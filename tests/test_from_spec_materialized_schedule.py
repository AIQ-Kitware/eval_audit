"""Exact-path replay wiring: materializer -> bridge submatrix -> docker node.

Covers the eval_audit side of ``docs/planning/run-from-relative-path-plan.md``
§4.2/4.3/4.4/4.6: ``build_schedule_params`` emits one kwdagger ``submatrices``
entry per materialized run (no run-entry axis, no corpus mount), the params
expand to exactly N jobs through the real kwdagger grid, the from-spec docker
node renders the staging ``:ro`` mount + ``--run_spec_json`` (and no corpus
mount), and ``prepare_schedule_request`` materializes copies on disk end to end.
No GPU / docker daemon required (a digest-pinned image short-circuits resolution).
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
import yaml

pytest.importorskip("kwdagger")

from kwdagger.utils.util_param_grid import expand_param_grid

from eval_audit.integrations.docker_provenance import ResolvedImage
from eval_audit.integrations.kwdagger_bridge import (
    build_schedule_params,
    prepare_schedule_request,
)
from eval_audit.manifests.run_spec_materializer import MaterializedRunSpec
from eval_audit.pipelines.helm_docker_pipeline import (
    helm_single_run_from_spec_docker_pipeline,
)

_FAKE_IMAGE = ResolvedImage(
    requested="img:dev",
    run_ref="img@sha256:deadbeef",
    digest="sha256:deadbeef",
    digest_kind="repo_digest",
    pinned=True,
)

_PINNED = "img@sha256:" + "d" * 64


def _materialized(n: int) -> list[MaterializedRunSpec]:
    return [
        MaterializedRunSpec(
            run_entry=f"mmlu:subject=s{i},model=allenai/olmo-7b",
            run_spec_json=f"/stage/run{i}/run_spec.json",
            official_run_spec_json=f"/data/.../run{i}/run_spec.json",
            rel_path=f"lite/.../run{i}",
            lease_endpoint=f"ep{i}",
            substitutions={"model_deployment": {"from": "together/olmo-7b", "to": f"vllm/m{i}"}},
        )
        for i in range(n)
    ]


# --------------------------------------------------------------------------- #
# build_schedule_params: submatrix carriage                                   #
# --------------------------------------------------------------------------- #
def test_materialized_path_emits_submatrix_not_run_entry_axis() -> None:
    manifest = {
        "from_run_spec": True,
        "run_entries": [],
        "max_eval_instances": 10,
        "suite": "olmo-smoke",
        "precomputed_root": "/data/crfm-helm-public",
    }
    runs = _materialized(3)
    params = build_schedule_params(
        manifest,
        resolved_image=_FAKE_IMAGE,
        # leasing requested ⇒ per-run frozen endpoints ride the submatrix
        lease_entries={"helm.lease_queue": [True]},
        materialized_runs=runs,
        staging_root="/exp/materialized_run_specs",
    )
    matrix = params["matrix"]

    # from-spec pipeline, submatrix carriage
    assert "from_spec" in params["pipeline"]
    assert len(matrix["submatrices"]) == 3
    for i, entry in enumerate(matrix["submatrices"]):
        assert entry["helm.run_spec_json"] == f"/stage/run{i}/run_spec.json"
        assert entry["helm.run_entry"] == runs[i].run_entry
        assert entry["helm.lease_endpoint"] == f"ep{i}"

    # staging mount knob present; the per-run-axis / corpus / rewrite keys are GONE
    assert matrix["helm.staging_root"] == ["/exp/materialized_run_specs"]
    assert "helm.run_entry" not in matrix          # not a cross-product axis
    assert "helm.precomputed_root" not in matrix   # no corpus mount
    assert "helm.max_eval_instances" not in matrix  # baked into the copy
    assert "helm.model_deployment" not in matrix    # baked into the copy


def test_submatrix_run_entry_strips_model_deployment_token() -> None:
    # Regression: the from-spec node uses helm.run_entry as the locator query for
    # the produced run dir, and HELM dir names encode model=… but never
    # model_deployment=…. A multi-deployment freeze carries an inline
    # model_deployment=<local> token on each run_entry (the per-run rewrite
    # target); if it reached the locator the token-subset match would fail and the
    # node would raise "produced run directory could not be located". The bridge
    # must strip it, leaving the bare discovery key that resolves 1:1.
    runs = [
        MaterializedRunSpec(
            run_entry=(
                "mmlu:subject=anatomy,model=allenai/olmo-1.7-7b,"
                "model_deployment=vllm/allenai-olmo-1.7-7b"
            ),
            run_spec_json="/stage/run0/run_spec.json",
            official_run_spec_json="/o",
            rel_path="r",
            lease_endpoint="ep0",
        )
    ]
    params = build_schedule_params(
        {"from_run_spec": True, "run_entries": [], "max_eval_instances": 5, "suite": "s"},
        resolved_image=_FAKE_IMAGE,
        materialized_runs=runs,
        staging_root="/stage",
        lease_entries={"helm.lease_queue": [True]},
    )
    entry = params["matrix"]["submatrices"][0]
    assert entry["helm.run_entry"] == "mmlu:subject=anatomy,model=allenai/olmo-1.7-7b"
    assert "model_deployment=" not in entry["helm.run_entry"]
    # the spec path + per-run lease endpoint still ride the same submatrix entry
    assert entry["helm.run_spec_json"] == "/stage/run0/run_spec.json"
    assert entry["helm.lease_endpoint"] == "ep0"


def test_materialized_params_expand_to_exactly_n_jobs() -> None:
    """End-to-end through the real kwdagger grid: N submatrix entries -> N jobs."""
    manifest = {
        "from_run_spec": True,
        "run_entries": [],
        "max_eval_instances": 10,
        "suite": "olmo-smoke",
        "precomputed_root": "/data/crfm-helm-public",
    }
    runs = _materialized(4)
    params = build_schedule_params(
        manifest, resolved_image=_FAKE_IMAGE,
        lease_entries={"helm.lease_queue": [True]},  # leasing on ⇒ endpoints emitted
        materialized_runs=runs, staging_root="/exp/materialized_run_specs",
    )
    # mirror schedule.py: pop 'pipeline', expand the matrix
    param_arg = {k: v for k, v in params.items() if k != "pipeline"}
    jobs = list(expand_param_grid(param_arg))

    assert len(jobs) == 4
    # each job carries the broadcast singletons + exactly its own spec/lease tuple
    by_spec = {r.run_spec_json: r for r in runs}
    for job in jobs:
        assert job["helm.suite"] == "olmo-smoke"
        assert job["helm.container_image"] == _FAKE_IMAGE.run_ref
        rec = by_spec[job["helm.run_spec_json"]]
        assert job["helm.lease_endpoint"] == rec.lease_endpoint
        assert job["helm.run_entry"] == rec.run_entry
    assert {j["helm.run_spec_json"] for j in jobs} == set(by_spec)


def test_frozen_lease_endpoint_omitted_without_lease() -> None:
    """A frozen source's lease endpoint must NOT force-lease a non-leased run."""
    runs = _materialized(2)  # records carry lease_endpoint=ep0/ep1
    params = build_schedule_params(
        {"from_run_spec": True, "run_entries": [], "max_eval_instances": 1, "suite": "s"},
        resolved_image=_FAKE_IMAGE, materialized_runs=runs, staging_root="/stage",
        lease_entries=None,  # no --lease
    )
    for entry in params["matrix"]["submatrices"]:
        assert "helm.lease_endpoint" not in entry


def test_materialized_runs_without_lease_omit_endpoint() -> None:
    runs = [
        MaterializedRunSpec(
            run_entry="x", run_spec_json="/stage/x/run_spec.json",
            official_run_spec_json="/o", rel_path="r", lease_endpoint=None,
        )
    ]
    params = build_schedule_params(
        {"from_run_spec": True, "run_entries": [], "max_eval_instances": 1, "suite": "s"},
        resolved_image=_FAKE_IMAGE, materialized_runs=runs, staging_root="/stage",
    )
    assert "helm.lease_endpoint" not in params["matrix"]["submatrices"][0]


# --------------------------------------------------------------------------- #
# from-spec docker node: staging mount + --run_spec_json, no corpus mount      #
# --------------------------------------------------------------------------- #
def test_from_spec_node_renders_staging_mount_and_spec_arg() -> None:
    pipe = helm_single_run_from_spec_docker_pipeline()
    node = pipe.node_dict["materialize_helm_run"]
    with tempfile.TemporaryDirectory() as td:
        pipe.configure(
            {
                "helm.run_spec_json": "/stage/run0/run_spec.json",
                "helm.staging_root": "/stage",
                "helm.suite": "s",
                "helm.container_image": "img@sha256:deadbeef",
            },
            root_dpath=td,
        )
        command = node.command

    # the materialized copy is mounted :ro at its own path, and named to the CLI
    assert "-v /stage:/stage:ro" in command
    assert "--run_spec_json=/stage/run0/run_spec.json" in command
    assert "materialize_helm_run_from_spec" in command
    # staging_root is a mount knob, never forwarded to the inner CLI
    assert "--staging_root=" not in command


# --------------------------------------------------------------------------- #
# prepare_schedule_request end-to-end (materialize copies on disk)            #
# --------------------------------------------------------------------------- #
_OFFICIAL_SPEC = {
    "name": "mmlu:subject=anatomy,model=allenai_olmo-7b",
    "adapter_spec": {
        "model": "allenai/olmo-7b",
        "model_deployment": "together/olmo-7b",
        "max_eval_instances": 1000,
    },
    "metric_specs": [],
}
_REL = "lite/benchmark_output/runs/v1.0.0/mmlu:subject=anatomy,model=allenai_olmo-7b"


def _write_corpus(root: Path) -> None:
    run_dir = root / _REL
    run_dir.mkdir(parents=True)
    (run_dir / "run_spec.json").write_text(json.dumps(_OFFICIAL_SPEC))


def _write_manifest(tmp_path: Path, corpus: Path, **extra) -> Path:
    doc = {
        "schema_version": 1,
        "experiment_name": "audit-olmo-smoke",
        "description": "d",
        "run_entries": [],
        "max_eval_instances": 5,
        "suite": "audit-olmo-smoke",
        "from_run_spec": True,
        "precomputed_root": str(corpus),
        "run_spec_sources": [
            {
                "run_entry": "mmlu:subject=anatomy,model=allenai/olmo-7b",
                "rel_path": _REL,
                "model_deployment": "vllm/allenai-olmo-7b",
                "lease_endpoint": "olmo-7b-ep",
            }
        ],
        "model_deployments_fpath": str(tmp_path / "model_deployments.yaml"),
        **extra,
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(doc))
    return path


def test_prepare_request_materializes_and_emits_submatrix(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _write_corpus(corpus)
    manifest = _write_manifest(tmp_path, corpus, container_image=_PINNED)

    req = prepare_schedule_request(
        manifest, container_image=_PINNED, root_dpath=tmp_path / "out"
    )
    params = yaml.safe_load(req.params_text)
    matrix = params["matrix"]

    # one submatrix entry pointing at a materialized copy that exists on disk
    assert len(matrix["submatrices"]) == 1
    copy_path = Path(matrix["submatrices"][0]["helm.run_spec_json"])
    assert copy_path.is_file()
    assert copy_path.parent.parent.name == "materialized_run_specs"

    # the copy carries the substituted deployment; model + name untouched
    materialized = json.loads(copy_path.read_text())
    assert materialized["adapter_spec"]["model_deployment"] == "vllm/allenai-olmo-7b"
    assert materialized["adapter_spec"]["model"] == "allenai/olmo-7b"
    assert materialized["adapter_spec"]["max_eval_instances"] == 5  # experiment default cap
    assert materialized["name"] == _OFFICIAL_SPEC["name"]

    # the copy filename is content-addressed, with a paired provenance sidecar
    assert copy_path.name.startswith("run_spec.") and copy_path.name.endswith(".json")
    sidecar = copy_path.with_name(copy_path.name.replace(".json", ".materialization.json"))
    assert sidecar.is_file()

    # staging mount knob present; no corpus mount on this path
    assert "helm.staging_root" in matrix
    assert "helm.precomputed_root" not in matrix
    # no --lease here ⇒ the source's frozen lease_endpoint is not emitted
    assert "helm.lease_endpoint" not in matrix["submatrices"][0]


def test_prepare_request_materialized_lease_uses_per_run_endpoint(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _write_corpus(corpus)
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text("endpoints: {}\n")
    manifest = _write_manifest(
        tmp_path, corpus, container_image=_PINNED, lease_ttl="2h",
        lease_catalog=str(catalog),
    )
    req = prepare_schedule_request(
        manifest, container_image=_PINNED, root_dpath=tmp_path / "out",
        lease=True, lease_ttl="2h", lease_catalog=str(catalog),
    )
    matrix = yaml.safe_load(req.params_text)["matrix"]

    # per-run lease endpoint rides in the submatrix (not a broadcast map/scalar)
    assert matrix["submatrices"][0]["helm.lease_endpoint"] == "olmo-7b-ep"
    assert "helm.lease_endpoint" not in matrix
    assert "helm.lease_endpoints" not in matrix
    # broadcast lease knobs + no-GPU client still applied
    assert matrix["helm.lease_ttl"] == ["2h"]
    assert matrix["helm.container_gpus"] == ["none"]

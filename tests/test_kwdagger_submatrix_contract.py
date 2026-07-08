"""Contract test: kwdagger's matrix/submatrix expansion semantics.

The ``(public_root, relative_path)`` reproduction plan
(``docs/historical/planning/run-from-relative-path-plan.md``) carries each run's per-run
tuple — the materialized ``run_spec.json`` path, its lease endpoint, and a
run-entry label — as a kwdagger ``submatrices`` entry rather than as parallel
plain matrix axes. That choice rests on two facts about kwdagger we do not own:

  * plain ``matrix:`` axes **cross-product** (so a parallel per-run axis would
    fan out N*N and mis-pair the tuples), and
  * a ``submatrices`` list of complete per-run dicts over a singleton base
    **zips** (N records -> exactly N jobs, each = the broadcast singletons plus
    that one record's fields).

These tests pin that contract against the vendored kwdagger so a submodule bump
that changed the semantics would fail here, loudly, before the bridge silently
fanned out wrong. They exercise the *exact* code path ``kwdagger schedule`` uses
(``expand_param_grid`` over the ``params`` dict after ``pipeline`` is popped —
see ``submodules/kwdagger/kwdagger/schedule.py``), and read ``submatrices`` from
*inside* ``matrix`` exactly as the bridge would emit it
(``util_param_grid.py`` ``extended_github_action_matrix`` line ~584).
"""
from __future__ import annotations

import pytest

pytest.importorskip("kwdagger")

from kwdagger.utils.util_param_grid import expand_param_grid


def _expand(arg: dict) -> list[dict]:
    return list(expand_param_grid(arg))


_BASE_SINGLETONS = {
    "helm.suite": ["my-suite"],
    "helm.container_image": ["img@sha256:deadbeef"],
}

_RUNS = [
    {
        "helm.run_spec_json": "/stage/run0/run_spec.json",
        "helm.lease_endpoint": "ep0",
        "helm.run_entry": "mmlu:subject=x,model=a",
    },
    {
        "helm.run_spec_json": "/stage/run1/run_spec.json",
        "helm.lease_endpoint": "ep1",
        "helm.run_entry": "mmlu:subject=x,model=b",
    },
    {
        "helm.run_spec_json": "/stage/run2/run_spec.json",
        "helm.lease_endpoint": "ep2",
        "helm.run_entry": "mmlu:subject=x,model=c",
    },
]


def test_plain_axes_cross_product() -> None:
    """Two length-3 plain axes -> 9 jobs. This is the hazard submatrices avoid."""
    items = _expand({"matrix": {"helm.a": [1, 2, 3], "helm.b": [10, 20, 30]}})
    assert len(items) == 9


def test_submatrices_zip_to_exactly_n_jobs() -> None:
    """Singleton base + N submatrix records -> exactly N jobs, tuples intact."""
    arg = {"matrix": {**_BASE_SINGLETONS, "submatrices": _RUNS}}
    items = _expand(arg)

    assert len(items) == len(_RUNS)

    by_spec = {r["helm.run_spec_json"]: r for r in _RUNS}
    seen = set()
    for item in items:
        # every job carries the broadcast singletons
        assert item["helm.suite"] == "my-suite"
        assert item["helm.container_image"] == "img@sha256:deadbeef"
        # ...plus exactly one run's fields, with NO bleed across records
        spec = item["helm.run_spec_json"]
        record = by_spec[spec]
        assert item["helm.lease_endpoint"] == record["helm.lease_endpoint"]
        assert item["helm.run_entry"] == record["helm.run_entry"]
        seen.add(spec)
    # each run scheduled exactly once
    assert seen == set(by_spec)


def test_schedule_params_shape_is_honored() -> None:
    """The exact bridge->schedule.py shape: pop 'pipeline', submatrices inside matrix."""
    params = {
        "pipeline": "eval_audit.pipelines.helm_docker_pipeline."
        "helm_single_run_from_spec_docker_pipeline()",
        "matrix": {**_BASE_SINGLETONS, "submatrices": _RUNS},
    }
    param_arg = dict(params)
    param_arg.pop("pipeline", None)  # schedule.py does exactly this before expanding
    items = _expand(param_arg)

    assert len(items) == len(_RUNS)
    assert {i["helm.run_spec_json"] for i in items} == {
        r["helm.run_spec_json"] for r in _RUNS
    }


def test_parallel_axes_would_blow_up_and_mispair() -> None:
    """Guard: the same per-run data as PLAIN axes is N*N and mis-pairs the tuple."""
    bad = {
        "matrix": {
            **_BASE_SINGLETONS,
            "helm.run_spec_json": [r["helm.run_spec_json"] for r in _RUNS],
            "helm.lease_endpoint": [r["helm.lease_endpoint"] for r in _RUNS],
        }
    }
    items = _expand(bad)
    assert len(items) == len(_RUNS) ** 2
    # a single lease endpoint gets paired with EVERY spec — the mis-pairing
    # submatrices exist to prevent.
    specs_for_ep0 = {i["helm.run_spec_json"] for i in items if i["helm.lease_endpoint"] == "ep0"}
    assert len(specs_for_ep0) == len(_RUNS)

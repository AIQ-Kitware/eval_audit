"""EEE artifact discovery + planner index-row synthesis (library home).

Moved out of ``eval_audit.cli.from_eee`` (Phase 3 sub-stage 4.4 of
docs/planning/phase3-comparison-core-unification.md): these are library
functions consumed by three surfaces (``from_eee``,
``compare_pair_eee``, ``virtual.compose``) and they belong with the EEE
adapter, not inside a CLI module. Pure relocation — bodies unchanged;
the leading underscores dropped because this is now the public home.
``from_eee`` keeps underscore-named compat aliases.
"""

from __future__ import annotations

import csv
import io
import json
import uuid as uuidlib
from pathlib import Path
from typing import Any

from eval_audit.infra.fs_publish import write_text_atomic
from eval_audit.infra.profiling import profile


# ---------------------------------------------------------------------------
# EEE artifact discovery
# ---------------------------------------------------------------------------


@profile
def discover_eee_artifacts(root: Path) -> list[dict[str, Any]]:
    """Walk ``root`` for EEE aggregate files and return one row per artifact dir.

    An "artifact dir" is the directory containing a ``<uuid>.json`` and the
    sibling ``<uuid>_samples.jsonl``. Multiple artifacts in the same dir are
    returned as separate rows.
    """
    from eval_audit.normalized.recipe_facts import is_aggregate_json_name

    rows: list[dict[str, Any]] = []
    if not root.exists():
        return rows
    for json_path in sorted(root.rglob("*.json")):
        # R-5: single shared aggregate-name predicate (also excludes run_spec.json
        # and *_samples.json, which the structural check below would drop anyway).
        if not is_aggregate_json_name(json_path.name):
            continue
        try:
            data = json.loads(json_path.read_text())
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        # EEE EvaluationLog has these top-level keys; cheap structural check
        # avoids importing every_eval_ever just to discover.
        if "evaluation_results" not in data or "model_info" not in data:
            continue
        rows.append({
            "json_path": json_path,
            "data": data,
        })
    return rows


def detect_helm_sidecars(artifact_dir: Path) -> dict[str, Any]:
    """Look for HELM-shape sidecar files next to an EEE artifact dir.

    When a HELM run was the upstream of the EEE artifact, the user
    *can* ship the original ``run_spec.json`` alongside ``<uuid>.json``
    and ``<uuid>_samples.jsonl`` — doing so lets the planner populate
    comparability facts (scenario class, deployment, instructions,
    max_eval_instances) instead of collapsing them to ``unknown``.

    Returns ``{"run_spec_fpath": <abs path or None>,
              "max_eval_instances": <str or None>}``. The
    ``max_eval_instances`` field is parsed out of ``run_spec.json``
    because the planner expects it on the index row, not in the
    ``run_spec_fpath`` blob — every other adapter/scenario field flows
    through the planner's existing ``extract_run_spec_fields`` reader.
    """
    run_spec_fpath = artifact_dir / "run_spec.json"
    if not run_spec_fpath.is_file():
        return {"run_spec_fpath": None, "max_eval_instances": None}
    max_eval_instances: str | None = None
    try:
        spec = json.loads(run_spec_fpath.read_text())
        adapter = spec.get("adapter_spec") if isinstance(spec, dict) else None
        if isinstance(adapter, dict):
            mei = adapter.get("max_eval_instances")
            if mei is not None:
                max_eval_instances = str(mei)
    except (OSError, json.JSONDecodeError):
        pass
    return {
        "run_spec_fpath": str(run_spec_fpath),
        "max_eval_instances": max_eval_instances,
    }


def extract_artifact_meta(row: dict[str, Any], *, root: Path) -> dict[str, Any]:
    """From a discovered artifact, pull model / benchmark / experiment fields."""
    data = row["data"]
    json_path: Path = row["json_path"]
    artifact_dir = json_path.parent

    model_info = data.get("model_info") or {}
    model_id = (model_info.get("id") or model_info.get("name") or "").strip()
    eval_results = data.get("evaluation_results") or []
    if eval_results:
        first = eval_results[0]
        source_data = first.get("source_data") or {}
        benchmark = (
            source_data.get("dataset_name")
            or first.get("evaluation_name")
            or "unknown"
        )
    else:
        benchmark = "unknown"

    # Experiment name = the path component just below "local/" (if present),
    # so the user can group local attempts however they like. The documented
    # contract is ``local/<experiment>/<benchmark>/<dev>/<model>/<uuid>.json``,
    # whose artifact_dir (the json's parent) is ``<experiment>/<benchmark>/
    # <dev>/<model>`` — exactly 4 parts relative to ``local/``. The old ``> 4``
    # guard never fired for this layout, silently collapsing every row to
    # ``eee_only_local`` and discarding the user's chosen grouping (D-1).
    rel = artifact_dir.relative_to(root)
    experiment_name: str | None = None
    if len(rel.parts) >= 4:
        experiment_name = rel.parts[0]
    sidecars = detect_helm_sidecars(artifact_dir)
    return {
        "artifact_dir": artifact_dir,
        "json_path": json_path,
        "model_id": model_id,
        "benchmark": benchmark,
        "experiment_name": experiment_name,
        "evaluation_id": data.get("evaluation_id"),
        "run_spec_fpath": sidecars["run_spec_fpath"],
        "max_eval_instances": sidecars["max_eval_instances"],
    }


def stable_short_hash(*parts: str) -> str:
    return uuidlib.uuid5(uuidlib.NAMESPACE_URL, "::".join(parts)).hex[:12]


def build_logical_run_key(meta: dict[str, Any]) -> str:
    """``<benchmark>:model=<model_id>`` — the comparison identity."""
    return f"{meta['benchmark']}:model={meta['model_id']}"


def build_official_index_row(meta: dict[str, Any]) -> dict[str, Any]:
    logical_run_key = build_logical_run_key(meta)
    component_id = (
        f"official::eee_only::{meta['model_id']}::{meta['benchmark']}::"
        f"{stable_short_hash(str(meta['artifact_dir']))}"
    )
    return {
        "source_kind": "official",
        "artifact_format": "eee",
        "eee_artifact_path": str(meta["artifact_dir"]),
        "component_id": component_id,
        "logical_run_key": logical_run_key,
        "run_name": logical_run_key,
        "run_spec_name": logical_run_key,
        "run_spec_fpath": meta.get("run_spec_fpath"),
        "max_eval_instances": meta.get("max_eval_instances"),
        "model": meta["model_id"],
        "benchmark": meta["benchmark"],
        "public_track": "eee_only_demo",
        "suite_version": "v1",
        "has_run_spec": "True",
    }


def build_local_index_row(meta: dict[str, Any], *, experiment_override: str | None = None) -> dict[str, Any]:
    logical_run_key = build_logical_run_key(meta)
    experiment_name = experiment_override or meta["experiment_name"] or "eee_only_local"
    artifact_short = stable_short_hash(str(meta["artifact_dir"]))
    job_id = f"job_{artifact_short}"
    component_id = (
        f"local::{experiment_name}::{job_id}::{meta.get('evaluation_id') or artifact_short}"
    )
    return {
        "source_kind": "local",
        "artifact_format": "eee",
        "eee_artifact_path": str(meta["artifact_dir"]),
        "component_id": component_id,
        "logical_run_key": logical_run_key,
        "run_entry": logical_run_key,
        "run_spec_name": logical_run_key,
        "run_spec_fpath": meta.get("run_spec_fpath"),
        "max_eval_instances": meta.get("max_eval_instances"),
        "model": meta["model_id"],
        "benchmark": meta["benchmark"],
        "experiment_name": experiment_name,
        "job_id": job_id,
        "attempt_uuid": meta.get("evaluation_id") or artifact_short,
        "attempt_identity": meta.get("evaluation_id") or artifact_short,
        "attempt_identity_kind": "eee_evaluation_id",
        "machine_host": "eee_only_demo",
        "status": "computed",
        "has_run_spec": "True",
    }


# ---------------------------------------------------------------------------
# Index synthesis
# ---------------------------------------------------------------------------


def write_index_csv(rows: list[dict[str, Any]], fpath: Path) -> Path:
    """Write rows to a CSV with stable header ordering.

    The header is the union of all row keys in sorted order; missing keys
    are written as empty strings. This avoids a hard-coded schema while
    still producing a CSV that the planner's ``csv.DictReader`` can read.
    """
    if not rows:
        write_text_atomic(fpath, "")
        return fpath
    fieldnames = sorted({k for r in rows for k in r.keys()})
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: ("" if row.get(k) is None else row[k]) for k in fieldnames})
    write_text_atomic(fpath, buf.getvalue())
    return fpath


__all__ = [
    "build_local_index_row",
    "build_logical_run_key",
    "build_official_index_row",
    "detect_helm_sidecars",
    "discover_eee_artifacts",
    "extract_artifact_meta",
    "stable_short_hash",
    "write_index_csv",
]

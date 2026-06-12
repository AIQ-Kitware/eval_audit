"""eval-audit-compare-pair-eee: pairwise EEE-only comparison and report.

The EEE-format analogue of :mod:`eval_audit.reports.pair_report`
(``eval-audit-compare-pair``). Takes one **official** EEE artifact and
one **local** EEE artifact, runs the comparison-intent planner + core
metrics, and writes the same shape of report ``eval-audit-from-eee``
produces per pair (``core_metric_report.{txt,json,png}`` plus
the standard sidecar manifests / scripts) into ``--out-dpath``.

What the EEE-only path can answer:

    - Run-level and instance-level abs-delta and agreement curves
    - Per-metric agreement breakdowns
    - Same-model identity (from EEE ``model_info``)
    - Logical-run-key identity (``<benchmark>:model=<model_id>``)

What it cannot answer **without HELM sidecars** (and so reports as
``status=unknown``):

    - ``same_scenario_class`` (HELM ``run_spec.json:scenario_spec.class_name``)
    - ``same_deployment``      (HELM ``run_spec.json:adapter_spec.model_deployment``)
    - ``same_instructions``    (HELM ``run_spec.json:adapter_spec.instructions``)
    - ``same_max_eval_instances`` (HELM ``run_spec.json:adapter_spec.max_eval_instances``)
    - ``same_benchmark_family`` (HELM scenario class taxonomy)

When ``run_spec.json`` is shipped *next to* the EEE artifact (i.e.,
inside ``<artifact_dir>/run_spec.json``), this CLI picks it up and the
above facts become evaluable. See
[``docs/eee-vs-helm-metadata.md``](../../docs/eee-vs-helm-metadata.md)
for the full HELM↔EEE field mapping and recommendations on preserving
more metadata in your pipeline.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

from eval_audit.cli.from_eee import _packets_with_manifests
from eval_audit.normalized.eee_sources import (
    build_local_index_row as _build_local_index_row,
    build_logical_run_key as _build_logical_run_key,
    build_official_index_row as _build_official_index_row,
    detect_helm_sidecars,
    write_index_csv as _write_index_csv,
)
from eval_audit.infra.logging import setup_cli_logging
from eval_audit.planning.core_report_planner import build_planning_artifact


def _resolve_eee_artifact_path(path: str | Path) -> Path:
    """Resolve a user-given path to an EEE aggregate ``<uuid>.json`` file.

    Accepts either:

    * the ``<uuid>.json`` aggregate file itself, or
    * the artifact directory containing a single EEE aggregate JSON
      (the partner ``<uuid>_samples.jsonl`` is read by the loader).
    """
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise SystemExit(f"FAIL: path does not exist: {p}")
    if p.is_file():
        if p.suffix.lower() != ".json":
            raise SystemExit(f"FAIL: expected an EEE aggregate .json file, got {p}")
        return p
    candidates = sorted(
        f for f in p.glob("*.json")
        if f.name not in {"fixture_manifest.json", "provenance.json", "status.json", "run_spec.json"}
    )
    if not candidates:
        raise SystemExit(f"FAIL: no EEE aggregate .json found in {p}")
    if len(candidates) > 1:
        listing = "\n".join(f"    {c.name}" for c in candidates)
        raise SystemExit(
            f"FAIL: multiple EEE aggregate JSONs in {p}; pass one explicitly:\n{listing}"
        )
    return candidates[0]


def _meta_from_artifact(json_path: Path) -> dict[str, Any]:
    """Load an EEE aggregate JSON and extract the planner-row inputs.

    Mirrors what ``from_eee._extract_artifact_meta`` produces for the
    discovery path, but takes a single explicit ``<uuid>.json`` rather
    than walking a tree.
    """
    try:
        data = json.loads(json_path.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"FAIL: cannot parse {json_path}: {exc}")
    if not isinstance(data, dict) or "evaluation_results" not in data or "model_info" not in data:
        raise SystemExit(f"FAIL: {json_path} does not look like an EEE aggregate JSON")
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
    sidecars = detect_helm_sidecars(artifact_dir)
    return {
        "artifact_dir": artifact_dir,
        "json_path": json_path,
        "model_id": model_id,
        "benchmark": benchmark,
        "experiment_name": None,
        "evaluation_id": data.get("evaluation_id"),
        "run_spec_fpath": sidecars["run_spec_fpath"],
        "max_eval_instances": sidecars["max_eval_instances"],
    }


def _format_pair_keys(official_meta: dict[str, Any], local_meta: dict[str, Any]) -> str:
    return (
        f"  official: {_build_logical_run_key(official_meta)} "
        f"(from {official_meta['json_path']})\n"
        f"  local:    {_build_logical_run_key(local_meta)} "
        f"(from {local_meta['json_path']})"
    )


def _write_caveats_file(
    report_dpath: Path,
    *,
    official_meta: dict[str, Any],
    local_meta: dict[str, Any],
    sidecar_status: dict[str, str],
) -> Path:
    """Emit a plain-text explainer of EEE-only metadata limitations.

    Sits next to ``core_metric_report.txt`` so a reader of the
    report can see what the EEE-only path could and couldn't answer
    *without* having to read the warnings manifest.
    """
    fpath = report_dpath / "eee_metadata_caveats.txt"
    sidecar_block = "\n".join(
        f"  {side:<8} run_spec.json: {status}"
        for side, status in sidecar_status.items()
    )
    fpath.write_text(textwrap.dedent(f"""\
    EEE-only pairwise comparison
    ============================

    This report was produced by ``eval-audit-compare-pair-eee`` against
    two ``every_eval_ever`` (EEE) artifacts. The HELM ``run_spec.json``
    that the HELM-driven path uses for several comparability checks
    {"was found alongside one or both EEE artifacts" if "present" in str(sidecar_status.values()) else "was not present alongside the EEE artifacts"}.

    Inputs
    ------
      official:           {official_meta['json_path']}
      local:              {local_meta['json_path']}
    {sidecar_block}

    Comparability facts that depend on HELM metadata
    ------------------------------------------------
    The planner records each fact with a ``status`` of ``yes`` / ``no``
    / ``unknown``. The ones that need ``run_spec.json`` (or the equivalent)
    to evaluate are:

      - ``same_scenario_class``      ← run_spec.json:scenario_spec.class_name
      - ``same_benchmark_family``    ← scenario class taxonomy
      - ``same_deployment``          ← run_spec.json:adapter_spec.model_deployment
      - ``same_instructions``        ← run_spec.json:adapter_spec.instructions
      - ``same_max_eval_instances``  ← run_spec.json:adapter_spec.max_eval_instances

    Without a sidecar ``run_spec.json``, those collapse to
    ``status=unknown`` and surface as ``comparability_unknown:*``
    warnings in ``warnings.json``. With a sidecar, they evaluate
    normally.

    What is *not* affected by the EEE-only constraint
    -------------------------------------------------
    Agreement metrics (run-level and instance-level abs-delta,
    agreement curves at every tolerance threshold, per-metric
    breakdowns) come from EEE-side data and are independent of the
    presence or absence of HELM metadata.

    Same-model identity (``same_model``) is derived from EEE
    ``model_info`` and evaluates normally.

    Recommendations
    ---------------
    See ``docs/eee-vs-helm-metadata.md`` for the full HELM↔EEE field
    mapping and recommendations on shipping ``run_spec.json`` alongside
    your EEE artifacts when you have it.
    """))
    return fpath


def _check_pairing(
    official_meta: dict[str, Any],
    local_meta: dict[str, Any],
    *,
    force: bool,
) -> None:
    keys_match = (
        official_meta["model_id"] == local_meta["model_id"]
        and official_meta["benchmark"] == local_meta["benchmark"]
    )
    if keys_match:
        return
    detail = _format_pair_keys(official_meta, local_meta)
    if not force:
        raise SystemExit(
            f"FAIL: official and local artifacts have different "
            f"logical-run keys (model+benchmark):\n{detail}\n"
            f"Pass --force-pair to compare them anyway "
            f"(local will be re-keyed to the official's identity)."
        )
    sys.stderr.write(
        f"WARN: --force-pair: re-keying local to match official's logical key.\n{detail}\n"
    )
    local_meta["model_id"] = official_meta["model_id"]
    local_meta["benchmark"] = official_meta["benchmark"]


def main(argv: list[str] | None = None) -> None:
    setup_cli_logging()
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--official",
        required=True,
        help=(
            "Path to the official EEE artifact: either the <uuid>.json "
            "aggregate file or its containing directory."
        ),
    )
    parser.add_argument(
        "--local",
        required=True,
        help=(
            "Path to the local EEE artifact: either the <uuid>.json "
            "aggregate file or its containing directory."
        ),
    )
    parser.add_argument("--out-dpath", required=True)
    parser.add_argument(
        "--experiment-name",
        default="eee_only_pair",
        help="Logical experiment name embedded in the local component ID.",
    )
    parser.add_argument(
        "--render-heavy-pairwise-plots",
        action="store_true",
        default=False,
        help="Render per-pair distribution + per-metric agreement PNGs (slow).",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        default=False,
        help="Remove --out-dpath before building.",
    )
    parser.add_argument(
        "--force-pair",
        action="store_true",
        default=False,
        help=(
            "Compare two EEE artifacts even when their model+benchmark "
            "(logical run key) doesn't match. The local side is re-keyed "
            "to the official's identity so the planner produces a packet."
        ),
    )
    args, plot_layout_args = parser.parse_known_args(argv)

    out_dir = Path(args.out_dpath).expanduser().resolve()
    if args.clean and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    official_path = _resolve_eee_artifact_path(args.official)
    local_path = _resolve_eee_artifact_path(args.local)

    official_meta = _meta_from_artifact(official_path)
    local_meta = _meta_from_artifact(local_path)
    _check_pairing(official_meta, local_meta, force=args.force_pair)

    sidecar_status = {
        "official": "present" if official_meta.get("run_spec_fpath") else "absent",
        "local": "present" if local_meta.get("run_spec_fpath") else "absent",
    }
    print(
        f"sidecar HELM run_spec.json: official={sidecar_status['official']}, "
        f"local={sidecar_status['local']}"
    )

    official_row = _build_official_index_row(official_meta)
    local_row = _build_local_index_row(local_meta, experiment_override=args.experiment_name)

    indexes_dir = out_dir / "_indexes"
    indexes_dir.mkdir(parents=True, exist_ok=True)
    official_index_fpath = _write_index_csv(
        [official_row], indexes_dir / "official_public_index.csv"
    )
    local_index_fpath = _write_index_csv(
        [local_row], indexes_dir / "audit_results_index.csv"
    )

    planning_artifact = build_planning_artifact(
        local_index_fpath=local_index_fpath,
        official_index_fpath=official_index_fpath,
        experiment_name=None,
        run_entry=None,
    )
    n_packets = planning_artifact.get("packet_count", 0)
    n_pairs = sum(
        len(packet.get("comparisons") or [])
        for packet in planning_artifact.get("packets", [])
    )
    if n_packets != 1:
        raise SystemExit(
            f"FAIL: expected planner to produce exactly 1 packet, got {n_packets}. "
            f"This likely means the two EEE artifacts didn't pair on logical_run_key."
        )
    if n_pairs == 0:
        raise SystemExit(
            "FAIL: planner produced 0 pairwise comparisons; the local row likely "
            "lacks the official counterpart with a matching logical_run_key."
        )

    print(f"planner: {n_packets} packet, {n_pairs} pairwise comparison(s)")

    packets = list(_packets_with_manifests(planning_artifact))
    packet = packets[0]

    # Write the planner manifests directly into out_dir so the report
    # surface lives at <out_dir>/core_metric_report.{txt,json,png}.
    (out_dir / "components_manifest.json").write_text(
        json.dumps(packet["components_manifest"], indent=2) + "\n"
    )
    (out_dir / "comparisons_manifest.json").write_text(
        json.dumps(packet["comparisons_manifest"], indent=2) + "\n"
    )

    cmd: list[str] = [
        sys.executable, "-m", "eval_audit.reports.core_metrics",
        "--report-dpath", str(out_dir),
        "--components-manifest", str(out_dir / "components_manifest.json"),
        "--comparisons-manifest", str(out_dir / "comparisons_manifest.json"),
    ]
    if args.render_heavy_pairwise_plots:
        cmd.append("--render-heavy-pairwise-plots")
    cmd += plot_layout_args
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2]) + (
        os.pathsep + env.get("PYTHONPATH", "") if env.get("PYTHONPATH") else ""
    )
    subprocess.run(cmd, check=True, env=env)

    caveats_fpath = _write_caveats_file(
        out_dir,
        official_meta=official_meta,
        local_meta=local_meta,
        sidecar_status=sidecar_status,
    )

    print(f"\nDONE: report at {out_dir}/core_metric_report.txt")
    print(f"      caveats at {caveats_fpath}")


if __name__ == "__main__":
    main()

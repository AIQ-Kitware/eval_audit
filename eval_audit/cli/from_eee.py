"""eval-audit-from-eee: build a comparison report from a directory of EEE artifacts.

Tutorial-grade entry point for the EEE-only path. Inputs:

* a root directory containing ``official/`` and ``local/`` subtrees, each
  containing one or more EEE artifact directories of the shape produced by
  ``every_eval_ever convert helm`` (i.e.,
  ``<root>/<dataset>/<dev>/<model>/<uuid>.json`` plus a sibling
  ``<uuid>_samples.jsonl``).

* an output directory.

The CLI:

  1. Walks both subtrees and builds in-memory index rows (no HELM
     metadata, no run_spec.json, no audit_results_index.csv on disk).
     The aggregate JSON of each EEE artifact provides the model id and
     the dataset name; the directory name above ``<uuid>.json`` gives the
     experiment name (for ``local/<experiment>/...``).

  2. Writes the synthesized indexes as CSVs alongside the output report
     so the rest of the pipeline (which is index-driven) can consume
     them unchanged.

  3. Calls ``core_report_planner.build_planning_artifact`` to pair up
     official and local runs by logical key, then runs ``rebuild_core``
     on each packet to render per-pair core-metric reports + comparability
     facts.

Each packet's report dir gets the standard ``redraw_plots.sh`` /
``reproduce.sh`` siblings so the user can iterate on plot styling
without re-running the analysis.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

from eval_audit.infra.fs_publish import write_text_atomic
from eval_audit.infra.logging import setup_cli_logging
from eval_audit.planning.core_report_planner import build_planning_artifact
from eval_audit.workflows.plan_core_report_packets import write_planning_outputs

from eval_audit.infra.profiling import profile



# ---------------------------------------------------------------------------
# EEE artifact discovery + index-row synthesis moved to
# eval_audit.normalized.eee_sources (Phase 3 sub-stage 4.4). The
# underscore aliases keep this module's historical import surface
# working (compare_pair_eee and virtual.compose imported from here
# before the move, and docs reference from_eee.detect_helm_sidecars).
# ---------------------------------------------------------------------------

from eval_audit.normalized.eee_sources import (  # noqa: F401
    build_local_index_row as _build_local_index_row,
    build_logical_run_key as _build_logical_run_key,
    build_official_index_row as _build_official_index_row,
    detect_helm_sidecars,
    discover_eee_artifacts as _discover_eee_artifacts,
    extract_artifact_meta as _extract_artifact_meta,
    stable_short_hash as _stable_short_hash,
    write_index_csv as _write_index_csv,
)



# ---------------------------------------------------------------------------
# Per-packet rebuild via the analyze_experiment workflow
# ---------------------------------------------------------------------------


@profile
def _render_packet(
    *,
    packet: dict[str, Any],
    out_root: Path,
    components_manifest_fpath: Path,
    comparisons_manifest_fpath: Path,
    plot_layout_args: list[str],
    render_heavy_plots: bool,
) -> Path:
    """Run rebuild_core_report on a single planner packet.

    The packet manifests are pre-written next to the output dir; this just
    invokes core_metrics so the per-pair plots + comparability facts are
    rendered.

    Output layout mirrors the canonical
    ``<root>/<experiment_name>/core-reports/<packet>/...`` shape so that
    ``eval-audit-build-summary --analysis-root <out_root>`` can pick the
    reports up via its standard glob without bespoke wiring.
    """
    packet_id = packet["packet_id"]
    experiment_name = (
        packet["components_manifest"].get("experiment_name") or "eee_only"
    )
    report_dpath = out_root / experiment_name / "core-reports" / packet_id
    report_dpath.mkdir(parents=True, exist_ok=True)

    (report_dpath / "components_manifest.json").write_text(
        json.dumps(packet["components_manifest"], indent=2) + "\n"
    )
    (report_dpath / "comparisons_manifest.json").write_text(
        json.dumps(packet["comparisons_manifest"], indent=2) + "\n"
    )

    cmd: list[str] = [
        sys.executable, "-m", "eval_audit.reports.core_metrics",
        "--report-dpath", str(report_dpath),
        "--components-manifest", str(report_dpath / "components_manifest.json"),
        "--comparisons-manifest", str(report_dpath / "comparisons_manifest.json"),
        # EEE-only mode: never enrich instances from HELM origins
        # (Phase 3 / 4.5 declared instance-source policy).
        "--instance-source", "eee-only",
    ]
    if render_heavy_plots:
        cmd.append("--render-heavy-pairwise-plots")
    cmd += plot_layout_args
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2]) + (
        os.pathsep + env.get("PYTHONPATH", "") if env.get("PYTHONPATH") else ""
    )
    subprocess.run(cmd, check=True, env=env)
    return report_dpath


def _packets_with_manifests(planning_artifact: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Yield per-packet dicts ready for ``_render_packet``.

    ``build_planning_artifact`` returns a single artifact dict containing all
    packets together; ``rebuild_core_report`` expects per-packet
    ``components_manifest`` + ``comparisons_manifest`` (one each per pair).
    """
    for packet in planning_artifact.get("packets", []):
        components = packet.get("components") or []
        comparisons = packet.get("comparisons") or []
        components_manifest = {
            "report_dpath": "",  # filled in by core_metrics
            "packet_id": packet.get("packet_id"),
            "run_entry": packet.get("run_entry"),
            "experiment_name": packet.get("experiment_name"),
            "planner_version": planning_artifact.get("planner_version"),
            "selected_public_track": packet.get("selected_public_track"),
            "warnings": packet.get("warnings") or [],
            "caveats": packet.get("caveats") or [],
            "comparability_facts": packet.get("comparability_facts") or {},
            "official_selection": packet.get("official_selection") or {},
            "components": components,
        }
        comparisons_manifest = {
            "report_dpath": "",
            "run_entry": packet.get("run_entry"),
            "experiment_name": packet.get("experiment_name"),
            "comparisons": comparisons,
        }
        yield {
            "packet_id": packet.get("packet_id"),
            "components_manifest": components_manifest,
            "comparisons_manifest": comparisons_manifest,
        }


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------


@profile
def _build_indexes(
    *,
    eee_root: Path,
    out_dir: Path,
    experiment_name: str | None = None,
) -> tuple[Path, Path, list[dict[str, Any]], list[dict[str, Any]]]:
    """Discover artifacts and write the synthesized index CSVs.

    ``experiment_name`` overrides the per-row experiment label that would
    otherwise be derived from the ``local/<experiment>/...`` subdirectory.
    Useful when the user wants every local row grouped under one logical
    experiment regardless of the source layout.

    Returns ``(local_index_fpath, official_index_fpath, local_rows, official_rows)``.
    """
    official_root = eee_root / "official"
    local_root = eee_root / "local"

    official_artifacts = _discover_eee_artifacts(official_root)
    local_artifacts = _discover_eee_artifacts(local_root)

    if not official_artifacts and not local_artifacts:
        raise SystemExit(
            f"FAIL: no EEE artifacts found under {eee_root}. Expected layout:\n"
            f"  {eee_root}/official/<dataset>/<dev>/<model>/<uuid>.json\n"
            f"  {eee_root}/local/<experiment>/<dataset>/<dev>/<model>/<uuid>.json"
        )

    official_rows = [
        _build_official_index_row(_extract_artifact_meta(row, root=official_root))
        for row in official_artifacts
    ]
    local_rows = [
        _build_local_index_row(
            _extract_artifact_meta(row, root=local_root),
            experiment_override=experiment_name,
        )
        for row in local_artifacts
    ]

    out_dir.mkdir(parents=True, exist_ok=True)
    official_index_fpath = _write_index_csv(
        official_rows, out_dir / "official_public_index.csv"
    )
    local_index_fpath = _write_index_csv(
        local_rows, out_dir / "audit_results_index.csv"
    )
    return local_index_fpath, official_index_fpath, local_rows, official_rows


@profile
def main(argv: list[str] | None = None) -> None:
    setup_cli_logging()
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--eee-root",
        required=True,
        help="Root of the EEE artifact tree; expects official/ and local/ subdirs.",
    )
    parser.add_argument(
        "--out-dpath",
        required=True,
        help="Output directory for the synthesized indexes + per-packet reports.",
    )
    parser.add_argument(
        "--experiment-name",
        default=None,
        help=(
            "Override the logical experiment name on every local index row "
            "(default: derive from the directory immediately below "
            "``local/`` for each artifact, falling back to ``eee_only_local`` "
            "when the layout is too shallow). The experiment name is used in "
            "component IDs and is the parent dir of the per-packet output "
            "tree, so passing this groups everything under one experiment "
            "regardless of source layout."
        ),
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
        "--build-aggregate-summary",
        action="store_true",
        default=False,
        help=(
            "After per-packet reports finish, run eval-audit-build-summary "
            "against the per-experiment subtrees produced under --out-dpath "
            "to generate a cross-packet aggregate report (agreement curves, "
            "per-metric breakdowns, README). The Stage-1 filter inventory is "
            "skipped automatically since EEE-only inputs have no Stage-1 "
            "filter sankey to fold in."
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help=(
            "Number of packets to render concurrently. Each packet runs the "
            "core_metrics CLI in its own subprocess, so the OS handles "
            "scheduling — set this to roughly half your physical cores when "
            "joining new-format EEE artifacts (those have 21x the records of "
            "old-format files and saturate a core for several minutes per "
            "packet). Default 1 (serial) preserves the original behavior. "
            "Use 0 to mean ``os.cpu_count() // 2``."
        ),
    )
    args, plot_layout_args = parser.parse_known_args(argv)

    eee_root = Path(args.eee_root).expanduser().resolve()
    out_dir = Path(args.out_dpath).expanduser().resolve()
    if args.clean and out_dir.exists():
        shutil.rmtree(out_dir)

    local_index_fpath, official_index_fpath, local_rows, official_rows = _build_indexes(
        eee_root=eee_root, out_dir=out_dir, experiment_name=args.experiment_name,
    )
    print(
        f"discovered: {len(official_rows)} official + {len(local_rows)} local artifacts under {eee_root}"
    )
    print(f"  official_index: {official_index_fpath}")
    print(f"  local_index:    {local_index_fpath}")

    planning_artifact = build_planning_artifact(
        local_index_fpath=local_index_fpath,
        official_index_fpath=official_index_fpath,
        experiment_name=None,  # don't filter by experiment_name; demo includes all
        run_entry=None,
    )

    planning_dpath = out_dir / "planning"
    write_planning_outputs(artifact=planning_artifact, out_dpath=planning_dpath)
    print(f"  planning_dir:   {planning_dpath}")

    n_packets = planning_artifact.get("packet_count", 0)
    n_pairs = sum(
        len(packet.get("comparisons") or [])
        for packet in planning_artifact.get("packets", [])
    )
    print(f"planner: {n_packets} packets, {n_pairs} pairwise comparisons")

    # Resolve --workers. ``0`` means "auto" -> half of cpu_count (rounded up,
    # at least 1) so we leave headroom for the OS, the user's other work,
    # and the per-subprocess pandas/matplotlib spike. Negative values pin
    # to 1 with a warning.
    if args.workers == 0:
        worker_count = max(1, (os.cpu_count() or 2) // 2)
    elif args.workers < 0:
        print(
            f"  WARN: --workers={args.workers} is invalid; using 1 (serial).",
            file=sys.stderr,
        )
        worker_count = 1
    else:
        worker_count = args.workers
    print(f"rendering: {worker_count} worker(s) (--workers={args.workers})")

    rendered: list[Path] = []
    packet_entries = list(_packets_with_manifests(planning_artifact))
    if worker_count <= 1:
        # Original serial path. Preserved verbatim so the behavior of the
        # default invocation does not change.
        for entry in packet_entries:
            report_dpath = _render_packet(
                packet=entry,
                out_root=out_dir,
                components_manifest_fpath=Path(),  # constructed inline
                comparisons_manifest_fpath=Path(),
                plot_layout_args=plot_layout_args,
                render_heavy_plots=args.render_heavy_pairwise_plots,
            )
            rendered.append(report_dpath)
            print(f"  rendered: {report_dpath}")
    else:
        # Parallel path. ThreadPoolExecutor (not ProcessPool) because each
        # _render_packet already spawns a core_metrics subprocess; we just
        # need to keep N of them in flight without blocking. Each thread
        # does almost no in-process work, so the GIL is irrelevant.
        from concurrent.futures import ThreadPoolExecutor, as_completed

        # We collect any rendering failures and re-raise the first one
        # *after* all in-flight workers finish, so the user gets a
        # complete picture of what did/didn't render rather than a
        # mid-flight crash. ``check=True`` inside ``_render_packet`` will
        # propagate CalledProcessError, which we catch per-future.
        first_failure: tuple[str, BaseException] | None = None
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            future_to_packet_id = {
                pool.submit(
                    _render_packet,
                    packet=entry,
                    out_root=out_dir,
                    components_manifest_fpath=Path(),
                    comparisons_manifest_fpath=Path(),
                    plot_layout_args=plot_layout_args,
                    render_heavy_plots=args.render_heavy_pairwise_plots,
                ): entry["packet_id"]
                for entry in packet_entries
            }
            for future in as_completed(future_to_packet_id):
                packet_id = future_to_packet_id[future]
                try:
                    report_dpath = future.result()
                except BaseException as exc:  # noqa: BLE001
                    print(f"  FAILED:   {packet_id}: {exc}", file=sys.stderr)
                    if first_failure is None:
                        first_failure = (packet_id, exc)
                else:
                    rendered.append(report_dpath)
                    print(f"  rendered: {report_dpath}  ({len(rendered)}/{len(packet_entries)})")
        if first_failure is not None:
            packet_id, exc = first_failure
            raise RuntimeError(
                f"per-packet rendering failed for {packet_id}; "
                f"{len(packet_entries) - len(rendered)} packet(s) did not "
                f"complete; first failure was: {exc}"
            ) from exc

    print(f"\nDONE: {len(rendered)} per-pair core-metric reports under {out_dir}/<experiment>/core-reports/")

    if args.build_aggregate_summary:
        summary_root = out_dir / "aggregate-summary"
        summary_cmd = [
            sys.executable, "-m", "eval_audit.workflows.build_reports_summary",
            "--no-filter-inventory",
            "--no-canonical-scan",
            "--analysis-root", str(out_dir),
            "--index-fpath", str(local_index_fpath),
            "--summary-root", str(summary_root),
        ]
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2]) + (
            os.pathsep + env.get("PYTHONPATH", "") if env.get("PYTHONPATH") else ""
        )
        print(f"\nBuilding aggregate summary under {summary_root}/ ...")
        subprocess.run(summary_cmd, check=True, env=env)
        print(f"DONE: aggregate summary at {summary_root}/all-results/")


if __name__ == "__main__":
    main()

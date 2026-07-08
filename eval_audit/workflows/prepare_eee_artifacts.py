from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from loguru import logger

import datetime as datetime_mod
import io as _io

from eval_audit.infra.fs_publish import write_text_atomic
from eval_audit.infra.logging import rich_link, setup_cli_logging
from eval_audit.normalized.eee_artifacts import (
    default_local_eee_root,
    default_official_eee_root,
    resolve_local_eee_artifact,
    resolve_official_eee_artifact,
)
from eval_audit.planning.core_report_planner import load_index_rows
from eval_audit.utils.coercion import clean_optional_text as _clean_optional_text  # R-6


def _selected_local_rows(
    rows: list[dict[str, Any]],
    *,
    experiment_name: str | None,
    run_entry: str | None,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for row in rows:
        if experiment_name and row.get("experiment_name") != experiment_name:
            continue
        if run_entry and row.get("run_entry") != run_entry and row.get("logical_run_key") != run_entry:
            continue
        if not _clean_optional_text(row.get("run_path") or row.get("run_dir")):
            continue
        selected.append(row)
    return selected


def _selected_official_rows(
    rows: list[dict[str, Any]],
    *,
    run_entry: str | None,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for row in rows:
        if run_entry and row.get("run_name") != run_entry and row.get("logical_run_key") != run_entry:
            continue
        selected.append(row)
    return selected


def _write_csv(rows: list[dict[str, Any]], fpath: Path) -> None:
    fieldnames = sorted({key for row in rows for key in row.keys()}) if rows else []
    if not fieldnames:
        write_text_atomic(fpath, "")
        return
    buf = _io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    write_text_atomic(fpath, buf.getvalue())


def _summary_lines(payload: dict[str, Any]) -> list[str]:
    lines = [
        "EEE Artifact Readiness",
        "",
        f"generated_utc: {payload.get('generated_utc')}",
        f"local_index_fpath: {payload.get('local_index_fpath')}",
        f"official_index_fpath: {payload.get('official_index_fpath')}",
        f"official_eee_root: {payload.get('official_eee_root')}",
        f"local_eee_root: {payload.get('local_eee_root')}",
        f"experiment_name: {payload.get('experiment_name')}",
        f"run_entry: {payload.get('run_entry')}",
        f"ensure_local: {payload.get('ensure_local')}",
        "",
        f"n_official_rows_checked: {payload.get('n_official_rows_checked')}",
        f"n_official_eee_found: {payload.get('n_official_eee_found')}",
        f"n_local_rows_checked: {payload.get('n_local_rows_checked')}",
        f"n_local_eee_found_or_generated: {payload.get('n_local_eee_found_or_generated')}",
        f"n_local_eee_generated: {payload.get('n_local_eee_generated')}",
        f"n_local_eee_missing: {payload.get('n_local_eee_missing')}",
        "",
        "official_rows:",
    ]
    for row in payload.get("official_rows", []):
        lines.append(
            f"  - {row.get('run_name')}: status={row.get('eee_status')} "
            f"artifact={row.get('eee_artifact_path')}"
        )
    lines.append("")
    lines.append("local_rows:")
    for row in payload.get("local_rows", []):
        lines.append(
            f"  - {row.get('experiment_name')} {row.get('job_id')} {row.get('run_entry')}: "
            f"status={row.get('eee_status')} artifact={row.get('eee_artifact_path')}"
        )
    return lines


def main(argv: list[str] | None = None) -> None:
    setup_cli_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-index-fpath", required=True)
    parser.add_argument("--official-index-fpath", required=True)
    parser.add_argument("--experiment-name", default=None)
    parser.add_argument("--run-entry", default=None)
    parser.add_argument("--official-eee-root", default=None)
    parser.add_argument("--local-eee-root", default=None)
    parser.add_argument("--ensure-local", action="store_true")
    parser.add_argument("--out-dpath", required=True)
    args = parser.parse_args(argv)

    local_index_fpath = Path(args.local_index_fpath).expanduser().resolve()
    official_index_fpath = Path(args.official_index_fpath).expanduser().resolve()
    official_eee_root = Path(args.official_eee_root).expanduser().resolve() if args.official_eee_root else default_official_eee_root()
    local_eee_root = Path(args.local_eee_root).expanduser().resolve() if args.local_eee_root else default_local_eee_root()
    out_dpath = Path(args.out_dpath).expanduser().resolve()
    out_dpath.mkdir(parents=True, exist_ok=True)
    stamp = datetime_mod.datetime.now(datetime_mod.UTC).strftime("%Y%m%dT%H%M%SZ")

    local_rows = _selected_local_rows(
        load_index_rows(local_index_fpath),
        experiment_name=args.experiment_name,
        run_entry=args.run_entry,
    )
    official_rows = _selected_official_rows(
        load_index_rows(official_index_fpath),
        run_entry=args.run_entry,
    )

    prepared_local: list[dict[str, Any]] = []
    for row in local_rows:
        resolution = resolve_local_eee_artifact(
            row,
            local_eee_root=local_eee_root,
            ensure=args.ensure_local,
        )
        prepared_local.append(
            {
                "source_kind": "local",
                "experiment_name": row.get("experiment_name"),
                "job_id": row.get("job_id"),
                "run_entry": row.get("run_entry"),
                "run_path": row.get("run_path") or row.get("run_dir"),
                "eee_artifact_path": str(resolution.artifact_path) if resolution.artifact_path else None,
                "eee_status": resolution.status,
                "eee_source": resolution.source,
                "eee_generated": resolution.generated,
                "eee_message": resolution.message,
                "status_path": str(resolution.status_path) if resolution.status_path else None,
                "provenance_path": str(resolution.provenance_path) if resolution.provenance_path else None,
            }
        )

    prepared_official: list[dict[str, Any]] = []
    for row in official_rows:
        resolution = resolve_official_eee_artifact(
            row,
            official_eee_root=official_eee_root,
        )
        prepared_official.append(
            {
                "source_kind": "official",
                "public_track": row.get("public_track"),
                "suite_version": row.get("suite_version"),
                "run_name": row.get("run_name"),
                "run_path": row.get("run_path") or row.get("public_run_dir"),
                "eee_artifact_path": str(resolution.artifact_path) if resolution.artifact_path else None,
                "eee_status": resolution.status,
                "eee_source": resolution.source,
                "eee_message": resolution.message,
                "status_path": str(resolution.status_path) if resolution.status_path else None,
            }
        )

    payload = {
        "generated_utc": stamp,
        "local_index_fpath": str(local_index_fpath),
        "official_index_fpath": str(official_index_fpath),
        "official_eee_root": str(official_eee_root),
        "local_eee_root": str(local_eee_root),
        "experiment_name": args.experiment_name,
        "run_entry": args.run_entry,
        "ensure_local": args.ensure_local,
        "n_official_rows_checked": len(prepared_official),
        "n_official_eee_found": sum(1 for row in prepared_official if row.get("eee_artifact_path")),
        "n_local_rows_checked": len(prepared_local),
        "n_local_eee_found_or_generated": sum(1 for row in prepared_local if row.get("eee_artifact_path")),
        "n_local_eee_generated": sum(1 for row in prepared_local if row.get("eee_generated") and row.get("eee_artifact_path")),
        "n_local_eee_missing": sum(1 for row in prepared_local if not row.get("eee_artifact_path")),
        "official_rows": prepared_official,
        "local_rows": prepared_local,
    }

    json_fpath = out_dpath / "eee_readiness.json"
    txt_fpath = out_dpath / "eee_readiness.txt"
    local_csv_fpath = out_dpath / "eee_local_rows.csv"
    official_csv_fpath = out_dpath / "eee_official_rows.csv"

    write_text_atomic(json_fpath, json.dumps(payload, indent=2) + "\n")
    write_text_atomic(txt_fpath, "\n".join(_summary_lines(payload)) + "\n")
    _write_csv(prepared_local, local_csv_fpath)
    _write_csv(prepared_official, official_csv_fpath)

    logger.info(f"Wrote EEE readiness json: {rich_link(json_fpath)}")
    logger.info(f"Wrote EEE readiness text: {rich_link(txt_fpath)}")
    logger.info(f"Wrote local readiness CSV: {rich_link(local_csv_fpath)}")
    logger.info(f"Wrote official readiness CSV: {rich_link(official_csv_fpath)}")


if __name__ == "__main__":
    setup_cli_logging()
    main()

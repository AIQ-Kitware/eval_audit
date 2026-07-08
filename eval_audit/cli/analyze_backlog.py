"""Operational tool: rebuild core reports for the analyzable backlog.

Reads the latest Stage 6 ``run_inventory`` and re-runs the per-pair
core report only for rows marked ``completed_with_run_artifacts`` that
have no existing ``repro_report_dir``. Invoked manually via
``python -m eval_audit.cli.analyze_backlog`` (not wired as a console
script; see dev/journals/codex.md for the operational history).
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from collections import Counter
from pathlib import Path

from eval_audit.infra.logging import setup_cli_logging
from eval_audit.infra.paths import experiment_analysis_dpath
from eval_audit.infra.report_layout import aggregate_summary_reports_root
from eval_audit.workflows.rebuild_core_report import (
    main as rebuild_core_report_main,
    slugify_identifier,
)
from eval_audit.workflows import build_reports_summary


def _default_all_results_history_root() -> Path:
    """The aggregate-summary run-inventory history root.

    Derived from the report-layout helper (honours
    ``HELM_AUDIT_PUBLICATION_ROOT``) rather than ``Path(__file__).parents[2]``,
    so a non-editable install does not point this into site-packages.
    """
    return aggregate_summary_reports_root() / "all-results" / ".history"


# R-6: shared with cli.portfolio_status; canonical def in infra.index_io.
from eval_audit.infra.index_io import latest_run_inventory_csv as _latest_run_inventory_csv


def _load_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as file:
        return [{k: ("" if v is None else v) for k, v in row.items()} for row in csv.DictReader(file)]


def _is_backlog_row(row: dict[str, str]) -> bool:
    return (
        row.get("lifecycle_stage") == "completed_with_run_artifacts"
        and not (row.get("repro_report_dir") or "").strip()
        and bool((row.get("run_entry") or "").strip())
    )


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen = set()
    out = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def main(argv: list[str] | None = None) -> None:
    setup_cli_logging()
    parser = argparse.ArgumentParser(
        description="Rebuild only the completed-but-not-analyzed run entries for one or more experiments."
    )
    parser.add_argument("--index-fpath", required=True)
    parser.add_argument(
        "--run-inventory-csv",
        default=None,
        help="Optional aggregate run_inventory CSV. Defaults to the latest all-results run inventory in the repo.",
    )
    parser.add_argument(
        "--all-results-history-root",
        default=None,
        help="Used only when --run-inventory-csv is omitted. Defaults to the "
             "aggregate-summary all-results history under the publication root.",
    )
    parser.add_argument(
        "--experiment-name",
        dest="experiment_names",
        action="append",
        required=True,
        help="Experiment to rebuild backlog for. Repeat for multiple experiments.",
    )
    parser.add_argument("--build-summary", action="store_true")
    parser.add_argument("--filter-inventory-json", default=None)
    args = parser.parse_args(argv)

    history_root = (
        Path(args.all_results_history_root).expanduser().resolve()
        if args.all_results_history_root
        else _default_all_results_history_root()
    )
    run_inventory_csv = (
        Path(args.run_inventory_csv).expanduser().resolve()
        if args.run_inventory_csv
        else _latest_run_inventory_csv(history_root)
    )
    rows = _load_csv_rows(run_inventory_csv)
    by_experiment: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_experiment[str(row.get("experiment_name") or "")].append(row)

    for experiment_name in args.experiment_names:
        exp_rows = by_experiment.get(experiment_name, [])
        backlog_entries = _dedupe_keep_order(
            [row["run_entry"] for row in exp_rows if _is_backlog_row(row)]
        )
        skipped = []
        print(
            f"BEGIN {experiment_name} backlog_count={len(backlog_entries)} run_inventory_csv={run_inventory_csv}",
            flush=True,
        )
        reports_dpath = experiment_analysis_dpath(experiment_name) / "core-reports"
        reports_dpath.mkdir(parents=True, exist_ok=True)
        for run_entry in backlog_entries:
            report_dpath = reports_dpath / f"core-metrics-{slugify_identifier(run_entry)}"
            cmd = [
                "--run-entry",
                run_entry,
                "--index-fpath",
                args.index_fpath,
                "--experiment-name",
                experiment_name,
                "--report-dpath",
                str(report_dpath),
            ]
            print(f"REBUILD {experiment_name} :: {run_entry}", flush=True)
            try:
                rebuild_core_report_main(cmd)
            except (Exception, SystemExit) as ex:
                skipped.append({"run_entry": run_entry, "error": str(ex)})
                print(f"SKIP {experiment_name} :: {run_entry} :: {ex}", flush=True)
        if skipped:
            error_counts = Counter(item["error"] for item in skipped)
            print(f"SKIP_SUMMARY {experiment_name}", flush=True)
            for error, count in error_counts.most_common():
                print(f"  - count={count} error={error}", flush=True)
        print(
            f"END {experiment_name} rebuilt={len(backlog_entries) - len(skipped)} skipped={len(skipped)}",
            flush=True,
        )

    if args.build_summary:
        print("BEGIN build_reports_summary", flush=True)
        cmd = ["--index-fpath", args.index_fpath]
        if args.filter_inventory_json:
            cmd.extend(["--filter-inventory-json", args.filter_inventory_json])
        build_reports_summary.main(cmd)
        print("END build_reports_summary", flush=True)


if __name__ == "__main__":
    main()

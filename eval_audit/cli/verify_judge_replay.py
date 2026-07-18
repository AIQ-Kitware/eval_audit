"""eval-audit-verify-judge-replay: the identity-replay stop gate.

Phase 3 of the open-judge experiment (docs/planning/open-judge-plan.md
§8): for each response snapshot, reattach the ORIGINAL official
annotations and prove they reproduce the published judge-dependent
metric exactly (tolerance 1e-12) BEFORE any request is sent to a
replacement judge. Exits nonzero if any snapshot fails — a runbook
preflight (09_verify_official_identity_replay.sh) treats that as a
hard stop.

Example::

    eval-audit-verify-judge-replay \\
        /data/crfm-helm-audit-store/open-judge/response-snapshots/* \\
        --output /data/crfm-helm-audit-store/open-judge/replay-report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from eval_audit.infra.logging import setup_cli_logging
from eval_audit.judging.metric_replay import REPLAY_TOLERANCE, replay_official_annotations


def main(argv: list[str] | None = None) -> None:
    setup_cli_logging()
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "snapshot",
        nargs="+",
        help="Response-snapshot directories to verify (one per response_set_hash).",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=REPLAY_TOLERANCE,
        help=f"Absolute tolerance for the exact-reproduction check (default {REPLAY_TOLERANCE}).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional JSON path to write the full per-snapshot replay report.",
    )
    args = parser.parse_args(argv)

    reports = []
    n_failed = 0
    for snapshot in args.snapshot:
        try:
            report = replay_official_annotations(snapshot, tolerance=args.tolerance)
        except Exception as ex:  # noqa: BLE001 - report, don't die mid-sweep
            n_failed += 1
            print(f"ERROR {snapshot}: {type(ex).__name__}: {ex}", file=sys.stderr)
            reports.append({"snapshot": str(snapshot), "error": f"{type(ex).__name__}: {ex}"})
            continue
        record = dict(report.as_dict(), snapshot=str(snapshot))
        reports.append(record)
        if report.ok:
            print(
                f"OK   {report.benchmark:<20} {snapshot}  "
                f"(agg={report.num_compared_aggregate_rows}, "
                f"inst={report.num_compared_instance_rows}, max_err={report.max_absolute_error:g})"
            )
        else:
            n_failed += 1
            print(f"FAIL {report.benchmark:<20} {snapshot}", file=sys.stderr)
            print(
                f"       aggregate_match={report.aggregate_match} "
                f"per_instance_match={report.per_instance_match} "
                f"missing={report.num_missing_source_rows} "
                f"extra={report.num_extra_replayed_rows} "
                f"max_err={report.max_absolute_error:g}",
                file=sys.stderr,
            )
            for mismatch in report.mismatches[:10]:
                print(f"         - {mismatch}", file=sys.stderr)

    if args.output:
        out_fpath = Path(args.output)
        out_fpath.parent.mkdir(parents=True, exist_ok=True)
        with open(out_fpath, "w", encoding="utf-8") as file:
            json.dump({"num_snapshots": len(reports), "num_failed": n_failed,
                       "reports": reports}, file, indent=2)
            file.write("\n")
        print(f"report: {out_fpath}")

    print(f"{len(reports) - n_failed}/{len(reports)} snapshot(s) reproduced exactly")
    if n_failed:
        sys.exit(1)


if __name__ == "__main__":
    main()

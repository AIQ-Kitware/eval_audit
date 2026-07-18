"""eval-audit-analyze-judges: judge-substitution comparison report.

Phase 13 of the open-judge experiment (docs/planning/open-judge-plan.md
§18): join a response snapshot's official annotations with the rejudge
artifacts produced against it (by response-set hash + display key) and
report how each open judge compares with each official judge, the
official ensemble baseline, and the other open arms — with replicate
variance and parser/request failure rates.

Example::

    eval-audit-analyze-judges \\
        --snapshot /data/.../response-snapshots/<response_set_hash> \\
        --results-root /data/.../open-judge-results \\
        --output /data/.../open-judge/analysis/<hash>.json \\
        --text /data/.../open-judge/analysis/<hash>.txt
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from eval_audit.infra.logging import setup_cli_logging
from eval_audit.judging.analysis import analyze_snapshot_judges, render_report_text
from eval_audit.judging.indexing import discover_rejudge_artifacts


def main(argv: list[str] | None = None) -> None:
    setup_cli_logging()
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--snapshot", required=True, help="Response-snapshot directory.")
    parser.add_argument(
        "--results-root",
        required=True,
        help="Root under which rejudge artifacts live (filtered by response-set hash).",
    )
    parser.add_argument("--output", default=None, help="JSON report path.")
    parser.add_argument("--text", default=None, help="Human-readable report path.")
    args = parser.parse_args(argv)

    artifacts = discover_rejudge_artifacts(args.results_root)
    if not artifacts:
        print(f"no rejudge artifacts under {args.results_root}", file=sys.stderr)
        sys.exit(1)

    report = analyze_snapshot_judges(args.snapshot, artifacts)
    if not report["open_arms"]:
        print(
            f"no rejudge artifacts match the snapshot's response_set_hash "
            f"({report['response_set_hash']})",
            file=sys.stderr,
        )
        sys.exit(1)

    text = render_report_text(report)
    if args.output:
        out_fpath = Path(args.output)
        out_fpath.parent.mkdir(parents=True, exist_ok=True)
        with open(out_fpath, "w", encoding="utf-8") as file:
            json.dump(report, file, indent=2)
            file.write("\n")
        print(f"report: {out_fpath}")
    if args.text:
        text_fpath = Path(args.text)
        text_fpath.parent.mkdir(parents=True, exist_ok=True)
        text_fpath.write_text(text)
        print(f"text report: {text_fpath}")
    print()
    print(text)


if __name__ == "__main__":
    main()

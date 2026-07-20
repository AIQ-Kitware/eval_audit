"""eval-audit-audit-judge-sources: audit HELM runs for rejudging suitability.

Phase 1 of the open-judge experiment (docs/planning/open-judge-plan.md
§6): sweep candidate source runs (public display artifacts or local run
dirs) and record, per run, whether the artifacts carry everything the
annotation-only rejudging reconstruction needs — display-key integrity,
official judge annotations, judge metrics in stats, and per-benchmark
instance fields. Emits a JSON report; never modifies the source tree.

Examples::

    eval-audit-audit-judge-sources /data/crfm-helm-public \\
        --model openai/gpt-oss-20b \\
        --benchmarks xstest wildbench omni_math \\
        --output /data/crfm-helm-audit-store/open-judge/source-audit.json

    eval-audit-audit-judge-sources --run-dir /path/to/one/run --output audit.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from eval_audit.infra.logging import setup_cli_logging
from eval_audit.judging.source_audit import BENCHMARK_PROFILES, audit_sources


def main(argv: list[str] | None = None) -> None:
    setup_cli_logging()
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=None,
        help="Corpus root to sweep for run dirs (dirs containing run_spec.json).",
    )
    parser.add_argument(
        "--run-dir",
        action="append",
        default=None,
        help="Audit this explicit run directory (repeatable; skips discovery).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Only audit runs whose adapter_spec.model matches exactly.",
    )
    parser.add_argument(
        "--benchmarks",
        nargs="+",
        default=None,
        choices=sorted(BENCHMARK_PROFILES),
        help="Only audit these benchmark families (default: all six).",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Where to write the JSON audit report.",
    )
    args = parser.parse_args(argv)

    if args.root is None and not args.run_dir:
        parser.error("provide a corpus root or at least one --run-dir")

    report = audit_sources(
        root=args.root or "",
        benchmarks=args.benchmarks,
        model=args.model,
        run_dirs=args.run_dir,
    )

    out_fpath = Path(args.output)
    out_fpath.parent.mkdir(parents=True, exist_ok=True)
    with open(out_fpath, "w", encoding="utf-8") as file:
        json.dump(report, file, indent=2)
        file.write("\n")

    print(f"audited {report['num_runs']} run(s): "
          f"{report['num_supported']} supported, "
          f"{report['num_unsupported']} unsupported")
    for record in report["records"]:
        status = "OK " if record["supported_for_rejudging"] else "SKIP"
        line = f"  [{status}] {record['run_spec_name'] or record['run_path']}"
        print(line)
        for reason in record["unsupported_reasons"]:
            print(f"         - {reason}")
    print(f"report: {out_fpath}")

    # Nonzero exit when nothing usable was found: runbook preflights
    # (05_audit_source_artifacts.sh) treat that as a hard failure.
    if report["num_runs"] == 0 or report["num_supported"] == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()

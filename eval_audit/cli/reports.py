"""Deprecated grouped dispatcher kept for previously generated artifacts.

Filter-report ``reproduce.sh`` / ``rebuild_analysis.sh`` scripts written
before 2026-06-11 invoke ``python -m eval_audit.cli.reports filter ...``,
so this module must keep resolving those commands (ADR 5: every
generated output gets a reproduce script). Newly generated scripts and
all documentation use the flat surface instead:

- ``filter``     -> ``python -m eval_audit.reports.filter_analysis``
- ``pair``       -> ``eval-audit-compare-pair``
- ``core``       -> ``eval-audit-report-core``
- ``aggregate``  -> ``eval-audit-report-aggregate``
- ``experiment`` -> ``eval-audit-analyze-experiment``

Do not add new subcommands here.
"""
from __future__ import annotations

import argparse

from eval_audit.infra.logging import setup_cli_logging
from eval_audit.reports.aggregate import main as aggregate_main
from eval_audit.reports.core_metrics import main as core_main
from eval_audit.reports.filter_analysis import main as filter_main
from eval_audit.reports.pair_report import main as pair_main
from eval_audit.reports.quantiles import main as quantiles_main
from eval_audit.workflows.analyze_experiment import main as experiment_main


def main(argv: list[str] | None = None) -> None:
    setup_cli_logging()
    parser = argparse.ArgumentParser(description="Report-oriented CLI surface.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("pair")
    subparsers.add_parser("core")
    subparsers.add_parser("aggregate")
    subparsers.add_parser("filter")
    subparsers.add_parser("quantiles")
    subparsers.add_parser("experiment")
    args, remaining = parser.parse_known_args(argv)
    if args.command == "pair":
        pair_main(remaining)
    elif args.command == "core":
        core_main(remaining)
    elif args.command == "aggregate":
        aggregate_main(remaining)
    elif args.command == "filter":
        filter_main(remaining)
    elif args.command == "quantiles":
        quantiles_main(remaining)
    else:
        experiment_main(remaining)


if __name__ == "__main__":
    main()

"""eval-audit-package-analyses: Stage 2 of transfer packaging.

Consumes the inventory from ``eval-audit-crawl-analyses``, follows every
external reference out of the included analyses, deduplicates what they
point at, and copies the result into one package directory.

    # what would be copied, and how big
    eval-audit-package-analyses --inventory-fpath analysis_inventory.jsonl \\
        --package-dpath /scratch/eval-audit-package --dry-run

    # do it
    eval-audit-package-analyses --inventory-fpath analysis_inventory.jsonl \\
        --package-dpath /scratch/eval-audit-package

    # after extracting the archive somewhere else
    eval-audit-package-analyses --repoint /new/location/eval-audit-package

Always dry-run first: the plan reports the byte total before anything is
copied, and the copy is the expensive half.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from loguru import logger

from eval_audit.infra.logging import setup_cli_logging
from eval_audit.packaging.crawl import read_inventory
from eval_audit.packaging.policy import DEFAULT_SOURCE_ROOTS
from eval_audit.packaging.pack import build_plan, execute_plan, repoint, write_plan


def _print_plan(plan) -> None:
    from collections import Counter

    by_rule: Counter[str] = Counter()
    bytes_by_rule: Counter[str] = Counter()
    for artifact in plan.artifacts:
        by_rule[artifact.rule] += 1
        bytes_by_rule[artifact.rule] += artifact.n_bytes

    analysis_bytes = sum(a.n_bytes for a in plan.analyses)
    print(f"\nanalyses included      {len(plan.analyses):>7}  "
          f"{analysis_bytes / 1e9:>8.2f} GB")
    print(f"references             {plan.ref_table.total_refs:>7}")
    print(f"distinct paths         {len(plan.ref_table):>7}  "
          f"({plan.ref_table.total_refs / max(len(plan.ref_table), 1):.2f}x dedup)")
    print(f"artifacts to copy      {len(plan.artifacts):>7}")
    for rule in sorted(by_rule):
        print(f"  {rule:<20} {by_rule[rule]:>7}  {bytes_by_rule[rule] / 1e9:>8.2f} GB")
    print(f"catalog-only, skipped  {plan.catalog_only:>7}  "
          f"(public runs no packet references)")
    print(f"unresolved references  {len(plan.missing):>7}")
    print(f"{'TOTAL':<22} {'':>7}  {plan.n_bytes / 1e9:>8.2f} GB\n")

    if plan.missing:
        from collections import Counter as C

        reasons = C(reason for _, reason, _ in plan.missing)
        print("unresolved by reason:")
        for reason, n in reasons.most_common():
            print(f"  {reason:<45} {n:>5}")
        print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Package analyses and their referenced artifacts for transfer.",
    )
    parser.add_argument(
        "--inventory-fpath",
        type=Path,
        help="JSONL inventory from eval-audit-crawl-analyses",
    )
    parser.add_argument(
        "--package-dpath",
        type=Path,
        help="destination package directory",
    )
    parser.add_argument(
        "--repoint",
        type=Path,
        metavar="PACKAGE_DPATH",
        help="re-apply recorded rewrites for a package that has moved, then exit",
    )
    parser.add_argument(
        "--source-root",
        action="append",
        dest="source_roots",
        metavar="PATH",
        help=(
            "absolute path root to follow and rewrite; repeatable. "
            f"Default: {' '.join(DEFAULT_SOURCE_ROOTS)}. This is an exact "
            "allowlist -- upstream HELM scenario paths under /data are "
            "deliberately left alone."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="plan and report sizes without copying",
    )
    parser.add_argument(
        "--plan-out",
        type=Path,
        help=(
            "write the resolved plan as JSON, largest artifact first. "
            "Review this before a long copy: a packaging mistake shows up "
            "as one implausibly large unit at the top."
        ),
    )
    parser.add_argument(
        "--no-rewrite",
        action="store_true",
        help="copy verbatim, leaving absolute paths pointing at the source store",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="re-copy files that already exist at the destination",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_cli_logging(level=args.log_level)

    if args.repoint:
        if not (args.repoint / "rewrites.json").exists():
            logger.error(f"no rewrites.json in {args.repoint}")
            return 2
        repoint(args.repoint)
        return 0

    if not args.inventory_fpath or not args.package_dpath:
        logger.error("--inventory-fpath and --package-dpath are required")
        return 2
    if not args.inventory_fpath.exists():
        logger.error(f"inventory not found: {args.inventory_fpath}")
        return 2

    roots = tuple(args.source_roots) if args.source_roots else DEFAULT_SOURCE_ROOTS
    _, records = read_inventory(args.inventory_fpath)
    included = [r for r in records if r.include]
    logger.info(f"{len(included)} of {len(records)} analyses included")
    if not included:
        logger.error("inventory includes no analyses")
        return 1

    plan = build_plan(records, roots=roots)
    _print_plan(plan)

    if args.plan_out:
        write_plan(plan, args.plan_out)
        logger.info(f"plan written to {args.plan_out}")

    if args.dry_run:
        logger.info("dry run: nothing copied")
        return 0

    package_dpath = args.package_dpath.resolve()
    manifest = execute_plan(
        plan,
        package_dpath,
        roots=roots,
        rewrite=not args.no_rewrite,
        resume=not args.no_resume,
    )

    errors = manifest["counts"]["verification_errors"]
    notes = manifest["counts"]["verification_notes"]
    logger.info(f"package written to {package_dpath}")
    if notes:
        logger.info(
            f"{notes} findings carried over from the source store "
            "(already-broken symlinks); see MANIFEST.json 'problems'"
        )
    if errors:
        logger.error(f"{errors} verification errors; see MANIFEST.json 'problems'")
        return 1
    logger.info("verification clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())

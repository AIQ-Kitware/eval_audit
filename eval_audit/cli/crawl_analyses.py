"""eval-audit-crawl-analyses: Stage 1 of transfer packaging.

Walks a store and writes a JSONL inventory of every analysis it finds.
Resolves no references and copies nothing --- the inventory is the
decision surface you edit (``"include": false``, ``"freshness": ...``)
before running ``eval-audit-package-analyses`` over it.

    eval-audit-crawl-analyses --store-dpath /data/crfm-helm-audit-store \\
        --out-fpath analysis_inventory.jsonl

Re-crawling an existing inventory preserves your hand edits unless
``--reset`` is passed: ``include``, ``freshness`` and ``notes`` are
carried over by analysis id, while the measured fields are refreshed.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from loguru import logger

from eval_audit.infra.logging import setup_cli_logging
from eval_audit.packaging.crawl import (
    crawl_store,
    read_inventory,
    summarize,
    write_inventory,
)


def _carry_forward(records, previous_fpath: Path) -> int:
    """Re-apply hand edits from an earlier inventory, by analysis id."""
    try:
        _, previous = read_inventory(previous_fpath)
    except (OSError, ValueError) as exc:
        logger.warning(f"cannot read previous inventory {previous_fpath}: {exc}")
        return 0
    edits = {
        r.id: (r.include, r.freshness, r.notes)
        for r in previous
        if not r.include or r.freshness != "unverified" or r.notes
    }
    n = 0
    for record in records:
        if record.id in edits:
            record.include, record.freshness, carried = edits[record.id]
            record.notes = [*carried, *record.notes]
            n += 1
    return n


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Enumerate packageable analyses in an eval_audit store.",
    )
    parser.add_argument(
        "--store-dpath",
        type=Path,
        default=Path("/data/crfm-helm-audit-store"),
        help="store root to crawl (default: %(default)s)",
    )
    parser.add_argument(
        "--out-fpath",
        type=Path,
        required=True,
        help="JSONL inventory to write",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="discard hand edits in an existing inventory instead of carrying them forward",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_cli_logging(level=args.log_level)

    if not args.store_dpath.is_dir():
        logger.error(f"store not found: {args.store_dpath}")
        return 2

    logger.info(f"crawling {args.store_dpath}")
    records = crawl_store(args.store_dpath)
    if not records:
        logger.error("no analyses found; is this a store root?")
        return 1

    if args.out_fpath.exists() and not args.reset:
        n = _carry_forward(records, args.out_fpath)
        if n:
            logger.info(f"carried forward hand edits on {n} analyses")

    write_inventory(records, args.out_fpath, args.store_dpath)
    logger.info(f"wrote {len(records)} analyses to {args.out_fpath}")
    print(summarize(records))
    return 0


if __name__ == "__main__":
    sys.exit(main())

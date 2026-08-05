"""Check that a store's packets still describe the artifacts on disk.

The reports record content digests of their scoring inputs
(``eval_audit/normalized/digests.py``). This re-hashes those artifacts from the
paths the reports name and compares, turning "the run artifacts survive" from a
claim into a check.

Verdicts, worst-first::

    drifted   the path resolves but the content differs — the report describes
              something that is no longer there. Any number read from this
              packet is unattributable. Fails.
    missing   the recorded artifacts are gone. Fails unless --allow-missing.
    unhashed  the report predates digests and records nothing to check. Passes,
              because most existing stores are in this state, but it is counted
              so the gap in coverage is visible rather than reading as
              "verified".
    match     re-hashes to what the report recorded.

Usage::

    eval-audit-verify-provenance /data/crfm-helm-audit-store/virtual-experiments
    eval-audit-verify-provenance <root> --store olmo-models-combined --json out.json

Pairs with ``eval-audit-lint-store``: the lint says whether a packet's number
depended on an unrecorded choice, this says whether its inputs are still there.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from eval_audit.infra.logging import setup_cli_logging
from eval_audit.reports.provenance import Scope, verify_root


def _render(result: dict[str, Any]) -> str:
    by_verdict = result["by_verdict"]
    lines = [
        "provenance verify — do the packets still describe what is on disk?",
        "=" * 66,
        f"root            : {result['root']}",
        f"packets scanned : {result['n_packets']}"
        + (f"  {by_verdict}" if by_verdict else ""),
        "",
    ]
    failing = [
        packet for packet in result["packets"] if packet["verdict"] in {"drifted", "missing"}
    ]
    if not failing:
        lines.append("No packet describes artifacts that have changed or vanished.")
    for packet in failing:
        lines.append(f"{packet['verdict'].upper():<9} {str(packet['packet_id'])[:72]}")
        for component in packet["components"]:
            if component["outcome"] in {"match", "unhashed"}:
                continue
            lines.append(
                f"          {component['outcome']:<8} {str(component['component_id'])[-58:]}"
            )
            if component["outcome"] == "drifted":
                lines.append(
                    f"                   recorded={str(component['recorded'])[:16]} "
                    f"actual={str(component['actual'])[:16]}"
                )
    n_unhashed = by_verdict.get("unhashed", 0)
    if n_unhashed:
        lines.append("")
        lines.append(
            f"{n_unhashed} packet(s) carry no digests (rendered before provenance "
            "landed). Re-render to bring them under this check."
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    setup_cli_logging()
    parser = argparse.ArgumentParser(
        description="Verify that stored reports still describe the artifacts on disk.",
    )
    parser.add_argument("root", type=Path, help="store root to scan recursively")
    parser.add_argument("--store", default=None, help="restrict to one store under the root")
    parser.add_argument("--model", action="append", default=[], help="restrict to these models")
    parser.add_argument(
        "--benchmark", action="append", default=[], help="restrict to these benchmark families"
    )
    parser.add_argument("--json", dest="json_fpath", type=Path, default=None)
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="treat vanished artifacts as non-fatal (drift still fails)",
    )
    parser.add_argument(
        "--require-digests",
        action="store_true",
        help="also fail on packets that carry no digests at all",
    )
    args = parser.parse_args(argv)

    result = verify_root(
        args.root, Scope(store=args.store, models=args.model, benchmarks=args.benchmark)
    )
    print(_render(result))
    if args.json_fpath is not None:
        args.json_fpath.parent.mkdir(parents=True, exist_ok=True)
        args.json_fpath.write_text(json.dumps(result, indent=2))
        print(f"\nwrote {args.json_fpath}")

    by_verdict = result["by_verdict"]
    if by_verdict.get("drifted"):
        return 1
    if by_verdict.get("missing") and not args.allow_missing:
        return 1
    if args.require_digests and by_verdict.get("unhashed"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Store lint: find packets whose reproduction number depends on an unrecorded choice.

A packet pairs one official row with the local run(s) that claim to reproduce it.
An experiment legitimately accumulates **more than one** local attempt for the
same official row — a pre-fix attempt and its rerun, a smoke and a full, two
suites covering one subject. The planner emits one ``official_vs_local``
comparison per local attempt, all ``enabled``, all peers
(``eval_audit/planning/core_report_planner.py``), so such a packet holds *n*
answers to "how well did this row reproduce?" and nothing in the artifact marks
which one is *the* answer.

Any reduction over those peers must **select**, never average: for
``allenai/olmo-7b`` the second attempt is the tokenizer collapse (completions are
prompt-independent boilerplate, ``exact_match`` 0.000), so averaging halves the
cell exactly — 0.295/**0.144** averaged against 0.295/**0.287** selected, from the
same artifacts. See ``docs/helm-gotchas.md`` §G14.

This lint is read-only. It reports, per packet:

* ``n_attempts``     — enabled ``official_vs_local`` comparisons;
* ``spread``         — max minus min zero-tolerance agreement across those
                       attempts, i.e. how much the unrecorded choice is worth;
* a severity, because ambiguity is only dangerous when the attempts disagree:

  ``MATERIAL``  spread > ``--tol``  — a number read from this packet depends on
                which attempt was picked. This is what fails the lint.
  ``BENIGN``    spread <= ``--tol`` — several attempts, but they agree, so any
                selection gives the same answer. Reported, not fatal.

Usage::

    python -m eval_audit.cli.lint_store /data/crfm-helm-audit-store/virtual-experiments
    python -m eval_audit.cli.lint_store <store> --json report.json --strict

Exit code is nonzero if any packet is ``MATERIAL`` (or, with ``--strict``, if any
packet has more than one attempt at all). Use it after building a store, and
before citing any figure read out of one.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from eval_audit.infra.logging import setup_cli_logging
from eval_audit.reports.attempt_selection import (
    local_component_id,
    official_vs_local_attempts,
    select_official_vs_local,
)

REPORT_GLOB = "**/core_metric_report.json"


def _zero_tol_agreement(pair: dict[str, Any]) -> float | None:
    """Zero-tolerance instance agreement for one rendered comparison."""
    instance_level = pair.get("instance_level") or {}
    direct = instance_level.get("agreement_at_zero")
    if isinstance(direct, (int, float)):
        return float(direct)
    for point in instance_level.get("agreement_vs_abs_tol") or []:
        if point.get("abs_tol") == 0:
            ratio = point.get("agree_ratio")
            if isinstance(ratio, (int, float)):
                return float(ratio)
    return None


def audit_packet(report_fpath: Path, tol: float) -> dict[str, Any] | None:
    """Return an ambiguity record for one packet, or None when unambiguous."""
    try:
        report = json.loads(report_fpath.read_text())
    except Exception as exc:  # unreadable packet is itself worth surfacing
        return {
            "packet": report_fpath.parent.name,
            "report_fpath": str(report_fpath),
            "severity": "UNREADABLE",
            "error": str(exc),
            "n_attempts": None,
            "spread": None,
            "attempts": [],
        }

    attempts = official_vs_local_attempts(report)
    if len(attempts) <= 1:
        return None

    # The rule the reporting layer would apply to this packet, so the lint and
    # the rendered number name the same attempt rather than merely agreeing
    # that a choice exists.
    selection = select_official_vs_local(report)
    scored = [
        {
            "local_component_id": local_component_id(pair),
            "agreement_at_zero": _zero_tol_agreement(pair),
            # Identity, not comparison_id: both come from the same parsed
            # report, and older packets may not carry a comparison_id at all.
            "selected": pair is selection.pair,
        }
        for pair in attempts
    ]
    values = [row["agreement_at_zero"] for row in scored if row["agreement_at_zero"] is not None]
    spread = (max(values) - min(values)) if len(values) > 1 else None

    if spread is None:
        severity = "UNSCORED"
    elif spread > tol:
        severity = "MATERIAL"
    else:
        severity = "BENIGN"

    return {
        "packet": report_fpath.parent.name,
        "report_fpath": str(report_fpath),
        "severity": severity,
        "n_attempts": len(attempts),
        "spread": spread,
        "selection_rule": selection.rule,
        "selected_comparison_id": selection.selected_comparison_id,
        "selected_agreement_at_zero": next(
            (row["agreement_at_zero"] for row in scored if row["selected"]), None
        ),
        "attempts": scored,
    }


def audit_paths(roots: list[Path], tol: float) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    n_packets = 0
    for root in roots:
        for report_fpath in sorted(root.glob(REPORT_GLOB)):
            n_packets += 1
            finding = audit_packet(report_fpath, tol)
            if finding is not None:
                findings.append(finding)
    by_severity: dict[str, int] = {}
    for finding in findings:
        by_severity[finding["severity"]] = by_severity.get(finding["severity"], 0) + 1
    return {
        "roots": [str(root) for root in roots],
        "tol": tol,
        "n_packets": n_packets,
        "n_ambiguous": len(findings),
        "by_severity": by_severity,
        "findings": findings,
    }


def _render(result: dict[str, Any]) -> str:
    lines = [
        "store lint — packets whose number depends on an unrecorded choice",
        "=" * 66,
        f"packets scanned : {result['n_packets']}",
        f"ambiguous       : {result['n_ambiguous']}"
        + (f"  {result['by_severity']}" if result["by_severity"] else ""),
        "",
    ]
    material = [f for f in result["findings"] if f["severity"] == "MATERIAL"]
    if not material:
        lines.append("No packet carries competing attempts that disagree.")
    for finding in material:
        lines.append(
            f"MATERIAL  spread={finding['spread']:.4f}  attempts={finding['n_attempts']}  "
            f"rule={finding.get('selection_rule')}  {finding['packet'][:72]}"
        )
        for attempt in finding["attempts"]:
            agreement = attempt["agreement_at_zero"]
            shown = f"{agreement:.4f}" if agreement is not None else "  n/a "
            marker = "->" if attempt.get("selected") else "  "
            lines.append(
                f"         {marker} agree@0={shown}  {str(attempt['local_component_id'])[-58:]}"
            )
    other = [f for f in result["findings"] if f["severity"] != "MATERIAL"]
    if other:
        lines.append("")
        for finding in other:
            lines.append(
                f"{finding['severity']:<10} attempts={finding['n_attempts']}  {finding['packet'][:72]}"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    setup_cli_logging()
    parser = argparse.ArgumentParser(
        description="Find packets whose reproduction number depends on an unrecorded choice.",
    )
    parser.add_argument("roots", nargs="+", type=Path, help="store roots to scan recursively")
    parser.add_argument(
        "--tol",
        type=float,
        default=1e-6,
        help="agreement spread below which competing attempts count as agreeing (default: 1e-6)",
    )
    parser.add_argument("--json", dest="json_fpath", type=Path, default=None, help="write the full report as JSON")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="fail on any packet with several attempts, even when they agree",
    )
    args = parser.parse_args(argv)

    result = audit_paths(args.roots, args.tol)
    print(_render(result))
    if args.json_fpath is not None:
        args.json_fpath.parent.mkdir(parents=True, exist_ok=True)
        args.json_fpath.write_text(json.dumps(result, indent=2))
        print(f"\nwrote {args.json_fpath}")

    n_material = result["by_severity"].get("MATERIAL", 0)
    n_unreadable = result["by_severity"].get("UNREADABLE", 0)
    if n_material or n_unreadable:
        return 1
    if args.strict and result["n_ambiguous"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

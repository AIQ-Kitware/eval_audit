"""Store lint: find packets whose reproduction number depends on an unrecorded choice.

A packet pairs one official row with the local run(s) that claim to reproduce it.
An experiment legitimately accumulates **more than one** local attempt for the
same official row — a pre-fix attempt and its rerun, a smoke and a full, two
suites covering one subject. Whichever way the packet records that, one attempt
ends up standing for the packet's number, and this lint asks what that choice was
worth.

Any reduction over the attempts must **select**, never average: for
``allenai/olmo-7b`` the second attempt is the tokenizer collapse (completions are
prompt-independent boilerplate, ``exact_match`` 0.000), so averaging halves the
cell exactly — 0.295/**0.144** averaged against 0.295/**0.287** selected, from the
same artifacts. See ``docs/helm-gotchas.md`` §G14.

Packets come in two shapes and the lint grades both:

``competing_attempts``
    Pre-2026-08 planning: every attempt is an enabled ``official_vs_local``
    peer, so the packet holds *n* rival answers and nothing marks which is
    *the* answer. Graded on the **spread** in zero-tolerance agreement across
    those peers.

``demoted_attempts``
    Current planning: only the canonical attempt keeps its
    ``official_vs_local``; the rest are disabled
    ``superseded_local_attempt`` and retyped as ``local_repeat``. The rival
    answers no longer exist to be compared, so the choice is graded on the
    **local-vs-local disagreement** the repeat measures — if two attempts
    produce identical per-instance metrics, picking either gives the same
    official comparison.

Either way the packet reports ``n_attempts``, a ``spread`` (how much the choice
is worth, in agreement units), and a severity, because multiplicity is only
dangerous when the attempts disagree:

  ``MATERIAL``  spread > ``--tol``  — a number read from this packet depends on
                which attempt was picked. This is what fails the lint.
  ``BENIGN``    spread <= ``--tol`` — several attempts, but they agree, so any
                selection gives the same answer. Reported, not fatal.
  ``UNSCORED``  the attempts exist but nothing scored them, so the choice
                cannot be graded either way.

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
    demoted = _demoted_comparisons(report)
    if len(attempts) <= 1 and not demoted:
        return None

    if len(attempts) > 1:
        shape, scored, spread = _grade_competing_attempts(report, attempts)
    else:
        shape, scored, spread = _grade_demoted_attempts(report, attempts, demoted)

    if spread is None:
        severity = "UNSCORED"
    elif spread > tol:
        severity = "MATERIAL"
    else:
        severity = "BENIGN"

    # The rule the reporting layer would apply, so the lint and the rendered
    # number name the same attempt rather than merely agreeing a choice exists.
    selection = select_official_vs_local(report)
    return {
        "packet": report_fpath.parent.name,
        "report_fpath": str(report_fpath),
        "severity": severity,
        "shape": shape,
        "n_attempts": len(scored),
        "spread": spread,
        "selection_rule": selection.rule,
        "selected_comparison_id": selection.selected_comparison_id,
        "selected_agreement_at_zero": next(
            (row["agreement_at_zero"] for row in scored if row["selected"]), None
        ),
        "attempts": scored,
    }


def _demoted_comparisons(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Planned-but-disabled attempts the planner superseded (post-2026-08 shape)."""
    return [
        comparison
        for comparison in report.get("comparisons") or []
        if comparison.get("disabled_reason") == "superseded_local_attempt"
    ]


def _grade_competing_attempts(
    report: dict[str, Any],
    attempts: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]], float | None]:
    """Grade rival enabled peers by the spread in their agreement with the official."""
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
    return "competing_attempts", scored, spread


def _grade_demoted_attempts(
    report: dict[str, Any],
    attempts: list[dict[str, Any]],
    demoted: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]], float | None]:
    """Grade a superseded attempt by how far its repeat sits from the canonical one.

    The rival ``official_vs_local`` no longer exists to be compared, so the
    question becomes local-vs-local: two attempts producing identical
    per-instance metrics make the choice free, however they were ranked.
    """
    repeat_agreement: dict[str, float | None] = {}
    for pair in report.get("pairs") or []:
        if (pair.get("comparison_kind") or "").strip() != "local_repeat":
            continue
        component_id = local_component_id(pair)
        if component_id:
            repeat_agreement[component_id] = _zero_tol_agreement(pair)

    scored = [
        {
            "local_component_id": local_component_id(pair),
            "agreement_at_zero": _zero_tol_agreement(pair),
            "selected": True,
        }
        for pair in attempts
    ]
    distances: list[float] = []
    for comparison in demoted:
        component_id = local_component_id(comparison)
        agreement = repeat_agreement.get(component_id or "")
        scored.append(
            {
                "local_component_id": component_id,
                # The superseded attempt was never diffed against the official,
                # so what is reported here is its agreement with the canonical
                # local attempt — a different quantity, hence the explicit key.
                "agreement_at_zero": None,
                "repeat_agreement_at_zero": agreement,
                "selected": False,
            }
        )
        if agreement is not None:
            distances.append(1.0 - agreement)
    spread = max(distances) if distances else None
    return "demoted_attempts", scored, spread


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
            f"shape={finding.get('shape')}  rule={finding.get('selection_rule')}  "
            f"{finding['packet'][:72]}"
        )
        for attempt in finding["attempts"]:
            marker = "->" if attempt.get("selected") else "  "
            if attempt["agreement_at_zero"] is not None:
                measure = f"agree@0={attempt['agreement_at_zero']:.4f}"
            elif attempt.get("repeat_agreement_at_zero") is not None:
                # Superseded attempts were never diffed against the official;
                # what is known is how far they sit from the canonical local.
                measure = f"vs-canon={attempt['repeat_agreement_at_zero']:.4f}"
            else:
                measure = "agree@0=  n/a "
            lines.append(
                f"         {marker} {measure:<20s}  {str(attempt['local_component_id'])[-58:]}"
            )
    other = [f for f in result["findings"] if f["severity"] != "MATERIAL"]
    if other:
        lines.append("")
        for finding in other:
            lines.append(
                f"{finding['severity']:<10} attempts={finding['n_attempts']}  "
                f"shape={finding.get('shape')}  {finding['packet'][:72]}"
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

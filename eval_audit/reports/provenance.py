"""Resolve packets to the runs and digests behind them, and check they still hold.

Two consumers share this layer:

* ``eval-audit-verify-provenance`` — re-hash each component's artifacts from the
  path its report recorded and compare against the recorded digest.
* ``eval-audit-export-cited-numbers`` — turn a paper figure's scope into the
  packet ids, run ids and digests standing behind it.

They are the same walk with different verdicts, so the walking and the scoping
live here and the CLIs stay thin.

Four outcomes, and the fourth is why this runs on stores that predate digests:

``match``     recorded digest reproduces from the artifacts on disk.
``drifted``   the path resolves and the content differs — the report describes
              something that is no longer there.
``missing``   the recorded path is gone.
``unhashed``  the report predates ``eval_audit.normalized.digests`` and records
              no digest to check. Not a failure: reported, so the gap in
              coverage is visible rather than silently passing as "verified".
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from eval_audit.normalized.digests import component_digest

REPORT_GLOB = "**/core_metric_report.json"


@dataclass
class Scope:
    """Which packets a claim is about."""

    store: str | None = None
    models: list[str] = field(default_factory=list)
    benchmarks: list[str] = field(default_factory=list)
    packet_ids: list[str] = field(default_factory=list)

    def matches(self, report: dict[str, Any]) -> bool:
        if self.packet_ids and str(report.get("packet_id")) not in set(self.packet_ids):
            return False
        if self.models and not (set(self.models) & report_models(report)):
            return False
        if self.benchmarks and not (set(self.benchmarks) & report_benchmarks(report)):
            return False
        return True


def benchmark_family(logical_run_key: str) -> str:
    """``mmlu:model=allenai/olmo-7b`` -> ``mmlu``."""
    if ":model=" in logical_run_key:
        family, _, _ = logical_run_key.partition(":model=")
    elif ":" in logical_run_key:
        family = logical_run_key.split(":")[0]
    else:
        family = logical_run_key
    return family.strip()


def component_model(component: dict[str, Any]) -> str | None:
    model = (component.get("model") or "").strip()
    if model:
        return model
    logical_run_key = (component.get("logical_run_key") or "").strip()
    if ":model=" in logical_run_key:
        _, _, model_part = logical_run_key.partition(":model=")
        return model_part.strip() or None
    return None


def report_models(report: dict[str, Any]) -> set[str]:
    return {
        model
        for model in (component_model(c) for c in report.get("components") or [])
        if model
    }


def report_benchmarks(report: dict[str, Any]) -> set[str]:
    return {
        family
        for family in (
            benchmark_family((c.get("logical_run_key") or "").strip())
            for c in report.get("components") or []
        )
        if family
    }


def iter_reports(root: Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Every readable packet report under a root, in a stable order."""
    for report_fpath in sorted(Path(root).glob(REPORT_GLOB)):
        try:
            yield report_fpath, json.loads(report_fpath.read_text())
        except (OSError, json.JSONDecodeError):
            continue


def store_root(root: Path, scope: Scope) -> Path:
    return Path(root) / scope.store if scope.store else Path(root)


def verify_component(
    component: dict[str, Any],
    recorded: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compare a component's artifacts on disk against what its report recorded."""
    component_id = str(component.get("component_id"))
    if not recorded or not recorded.get("scores"):
        # No recorded digest at all: either the report predates digests, or the
        # artifacts were already gone when it was written. The two are worth
        # distinguishing, because only the first is fixed by re-rendering.
        return {
            "component_id": component_id,
            "outcome": "unhashed" if recorded is None else "missing",
            "recorded": (recorded or {}).get("scores"),
            "actual": None,
        }

    actual = component_digest(component, include_completions=recorded.get("completions") is not None)
    if actual["status"] == "missing":
        outcome = "missing"
    elif actual["scores"] == recorded["scores"] and actual["completions"] == recorded["completions"]:
        outcome = "match"
    else:
        outcome = "drifted"
    return {
        "component_id": component_id,
        "outcome": outcome,
        "recorded": recorded.get("scores"),
        "actual": actual["scores"],
        "root": actual.get("root"),
    }


def verify_report(report: dict[str, Any]) -> dict[str, Any]:
    """Verify every component of one packet."""
    recorded_digests = report.get("component_digests") or {}
    components = report.get("components") or []
    results = [
        verify_component(component, recorded_digests.get(str(component.get("component_id"))))
        for component in components
    ]
    outcomes = Counter(result["outcome"] for result in results)
    if outcomes.get("drifted"):
        verdict = "drifted"
    elif outcomes.get("missing"):
        verdict = "missing"
    elif outcomes.get("unhashed"):
        verdict = "unhashed"
    else:
        verdict = "match"
    return {
        "packet_id": report.get("packet_id"),
        "verdict": verdict,
        "outcomes": dict(outcomes),
        "components": results,
    }


def verify_root(root: Path, scope: Scope | None = None) -> dict[str, Any]:
    scope = scope or Scope()
    packets: list[dict[str, Any]] = []
    for report_fpath, report in iter_reports(store_root(root, scope)):
        if not scope.matches(report):
            continue
        result = verify_report(report)
        result["report_fpath"] = str(report_fpath)
        packets.append(result)
    verdicts = Counter(packet["verdict"] for packet in packets)
    return {
        "root": str(store_root(root, scope)),
        "n_packets": len(packets),
        "by_verdict": dict(verdicts),
        "packets": packets,
    }


def resolve_scope(root: Path, scope: Scope) -> dict[str, Any]:
    """Everything standing behind the packets a claim covers.

    Returns the packet ids, the run paths on both sides, digest coverage, and
    the distinct code identities — more than one means the claim aggregates
    packets rendered by different builds, which is itself worth seeing.
    """
    packet_ids: list[str] = []
    run_paths: set[str] = set()
    digest_status: Counter[str] = Counter()
    code_identities: set[str] = set()
    models: set[str] = set()
    benchmarks: set[str] = set()

    for _, report in iter_reports(store_root(root, scope)):
        if not scope.matches(report):
            continue
        packet_ids.append(str(report.get("packet_id")))
        models |= report_models(report)
        benchmarks |= report_benchmarks(report)
        code_identities.add(
            json.dumps(report.get("code_identity") or {}, sort_keys=True)
        )
        recorded = report.get("component_digests") or {}
        for component in report.get("components") or []:
            path = component.get("run_path") or component.get("eee_artifact_path")
            if path:
                run_paths.add(str(path))
            entry = recorded.get(str(component.get("component_id")))
            digest_status[(entry or {}).get("status", "unhashed")] += 1

    return {
        "n_packets": len(packet_ids),
        "packet_ids": sorted(packet_ids),
        "n_runs": len(run_paths),
        "run_paths": sorted(run_paths),
        "models": sorted(models),
        "benchmarks": sorted(benchmarks),
        "digest_status": dict(digest_status),
        "code_identities": [json.loads(item) for item in sorted(code_identities)],
    }

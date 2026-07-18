"""Index ``helm_rejudge_v1`` artifacts distinctly from candidate runs.

Phase 12 of ``docs/planning/open-judge-plan.md`` (§17): a rejudge
artifact is NOT a locally reproduced candidate run — the candidate
identity stays the source run's logical key; judge identity is an
orthogonal dimension (``response_set_hash`` × ``judge_arm`` ×
``replicate``). This module reads a rejudge artifact directory into the
explicit indexed fields the analysis joins on, and never routes these
rows through the ordinary candidate-reproduction planner.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from eval_audit.judging.display_keys import DisplayKey

REJUDGE_INDEX_FIELDS = (
    "execution_kind",
    "response_source_kind",
    "response_source_path",
    "response_set_hash",
    "candidate_inference_reused",
    "benchmark",
    "judge_arm_id",
    "judge_model",
    "judge_model_deployment",
    "judge_spec_hash",
    "judge_replicate",
    "judge_prompt_version",
    "judge_parser_version",
    "judge_thinking_mode",
    "judge_substitution_planned",
    "attempt_hash",
)


def _load_json(fpath: Path) -> Any:
    with open(fpath, "r", encoding="utf-8") as file:
        return json.load(file)


def is_rejudge_artifact(dpath: str | Path) -> bool:
    dpath = Path(dpath)
    return (dpath / "DONE").is_file() and (dpath / "rejudge_manifest.json").is_file()


def discover_rejudge_artifacts(root: str | Path) -> list[Path]:
    """All completed rejudge artifact dirs under ``root`` (sorted)."""
    root = Path(root)
    found = [
        manifest.parent
        for manifest in sorted(root.rglob("rejudge_manifest.json"))
        if is_rejudge_artifact(manifest.parent)
    ]
    return found


def index_rejudge_artifact(dpath: str | Path) -> dict[str, Any]:
    """The explicit index row for one rejudge artifact (§17 fields)."""
    dpath = Path(dpath)
    rejudge = _load_json(dpath / "rejudge_manifest.json")
    judge = _load_json(dpath / "judge_manifest.json")
    response = _load_json(dpath / "response_manifest.json")
    # public mirrors reconstruct annotation-only from display artifacts.
    source_kind = (
        "public_display"
        if response.get("reconstruction_scope") == "annotation_only"
        else "local_scenario_state"
    )
    return {
        "execution_kind": rejudge["execution_kind"],
        "response_source_kind": source_kind,
        "response_source_path": rejudge.get("source_run"),
        "response_set_hash": rejudge["response_set_hash"],
        "candidate_inference_reused": rejudge["candidate_inference_reused"],
        "benchmark": rejudge["benchmark"],
        "judge_arm_id": rejudge["judge_id"],
        "judge_model": judge["model"],
        "judge_model_deployment": judge["model_deployment"],
        "judge_spec_hash": rejudge["judge_spec_hash"],
        "judge_replicate": rejudge["replicate"],
        "judge_prompt_version": judge["prompt_version"],
        "judge_parser_version": judge["parser_version"],
        "judge_thinking_mode": judge["thinking_mode"],
        # These are declared judge substitutions by construction.
        "judge_substitution_planned": True,
        "attempt_hash": rejudge["attempt_hash"],
        "artifact_dpath": str(dpath),
    }


def load_rejudge_judgments(dpath: str | Path) -> dict[DisplayKey, dict]:
    """The per-display-key judge annotations from one artifact."""
    dpath = Path(dpath)
    judgments: dict[DisplayKey, dict] = {}
    with open(dpath / "judgments.jsonl", "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            judgments[DisplayKey(**record["key"])] = record["annotation"]
    return judgments


def build_rejudge_index(root: str | Path) -> list[dict[str, Any]]:
    """Index every rejudge artifact under ``root`` (one row per attempt)."""
    return [index_rejudge_artifact(dpath) for dpath in discover_rejudge_artifacts(root)]


__all__ = [
    "REJUDGE_INDEX_FIELDS",
    "build_rejudge_index",
    "discover_rejudge_artifacts",
    "index_rejudge_artifact",
    "is_rejudge_artifact",
    "load_rejudge_judgments",
]

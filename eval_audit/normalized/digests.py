"""Content digests of the artifacts a reported number is computed from.

A figure read out of a store is currently traceable only through prose: the
report names a ``run_path``, and whether the thing at that path is still what
produced the number is a matter of trust. These digests make it a check —
``eval-audit-verify-provenance`` re-hashes and compares.

**What gets hashed.** Not the run directory: it carries logs, timestamps, and
absolute paths, so it would differ after every re-run and every packaging pass,
producing false alarms that train a reader to ignore the tool. The unit is the
artifacts the scoring layer actually consumes, split in two because they answer
different questions and cost different amounts:

``scores``
    ``run_spec.json`` + ``stats.json`` + ``per_instance_stats.json`` for HELM
    runs (the aggregate ``.json`` and ``_samples.jsonl`` for EEE artifacts).
    Every reported metric is a function of these. ~3.7 MB per HELM run side.

``completions``
    ``scenario_state.json`` — the raw model outputs the diagnostics read
    (empty-completion rate, output-token counts, ``<think>`` leakage). ~1.4 MB.
    Separate so that a re-conversion touching completions does not invalidate a
    score claim, and vice versa.

**Stability.** Packaging rewrites absolute ``/data`` paths inside text
artifacts (``eval_audit/packaging/pack.py``), which would break a content
digest of any file it touches. Verified 2026-08-05 against 22 real runs: none
of the four files above contains a rewritable root, so a digest taken before
packaging still matches inside the package. If that ever changes, the file
sets here are the thing to revisit — which is why the spec version below names
them.

**Self-describing.** ``DIGEST_SPEC``/``COMPLETIONS_SPEC`` name the file set, so
changing it later is visible in the artifact instead of silently producing
hashes that are not comparable to the old ones.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

DIGEST_SPEC = "scores.v1"
COMPLETIONS_SPEC = "completions.v1"
COMPARISON_SPEC = "comparison.v1"

HELM_SCORE_FILES = ("run_spec.json", "stats.json", "per_instance_stats.json")
HELM_COMPLETION_FILES = ("scenario_state.json",)

_CHUNK = 1 << 20


def file_sha256(fpath: Path) -> tuple[str, int] | None:
    """Streaming digest of one file, or None when it is absent/unreadable."""
    try:
        digest = hashlib.sha256()
        size = 0
        with fpath.open("rb") as handle:
            while chunk := handle.read(_CHUNK):
                digest.update(chunk)
                size += len(chunk)
        return digest.hexdigest(), size
    except OSError:
        return None


def _combine(entries: list[dict[str, Any]]) -> str | None:
    """Order-independent digest over a set of named file digests."""
    if not entries:
        return None
    canonical = "".join(
        f"{entry['name']}\0{entry['sha256']}\n"
        for entry in sorted(entries, key=lambda item: item["name"])
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _digest_files(root: Path, names: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    entries: list[dict[str, Any]] = []
    missing: list[str] = []
    for name in names:
        result = file_sha256(root / name)
        if result is None:
            missing.append(name)
            continue
        sha, size = result
        entries.append({"name": name, "sha256": sha, "bytes": size})
    return entries, missing


def _eee_artifact_files(artifact_dpath: Path) -> list[str]:
    """Score-bearing files in an EEE artifact tree, relative to its root.

    An EEE artifact carries per-instance rows in ``*_samples.jsonl`` and the
    aggregate beside it; both are scoring inputs, so neither is a separate
    "completions" half the way raw HELM's ``scenario_state.json`` is.
    """
    try:
        return sorted(
            fpath.relative_to(artifact_dpath).as_posix()
            for fpath in artifact_dpath.rglob("*")
            if fpath.is_file() and fpath.suffix in {".json", ".jsonl"}
        )
    except OSError:
        return []


def component_digest(
    component: dict[str, Any],
    *,
    include_completions: bool = True,
) -> dict[str, Any]:
    """Content digest of one comparison side.

    Never raises: a component whose artifacts have been pruned reports
    ``status="missing"``, which is itself the finding rather than an error.
    """
    artifact_format = (component.get("artifact_format") or "helm").strip()
    record: dict[str, Any] = {
        "artifact_format": artifact_format,
        "spec": DIGEST_SPEC,
        "scores": None,
        "completions": None,
        "files": [],
        "missing_files": [],
        "status": "missing",
    }

    if artifact_format == "eee":
        artifact_path = component.get("eee_artifact_path")
        if not artifact_path:
            return record
        root = Path(artifact_path)
        names = _eee_artifact_files(root)
        entries, missing = _digest_files(root, names)
        record["root"] = str(root)
        record["files"] = entries
        record["missing_files"] = missing
        record["scores"] = _combine(entries)
    else:
        run_path = component.get("run_path")
        if not run_path:
            return record
        root = Path(run_path)
        entries, missing = _digest_files(root, list(HELM_SCORE_FILES))
        record["root"] = str(root)
        record["files"] = entries
        record["missing_files"] = missing
        record["scores"] = _combine(entries)
        if include_completions:
            completion_entries, completion_missing = _digest_files(
                root, list(HELM_COMPLETION_FILES)
            )
            record["completions_spec"] = COMPLETIONS_SPEC
            record["completions"] = _combine(completion_entries)
            record["files"].extend(completion_entries)
            record["missing_files"].extend(completion_missing)

    if record["scores"] is None:
        record["status"] = "missing"
    elif record["missing_files"]:
        record["status"] = "partial"
    else:
        record["status"] = "ok"
    return record


def comparison_digest(
    component_ids: list[str],
    component_digests: dict[str, dict[str, Any]],
    *,
    thresholds: Any,
    code: dict[str, Any],
) -> dict[str, Any]:
    """Digest naming everything a single comparison's number is a function of.

    Inputs on both sides, the tolerance grid the agreement curve is swept over,
    and the code identity — the last because identical artifacts through
    changed code produce a different answer, so a digest that omits it would
    certify a number it cannot actually reproduce.
    """
    sides = {
        component_id: {
            "scores": (component_digests.get(component_id) or {}).get("scores"),
            "completions": (component_digests.get(component_id) or {}).get("completions"),
        }
        for component_id in component_ids
    }
    payload = {
        "spec": COMPARISON_SPEC,
        "sides": sides,
        "thresholds": thresholds,
        "code": code,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return {
        "spec": COMPARISON_SPEC,
        "digest": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "status": (
            "ok"
            if all(side["scores"] for side in sides.values()) and sides
            else "incomplete"
        ),
    }

"""Immutable, content-addressed candidate response snapshots.

Phase 2 of ``docs/planning/open-judge-plan.md`` (§7): freeze a source
run's candidate responses **once**, hash them, and let every judge arm
and replicate refer to the same ``response_set_hash``. A snapshot is
built from display artifacts (public mirrors carry no
``scenario_state.json``), is **annotation-only** — sufficient for
annotator replay, not a token-level reconstruction — and never depends
on the mutable source corpus continuing to exist.

Layout (``<snapshot_root>/<response_set_hash>/``)::

    response_manifest.json          identity + provenance (§7.5)
    source_run_spec.json            verbatim source run_spec
    instances.json                  verbatim source instances
    display_requests.json           verbatim source requests
    display_predictions.json        verbatim source predictions
    source_stats.json               verbatim source stats (replay gate input)
    source_per_instance_stats.json  verbatim source per-instance stats
    response_scenario_state.json    judge-neutral reconstructed state (§7.3)
    official_annotations.jsonl      detached original annotations (§7.4)
    DONE                            written last; absent => not a snapshot

Hash identity (§7.2): only judging-relevant facts — display key, the
complete serialized instance, the exact candidate request, candidate
output/thinking text, reference index — sorted by display key,
canonical JSON, sha256. Source paths, original annotations, aggregate
stats, and reconstruction defaults are excluded, so identical response
sets copied anywhere share one identity.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from helm.benchmark.adaptation.adapter_spec import AdapterSpec
from helm.benchmark.adaptation.request_state import RequestState
from helm.benchmark.adaptation.scenario_state import ScenarioState
from helm.benchmark.scenarios.scenario import Instance
from helm.common.codec import from_json, to_json
from helm.common.request import GeneratedOutput, Request, RequestResult, Thinking

from eval_audit.judging.display_keys import DisplayKey, instance_key
from eval_audit.judging.source_audit import (
    BENCHMARK_PROFILES,
    SourceAuditRecord,
    audit_run,
)

SNAPSHOT_SCHEMA_VERSION = 1
SNAPSHOT_ARTIFACT_TYPE = "helm_response_snapshot"
DONE_FNAME = "DONE"

#: Source files copied verbatim into the snapshot. The stats copies are
#: not part of the plan §7.1 layout but are required by the Phase 3
#: identity-replay gate, which must stay meaningful after the source
#: corpus moves; they are excluded from hash identity like everything
#: else outside the normalized response records.
_COPIED_SOURCE_FILES = {
    "run_spec.json": "source_run_spec.json",
    "instances.json": "instances.json",
    "display_requests.json": "display_requests.json",
    "display_predictions.json": "display_predictions.json",
    "stats.json": "source_stats.json",
    "per_instance_stats.json": "source_per_instance_stats.json",
}


class SnapshotBuildError(RuntimeError):
    """The source run cannot be converted into a valid snapshot."""


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _load_json(fpath: Path) -> Any:
    with open(fpath, "r", encoding="utf-8") as file:
        return json.load(file)


def _sha256_file(fpath: Path) -> str:
    digest = hashlib.sha256()
    with open(fpath, "rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _index_source_artifacts(
    instances: list[dict],
    display_requests: list[dict],
    display_predictions: list[dict],
) -> tuple[dict[tuple, dict], dict[DisplayKey, dict], dict[DisplayKey, dict]]:
    """Index the source artifacts by their join keys (audit guarantees
    uniqueness; assert anyway — the snapshot must never silently pick)."""
    instance_by_key: dict[tuple, dict] = {}
    for instance in instances:
        key = instance_key(instance)
        assert key not in instance_by_key, f"duplicate instance {key}"
        instance_by_key[key] = instance
    request_by_key: dict[DisplayKey, dict] = {}
    for entry in display_requests:
        key = DisplayKey.from_entry(entry)
        assert key not in request_by_key, f"duplicate display request {key}"
        request_by_key[key] = entry
    prediction_by_key: dict[DisplayKey, dict] = {}
    for entry in display_predictions:
        key = DisplayKey.from_entry(entry)
        assert key not in prediction_by_key, f"duplicate display prediction {key}"
        prediction_by_key[key] = entry
    return instance_by_key, request_by_key, prediction_by_key


def normalized_response_records(
    instances: list[dict],
    display_requests: list[dict],
    display_predictions: list[dict],
) -> list[dict[str, Any]]:
    """The ordered, normalized sequence that defines snapshot identity.

    Contains ONLY judging-relevant facts (§7.2). The instance and
    request dicts come verbatim from the source artifacts (canonicalized
    by JSON key order at hash time), never from a codec round-trip, so
    the hash is a property of the published bytes' content.
    """
    instance_by_key, request_by_key, prediction_by_key = _index_source_artifacts(
        instances, display_requests, display_predictions
    )
    records: list[dict[str, Any]] = []
    for key in sorted(request_by_key, key=DisplayKey.sort_tuple):
        request_entry = request_by_key[key]
        prediction = prediction_by_key[key]
        instance = instance_by_key[(key.instance_id, key.perturbation)]
        records.append(
            {
                "key": key.as_dict(),
                "instance": instance,
                "request": request_entry["request"],
                "predicted_text": prediction["predicted_text"],
                "thinking_text": prediction.get("thinking_text"),
                "reference_index": prediction.get("reference_index"),
            }
        )
    return records


def compute_response_set_hash(records: list[dict[str, Any]]) -> str:
    return hashlib.sha256(_canonical_json(records).encode("utf-8")).hexdigest()


def _reconstruct_request_state(record: Mapping[str, Any]) -> RequestState:
    """One judging-only ``RequestState`` per display key (§7.3).

    Reconstruction defaults (request_mode=None, output_mapping=None,
    num_train_instances=0, prompt_truncated=False,
    num_conditioning_tokens=0) are valid only for the generation-shaped
    benchmarks the source audit admits — their annotators never consume
    those fields.
    """
    instance = from_json(json.dumps(record["instance"]), Instance)
    request = from_json(json.dumps(record["request"]), Request)
    thinking_text = record.get("thinking_text")
    result = RequestResult(
        success=True,
        embedding=[],
        completions=[
            GeneratedOutput(
                text=record["predicted_text"],
                logprob=0.0,
                tokens=[],
                thinking=Thinking(text=thinking_text) if thinking_text is not None else None,
            )
        ],
        cached=True,
        request_time=None,
        request_datetime=None,
    )
    return RequestState(
        instance=instance,
        reference_index=record.get("reference_index"),
        request_mode=None,
        train_trial_index=int(record["key"]["train_trial_index"]),
        output_mapping=None,
        request=request,
        result=result,
        num_train_instances=0,
        prompt_truncated=False,
        num_conditioning_tokens=0,
        annotations=None,
    )


def reconstruct_scenario_state(
    run_spec: Mapping[str, Any],
    records: list[dict[str, Any]],
) -> ScenarioState:
    """Judge-neutral post-inference state: candidate results attached,
    ``annotations`` absent, ``annotator_specs`` deliberately None."""
    adapter_spec = from_json(json.dumps(run_spec["adapter_spec"]), AdapterSpec)
    request_states = [_reconstruct_request_state(record) for record in records]
    return ScenarioState(
        adapter_spec=adapter_spec,
        request_states=request_states,
        annotator_specs=None,
    )


@dataclass(frozen=True)
class SnapshotResult:
    snapshot_dpath: Path
    response_set_hash: str
    cache_hit: bool


def build_response_snapshot(
    run_dpath: str | Path,
    snapshot_root: str | Path,
    audit_record: SourceAuditRecord | None = None,
) -> SnapshotResult:
    """Freeze one source run into ``<snapshot_root>/<response_set_hash>/``.

    Audits the source first (unless a passing record is supplied) and
    refuses unsupported runs. Builds into a temporary sibling directory,
    validates, writes ``DONE``, then atomically renames into place — a
    partially constructed directory is never a cache hit. Never
    modifies the source run.
    """
    run_dpath = Path(run_dpath)
    snapshot_root = Path(snapshot_root)

    if audit_record is None:
        audit_record = audit_run(run_dpath)
    if not audit_record.supported_for_rejudging:
        raise SnapshotBuildError(
            f"source run not supported for rejudging: {run_dpath}"
            f" reasons={audit_record.unsupported_reasons}"
        )
    profile = BENCHMARK_PROFILES[audit_record.benchmark]

    run_spec = _load_json(run_dpath / "run_spec.json")
    instances = _load_json(run_dpath / "instances.json")
    display_requests = _load_json(run_dpath / "display_requests.json")
    display_predictions = _load_json(run_dpath / "display_predictions.json")

    records = normalized_response_records(instances, display_requests, display_predictions)
    response_set_hash = compute_response_set_hash(records)

    final_dpath = snapshot_root / response_set_hash
    if (final_dpath / DONE_FNAME).is_file():
        verify_snapshot(final_dpath)
        return SnapshotResult(final_dpath, response_set_hash, cache_hit=True)
    if final_dpath.exists():
        # Partial leftover from an interrupted build: not a cache hit.
        shutil.rmtree(final_dpath)

    scenario_state = reconstruct_scenario_state(run_spec, records)

    # Detached official annotations, one JSONL record per display key (§7.4).
    _, _, prediction_by_key = _index_source_artifacts(
        instances, display_requests, display_predictions
    )
    annotation_lines = []
    for record in records:
        key = DisplayKey(**record["key"])
        prediction = prediction_by_key[key]
        annotation_lines.append(
            _canonical_json({"key": key.as_dict(), "annotations": prediction["annotations"]})
        )

    manifest = {
        "artifact_type": SNAPSHOT_ARTIFACT_TYPE,
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "reconstruction_scope": "annotation_only",
        "candidate_inference_reused": True,
        "response_set_hash": response_set_hash,
        "source_run": str(run_dpath),
        "source_run_spec_name": audit_record.run_spec_name,
        "source_artifact_hashes": {
            fname: _sha256_file(run_dpath / fname) for fname in sorted(_COPIED_SOURCE_FILES)
        },
        "num_request_states": len(records),
        "supported_benchmark": audit_record.benchmark,
        "annotator_name": profile.annotator_name,
        "judge_metrics": list(profile.judge_metrics),
        "note": (
            "Annotation-only reconstruction from display artifacts: sufficient "
            "for annotator replay, NOT a complete token-level reconstruction "
            "of the original HELM run."
        ),
    }

    snapshot_root.mkdir(parents=True, exist_ok=True)
    tmp_dpath = snapshot_root / f".tmp-{response_set_hash}-{os.getpid()}"
    if tmp_dpath.exists():
        shutil.rmtree(tmp_dpath)
    tmp_dpath.mkdir(parents=True)
    try:
        for src_fname, dst_fname in _COPIED_SOURCE_FILES.items():
            shutil.copyfile(run_dpath / src_fname, tmp_dpath / dst_fname)
        with open(tmp_dpath / "response_scenario_state.json", "w", encoding="utf-8") as file:
            file.write(to_json(scenario_state))
        with open(tmp_dpath / "official_annotations.jsonl", "w", encoding="utf-8") as file:
            file.write("\n".join(annotation_lines) + "\n")
        with open(tmp_dpath / "response_manifest.json", "w", encoding="utf-8") as file:
            json.dump(manifest, file, indent=2)
            file.write("\n")

        _validate_snapshot_files(tmp_dpath, expected_hash=response_set_hash)
        with open(tmp_dpath / DONE_FNAME, "w", encoding="utf-8") as file:
            file.write(response_set_hash + "\n")
        os.replace(tmp_dpath, final_dpath)
    except BaseException:
        shutil.rmtree(tmp_dpath, ignore_errors=True)
        raise
    return SnapshotResult(final_dpath, response_set_hash, cache_hit=False)


def _validate_snapshot_files(dpath: Path, expected_hash: str) -> None:
    """Every invariant a judgment attempt will later rely on."""
    recomputed = compute_snapshot_response_set_hash(dpath)
    if recomputed != expected_hash:
        raise SnapshotBuildError(
            f"response_set_hash mismatch after write: {recomputed} != {expected_hash}"
        )
    scenario_state = load_snapshot_scenario_state(dpath)
    for request_state in scenario_state.request_states:
        if request_state.result is None or not request_state.result.success:
            raise SnapshotBuildError("reconstructed request state lacks a successful result")
        if len(request_state.result.completions) != 1:
            raise SnapshotBuildError("reconstructed request state must have one completion")
        if request_state.annotations is not None:
            raise SnapshotBuildError("reconstructed request state must be judge-neutral")
    if scenario_state.annotator_specs is not None:
        raise SnapshotBuildError("reconstructed scenario state must not carry annotator specs")


def compute_snapshot_response_set_hash(snapshot_dpath: str | Path) -> str:
    """Recompute identity from the snapshot's own files (no source needed)."""
    snapshot_dpath = Path(snapshot_dpath)
    records = normalized_response_records(
        _load_json(snapshot_dpath / "instances.json"),
        _load_json(snapshot_dpath / "display_requests.json"),
        _load_json(snapshot_dpath / "display_predictions.json"),
    )
    return compute_response_set_hash(records)


def verify_snapshot(snapshot_dpath: str | Path) -> str:
    """Assert a completed snapshot is internally consistent; return its hash."""
    snapshot_dpath = Path(snapshot_dpath)
    if not (snapshot_dpath / DONE_FNAME).is_file():
        raise SnapshotBuildError(f"snapshot has no {DONE_FNAME} sentinel: {snapshot_dpath}")
    manifest = _load_json(snapshot_dpath / "response_manifest.json")
    declared = manifest["response_set_hash"]
    recomputed = compute_snapshot_response_set_hash(snapshot_dpath)
    if declared != recomputed or snapshot_dpath.name != declared:
        raise SnapshotBuildError(
            f"snapshot identity mismatch: dir={snapshot_dpath.name}"
            f" manifest={declared} recomputed={recomputed}"
        )
    return declared


def load_snapshot_manifest(snapshot_dpath: str | Path) -> dict[str, Any]:
    return _load_json(Path(snapshot_dpath) / "response_manifest.json")


def load_snapshot_scenario_state(snapshot_dpath: str | Path) -> ScenarioState:
    with open(Path(snapshot_dpath) / "response_scenario_state.json", "r", encoding="utf-8") as file:
        return from_json(file.read(), ScenarioState)


def load_official_annotations(snapshot_dpath: str | Path) -> dict[DisplayKey, Any]:
    """The detached original annotations, keyed by display key."""
    annotations: dict[DisplayKey, Any] = {}
    with open(Path(snapshot_dpath) / "official_annotations.jsonl", "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            annotations[DisplayKey(**record["key"])] = record["annotations"]
    return annotations


__all__ = [
    "DONE_FNAME",
    "SNAPSHOT_ARTIFACT_TYPE",
    "SNAPSHOT_SCHEMA_VERSION",
    "SnapshotBuildError",
    "SnapshotResult",
    "build_response_snapshot",
    "compute_response_set_hash",
    "compute_snapshot_response_set_hash",
    "load_official_annotations",
    "load_snapshot_manifest",
    "load_snapshot_scenario_state",
    "normalized_response_records",
    "reconstruct_scenario_state",
    "verify_snapshot",
]

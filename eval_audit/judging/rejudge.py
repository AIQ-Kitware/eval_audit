"""The annotation-only rejudge runner.

Phase 7 of ``docs/planning/open-judge-plan.md`` (§12): a standalone
runner that applies ONE configurable judge to ONE frozen response
snapshot for ONE replicate. It never subclasses the HELM ``Runner``,
never calls ``run_benchmarking()``, and performs **no candidate
inference** — the only executor invoked is HELM's
``AnnotationExecutor``, and the candidate facts are proven unchanged
before the artifact is finalized.

Artifact (``<out_root>/<attempt_hash>/``, format ``helm_rejudge_v1``)::

    run_spec.json            derived rejudge spec (HELM-shaped)
    scenario_state.json      annotated reconstructed state
    judgments.jsonl          one provenance record per display key
    response_manifest.json   copied from the snapshot
    judge_manifest.json      full JudgeSpec + request_random
    rejudge_manifest.json    identity: hashes, replicate, references
    process_context.json     runtime provenance
    stats.json               judge-attributed metrics (Phase 6 metric)
    per_instance_stats.json
    DONE                     written last

Caches are per (response set, judge spec, replicate) —
``<cache_root>/<response_set_hash>/<judge_spec_hash>/replicate-<n>/``
— so a restarted attempt reuses its completed judge requests and
distinct replicates can never share responses (each also carries its
own ``Request.random``).
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from helm.benchmark.adaptation.scenario_state import ScenarioState
from helm.benchmark.annotation.annotator import AnnotatorSpec
from helm.benchmark.annotation_executor import AnnotationExecutionSpec, AnnotationExecutor
from helm.benchmark.config_registry import (
    register_builtin_configs_from_helm_package,
    register_configs_from_directory,
)
from helm.common.cache_backend_config import SqliteCacheBackendConfig
from helm.common.codec import to_json

from eval_audit.judging.display_keys import DisplayKey, serialize_perturbation
from eval_audit.judging.response_snapshot import (
    load_snapshot_manifest,
    load_snapshot_scenario_state,
    normalized_response_records,
    verify_snapshot,
)
from eval_audit.judging.specs import JudgeSpec, JudgmentAttemptSpec, default_request_random

REJUDGE_ARTIFACT_FORMAT = "helm_rejudge_v1"
DONE_FNAME = "DONE"

#: benchmark -> configurable single-judge annotator (plan Phase 5).
CONFIGURABLE_ANNOTATOR_CLASSES: dict[str, str] = {
    "xstest": "eval_audit.integrations.helm_judging.safety.ConfigurableXSTestAnnotator",
}


class RejudgeError(RuntimeError):
    pass


_REGISTERED_CONFIG_DIRS: set[str] = set()
_BUILTINS_REGISTERED = [False]


def _ensure_helm_configs(sidecar_config_dpaths: tuple[str, ...]) -> None:
    """Idempotently register built-in HELM configs plus judge sidecars
    (registries are process-global; re-registration is redundant work)."""
    if not _BUILTINS_REGISTERED[0]:
        register_builtin_configs_from_helm_package()
        _BUILTINS_REGISTERED[0] = True
    for dpath in sidecar_config_dpaths:
        resolved = str(Path(dpath).resolve())
        if resolved not in _REGISTERED_CONFIG_DIRS:
            register_configs_from_directory(resolved)
            _REGISTERED_CONFIG_DIRS.add(resolved)


def _state_display_key(request_state) -> DisplayKey:
    perturbation = request_state.instance.perturbation
    return DisplayKey(
        instance_id=str(request_state.instance.id),
        perturbation=serialize_perturbation(
            json.loads(to_json(perturbation)) if perturbation is not None else None
        ),
        train_trial_index=request_state.train_trial_index,
    )


def _assert_candidates_unchanged(
    snapshot_dpath: Path, annotated_state: ScenarioState
) -> None:
    """§12.1 step 14: prove annotation did not touch a single candidate
    fact (text, thinking, reference index, count, success)."""
    records = normalized_response_records(
        json.loads((snapshot_dpath / "instances.json").read_text()),
        json.loads((snapshot_dpath / "display_requests.json").read_text()),
        json.loads((snapshot_dpath / "display_predictions.json").read_text()),
    )
    expected = {
        DisplayKey(**record["key"]): record for record in records
    }
    seen = set()
    for request_state in annotated_state.request_states:
        key = _state_display_key(request_state)
        record = expected.get(key)
        if record is None:
            raise RejudgeError(f"annotated state contains unknown display key {key}")
        seen.add(key)
        result = request_state.result
        if result is None or not result.success or len(result.completions) != 1:
            raise RejudgeError(f"candidate result mutated for {key}")
        completion = result.completions[0]
        if completion.text != record["predicted_text"]:
            raise RejudgeError(f"candidate text mutated for {key}")
        thinking_text = completion.thinking.text if completion.thinking else None
        if thinking_text != record.get("thinking_text"):
            raise RejudgeError(f"candidate thinking mutated for {key}")
        if request_state.reference_index != record.get("reference_index"):
            raise RejudgeError(f"reference index mutated for {key}")
    if seen != set(expected):
        raise RejudgeError("annotated state lost display keys")


@dataclass(frozen=True)
class RejudgeResult:
    out_dpath: Path
    attempt_hash: str
    response_set_hash: str
    cache_hit: bool


def run_rejudge(
    snapshot_dpath: str | Path,
    judge: JudgeSpec,
    replicate: int,
    out_root: str | Path,
    cache_root: str | Path,
    experiment_name: str = "open-judge",
    request_random: str | None = None,
    sidecar_config_dpaths: tuple[str, ...] = (),
    parallelism: int = 4,
) -> RejudgeResult:
    """Execute one judgment attempt; idempotent per attempt hash."""
    snapshot_dpath = Path(snapshot_dpath)
    out_root = Path(out_root)
    cache_root = Path(cache_root)

    response_set_hash = verify_snapshot(snapshot_dpath)
    snapshot_manifest = load_snapshot_manifest(snapshot_dpath)
    benchmark = snapshot_manifest["supported_benchmark"]
    annotator_class = CONFIGURABLE_ANNOTATOR_CLASSES.get(benchmark)
    if annotator_class is None:
        raise RejudgeError(
            f"no configurable annotator implemented for benchmark {benchmark!r}"
            f" (have: {sorted(CONFIGURABLE_ANNOTATOR_CLASSES)})"
        )

    if request_random is None:
        request_random = default_request_random(experiment_name, judge.id, replicate)
    attempt = JudgmentAttemptSpec(
        response_set_hash=response_set_hash,
        benchmark=benchmark,
        judge=judge,
        replicate=replicate,
        request_random=request_random,
    )
    attempt_hash = attempt.attempt_hash()
    out_dpath = out_root / attempt_hash
    if (out_dpath / DONE_FNAME).is_file():
        return RejudgeResult(out_dpath, attempt_hash, response_set_hash, cache_hit=True)
    if out_dpath.exists():
        shutil.rmtree(out_dpath)  # partial output is never trusted

    _ensure_helm_configs(sidecar_config_dpaths)

    scenario_state = load_snapshot_scenario_state(snapshot_dpath)
    for request_state in scenario_state.request_states:
        if request_state.result is None or not request_state.result.success:
            raise RejudgeError("snapshot request state lacks a successful candidate result")
        if request_state.annotations is not None:
            raise RejudgeError("snapshot request state already carries annotations")

    annotator_args = judge.annotator_args(request_random)
    annotator_args["judge_spec_hash"] = judge.spec_hash()
    annotator_spec = AnnotatorSpec(class_name=annotator_class, args=annotator_args)
    judging_state = ScenarioState(
        adapter_spec=scenario_state.adapter_spec,
        request_states=scenario_state.request_states,
        annotator_specs=[annotator_spec],
    )

    cache_dpath = (
        cache_root / response_set_hash / judge.spec_hash() / f"replicate-{replicate}"
    )
    cache_dpath.mkdir(parents=True, exist_ok=True)
    helm_local_path = cache_root / "helm_local"
    helm_local_path.mkdir(parents=True, exist_ok=True)

    started_at = time.time()
    executor = AnnotationExecutor(
        AnnotationExecutionSpec(
            local_path=str(helm_local_path),
            parallelism=parallelism,
            dry_run=False,
            sqlite_cache_backend_config=SqliteCacheBackendConfig(str(cache_dpath)),
        )
    )
    annotated_state = executor.execute(judging_state)

    # The one invariant that makes this a rejudge and not a rerun.
    _assert_candidates_unchanged(snapshot_dpath, annotated_state)

    annotator_name = snapshot_manifest["annotator_name"]
    judgments = []
    for request_state in annotated_state.request_states:
        if not request_state.annotations or annotator_name not in request_state.annotations:
            raise RejudgeError(
                f"annotation missing for {_state_display_key(request_state)}"
            )
        judgments.append(
            {
                "key": _state_display_key(request_state).as_dict(),
                "annotation": request_state.annotations[annotator_name],
            }
        )

    source_run_spec = json.loads((snapshot_dpath / "source_run_spec.json").read_text())
    derived_run_spec = dict(source_run_spec)
    derived_run_spec["name"] = (
        f"{source_run_spec['name']},rejudge={judge.id},replicate={replicate}"
    )
    derived_run_spec["annotators"] = [
        {"class_name": annotator_class, "args": annotator_args}
    ]
    # Judge-attributed metric specs land with the Phase 6 metric; the
    # canonical official metric specs must NOT survive into a rejudge.
    derived_run_spec["metric_specs"] = []

    rejudge_manifest = {
        "artifact_format": REJUDGE_ARTIFACT_FORMAT,
        "execution_kind": "rejudge",
        "candidate_inference_reused": True,
        "response_set_hash": response_set_hash,
        "judge_spec_hash": judge.spec_hash(),
        "attempt_hash": attempt_hash,
        "replicate": replicate,
        "benchmark": benchmark,
        "judge_id": judge.id,
        "request_random": request_random,
        "annotator_class": annotator_class,
        "snapshot_dpath": str(snapshot_dpath),
        "source_run": snapshot_manifest["source_run"],
        "source_run_spec_name": snapshot_manifest["source_run_spec_name"],
        "num_judgments": len(judgments),
        "parallelism": parallelism,
    }
    process_context = {
        "python_version": sys.version,
        "platform": platform.platform(),
        "hostname": platform.node(),
        "started_at_unix": started_at,
        "finished_at_unix": time.time(),
        "argv": sys.argv,
    }

    tmp_dpath = out_root / f".tmp-{attempt_hash}-{os.getpid()}"
    if tmp_dpath.exists():
        shutil.rmtree(tmp_dpath)
    tmp_dpath.mkdir(parents=True)
    try:
        (tmp_dpath / "scenario_state.json").write_text(to_json(annotated_state))
        with open(tmp_dpath / "judgments.jsonl", "w", encoding="utf-8") as file:
            for judgment in judgments:
                file.write(json.dumps(judgment, sort_keys=True) + "\n")
        _write_json(tmp_dpath / "run_spec.json", derived_run_spec)
        shutil.copyfile(
            snapshot_dpath / "response_manifest.json", tmp_dpath / "response_manifest.json"
        )
        _write_json(
            tmp_dpath / "judge_manifest.json",
            dict(judge.as_dict(), request_random=request_random),
        )
        _write_json(tmp_dpath / "rejudge_manifest.json", rejudge_manifest)
        _write_json(tmp_dpath / "process_context.json", process_context)
        _write_metrics(tmp_dpath, annotated_state, benchmark, judge)
        with open(tmp_dpath / DONE_FNAME, "w", encoding="utf-8") as file:
            file.write(attempt_hash + "\n")
        os.replace(tmp_dpath, out_dpath)
    except BaseException:
        shutil.rmtree(tmp_dpath, ignore_errors=True)
        raise
    return RejudgeResult(out_dpath, attempt_hash, response_set_hash, cache_hit=False)


def _write_json(fpath: Path, obj: Any) -> None:
    with open(fpath, "w", encoding="utf-8") as file:
        json.dump(obj, file, indent=2)
        file.write("\n")


def _write_metrics(
    tmp_dpath: Path, annotated_state: ScenarioState, benchmark: str, judge: JudgeSpec
) -> None:
    """Evaluate the judge-attributed metric (§12.1 step 13). Lands with
    the Phase 6 metric commit; until then rejudge artifacts carry
    judgments but no stats files."""
    del tmp_dpath, annotated_state, benchmark, judge


__all__ = [
    "CONFIGURABLE_ANNOTATOR_CLASSES",
    "DONE_FNAME",
    "REJUDGE_ARTIFACT_FORMAT",
    "RejudgeError",
    "RejudgeResult",
    "run_rejudge",
]

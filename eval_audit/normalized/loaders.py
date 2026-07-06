"""Loader registry and concrete loaders for the normalized layer.

Loaders convert artifacts on disk into :class:`NormalizedRun` instances.
They are registered against an :class:`ArtifactFormat`, and dispatched by
:func:`load_run`.

Two loaders ship in Stage 2:

* :class:`EeeArtifactLoader` reads converted EEE artifact directories
  produced by ``every_eval_ever convert helm`` (or another converter that
  emits the same shape).
* :class:`HelmRawLoader` reads raw HELM run directories and converts to EEE
  in-memory using the ``every_eval_ever.converters.helm.HELMAdapter``.

Both loaders preserve the :class:`Origin` so downstream reports can drill
back to the canonical raw evidence.
"""

from __future__ import annotations

import abc
import importlib.metadata
import json
import os
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from eval_audit.normalized.model import (
    ArtifactFormat,
    InstanceRecord,
    NormalizedRun,
    NormalizedRunRef,
    Origin,
)

# every_eval_ever's pydantic schemas — used by EeeArtifactLoader and
# (separately) by HelmRawLoader. Imported at module level so the
# pydantic schema build cost (~1s combined) happens once at import
# rather than on every loader call. No circular import concern.
from every_eval_ever.eval_types import EvaluationLog
from every_eval_ever.instance_level_types import InstanceLevelEvaluationLog

from eval_audit.metrics_taxonomy import is_binary_instance_metric

# orjson is ~3x faster than stdlib json on EEE samples.jsonl; we do
# millions of line-parses per heatmap run. Bind once at module load
# with a stdlib fallback so individual loaders can just call
# ``_loads(raw)`` without re-importing.
try:
    import orjson as _orjson
    _loads = _orjson.loads
except ImportError:
    _loads = json.loads

from eval_audit.infra.profiling import profile

# Concrete loaders are registered by ArtifactFormat.
_REGISTRY: dict[ArtifactFormat, "Loader"] = {}


class LoaderError(RuntimeError):
    """Raised when a loader cannot produce a normalized run."""


class Loader(abc.ABC):
    """Abstract loader: ref → :class:`NormalizedRun`."""

    artifact_format: ArtifactFormat

    @abc.abstractmethod
    def load(self, ref: NormalizedRunRef) -> NormalizedRun:
        ...


def register_loader(loader: Loader) -> None:
    _REGISTRY[loader.artifact_format] = loader


def get_loader(artifact_format: ArtifactFormat) -> Loader:
    try:
        return _REGISTRY[artifact_format]
    except KeyError as exc:
        raise LoaderError(
            f"No loader registered for artifact_format={artifact_format!r}"
        ) from exc


@profile
def load_run(ref: NormalizedRunRef) -> NormalizedRun:
    """Load a normalized run, dispatching on ``ref.artifact_format``."""
    return get_loader(ref.artifact_format).load(ref)


#: ``ref.extra`` key declaring which instance source an EEE load uses.
INSTANCE_SOURCE_POLICY_KEY = "instance_source_policy"

_INSTANCE_SOURCE_POLICIES = {"eee-only", "helm-preferred"}


def _resolve_instance_source_policy(ref: NormalizedRunRef) -> str:
    """Resolve the declared instance-source policy for an EEE load.

    Priority: an explicit ``instance_source_policy`` on ``ref.extra``
    (set by the entry point — EEE-only CLIs declare ``eee-only``, the
    HELM-driven renderer declares ``helm-preferred``), then the
    deprecated ``EVAL_AUDIT_EEE_STRICT`` env override (one deprecation
    cycle; equivalent to ``eee-only``), then ``helm-preferred`` — the
    legacy enriched behavior, now explicit and recorded instead of
    silent (Phase 3 / 4.5).
    """
    raw = str(ref.extra.get(INSTANCE_SOURCE_POLICY_KEY) or "").strip().lower()
    if raw in _INSTANCE_SOURCE_POLICIES:
        return raw
    if raw:
        raise LoaderError(
            f"Unknown instance_source_policy {raw!r}; "
            f"expected one of {sorted(_INSTANCE_SOURCE_POLICIES)}"
        )
    eee_strict = os.environ.get(
        "EVAL_AUDIT_EEE_STRICT", ""
    ).strip().lower() in {"1", "true", "yes"}
    if eee_strict:
        return "eee-only"
    return "helm-preferred"


# ---------------------------------------------------------------------------
# EEE artifact loader
# ---------------------------------------------------------------------------

class EeeArtifactLoader(Loader):
    """Read a converted EEE artifact directory.

    Layout produced by ``every_eval_ever convert helm``:

    .. code-block::

        <artifact_path>/
          <evaluation_name>/<org>/<model>/
            <uuid>.json              # EvaluationLog (aggregate / run-level)
            <uuid>_samples.jsonl     # InstanceLevelEvaluationLog records

    A single artifact_path may contain multiple ``<uuid>.json`` aggregates if
    the converter discovered multiple HELM runs under the same source dir.
    The loader merges them into one :class:`NormalizedRun` keyed by
    ``ref.logical_run_key`` when present; otherwise it picks the most-recent
    aggregate by ``retrieved_timestamp``.
    """

    artifact_format = ArtifactFormat.EEE

    @profile
    def load(self, ref: NormalizedRunRef) -> NormalizedRun:
        if ref.artifact_format is not ArtifactFormat.EEE:
            raise LoaderError(f"EeeArtifactLoader cannot load {ref.artifact_format!r}")

        artifact_path = Path(ref.artifact_path)
        if not artifact_path.exists():
            raise LoaderError(f"EEE artifact path does not exist: {artifact_path}")

        # R-5: single shared aggregate-name predicate (excludes provenance/
        # status/run_spec/fixture_manifest sidecars and *_samples.json dumps).
        from eval_audit.normalized.recipe_facts import is_aggregate_json_name

        aggregate_paths = sorted(
            p for p in artifact_path.rglob("*.json") if is_aggregate_json_name(p.name)
        )
        if not aggregate_paths:
            raise LoaderError(
                f"No EEE aggregate JSON files found under {artifact_path}"
            )

        candidates: list[tuple[EvaluationLog, Path]] = []
        for p in aggregate_paths:
            # Aggregate JSON parse: validate (not construct) because we
            # only do this once per *.json found in the dir, and getting
            # validation errors here points at a broken converter output
            # rather than a hot-loop concern. The expensive loop is the
            # samples.jsonl one below.
            #
            # Split read+parse so the line profiler can attribute disk
            # I/O independently from pydantic validation when this dir
            # contains many candidates (pre-dedupe).
            try:
                aggregate_text = p.read_text()
                log = EvaluationLog.model_validate_json(aggregate_text)
            except Exception:
                continue
            candidates.append((log, p))

        if not candidates:
            raise LoaderError(
                f"None of the JSON files under {artifact_path} parsed as EvaluationLog"
            )

        if ref.logical_run_key:
            named = [
                (log, p) for (log, p) in candidates
                if any(
                    er.evaluation_name == ref.logical_run_key
                    or ref.logical_run_key.startswith(er.evaluation_name + ":")
                    for er in (log.evaluation_results or [])
                )
            ]
            if named:
                candidates = named

        # Pick the newest by retrieved_timestamp when multiple candidates remain.
        # P2: a non-numeric timestamp used to raise an uncaught ValueError and
        # fail the whole load; coerce defensively and treat unparseable as 0.
        def _ts(lp: Any) -> float:
            try:
                return float(lp[0].retrieved_timestamp or 0)
            except (TypeError, ValueError):
                return 0.0

        candidates.sort(key=_ts, reverse=True)
        chosen_log, chosen_path = candidates[0]

        # Locate the matching samples.jsonl, if any.
        samples_path = chosen_path.with_name(chosen_path.stem + "_samples.jsonl")
        instances: list[InstanceRecord] = []
        if samples_path.exists():
            # Hot loop. Profile shows this is the dominant cost in the
            # whole analysis pipeline (~96s on a 4-packet heatmap with
            # civil_comments-scale samples files, ~798k lines total).
            #
            # Two modes, picked by EVAL_AUDIT_TRUST_EEE_SCHEMA:
            #
            # 1. Trust mode (env var = 1/true/yes): skip pydantic
            #    validation entirely. Parse the line with orjson and
            #    project directly into InstanceRecord using dict
            #    access. About 2.4x faster than mode 2 because it
            #    drops both the per-line pydantic schema check and the
            #    cost of building 798k nested model trees that nobody
            #    in this repo reads (see ``record`` field comment in
            #    model.py — that field is dead-weight today).
            #    Use this for paper-pass iteration; reviewers can flip
            #    it off with the env var unset to re-validate the same
            #    inputs.
            #
            # 2. Validate mode (default): orjson.loads + pydantic
            #    model_validate. Same shape as before; no behavior
            #    change vs. the previous commit. ~120µs/line on
            #    production-shape records.
            _trust = os.environ.get(
                "EVAL_AUDIT_TRUST_EEE_SCHEMA", ""
            ).strip().lower() in {"1", "true", "yes"}
            # Iterate the file object directly instead of
            # ``read_bytes().split(b"\n")``: the bulk read +
            # split materialized a 12.5M-entry bytes list before the
            # loop even started (~44s on the previous profile).
            # ``for raw in f`` returns one line at a time (with
            # trailing ``\n``), keeps memory flat, and orjson tolerates
            # the trailing newline.
            #
            # Positional InstanceRecord construction below — kwargs
            # were costing ~200ns/instance × 12.5M ≈ 2.5s of pure
            # kwargs-dict allocation. Field order matches the
            # @dataclass declaration in eval_audit.normalized.model.
            # IM-3: count corrupted/unparseable lines instead of silently
            # dropping them — a skipped line invisibly shrinks the agreement
            # denominator downstream.
            n_skipped_lines = 0
            if _trust:
                with samples_path.open("rb") as samples_fh:
                    for raw in samples_fh:
                        if not raw.strip():
                            continue
                        try:
                            d = _loads(raw)
                            ev = d["evaluation"]
                            sample_id = d["sample_id"]
                            sample_hash = d.get("sample_hash")
                            metric_id = d.get("evaluation_result_id") or d.get("evaluation_name")
                            score = float(ev["score"])
                            is_correct = ev.get("is_correct")
                            rec = InstanceRecord(
                                sample_id,
                                sample_hash,
                                metric_id,
                                None,           # metric_kind
                                score,
                                is_correct,
                                None,           # record (dead-weight; trust mode)
                            )
                            instances.append(rec)
                        except Exception:
                            n_skipped_lines += 1
                            continue
            else:
                instance_validate = InstanceLevelEvaluationLog.model_validate
                with samples_path.open("rb") as samples_fh:
                    for raw in samples_fh:
                        if not raw.strip():
                            continue
                        try:
                            # Split parse from validate so the profile
                            # shows orjson cost independently from the
                            # pydantic schema check.
                            parsed = _loads(raw)
                            rec = instance_validate(parsed)
                        except Exception:
                            n_skipped_lines += 1
                            continue
                        instances.append(_instance_record_from_eee(rec))
            if n_skipped_lines:
                logger.warning(
                    f"{samples_path}: skipped {n_skipped_lines} unparseable "
                    "samples.jsonl line(s); agreement denominators exclude them."
                )

        # HELM-origin EEE artifacts use the EEE aggregate as the run-level
        # source, but report drilldown still needs stable HELM sample ids.
        # Older conversions lacked metric ids; newer conversions may carry
        # metric rows with sample hashes that do not join across separately
        # converted public/local artifacts. Raw HELM per_instance_stats can
        # therefore be the better instance source — but ONLY as a declared
        # policy, never implicitly from what happens to be on disk
        # (Phase 3 / 4.5, design doc §3.7; replaces the silent fallback
        # flagged as the hot finding in docs/eee-only-hard-split-todo.md).
        #
        # ``instance_source_policy`` on ref.extra:
        #   'helm-preferred' — use HELM-derived instances when the origin
        #       run dir yields them; if the origin is recorded but
        #       unreadable, degrade to EEE instances and RECORD the
        #       degradation (EEE artifacts are self-sufficient; missing
        #       enrichment is a caveat, not a crash).
        #   'eee-only' — never read HELM JSONs; joins that needed stable
        #       HELM sample ids land in join_failed, the honest EEE-only
        #       signal.
        # The resulting choice is recorded as ``instance_source`` on the
        # returned ref's extra so reports carry the provenance.
        policy = _resolve_instance_source_policy(ref)
        instance_source = "eee"
        instance_source_note: str | None = None
        if policy == "helm-preferred" and ref.origin.helm_run_path is not None:
            try:
                raw_instances = _instances_from_raw_helm(
                    ref.origin.helm_run_path, chosen_log
                )
            except OSError as ex:
                raw_instances = []
                instance_source_note = f"helm_origin_unreadable: {ex!r}"
            if raw_instances:
                instances = raw_instances
                instance_source = "helm"
            elif instance_source_note is None:
                instance_source_note = "helm_origin_yielded_no_instances"

        # Augment ref.origin with the actual chosen artifact path.
        new_origin = Origin(
            helm_run_path=ref.origin.helm_run_path,
            eee_artifact_path=chosen_path,
            converter_name=ref.origin.converter_name or _eee_converter_name(),
            converter_version=ref.origin.converter_version or _eee_converter_version(),
        )
        new_extra = dict(ref.extra)
        new_extra["instance_source"] = instance_source
        new_extra["instance_source_policy"] = policy
        if instance_source_note is not None:
            new_extra["instance_source_note"] = instance_source_note
        new_ref = NormalizedRunRef(
            source_kind=ref.source_kind,
            artifact_format=ref.artifact_format,
            artifact_path=ref.artifact_path,
            origin=new_origin,
            component_id=ref.component_id,
            logical_run_key=ref.logical_run_key,
            display_name=ref.display_name,
            extra=new_extra,
        )
        return NormalizedRun(
            ref=new_ref,
            evaluation_log=chosen_log,
            instances=instances,
            raw_helm=None,
        )


@profile
def _instance_record_from_eee(rec) -> InstanceRecord:
    """Project an InstanceLevelEvaluationLog into the comparison-friendly shape."""
    return InstanceRecord(
        sample_id=rec.sample_id,
        sample_hash=rec.sample_hash,
        # EEE per-instance records currently carry one score per (sample,
        # metric) via ``evaluation_result_id``. When present we use it as the
        # metric handle; otherwise we use ``evaluation_name`` so each sample
        # is at least tagged with a stable per-eval identifier.
        metric_id=rec.evaluation_result_id or rec.evaluation_name,
        metric_kind=None,
        score=float(rec.evaluation.score),
        is_correct=rec.evaluation.is_correct,
        record=rec,
    )


# ---------------------------------------------------------------------------
# Raw-HELM loader (in-memory conversion)
# ---------------------------------------------------------------------------

class HelmRawLoader(Loader):
    """Load a raw HELM run directory by converting to EEE in-memory.

    This loader is the fallback for runs we have not yet (or cannot) convert
    to canonical EEE artifacts on disk. It uses
    :class:`every_eval_ever.converters.helm.adapter.HELMAdapter` directly so
    no subprocess or filesystem write is required.

    Raw HELM JSONs (``run_spec``, ``scenario_state``, ``stats``,
    ``per_instance_stats``) are also exposed via :attr:`NormalizedRun.raw_helm`
    so any legacy comparison code that still needs them during migration can
    reach them without re-reading the disk.
    """

    artifact_format = ArtifactFormat.HELM

    REQUIRED_FILES = (
        "run_spec.json",
        "scenario_state.json",
        "stats.json",
        "per_instance_stats.json",
    )

    @profile
    def load(self, ref: NormalizedRunRef) -> NormalizedRun:
        if ref.artifact_format is not ArtifactFormat.HELM:
            raise LoaderError(f"HelmRawLoader cannot load {ref.artifact_format!r}")

        run_path = Path(ref.artifact_path)
        if not run_path.is_dir():
            raise LoaderError(f"HELM run path is not a directory: {run_path}")
        missing = [n for n in self.REQUIRED_FILES if not (run_path / n).exists()]
        if missing:
            raise LoaderError(
                f"HELM run {run_path} is missing required files: {missing}"
            )

        # Delegate to the content-addressed cache. On hit, read directly via
        # :class:`EeeArtifactLoader`; on miss, run the HELM->EEE conversion
        # once into the cache (atomic per file via :mod:`safer`) and load the
        # cached artifact. This replaces the historical "convert into a
        # /tmp dir and discard" pattern that re-ran the converter on every
        # call.
        from eval_audit.normalized.eee_artifacts import (
            _artifact_has_aggregate,
            _status_permits_use,
            convert_helm_run_to_cached_eee,
            helm_raw_cache_parent,
        )

        cache_parent = helm_raw_cache_parent(run_path)
        cache_artifact = cache_parent / "eee_output"
        # P1-7: a cache hit is only valid if a successful status.json permits it;
        # a partially-failed prior conversion can leave an aggregate with a
        # non-ok status, which was otherwise reused forever.
        if not (
            _artifact_has_aggregate(cache_artifact)
            and _status_permits_use(cache_parent / "status.json")
        ):
            resolution = convert_helm_run_to_cached_eee(
                run_path,
                source_kind=ref.source_kind.value if hasattr(ref.source_kind, "value") else str(ref.source_kind),
                source_organization_name=ref.extra.get("source_organization_name", "eval_audit_helm_raw"),
                eval_library_name=ref.extra.get("eval_library_name", "HELM"),
                eval_library_version=ref.extra.get("eval_library_version", "unknown"),
                evaluator_relationship=ref.extra.get("evaluator_relationship", "third_party"),
            )
            if resolution.artifact_path is None:
                raise LoaderError(
                    f"HELM->EEE conversion failed for {run_path}: "
                    f"status={resolution.status} message={resolution.message}"
                )
            cache_artifact = resolution.artifact_path

        eee_ref = NormalizedRunRef(
            source_kind=ref.source_kind,
            artifact_format=ArtifactFormat.EEE,
            artifact_path=cache_artifact,
            origin=Origin(
                helm_run_path=run_path,
                eee_artifact_path=cache_artifact,
                converter_name=_eee_converter_name(),
                converter_version=_eee_converter_version(),
            ),
            component_id=ref.component_id,
            logical_run_key=ref.logical_run_key,
            display_name=ref.display_name,
            extra=ref.extra,
        )
        run = get_loader(ArtifactFormat.EEE).load(eee_ref)
        # Preserve the original ref's HELM artifact path / format so callers
        # that introspect ``run.ref`` see the same identity they passed in.
        new_ref = NormalizedRunRef(
            source_kind=ref.source_kind,
            artifact_format=ref.artifact_format,
            artifact_path=ref.artifact_path,
            origin=Origin(
                helm_run_path=run_path,
                eee_artifact_path=cache_artifact,
                converter_name=run.ref.origin.converter_name,
                converter_version=run.ref.origin.converter_version,
            ),
            component_id=ref.component_id,
            logical_run_key=ref.logical_run_key,
            display_name=ref.display_name,
            # P2: merge the EEE loader's recorded provenance (run.ref.extra)
            # over the original ref's extra — using ref.extra alone dropped the
            # instance-source provenance the EEE load recorded, so degraded
            # loads mislabelled their source as "helm".
            extra={**(ref.extra or {}), **(run.ref.extra or {})},
        )
        # IM-4: do NOT eager-load the raw HELM JSONs here. scenario_state.json is
        # the largest file in a run dir and was parsed on every load even under
        # --skip-diagnosis. Leave raw_helm=None; the only consumer
        # (_NormalizedJsonView in helm_compat) reads each JSON lazily from
        # Origin.helm_run_path (= run_path, set on new_ref above) on demand, so
        # this is behavior-preserving.
        return NormalizedRun(
            ref=new_ref,
            evaluation_log=run.evaluation_log,
            instances=run.instances,
            raw_helm=None,
        )


@profile
def _instances_from_raw_helm(run_path: Path, evaluation_log) -> list[InstanceRecord]:
    """Lift HELM ``per_instance_stats.json`` rows into :class:`InstanceRecord`.

    One :class:`InstanceRecord` per (instance_id, metric) so per-metric
    agreement curves stay computable. The ``record`` slot is populated with
    a minimal :class:`InstanceLevelEvaluationLog` so the comparison layer can
    reach the EEE schema if it needs to (input.raw, etc.).
    """
    from every_eval_ever.instance_level_types import (
        AnswerAttributionItem,
        Evaluation,
        Input,
        InstanceLevelEvaluationLog,
        InteractionType,
        Output,
    )

    per_instance_path = run_path / "per_instance_stats.json"
    scenario_state_path = run_path / "scenario_state.json"
    if not per_instance_path.exists():
        return []
    try:
        per_instance = json.loads(per_instance_path.read_text())
    except Exception:
        return []
    request_states_by_id: dict[str, dict[str, Any]] = {}
    if scenario_state_path.exists():
        try:
            scenario_state = json.loads(scenario_state_path.read_text())
        except Exception:
            scenario_state = {}
        for rs in scenario_state.get("request_states") or []:
            inst = rs.get("instance") or {}
            iid = inst.get("id")
            if iid is None:
                continue
            # Keep first occurrence per id to avoid stomping perturbed variants
            request_states_by_id.setdefault(str(iid), rs)

    eval_id = evaluation_log.evaluation_id
    model_id = evaluation_log.model_info.id
    eval_name = (
        evaluation_log.evaluation_results[0].evaluation_name
        if evaluation_log.evaluation_results
        else "unknown"
    )

    records: list[InstanceRecord] = []
    for bundle in per_instance:
        iid = bundle.get("instance_id")
        if iid is None:
            continue
        rs = request_states_by_id.get(str(iid), {})
        inst = rs.get("instance") or {}
        prompt = (rs.get("request") or {}).get("prompt") or inst.get("input", {}).get("text", "")
        completions = (rs.get("result") or {}).get("completions") or []
        completion_texts = [c.get("text", "") for c in completions]
        refs = [
            r.get("output", {}).get("text", "")
            for r in inst.get("references") or []
            if "correct" in (r.get("tags") or [])
        ]

        for stat in bundle.get("stats") or []:
            name_obj = stat.get("name") or {}
            metric_name = name_obj.get("name")
            if metric_name is None:
                continue
            mean = stat.get("mean")
            if mean is None:
                continue
            try:
                score = float(mean)
            except (TypeError, ValueError):
                continue
            # IM-5: only derive a correctness bool for genuinely-binary (0/1)
            # metrics; thresholding a continuous metric (f1/rouge/bleu/…) at 0.5
            # fabricates a signal that does not exist. None means "not
            # applicable" and no in-repo consumer reads InstanceRecord.is_correct
            # (audit 2026-05-01), so None is safe.
            is_correct_value: bool | None = (
                (score >= 0.5) if is_binary_instance_metric(metric_name) else None
            )
            try:
                rec = InstanceLevelEvaluationLog(
                    schema_version="0.2.2",
                    evaluation_id=eval_id,
                    model_id=model_id,
                    evaluation_name=eval_name,
                    evaluation_result_id=metric_name,
                    sample_id=str(iid),
                    sample_hash=None,
                    interaction_type=InteractionType.single_turn,
                    input=Input(raw=prompt or "", reference=refs),
                    output=Output(raw=completion_texts or [""]),
                    answer_attribution=[
                        AnswerAttributionItem(
                            turn_idx=0,
                            source="output.raw",
                            extracted_value=(completion_texts[0] if completion_texts else ""),
                            extraction_method="raw",
                            is_terminal=True,
                        )
                    ],
                    # The EEE Evaluation schema requires a bool; this synthesized
                    # record is never read for scoring (audit 2026-05-01), so a
                    # non-binary metric falls back to False here while the
                    # authoritative InstanceRecord.is_correct below stays None.
                    evaluation=Evaluation(
                        score=score,
                        is_correct=bool(is_correct_value) if is_correct_value is not None else False,
                    ),
                )
            except Exception:
                # If the EEE schema rejects this record (e.g. empty refs +
                # validator), fall back to skipping the per-instance schema
                # construction; the score still flows through InstanceRecord.
                rec = None
            records.append(
                InstanceRecord(
                    sample_id=str(iid),
                    sample_hash=None,
                    metric_id=metric_name,
                    metric_kind=None,
                    score=score,
                    is_correct=is_correct_value,
                    record=rec,
                )
            )
    return records


def _eee_converter_name() -> str:
    return "every_eval_ever.converters.helm"


def _eee_converter_version() -> str | None:
    try:
        return importlib.metadata.version("every_eval_ever")
    except Exception:
        return None


# Register defaults at import time so callers can use ``load_run`` directly.
register_loader(EeeArtifactLoader())
register_loader(HelmRawLoader())


__all__ = [
    "EeeArtifactLoader",
    "HelmRawLoader",
    "Loader",
    "LoaderError",
    "get_loader",
    "load_run",
    "register_loader",
]

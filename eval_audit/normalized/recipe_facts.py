"""Recipe-facts accessor: one resolver for "what recipe produced this run?".

The comparability/diagnosis layer needs scalar recipe facts
(scenario_class, model_deployment, instructions, max_eval_instances,
benchmark_family, run-spec name/hash — plus judge identity for the
open-judge extension). This module is the single place that answers
where those facts come from, in priority order
(design doc §3.6, sub-stage 4.1):

1. **native** — a ``recipe_facts`` block carried inside the EEE
   aggregate itself, under
   ``source_metadata.additional_details["recipe_facts"]`` as a
   JSON-encoded string. ``EvaluationLog`` is ``extra='forbid'`` so a
   new top-level slot needs upstream coordination (sub-stage 4.7);
   ``additional_details`` is free-form ``dict[str, str]``, so this
   interim convention works with the schema as-is and converters can
   adopt it today. A native block makes the artifact self-describing —
   the keystone for non-HELM frameworks participating in diagnosis.
2. **sidecar** — a ``run_spec.json`` shipped next to the EEE artifact
   (or the HELM run dir's own ``run_spec.json``), read via
   :func:`eval_audit.indexing.schema.extract_run_spec_fields`.
3. **unknown** — neither available. All facts ``None``; downstream
   comparability facts collapse to ``status='unknown'`` and emit
   ``comparability_unknown:*`` warnings. That collapse is the honest
   "recipe unverifiable" signal and must not be defaulted away.

Wiring status: the planner still derives comparability facts by calling
``extract_run_spec_fields`` directly (``planning/core_report_planner.py``);
:func:`resolve_recipe_facts` is exercised by the native-block read path
and by ``tests/test_recipe_facts.py``, but is not yet the planner's
entry point. Routing the planner through this resolver remains open
follow-up work.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eval_audit.indexing.schema import extract_judge_models, extract_run_spec_fields

#: Key inside ``source_metadata.additional_details`` carrying the
#: JSON-encoded native recipe-facts block (interim convention; 4.7
#: upstreams a proper schema slot).
NATIVE_RECIPE_FACTS_KEY = "recipe_facts"

#: Filenames next to an EEE aggregate that are never the aggregate
#: itself. Single source of truth for the aggregate-detection sites
#: across the normalized package (R-5).
_NON_AGGREGATE_NAMES = {
    "provenance.json",
    "status.json",
    "run_spec.json",
    "fixture_manifest.json",
}


def is_aggregate_json_name(name: str) -> bool:
    """True if a ``*.json`` filename could be an EEE aggregate.

    Excludes the fixed sidecars that are never the aggregate
    (:data:`_NON_AGGREGATE_NAMES`: provenance/status/run_spec/fixture_manifest)
    and per-instance ``*_samples.json`` dumps. Using the *complete* name set is
    what closes the sidecar-only-dir bug (R-5): a directory containing only
    ``run_spec.json`` no longer counts as having an aggregate.
    """
    return name not in _NON_AGGREGATE_NAMES and not name.endswith("_samples.json")


def artifact_has_aggregate(artifact_path: Path) -> bool:
    """True if ``artifact_path`` is a dir holding at least one candidate aggregate JSON."""
    if not artifact_path.is_dir():
        return False
    return any(
        is_aggregate_json_name(path.name) for path in artifact_path.rglob("*.json")
    )

#: Scalar fact fields a native block may carry. Anything else in the
#: block is preserved in ``extra`` for forward compatibility.
_FACT_FIELDS = (
    "run_spec_name",
    "model",
    "model_deployment",
    "scenario_class",
    "benchmark_group",
    "instructions",
    "max_eval_instances",
    "run_spec_hash",
)


@dataclass(frozen=True)
class RecipeFacts:
    """Resolved recipe facts plus where they came from.

    ``source`` ∈ {'native', 'sidecar', 'unknown'}. All fact fields are
    ``None`` when unavailable. ``judge_models`` is ``None`` when judge
    identity is unknown and an (alphabetically sorted) tuple — possibly
    empty, meaning *known to use no judge* — when it is known.
    """

    source: str
    run_spec_name: str | None = None
    model: str | None = None
    model_deployment: str | None = None
    scenario_class: str | None = None
    benchmark_group: str | None = None
    instructions: str | None = None
    max_eval_instances: str | None = None
    run_spec_hash: str | None = None
    judge_models: tuple[str, ...] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "run_spec_name": self.run_spec_name,
            "model": self.model,
            "model_deployment": self.model_deployment,
            "scenario_class": self.scenario_class,
            "benchmark_group": self.benchmark_group,
            "instructions": self.instructions,
            "max_eval_instances": self.max_eval_instances,
            "run_spec_hash": self.run_spec_hash,
            "judge_models": (
                list(self.judge_models) if self.judge_models is not None else None
            ),
            "extra": dict(self.extra),
        }


UNKNOWN_RECIPE_FACTS = RecipeFacts(source="unknown")


def _clean_text(value: Any) -> str | None:
    text = str(value if value is not None else "").strip()
    return text or None


def _facts_from_run_spec(run_spec_fpath: Path) -> RecipeFacts:
    fields = extract_run_spec_fields(run_spec_fpath)
    instructions: str | None = None
    max_eval_instances: str | None = None
    judge_models: tuple[str, ...] | None = None
    try:
        spec = json.loads(Path(run_spec_fpath).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        spec = None
    if isinstance(spec, dict):
        adapter = spec.get("adapter_spec")
        if isinstance(adapter, dict):
            instructions = _clean_text(adapter.get("instructions"))
            mei = adapter.get("max_eval_instances")
            if mei is not None:
                max_eval_instances = str(mei)
        judge_models = extract_judge_models(spec)
    return RecipeFacts(
        source="sidecar",
        run_spec_name=fields.get("run_spec_name"),
        model=fields.get("model"),
        model_deployment=fields.get("model_deployment"),
        scenario_class=fields.get("scenario_class"),
        benchmark_group=fields.get("benchmark_group"),
        instructions=instructions,
        max_eval_instances=max_eval_instances,
        run_spec_hash=fields.get("run_spec_hash"),
        judge_models=judge_models,
    )


def _iter_aggregate_jsons(artifact_dir: Path) -> list[Path]:
    return [
        fpath
        for fpath in sorted(artifact_dir.rglob("*.json"))
        if is_aggregate_json_name(fpath.name)
    ]


def _native_block_from_aggregate(fpath: Path) -> dict[str, Any] | None:
    """Read the JSON-encoded native block from one aggregate, leniently.

    Lenient raw-JSON read (not pydantic): the accessor must work on
    artifacts from any producer version, and only needs two nested
    keys. Returns None when the file isn't an aggregate or carries no
    block.
    """
    try:
        data = json.loads(fpath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or "evaluation_results" not in data:
        return None
    source_metadata = data.get("source_metadata")
    if not isinstance(source_metadata, dict):
        return None
    details = source_metadata.get("additional_details")
    if not isinstance(details, dict):
        return None
    raw = details.get(NATIVE_RECIPE_FACTS_KEY)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        block = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return block if isinstance(block, dict) else None


def _facts_from_native_block(block: dict[str, Any]) -> RecipeFacts:
    known = {name: _clean_text(block.get(name)) for name in _FACT_FIELDS}
    judge_models: tuple[str, ...] | None = None
    if "judge_models" in block:
        raw = block.get("judge_models")
        if isinstance(raw, list):
            judge_models = tuple(sorted({v for v in (_clean_text(x) for x in raw) if v}))
    extra = {
        key: value
        for key, value in block.items()
        if key not in _FACT_FIELDS and key != "judge_models"
    }
    return RecipeFacts(source="native", judge_models=judge_models, extra=extra, **known)


def resolve_recipe_facts(
    *,
    eee_artifact_dir: str | Path | None = None,
    run_spec_fpath: str | Path | None = None,
) -> RecipeFacts:
    """Resolve recipe facts: native block → sidecar run_spec → unknown.

    ``eee_artifact_dir`` is searched first for an aggregate carrying a
    native block; when none is found, a ``run_spec.json`` directly
    inside the artifact dir acts as the sidecar (matching
    ``detect_helm_sidecars``). An explicitly passed ``run_spec_fpath``
    is used when the artifact dir yields nothing (or when no artifact
    dir is given at all — the raw-HELM case).
    """
    if eee_artifact_dir is not None:
        artifact_dir = Path(eee_artifact_dir)
        if artifact_dir.is_dir():
            for aggregate in _iter_aggregate_jsons(artifact_dir):
                block = _native_block_from_aggregate(aggregate)
                if block is not None:
                    return _facts_from_native_block(block)
            sidecar = artifact_dir / "run_spec.json"
            if sidecar.is_file():
                return _facts_from_run_spec(sidecar)
    if run_spec_fpath is not None:
        fpath = Path(run_spec_fpath)
        if fpath.is_file():
            return _facts_from_run_spec(fpath)
    return UNKNOWN_RECIPE_FACTS


__all__ = [
    "NATIVE_RECIPE_FACTS_KEY",
    "RecipeFacts",
    "UNKNOWN_RECIPE_FACTS",
    "extract_judge_models",
    "resolve_recipe_facts",
]

"""Schedule-time resolver + materializer for ``(public_root, relative_path)`` replay.

This is the host-side step that replaces the run-entry **locator** (the in-container
token-subset scan ``find_best_precomputed_run``) with exact addressing. For each
run we want to reproduce, the operator names the official run by its path relative
to the public-HELM root; before kwdagger runs, this module:

1. **resolves** ``<precomputed_root>/<rel_path>`` to the official ``run_spec.json``
   (a real file on disk) and validates it exists — a bad address fails loud and
   early, on the host, naming the path tried;
2. **substitutes** the declared fields as **raw-JSON scalar edits** —
   ``adapter_spec.model_deployment`` (the local engine that will serve the run)
   and ``adapter_spec.max_eval_instances`` (the instance cap). It edits *only*
   those scalars and re-dumps, so every other key is preserved exactly: no cattrs
   round-trip, hence none of the silent field-drift that
   ``docs/planning/run-from-run-spec-json-plan.md`` §1 warns about;
3. **materializes** a substituted copy to a staging dir, plus a
   ``materialization.json`` sidecar recording the official source, the rel-path,
   and each field's ``from -> to`` (the diffable provenance record).

The materialized copy is what Stage 3 replays verbatim (``--run-spec-json``), so the
in-container ``--model-deployment`` / ``--max-eval-instances`` rewrite is not
exercised on this path; ``adapter_spec.model`` is never touched, so the produced
run dir keeps the official ``run_spec.name`` and Stages 4–6 are unchanged.

See ``docs/planning/run-from-relative-path-plan.md`` §4.1.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# The two (and only two) recipe fields this path substitutes. Both live under
# ``adapter_spec``; ``adapter_spec.model`` is deliberately NOT in this set.
_MODEL_DEPLOYMENT_KEY = "model_deployment"
_MAX_EVAL_INSTANCES_KEY = "max_eval_instances"


@dataclass(frozen=True)
class RunSpecSource:
    """One run to replay, addressed by exact path (pre-resolution).

    ``rel_path`` is relative to ``precomputed_root`` and may name either the run
    directory or the ``run_spec.json`` inside it. ``run_entry`` is a label kept
    for provenance / logging only — it no longer locates anything. ``model_deployment``
    is the LOCAL rewrite target (``None`` ⇒ replay the official deployment verbatim).
    ``lease_endpoint`` is the per-run catalog endpoint to lease (``None`` ⇒ no
    lease). ``max_eval_instances`` overrides the experiment default when set.
    """

    run_entry: str
    rel_path: str
    model_deployment: str | None = None
    lease_endpoint: str | None = None
    max_eval_instances: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunSpecSource":
        try:
            run_entry = data["run_entry"]
            rel_path = data["rel_path"]
        except KeyError as exc:  # pragma: no cover - defensive
            raise ValueError(
                f"run_spec source is missing required key {exc!s}: {data!r}"
            ) from exc
        return cls(
            run_entry=str(run_entry),
            rel_path=str(rel_path),
            model_deployment=_opt_str(data.get("model_deployment")),
            lease_endpoint=_opt_str(data.get("lease_endpoint")),
            max_eval_instances=_opt_int(data.get("max_eval_instances")),
        )


@dataclass(frozen=True)
class MaterializedRunSpec:
    """The result of resolving + substituting one :class:`RunSpecSource`."""

    run_entry: str
    run_spec_json: str  # absolute path to the materialized (substituted) copy
    official_run_spec_json: str  # absolute path to the official source
    rel_path: str
    lease_endpoint: str | None
    # {field_name: {"from": <official>, "to": <substituted>}} — only changed fields.
    substitutions: dict[str, dict[str, Any]] = field(default_factory=dict)


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _opt_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def resolve_official_run_spec(precomputed_root: str | Path, rel_path: str) -> Path:
    """Resolve ``<precomputed_root>/<rel_path>`` to the official ``run_spec.json``.

    ``rel_path`` may name the run directory or the ``run_spec.json`` itself. Raises
    ``FileNotFoundError`` naming the exact path tried if it does not exist — the
    whole point of exact addressing is that "not found" is precise, not a silent
    best-effort match.
    """
    root = Path(precomputed_root).expanduser()
    joined = (root / rel_path).expanduser()
    if joined.name == "run_spec.json":
        candidate = joined
    elif joined.is_dir():
        candidate = joined / "run_spec.json"
    else:
        # rel_path that neither ends in run_spec.json nor is an existing dir:
        # treat it as a run-dir path and look inside (covers not-yet-existing
        # dirs so the error message points at the run_spec.json we wanted).
        candidate = joined / "run_spec.json"
    if not candidate.is_file():
        raise FileNotFoundError(
            "official run_spec.json not found for rel_path "
            f"{rel_path!r} under precomputed_root {str(root)!r}: tried {str(candidate)!r}"
        )
    return candidate


def _run_id(source: RunSpecSource) -> str:
    """Deterministic, filesystem-safe, collision-resistant staging id.

    Readable benchmark stem + a short hash of the fields that make the run unique
    (rel_path + the local deployment, so multi-deployment replays of one official
    run do not collide). No timestamp — materialization must be deterministic.
    """
    stem = source.run_entry.split(":", 1)[0] or "run"
    stem = "".join(c if (c.isalnum() or c in "-_") else "_" for c in stem)[:40]
    digest = hashlib.sha1(
        f"{source.rel_path}|{source.model_deployment}".encode()
    ).hexdigest()[:12]
    return f"{stem}_{digest}"


def materialize_run_spec(
    source: RunSpecSource,
    *,
    precomputed_root: str | Path,
    staging_dir: str | Path,
    default_max_eval_instances: int | None = None,
) -> MaterializedRunSpec:
    """Resolve, substitute (raw-JSON), and write one run's materialized copy.

    Edits **only** ``adapter_spec.model_deployment`` (when a local rewrite target
    is given) and ``adapter_spec.max_eval_instances`` (per-run override else the
    experiment default, when either is set). Every other key is preserved exactly.
    """
    official_path = resolve_official_run_spec(precomputed_root, source.rel_path)
    spec = json.loads(official_path.read_text())

    adapter_spec = spec.get("adapter_spec")
    if not isinstance(adapter_spec, dict):
        raise ValueError(
            f"official run_spec.json {str(official_path)!r} has no object "
            "'adapter_spec'; cannot apply substitutions"
        )

    substitutions: dict[str, dict[str, Any]] = {}

    if source.model_deployment is not None:
        before = adapter_spec.get(_MODEL_DEPLOYMENT_KEY)
        if before != source.model_deployment:
            adapter_spec[_MODEL_DEPLOYMENT_KEY] = source.model_deployment
            substitutions[_MODEL_DEPLOYMENT_KEY] = {
                "from": before,
                "to": source.model_deployment,
            }

    cap = (
        source.max_eval_instances
        if source.max_eval_instances is not None
        else default_max_eval_instances
    )
    if cap is not None:
        before = adapter_spec.get(_MAX_EVAL_INSTANCES_KEY)
        if before != cap:
            adapter_spec[_MAX_EVAL_INSTANCES_KEY] = cap
            substitutions[_MAX_EVAL_INSTANCES_KEY] = {"from": before, "to": cap}

    run_dir = Path(staging_dir).expanduser() / _run_id(source)
    run_dir.mkdir(parents=True, exist_ok=True)
    copy_path = run_dir / "run_spec.json"
    # Stable formatting (sorted keys, trailing newline) so repeated materialization
    # is byte-identical — supports the deterministic-output guarantee.
    copy_path.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n")

    result = MaterializedRunSpec(
        run_entry=source.run_entry,
        run_spec_json=str(copy_path.resolve()),
        official_run_spec_json=str(official_path.resolve()),
        rel_path=source.rel_path,
        lease_endpoint=source.lease_endpoint,
        substitutions=substitutions,
    )

    sidecar = {
        "schema": "eval-audit/run-spec-materialization/1",
        "run_entry": source.run_entry,
        "rel_path": source.rel_path,
        "precomputed_root": str(Path(precomputed_root).expanduser().resolve()),
        "official_run_spec_json": result.official_run_spec_json,
        "materialized_run_spec_json": result.run_spec_json,
        "lease_endpoint": source.lease_endpoint,
        "substitutions": substitutions,
    }
    (run_dir / "materialization.json").write_text(
        json.dumps(sidecar, indent=2, sort_keys=True) + "\n"
    )
    return result


def materialize_run_specs(
    sources: list[RunSpecSource],
    *,
    precomputed_root: str | Path,
    staging_dir: str | Path,
    default_max_eval_instances: int | None = None,
) -> list[MaterializedRunSpec]:
    """Materialize every run. Fails on the first unresolvable address (no silent skip)."""
    return [
        materialize_run_spec(
            source,
            precomputed_root=precomputed_root,
            staging_dir=staging_dir,
            default_max_eval_instances=default_max_eval_instances,
        )
        for source in sources
    ]


def coerce_sources(raw: list[Any]) -> list[RunSpecSource]:
    """Coerce manifest ``run_spec_sources`` entries (dicts) into dataclasses."""
    out: list[RunSpecSource] = []
    for item in raw:
        if isinstance(item, RunSpecSource):
            out.append(item)
        elif isinstance(item, dict):
            out.append(RunSpecSource.from_dict(item))
        else:  # pragma: no cover - defensive
            raise TypeError(f"run_spec source must be a dict, got {type(item)!r}")
    return out


def source_to_dict(source: RunSpecSource) -> dict[str, Any]:
    """Manifest-serializable form of a :class:`RunSpecSource` (drops None values)."""
    return {k: v for k, v in asdict(source).items() if v is not None}

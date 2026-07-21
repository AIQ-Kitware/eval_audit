"""Compute-from-spec: synthesize a frozen ``run_spec.json`` per authored key.

The reproduction path (``--freeze-rel-paths``) pins each run-entry to a matching
*official* run dir under ``precomputed_root`` and replays that frozen
``run_spec.json``. A de-novo **compute** run has no official to match — the
model was never in public HELM — so the run-key string has, until now, been the
stored source of truth and was re-expanded live at execution time under whatever
crfm-helm happened to be installed. That inherits exactly the version-coupling
this project criticizes HELM for (see
``docs/planning/compute-run-spec-freeze-plan.md``).

This module closes that gap without inventing a second executor. It **expands
the authored run-key once, at export, under the pinned HELM**
(``construct_run_specs`` — no inference, no GPU), writes each resulting
``run_spec.json`` into a *synthesized* ``precomputed_root``, and returns the
same ``run_spec_sources`` shape the reproduction freeze emits. The existing
``run_spec_materializer`` then replays those frozen specs byte-for-byte — the
run-key string becomes a transient authoring input and the frozen spec is the
durable identity. The expander touches each run exactly once, at birth.

Faithfulness: the spec written here is what ``construct_run_specs`` produces for
the *full* authored key (deployment token included), i.e. byte-identical to what
live expansion would have produced under the same HELM — so freezing provably
changes nothing about the recipe, it only pins it. The materializer's
``adapter_spec.model_deployment`` rewrite is a no-op here (the spec already
carries the local name), matching the reproduction path's per-source rewrite
target by construction.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from eval_audit.integrations.infer_stack.freeze import _strip_local_deployment

#: Filenames ``helm.benchmark.config_registry.register_configs_from_directory``
#: reads out of a prod_env dir. We stage the preset's sidecars + the generated
#: model_deployments under these exact names so offline expansion resolves the
#: net-new (qwen3.5) model / tokenizer / deployment ids the same way the runner
#: container will at execution time.
_MODEL_METADATA_NAME = "model_metadata.yaml"
_TOKENIZER_CONFIGS_NAME = "tokenizer_configs.yaml"
_MODEL_DEPLOYMENTS_NAME = "model_deployments.yaml"

#: Process-global guards so smoke+full (two calls) don't re-register builtins or
#: re-read the same prod_env dir. HELM's registries are module-level dicts, so
#: registration is a global side effect; keep it idempotent.
_BUILTINS_REGISTERED = False
_REGISTERED_DIRS: set[str] = set()


def _ensure_helm_registered(prod_env_dir: Path) -> None:
    """Register HELM builtins + this prod_env dir's sidecars/deployments once.

    ``register_configs_from_directory`` merges ``model_metadata.yaml`` /
    ``tokenizer_configs.yaml`` / ``model_deployments.yaml`` into HELM's global
    registry (upsert), so the qwen3.5 ids expand with the *served* metadata
    rather than HELM's default-model fallback.
    """
    global _BUILTINS_REGISTERED
    from helm.benchmark.config_registry import (
        register_builtin_configs_from_helm_package,
        register_configs_from_directory,
    )

    if not _BUILTINS_REGISTERED:
        register_builtin_configs_from_helm_package()
        _BUILTINS_REGISTERED = True
    key = str(prod_env_dir.resolve())
    if key not in _REGISTERED_DIRS:
        register_configs_from_directory(key)
        _REGISTERED_DIRS.add(key)


def stage_prod_env(
    *,
    prod_env_dir: Path,
    model_deployments_path: Path,
    model_metadata_fpath: str | None,
    tokenizer_configs_fpath: str | None,
) -> Path:
    """Assemble the prod_env dir HELM registration reads.

    Copies the generated ``model_deployments.yaml`` plus the preset's
    model_metadata / tokenizer_configs sidecars (repo-relative) under the fixed
    names ``register_configs_from_directory`` expects. Sidecars are optional (a
    preset serving only builtin-known ids ships none).
    """
    from eval_audit.infra.paths import repo_root

    prod_env_dir.mkdir(parents=True, exist_ok=True)
    _copy(model_deployments_path, prod_env_dir / _MODEL_DEPLOYMENTS_NAME)
    if model_metadata_fpath is not None:
        _copy(repo_root() / model_metadata_fpath, prod_env_dir / _MODEL_METADATA_NAME)
    if tokenizer_configs_fpath is not None:
        _copy(
            repo_root() / tokenizer_configs_fpath,
            prod_env_dir / _TOKENIZER_CONFIGS_NAME,
        )
    return prod_env_dir


def _copy(src: Path, dst: Path) -> None:
    import shutil

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def expand_run_entry_to_run_spec(run_entry: str) -> tuple[str, dict[str, Any]]:
    """Expand one authored run-key string to its HELM ``RunSpec`` JSON dict.

    Uses HELM's own ``construct_run_specs`` + ``asdict_without_nones`` so the
    dict is byte-identical to what the runner writes to ``run_spec.json``. A
    run-entry names exactly one run; anything else is a malformed key and a hard
    error (silently freezing the wrong count would corrupt the corpus).
    """
    from helm.benchmark.run_spec_factory import construct_run_specs
    from helm.common.general import asdict_without_nones
    from helm.common.object_spec import parse_object_spec

    specs = construct_run_specs(parse_object_spec(run_entry))
    if len(specs) != 1:
        raise ValueError(
            f"run-entry {run_entry!r} expanded to {len(specs)} run specs; a frozen "
            "compute source must name exactly one run. Split or fix the key."
        )
    run_spec = specs[0]
    return run_spec.name, asdict_without_nones(run_spec)


def synthesize_compute_run_spec_sources(
    spec: dict[str, Any],
    *,
    synth_root: Path,
    tag: str,
    prod_env_dir: Path,
    model_entries: list[dict[str, Any]],
    lease_facts: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Freeze every run-entry of ``spec`` to a synthesized ``run_spec.json``.

    Writes ``synth_root/<tag>/<run-name>/run_spec.json`` for each run-entry and
    returns ``(run_spec_sources, provenance_rows)``. Each source has the same
    shape ``_freeze_run_spec_sources`` emits — ``run_entry``, ``rel_path``
    (relative to ``synth_root``, which becomes the manifest ``precomputed_root``),
    ``model_deployment`` (the LOCAL rewrite target), and ``lease_endpoint`` — so
    the exact-path replay consumer handles it unchanged. Provenance rows carry
    the content ``sha256`` so a downstream reader can verify the frozen bytes.
    """
    _ensure_helm_registered(prod_env_dir)

    local_names = frozenset(entry["name"] for entry in model_entries)
    single_name = model_entries[0]["name"] if len(model_entries) == 1 else None
    lease_scalar = (lease_facts or {}).get("lease_endpoint")
    lease_map = (lease_facts or {}).get("lease_endpoints") or {}

    sources: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    seen_rel: dict[str, str] = {}
    for run_entry in spec["run_entries"]:
        # Name the LOCAL deployment (rewrite target) exactly as the reproduction
        # freeze does: the inline local token, else the sole bundle deployment.
        _, local_token = _strip_local_deployment(run_entry, local_names)
        deployment = local_token or single_name
        if deployment is None:
            raise ValueError(
                f"cannot synthesize run-entry {run_entry!r}: a multi-deployment "
                "bundle needs an inline model_deployment=<local> token to name the "
                "rewrite target, but none was present."
            )
        run_name, run_spec_json = expand_run_entry_to_run_spec(run_entry)
        text = json.dumps(run_spec_json, indent=2)
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()

        rel_path = f"{tag}/{_sanitize(run_name)}/run_spec.json"
        prior = seen_rel.get(rel_path)
        if prior is not None and prior != sha:
            raise ValueError(
                f"two run-entries collide on synthesized path {rel_path!r} with "
                f"different specs ({prior[:12]} vs {sha[:12]}); run names must be "
                f"unique. Offending entry: {run_entry!r}."
            )
        seen_rel[rel_path] = sha
        dest = synth_root / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")

        source: dict[str, Any] = {
            "run_entry": run_entry,
            "rel_path": rel_path,
            "model_deployment": deployment,
        }
        endpoint = lease_scalar or lease_map.get(deployment)
        if endpoint:
            source["lease_endpoint"] = endpoint
        sources.append(source)
        provenance.append(
            {
                "run_entry": run_entry,
                "run_spec_name": run_name,
                "rel_path": rel_path,
                "sha256": sha,
                "model_deployment": deployment,
            }
        )
    return sources, provenance


def _sanitize(run_name: str) -> str:
    """Directory-safe run-dir name mirroring HELM's ``/`` -> ``_`` convention.

    HELM already sanitizes the ``model=`` token inside the run name at
    construction (``qwen/qwen3.5`` -> ``qwen_qwen3.5``); the remaining ``:`` /
    ``,`` / ``=`` are filesystem-safe. We only guard against a stray ``/`` so a
    scenario key can never escape the synth root.
    """
    return run_name.replace("/", "_")


def write_provenance(
    synth_root: Path,
    *,
    helm_version: str,
    manifests: dict[str, list[dict[str, Any]]],
) -> Path:
    """Stamp the synthesized corpus with the expander version + per-run hashes.

    This is the provenance handle the freeze discipline promises: which HELM
    build expanded the keys, and the content ``sha256`` of every frozen spec, so
    a future reader can tell whether their re-expansion matches ours.
    """
    path = synth_root / "synthesized_specs.provenance.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "helm_version": helm_version,
        "note": (
            "Frozen compute run_spec.json corpus. Expanded once at export via "
            "HELM construct_run_specs; the run-key strings are transient authoring "
            "inputs, these specs are the durable identity."
        ),
        "manifests": manifests,
    }
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path


def helm_version() -> str:
    """Best-effort crfm-helm version string for the provenance stamp."""
    try:
        from importlib.metadata import version

        return version("crfm-helm")
    except Exception:  # pragma: no cover - version metadata missing
        try:
            import helm

            return getattr(helm, "__version__", "unknown")
        except Exception:
            return "unknown"

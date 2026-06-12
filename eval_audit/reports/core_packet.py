"""On-disk format of a core-report packet (names, slugs, manifests).

Write-side conventions for the artifacts that ``reports.core_metrics``
emits: manifest filenames, slug rules, and latest-symlink naming. The
read-side loader lives in ``reports.core_packet_summary``; see
``reports.core_metrics`` for the role map of all ``core_*`` modules.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from eval_audit.utils.hashers import stable_hash36
from eval_audit.infra.fs_publish import safe_unlink, write_text_atomic


def slugify_identifier(text: str) -> str:
    return (
        str(text)
        .replace("/", "-")
        .replace(":", "-")
        .replace(",", "-")
        .replace("=", "-")
        .replace("@", "-")
        .replace(" ", "-")
    )


def comparison_artifact_stem(comparison_id: str, *, max_slug_len: int = 48, hash_len: int = 10) -> str:
    slug = slugify_identifier(comparison_id).strip("-") or "comparison"
    short_slug = slug[:max_slug_len].rstrip("-") or "comparison"
    suffix = stable_hash36({"comparison_id": str(comparison_id)})[:hash_len]
    return f"{short_slug}--{suffix}"


def comparison_sample_latest_name(comparison_id: str) -> str:
    return f"instance_samples_{comparison_artifact_stem(comparison_id)}.txt"


def comparison_sample_history_name(comparison_id: str, stamp: str) -> str:
    """Deprecated: kept for backwards-compat with callers that still pass a
    stamp. Returns the same path as :func:`comparison_sample_latest_name`
    (the stamp infix is no longer written; see fs_publish.py docstring)."""
    del stamp
    return comparison_sample_latest_name(comparison_id)


def write_manifest(
    report_dpath: Path,
    *,
    stem: str,
    latest_name: str,
    payload: dict[str, Any],
) -> Path:
    out_fpath = Path(report_dpath) / latest_name
    write_text_atomic(out_fpath, json.dumps(payload, indent=2) + "\n")
    return out_fpath


def load_manifest(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError(f"Manifest must decode to a dict: {path}")
    return data


def load_packet_manifests(
    *,
    report_dpath: str | Path,
    components_manifest: str | Path | None = None,
    comparisons_manifest: str | Path | None = None,
) -> tuple[Path, dict[str, Any], Path, dict[str, Any]]:
    report_dpath = Path(report_dpath).expanduser().resolve()
    components_fpath = (
        Path(components_manifest).expanduser().resolve()
        if components_manifest is not None
        else (report_dpath / "components_manifest.json").resolve()
    )
    comparisons_fpath = (
        Path(comparisons_manifest).expanduser().resolve()
        if comparisons_manifest is not None
        else (report_dpath / "comparisons_manifest.json").resolve()
    )
    return (
        components_fpath,
        load_manifest(components_fpath),
        comparisons_fpath,
        load_manifest(comparisons_fpath),
    )


def component_link_basename(component_id: str, *, max_slug_len: int = 72, hash_len: int = 10) -> str:
    slug = slugify_identifier(component_id).strip("-") or "component"
    if len(slug) <= max_slug_len:
        return slug
    short_slug = slug[:max_slug_len].rstrip("-") or "component"
    suffix = stable_hash36({"component_id": str(component_id)})[:hash_len]
    return f"{short_slug}--{suffix}"


def cleanup_glob(root: Path, pattern: str, keep_names: set[str]) -> None:
    if not root.exists():
        return
    for path in root.glob(pattern):
        if path.name not in keep_names:
            safe_unlink(path)

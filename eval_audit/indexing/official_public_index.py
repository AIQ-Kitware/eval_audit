"""Build and write the official public index from a HELM corpus mirror.

Split out of ``eval_audit.cli.index_historic_helm_runs`` on 2026-06-11
(Phase 2 of docs/planning/repo-refactor-plan.md). Pure relocation:
function bodies are unchanged.
"""
from __future__ import annotations
import fnmatch
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import ubelt as ub
from eval_audit.indexing.schema import (
    OFFICIAL_COMPONENT_COLUMNS,
    classify_run_entry as _classify_run_entry_impl,
    compute_run_spec_hash as _compute_run_spec_hash_impl,
    normalize_for_hash as _normalize_for_hash_impl,
    component_id_for_official,
    extract_run_spec_fields,
    logical_run_key_for_official,
    now_utc_iso,
)


# ---------------------------------------------------------------------------
# Official/public index — version-aware canonical artifact
# ---------------------------------------------------------------------------

#: Backwards-compat alias for the canonical official-index column order.
OFFICIAL_INDEX_COLUMNS: list[str] = OFFICIAL_COMPONENT_COLUMNS


def _normalize_for_hash(obj: Any) -> Any:
    """Back-compat shim for ``eval_audit.indexing.schema.normalize_for_hash``."""
    return _normalize_for_hash_impl(obj)


def _compute_run_spec_hash(run_spec_fpath: Path) -> str | None:
    """Back-compat shim for ``eval_audit.indexing.schema.compute_run_spec_hash``."""
    return _compute_run_spec_hash_impl(run_spec_fpath)


def _classify_run_entry(entry_name: str) -> tuple[str, bool]:
    """Back-compat shim for ``eval_audit.indexing.schema.classify_run_entry``."""
    return _classify_run_entry_impl(entry_name)


def _scan_benchmark_output_dir(
    bo_dir: Path,
    public_root: str | None,
    public_track: str,
    suite_pattern: str = '*',
    index_generated_utc: str = '',
) -> list[dict[str, Any]]:
    """
    Scan a single benchmark_output directory and return official index rows.

    This is the inner loop extracted so it can be unit-tested without magnet.
    Emits component-style rows whose schema matches
    :data:`eval_audit.indexing.schema.OFFICIAL_COMPONENT_COLUMNS`.
    """
    rows: list[dict[str, Any]] = []
    runs_dir = bo_dir / 'runs'
    if not runs_dir.is_dir():
        return rows

    for suite_dir in sorted(runs_dir.iterdir()):
        if not suite_dir.is_dir():
            continue
        suite_version = suite_dir.name
        if suite_pattern != '*' and not fnmatch.fnmatch(suite_version, suite_pattern):
            continue

        for entry_dir in sorted(suite_dir.iterdir()):
            if not entry_dir.is_dir():
                continue
            run_name = entry_dir.name
            entry_kind, is_structural_junk = _classify_run_entry_impl(run_name)

            run_spec_fpath = entry_dir / 'run_spec.json'
            spec_fields = extract_run_spec_fields(run_spec_fpath)
            has_run_spec_json = spec_fields['has_run_spec_json']
            # For benchmark runs, fall back to directory-name prefix if the
            # spec didn't yield a benchmark group.
            benchmark_group = spec_fields['benchmark_group']
            if benchmark_group is None and ':' in run_name:
                benchmark_group = run_name.split(':', 1)[0]

            rows.append({
                'source_kind': 'official',
                'artifact_format': 'helm',
                'eee_artifact_path': None,
                'component_id': component_id_for_official(
                    public_track=public_track,
                    suite_version=suite_version,
                    run_name=run_name,
                ),
                'logical_run_key': logical_run_key_for_official(
                    run_spec_name=spec_fields['run_spec_name'],
                    run_name=run_name,
                ),
                'public_root': public_root,
                'public_track': public_track,
                'suite_version': suite_version,
                'public_run_dir': str(entry_dir),
                'run_path': str(entry_dir),
                'run_name': run_name,
                'entry_kind': entry_kind,
                'has_run_spec_json': has_run_spec_json,
                'run_spec_fpath': str(run_spec_fpath) if has_run_spec_json else None,
                'run_spec_name': spec_fields['run_spec_name'],
                'run_spec_hash': spec_fields['run_spec_hash'],
                'model': spec_fields['model'],
                'model_deployment': spec_fields['model_deployment'],
                'scenario_class': spec_fields['scenario_class'],
                'benchmark_group': benchmark_group,
                # P1-2: carry the official cap (was hardcoded None) so
                # same_max_eval_instances can detect drift vs a local cap.
                'max_eval_instances': spec_fields.get('max_eval_instances'),
                'is_structural_junk': is_structural_junk,
                'index_generated_utc': index_generated_utc,
            })

    return rows


def build_official_public_index_rows(
    roots: list[Path],
    suite_pattern: str = '*',
    index_generated_utc: str | None = None,
) -> list[dict[str, Any]]:
    """
    Scan public HELM roots and build the canonical version-aware official index.

    Unlike gather_runs(), this function:
    - Does NOT filter for run completeness.
    - Records every directory entry including structural junk.
    - Preserves explicit public_track and suite_version provenance.
    - Computes a stable run_spec_hash from normalised run_spec.json content.
    """
    from magnet.backends.helm.cli.materialize_helm_run import discover_benchmark_output_dirs

    if index_generated_utc is None:
        index_generated_utc = now_utc_iso()

    bo_dirs = list(ub.ProgIter(
        discover_benchmark_output_dirs(roots),
        desc='discovering benchmark_output dirs for official index',
        verbose=3,
        homogeneous=False,
    ))

    rows: list[dict[str, Any]] = []
    for bo_dir in ub.ProgIter(bo_dirs, desc='Building official public index'):
        bo_dir = Path(bo_dir)
        public_root: str | None = None
        public_track = 'unknown'
        for root in roots:
            try:
                rel = bo_dir.parent.relative_to(root)
                public_root = str(root)
                parts = rel.parts
                public_track = '/'.join(parts) if parts else 'main'
                break
            except ValueError:
                continue

        rows.extend(_scan_benchmark_output_dir(
            bo_dir=bo_dir,
            public_root=public_root,
            public_track=public_track,
            suite_pattern=suite_pattern,
            index_generated_utc=index_generated_utc,
        ))

    rows.sort(key=lambda r: (r['public_track'], r['suite_version'], r['run_name']))
    return rows


def write_official_public_index(
    rows: list[dict[str, Any]],
    out_dpath: Path,
    timestamp: str | None = None,
) -> tuple[Path, Path]:
    """
    Write the official public index to ``official_public_index.csv``.

    The ``timestamp`` argument is preserved for backwards compatibility
    with callers but is no longer used: the post-history-retirement
    publishing model writes the canonical artifact directly to
    ``out_dpath / 'official_public_index.csv'`` and overwrites it
    atomically. Returns ``(latest_fpath, latest_fpath)``; the duplicated
    return is preserved so existing call-sites unpack two values.
    """
    import io

    import pandas as pd
    import safer

    if timestamp is None:
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    del timestamp  # currently unused; preserved as an arg for callers

    out_dpath.mkdir(parents=True, exist_ok=True)
    latest_fpath = out_dpath / 'official_public_index.csv'

    df = pd.DataFrame(rows)
    for col in OFFICIAL_COMPONENT_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[OFFICIAL_COMPONENT_COLUMNS]
    # pandas .to_csv accepts a file-like; use safer.open so a crash mid-write
    # leaves the previous official_public_index.csv intact.
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    with safer.open(latest_fpath, 'w', make_parents=True) as fp:
        fp.write(buf.getvalue())
    # Returns (path, path) for backward-compat with callers that expected
    # (timestamped_fpath, latest_fpath) — both now the same canonical file.
    return latest_fpath, latest_fpath

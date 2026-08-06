"""Stamped-artifact writers, TSV/markdown serializers, and the
reproduce/rebuild script generators for the Stage 1 filter report.

Split out of ``eval_audit.reports.filter_analysis`` on 2026-06-11
(Phase 2 of docs/historical/planning/repo-refactor-plan.md). Pure relocation:
function bodies are unchanged.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
import kwutil
from eval_audit.infra.fs_publish import link_alias, write_text_atomic
from eval_audit.infra.logging import rich_link
from eval_audit.infra.report_layout import (
    portable_repo_root_lines,
    write_reproduce_script,
)
from loguru import logger


def to_tsv(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '\n'
    columns = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    lines = ['\t'.join(columns)]
    for row in rows:
        parts = []
        for col in columns:
            value = row.get(col, '')
            if isinstance(value, (list, dict)):
                value = json.dumps(value, sort_keys=True)
            parts.append(str(value))
        lines.append('\t'.join(parts))
    return '\n'.join(lines) + '\n'


def to_markdown(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '(no rows)\n'
    columns = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    str_rows = []
    for row in rows:
        str_rows.append([str(row.get(col, '')) for col in columns])
    widths = []
    for idx, col in enumerate(columns):
        widths.append(max(len(col), *(len(r[idx]) for r in str_rows)))

    def fmt(cells: list[str]) -> str:
        return '| ' + ' | '.join(cell.ljust(widths[idx]) for idx, cell in enumerate(cells)) + ' |'

    lines = [
        fmt(columns),
        '| ' + ' | '.join('-' * width for width in widths) + ' |',
    ]
    for row in str_rows:
        lines.append(fmt(row))
    return '\n'.join(lines) + '\n'


def _write_stamped_text(report_root: Path, root: Path, stem: str, stamp: str, suffix: str, text: str) -> Path:
    """Write ``text`` directly to ``root/<stem><suffix>``.

    The ``report_root`` and ``stamp`` arguments are vestigial after the
    simplification (2026-04-28b); they're kept in the signature so existing
    callers don't have to be rewritten. Stamp infixes are no longer used in
    filenames, and the prior ``.latest`` placeholder was dropped on
    2026-04-29 (it had no disambiguation function once stamped siblings
    went away).
    """
    del report_root, stamp
    fpath = root / f'{stem}{suffix}'
    logger.debug(f'Write to: {rich_link(fpath)}')
    write_text_atomic(fpath, text)
    return fpath


def _write_stamped_json(report_root: Path, root: Path, stem: str, stamp: str, payload: Any) -> Path:
    text = json.dumps(kwutil.Json.ensure_serializable(payload), indent=2, ensure_ascii=False, default=str) + '\n'
    return _write_stamped_text(report_root, root, stem, stamp, '.json', text)


def _write_stamped_table(report_root: Path, root: Path, stem: str, stamp: str, rows: list[dict[str, Any]]) -> Path:
    return _write_stamped_text(report_root, root, stem, stamp, '.tsv', to_tsv(rows))


def write_filter_rebuild_script(report_dpath: Path, *, inventory_json: Path | None = None) -> Path:
    _ = inventory_json
    cmd = [
        '"${PYTHON_BIN}"',
        '-m',
        'eval_audit.reports.filter_analysis',
        '--report-dpath',
        '"${REPORT_DPATH}"',
        '--inventory-json',
        '"${REPORT_DPATH}/machine/model_filter_inventory.json"',
    ]
    script = write_reproduce_script(report_dpath / 'rebuild_analysis.sh', [
        '#!/usr/bin/env bash',
        'set -euo pipefail',
        *portable_repo_root_lines(),
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"',
        'REPORT_DPATH="$SCRIPT_DIR"',
        'cd "$REPO_ROOT"',
        f'PYTHONPATH="$REPO_ROOT" {" ".join(cmd)} "$@"',
    ])
    link_alias(script, report_dpath, 'rebuild_analysis.sh')
    return script


def write_filter_reproduce_script(report_dpath: Path, *, source_command: str | None = None) -> Path:
    lines = [
        '#!/usr/bin/env bash',
        'set -euo pipefail',
        *portable_repo_root_lines(),
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"',
        'REPORT_DPATH="$SCRIPT_DIR"',
        'cd "$REPO_ROOT"',
    ]
    if source_command:
        lines.extend([
            '',
            '# Re-run Stage 1 discovery/filtering and then rebuild the report bundle.',
            source_command,
        ])
    else:
        lines.extend([
            '',
            '# Rebuild the filter report bundle from the latest saved inventory.',
            'PYTHONPATH="$REPO_ROOT" "$PYTHON_BIN" -m eval_audit.reports.filter_analysis --report-dpath "$REPORT_DPATH" "$@"',
        ])
    script = write_reproduce_script(report_dpath / 'reproduce.sh', lines)
    link_alias(script, report_dpath, 'reproduce.sh')
    return script


def _load_inventory_json(report_dpath: Path, inventory_json: Path | None = None) -> list[dict[str, Any]]:
    if inventory_json is not None:
        payload = json.loads(inventory_json.read_text())
        return payload
    latest = report_dpath / 'machine' / 'model_filter_inventory.json'
    if latest.exists():
        return json.loads(latest.read_text())
    candidates = sorted((report_dpath / 'machine').glob('model_filter_inventory_*.json'), reverse=True)
    if candidates:
        return json.loads(candidates[0].read_text())
    raise FileNotFoundError(
        f'No filter inventory JSON found under {report_dpath}. '
        'Re-run Stage 1 with the updated index_historic_helm_runs flow so it emits '
        'machine/model_filter_inventory.json, or pass --inventory-json explicitly.'
    )

from __future__ import annotations

from pathlib import Path

from eval_audit.infra.paths import (
    audit_store_root,
    publication_root,
    repo_reports_root,
)
from eval_audit.infra.logging import rich_link
from loguru import logger


# ----------------------------------------------------------------------
# Publication surface (ADR 3): one folder named ``reports/`` whose
# location is parameterized via ``publication_root()``. All publication
# helpers below derive from that single root so a deployment can move
# the surface (e.g. out of the repo) by setting one env var.
# ----------------------------------------------------------------------


def filtering_reports_root() -> Path:
    """``<publication_root>/filtering`` — Stage-1 filter visualizations."""
    return publication_root() / "filtering"


def aggregate_summary_reports_root() -> Path:
    """``<publication_root>/aggregate-summary`` — story-arc sankeys etc."""
    return publication_root() / "aggregate-summary"


def publication_experiments_root() -> Path:
    """``<publication_root>/core-run-analysis`` — the publication-side
    symlink directory that points at canonical analysis outputs.

    Was previously named ``compat_core_run_reports_root`` and hard-coded
    to ``<repo>/reports/core-run-analysis``. Now follows the configured
    publication root so virtual experiments (and any future re-route of
    the publication surface) compose cleanly.
    """
    return publication_root() / "core-run-analysis"


# ----------------------------------------------------------------------
# Canonical analysis storage. NOT a publication path — lives in the
# audit store regardless of where the publication surface points.
# ----------------------------------------------------------------------


def experiments_analysis_root() -> Path:
    """``<audit_store>/analysis/experiments`` — canonical per-experiment
    analysis outputs (manifests, packets, core reports). This is the
    canonical *storage* location, never the publication surface.
    """
    return audit_store_root() / "analysis" / "experiments"


# ----------------------------------------------------------------------
# Legacy location. Kept for migration code that still needs to reach the
# pre-move in-repo path. New code must not introduce calls to this.
# ----------------------------------------------------------------------


def legacy_repo_publication_root() -> Path:
    """The pre-parameterization in-repo ``<repo>/reports/core-run-analysis``.

    Used only by :mod:`eval_audit.workflows.analyze_experiment` to migrate
    legacy in-repo analysis directories to the canonical store path.
    """
    return repo_reports_root() / "core-run-analysis"


# ----------------------------------------------------------------------
# Reproduce-script helpers
# ----------------------------------------------------------------------


def portable_repo_root_lines(repo_root_fallback: Path | None = None) -> list[str]:
    """Bash lines that resolve ``REPO_ROOT`` for a generated reproduce script.

    Resolution order:

    1. ``$REPO_ROOT`` env var, if it points at a real ``eval_audit`` checkout.
    2. Walk up from the script's directory (resolving symlinks first, so a
       ``redraw_plots.sh`` that's a symlink into ``level_001/`` still
       finds the repo from the symlink target, not the alias). The walk
       only succeeds when the script lives *inside* a working tree —
       which the in-repo runbooks do, but the publication-root scripts
       don't.
    3. ``python -c "import eval_audit; ..."`` to locate the package via
       its installed ``__file__``. This is the cheapest mechanism for
       scripts that live outside the source tree (under
       ``$AUDIT_STORE_ROOT/...``) but were generated from a host that
       has ``eval_audit`` installed editable. Catches the common case
       where the absolute fallback baked at generation time has moved.
    4. The absolute fallback baked in at generation time. Last-ditch.
    """
    if repo_root_fallback is None:
        # Late import to avoid a cycle with paths.py.
        from eval_audit.infra.paths import repo_root as _repo_root
        repo_root_fallback = _repo_root()
    fallback_repr = str(repo_root_fallback).replace('"', '\\"')
    return [
        'SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"',
        'SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"',
        '_repo_root_ok() { [[ -f "$1/pyproject.toml" && -d "$1/eval_audit" ]]; }',
        'if [[ -n "${REPO_ROOT:-}" ]] && _repo_root_ok "$REPO_ROOT"; then',
        '  :',
        'else',
        '  REPO_ROOT="$SCRIPT_DIR"',
        '  while ! _repo_root_ok "$REPO_ROOT"; do',
        '    NEXT="$(dirname "$REPO_ROOT")"',
        '    if [[ "$NEXT" == "$REPO_ROOT" ]]; then',
        '      REPO_ROOT=""',
        '      break',
        '    fi',
        '    REPO_ROOT="$NEXT"',
        '  done',
        'fi',
        'if [[ -z "${REPO_ROOT:-}" ]] || ! _repo_root_ok "$REPO_ROOT"; then',
        '  # Try installed eval_audit (editable install in any active venv).',
        '  IMPORTED_REPO="$( { python -c "import eval_audit, pathlib; print(pathlib.Path(eval_audit.__file__).resolve().parent.parent)" 2>/dev/null; } || true )"',
        '  if [[ -n "$IMPORTED_REPO" ]] && _repo_root_ok "$IMPORTED_REPO"; then',
        '    REPO_ROOT="$IMPORTED_REPO"',
        '  fi',
        'fi',
        'if [[ -z "${REPO_ROOT:-}" ]] || ! _repo_root_ok "$REPO_ROOT"; then',
        f'  REPO_ROOT="{fallback_repr}"',
        'fi',
        'if ! _repo_root_ok "$REPO_ROOT"; then',
        '  echo "Could not locate eval_audit repo root from $SCRIPT_DIR (fallback ' f'{fallback_repr}' ' missing; \"import eval_audit\" failed too); set REPO_ROOT=" >&2',
        '  exit 1',
        'fi',
        'PYTHON_BIN="${PYTHON_BIN:-python}"',
    ]


def write_reproduce_script(script_fpath: Path, lines: list[str]) -> Path:
    script_fpath.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines).rstrip() + "\n"
    script_fpath.write_text(text)
    script_fpath.chmod(0o755)
    logger.debug(f'Write to: {rich_link(script_fpath)}')
    return script_fpath


def write_python_reproduce_script(
    script_fpath: Path,
    cmd_parts: list[str],
    *,
    comment_lines: tuple[str, ...] | list[str] = (),
) -> Path:
    """Write the standard "re-run this ``python -m`` command" script (ADR 5).

    Assembles the canonical preamble (shebang, ``set -euo pipefail``,
    optional comment lines, portable repo-root/python resolution,
    ``cd "$REPO_ROOT"``) around a shell-quoted
    ``PYTHONPATH="$REPO_ROOT" "$PYTHON_BIN" <cmd_parts> "$@"`` line.
    Previously hand-assembled at four sites in ``analyze_experiment`` /
    ``rebuild_core_report`` (D1). Scripts needing a different shape (env
    capture, ``SCRIPT_DIR`` vars) keep composing
    :func:`write_reproduce_script` directly.
    """
    import shlex

    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        *comment_lines,
        *portable_repo_root_lines(),
        'cd "$REPO_ROOT"',
        'PYTHONPATH="$REPO_ROOT" "$PYTHON_BIN" '
        + " ".join(shlex.quote(part) for part in cmd_parts)
        + ' "$@"',
    ]
    return write_reproduce_script(script_fpath, lines)

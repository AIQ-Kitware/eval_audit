#!/usr/bin/env bash
# package-analyses.sh — build an analysis transfer package from a store.
#
# Runs where the store is. Drives the two packaging CLIs and leaves a
# compressed archive ready to move. See docs/transfer-packaging.md for what
# ends up inside and why.
#
# Phases run in order and are individually selectable, because they fail for
# different reasons and `pack` is the expensive one:
#
#   plan     crawl the store, dry-run the packager, print the biggest artifacts
#   pack     build the package directory (resumable; safe to re-run)
#   archive  deterministic tar.zst + sha256 beside it
#
# Usage:
#   ./package-analyses.sh plan            # look before you leap
#   ./package-analyses.sh                 # plan, pack, archive
#   ./package-analyses.sh pack archive    # skip re-planning
#   DRY_RUN=1 ./package-analyses.sh       # print commands, run nothing
#
# Env knobs:
#   STORE_DPATH   store to crawl        (default /data/crfm-helm-audit-store)
#   OUT_DPATH     where the package and archive are written
#                                       (default /data/_transfer-package)
#   PACKAGE_NAME  package dir + archive basename (default eval-audit-analyses)
#   EVAL_AUDIT_PY python that has eval_audit installed. Only needed if auto-
#                 detection fails: the script already tries the eval-audit-*
#                 console scripts, then .venv/ beside this script, then python3.
#                 Set it to fail loudly on a specific interpreter instead.
#   SOURCE_ROOTS  space-separated absolute path roots to follow and rewrite,
#                 replacing the defaults (/data/crfm-helm-audit-store
#                 /data/crfm-helm-audit /data/crfm-helm-public). Only needed
#                 when the store and its artifacts do not live under /data.
#   INVENTORY_EDIT  1 = stop after `plan` so you can hand-edit the inventory
#                   (set "include": false on analyses you do not want) before
#                   running `pack`. Default 0 — include everything found.
#   KEEP_PACKAGE  0 = delete the package directory once the archive exists,
#                 to reclaim the space. Default 1 (keep it).
#   DRY_RUN       print every command instead of running it
#
# On the receiving machine: extract the archive, then run `python3 repoint.py`
# from inside the package. That needs no eval_audit install — only python3.
set -euo pipefail

STORE_DPATH="${STORE_DPATH:-/data/crfm-helm-audit-store}"
OUT_DPATH="${OUT_DPATH:-/data/_transfer-package}"
PACKAGE_NAME="${PACKAGE_NAME:-eval-audit-analyses}"
INVENTORY_EDIT="${INVENTORY_EDIT:-0}"
KEEP_PACKAGE="${KEEP_PACKAGE:-1}"
DRY_RUN="${DRY_RUN:-0}"

INVENTORY_FPATH="$OUT_DPATH/analysis_inventory.jsonl"
PLAN_FPATH="$OUT_DPATH/plan.json"
PACKAGE_DPATH="$OUT_DPATH/$PACKAGE_NAME"
ARCHIVE_NAME="$PACKAGE_NAME.tar.zst"

ALL_PHASES=(plan pack archive)

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

run() {
  if [[ "$DRY_RUN" == 1 ]]; then
    printf '\033[2m%s\033[0m\n' "$*" >&2
    return 0
  fi
  "$@"
}

# --- entry points -----------------------------------------------------------

REPO_DPATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Can this interpreter (or console script) actually run the packager?
# Returns 0 and leaves the reason in WHY_NOT on failure.
can_run() {
  WHY_NOT="$("$@" --help 2>&1 >/dev/null)" && return 0
  return 1
}

# Find something that can run the CLIs. The repository travels to the host it
# runs on, so a venv beside this script is the likeliest answer after an
# explicitly-set interpreter -- and an unactivated venv is the single most
# common reason this fails on a machine that is otherwise fine.
resolve_python() {
  local candidates=() why_first=""

  if [[ -n "${EVAL_AUDIT_PY:-}" ]]; then
    # Explicitly set: use it or fail loudly. Silently falling back to some
    # other interpreter would package a store with code the user did not pick.
    if can_run "$EVAL_AUDIT_PY" -m eval_audit.cli.package_analyses; then
      PY=("$EVAL_AUDIT_PY"); return 0
    fi
    die "EVAL_AUDIT_PY=$EVAL_AUDIT_PY cannot run the packager:
${WHY_NOT:-(no output)}"
  fi

  if can_run eval-audit-package-analyses; then
    CRAWL_CMD=(eval-audit-crawl-analyses)
    PACK_CMD=(eval-audit-package-analyses)
    return 0
  fi
  why_first="console scripts on PATH: ${WHY_NOT:-not found}"

  candidates=(
    "$REPO_DPATH/.venv/bin/python"
    "$REPO_DPATH/.venv/bin/python3"
    "$(command -v python3 || true)"
  )
  for candidate in "${candidates[@]}"; do
    [[ -n "$candidate" && -x "$candidate" ]] || continue
    if can_run "$candidate" -m eval_audit.cli.package_analyses; then
      PY=("$candidate")
      log "using $candidate"
      return 0
    fi
  done

  die "cannot run the eval_audit packaging CLIs.
  $why_first
  tried: ${candidates[*]}
  last error: ${WHY_NOT:-(no output)}

Install the package on this host, e.g. from the repo at $REPO_DPATH:
    uv venv && uv pip install -e .
then re-run, or point at an existing environment:
    EVAL_AUDIT_PY=/path/to/.venv/bin/python $0 ${*:-}"
}

CRAWL_CMD=(); PACK_CMD=(); PY=()
resolve_python
if [[ ${#PY[@]} -gt 0 ]]; then
  CRAWL_CMD=("${PY[@]}" -m eval_audit.cli.crawl_analyses)
  PACK_CMD=("${PY[@]}" -m eval_audit.cli.package_analyses)
fi

ROOT_ARGS=()
if [[ -n "${SOURCE_ROOTS:-}" ]]; then
  for root in $SOURCE_ROOTS; do ROOT_ARGS+=(--source-root "$root"); done
fi

preflight() {
  [[ -d "$STORE_DPATH" ]] || die "store not found: $STORE_DPATH
Set STORE_DPATH to the store you want packaged."
  mkdir -p "$OUT_DPATH" 2>/dev/null || die "cannot create OUT_DPATH: $OUT_DPATH
Set OUT_DPATH to somewhere writable with room for the package, e.g.
    OUT_DPATH=\$HOME/transfer-package $0 ${*:-}"
  [[ -w "$OUT_DPATH" ]] || die "OUT_DPATH is not writable: $OUT_DPATH"
}

# --- phases -----------------------------------------------------------------

phase_plan() {
  preflight
  log "crawling $STORE_DPATH"
  run "${CRAWL_CMD[@]}" --store-dpath "$STORE_DPATH" --out-fpath "$INVENTORY_FPATH"

  log "planning (dry run — nothing is copied)"
  run "${PACK_CMD[@]}" --inventory-fpath "$INVENTORY_FPATH" \
    --package-dpath "$PACKAGE_DPATH" --dry-run --plan-out "$PLAN_FPATH" \
    "${ROOT_ARGS[@]}"

  [[ "$DRY_RUN" == 1 ]] || summarize_plan

  if [[ "$INVENTORY_EDIT" == 1 ]]; then
    log "INVENTORY_EDIT=1 — edit $INVENTORY_FPATH, then run: $0 pack archive"
    exit 0
  fi
}

# The largest artifacts are the cheapest defect detector there is: a packaging
# mistake shows up as one implausible entry at the top of this list, minutes
# in, rather than after a multi-hour copy.
summarize_plan() {
  [[ -f "$PLAN_FPATH" ]] || return 0
  # Reuse whatever interpreter resolve_python settled on; a host that runs the
  # CLIs via console scripts may still have no bare `python3` on PATH.
  local summary_py="${PY[0]:-python3}"
  "$summary_py" - "$PLAN_FPATH" <<'PY' || true
import json, sys
p = json.load(open(sys.argv[1]))
print(f"\n  artifacts {p['n_artifacts']}   {p['n_bytes']/1e9:.2f} GB"
      f"   containers skipped {p.get('containers_not_followed', 0)}"
      f"   catalog skipped {p.get('catalog_only_not_followed', 0)}")
print("  largest artifacts — these should look like run directories:")
for a in p["artifacts"][:5]:
    print(f"    {a['n_bytes']/1e6:9.1f} MB  {a['rule']:<12} {a['src'][:94]}")
if p.get("missing"):
    print(f"  unresolved references: {len(p['missing'])} (typed in missing.tsv)")
print()
PY
}

phase_pack() {
  preflight
  [[ -f "$INVENTORY_FPATH" ]] \
    || die "no inventory at $INVENTORY_FPATH — run the 'plan' phase first"
  log "packing into $PACKAGE_DPATH (resumable; safe to re-run)"
  run "${PACK_CMD[@]}" --inventory-fpath "$INVENTORY_FPATH" \
    --package-dpath "$PACKAGE_DPATH" "${ROOT_ARGS[@]}"
  run du -sh "$PACKAGE_DPATH"
}

phase_archive() {
  [[ -f "$PACKAGE_DPATH/MANIFEST.json" ]] \
    || die "no MANIFEST.json in $PACKAGE_DPATH — run the 'pack' phase first"
  command -v zstd >/dev/null 2>&1 || die "zstd is not installed"

  log "archiving $PACKAGE_DPATH"
  # Deterministic: sorted entries, zeroed mtimes and ownership, pax format.
  # pax is not optional -- HELM run-spec directory names blow past tar's
  # 100-char legacy name limit. Symlinks are stored as symlinks (tar's
  # default), which is what keeps the package's relative components/ links
  # working on the far side.
  if [[ "$DRY_RUN" == 1 ]]; then
    printf '\033[2mtar --sort=name --mtime=@0 --owner=0 --group=0 \
--format=pax -cf - %s | zstd -19 --long -T0 -o %s\033[0m\n' \
      "$PACKAGE_NAME" "$OUT_DPATH/$ARCHIVE_NAME" >&2
  else
    ( cd "$OUT_DPATH" \
      && tar --sort=name --mtime='@0' --owner=0 --group=0 --numeric-owner \
             --format=pax -cf - "$PACKAGE_NAME" \
       | zstd -19 --long -T0 -q -o "$ARCHIVE_NAME" -f )
    ( cd "$OUT_DPATH" && sha256sum "$ARCHIVE_NAME" > "$ARCHIVE_NAME.sha256" )
    ls -lh "$OUT_DPATH/$ARCHIVE_NAME"
    cat "$OUT_DPATH/$ARCHIVE_NAME.sha256"
  fi

  if [[ "$KEEP_PACKAGE" == 0 ]]; then
    warn "KEEP_PACKAGE=0 — removing $PACKAGE_DPATH"
    run rm -rf "$PACKAGE_DPATH"
  fi

  log "archive ready: $OUT_DPATH/$ARCHIVE_NAME"
  log "on the receiving machine: extract it, then 'python3 repoint.py' inside"
}

# --- main -------------------------------------------------------------------

phases=("$@")
[[ ${#phases[@]} -eq 0 ]] && phases=("${ALL_PHASES[@]}")

for phase in "${phases[@]}"; do
  case "$phase" in
    plan|pack|archive) ;;
    *) die "unknown phase '$phase' (want: ${ALL_PHASES[*]})" ;;
  esac
done

[[ "$DRY_RUN" == 1 ]] && warn "DRY_RUN=1 — commands are printed, not executed"

for phase in "${phases[@]}"; do
  "phase_$phase"
done

log "done: ${phases[*]}"

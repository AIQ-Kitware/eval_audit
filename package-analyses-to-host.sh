#!/usr/bin/env bash
# package-analyses-to-host.sh — build an analysis transfer package and move it
# to another machine, end to end.
#
# The package is what `eval-audit-crawl-analyses` + `eval-audit-package-analyses`
# produce: the analyses from a store plus every artifact they reference, minus
# the execution state no analysis reads. See docs/transfer-packaging.md.
#
# Either side may be remote. The store is read wherever it lives (SRC_HOST), the
# package is unpacked wherever it is going (DST_HOST); leave a host unset and
# that side is local. This covers both "run the packager on the big machine and
# bring the archive back" and "build here and restore over there".
#
# Phases run in order and are individually selectable, because they fail for
# different reasons and the expensive ones should not be repeated:
#
#   plan     crawl the store, dry-run the packager, fetch plan.json for review
#   pack     build the package directory (the long one; resumable)
#   archive  deterministic tar.zst + sha256 beside it
#   ship     transfer the archive to the destination
#   restore  extract and repoint absolute paths at the new location
#
# Usage:
#   ./package-analyses-to-host.sh plan                  # look before you leap
#   ./package-analyses-to-host.sh                       # all phases
#   ./package-analyses-to-host.sh pack archive          # just those two
#   SRC_HOST=aiq-gpu DST_HOST=namek ./package-analyses-to-host.sh
#   DRY_RUN=1 ./package-analyses-to-host.sh ship        # print, do not execute
#
# Env knobs:
#   SRC_HOST      ssh host holding the store (unset = local)
#   SRC_USER      remote user for SRC_HOST (prepended when host is not user@…)
#   DST_HOST      ssh host receiving the package (unset = local)
#   DST_USER      remote user for DST_HOST
#   SSH_PORT      ssh/rsync port for both sides (optional)
#   STORE_DPATH   store to crawl on the source   (default /data/crfm-helm-audit-store)
#   WORK_DPATH    scratch on the source for inventory/plan/package
#                                                 (default /data/_transfer-package)
#   PACKAGE_NAME  package directory + archive basename (default eval-audit-analyses)
#   DST_DPATH     where to extract on the destination (default $HOME)
#   EVAL_AUDIT_PY python/venv on the SOURCE that has eval_audit installed
#                 (default: whatever `eval-audit-crawl-analyses` resolves to)
#   SOURCE_ROOTS  space-separated absolute path roots to follow and rewrite,
#                 replacing the packager's defaults (/data/crfm-helm-audit-store
#                 /data/crfm-helm-audit /data/crfm-helm-public). Only needed
#                 when the store and its artifact roots do not live under /data.
#   INVENTORY_EDIT  1 = stop after `plan` so you can hand-edit the inventory
#                   before `pack` (default 0 — the inventory includes everything)
#   KEEP_PACKAGE  1 = keep the package directory after archiving (default 1;
#                 set 0 to reclaim the space once the archive exists)
#   DRY_RUN       print every remote/local command instead of running it
#
# Re-running is safe: `pack` skips files already copied at the right size, and
# `restore` is idempotent.
set -euo pipefail

STORE_DPATH="${STORE_DPATH:-/data/crfm-helm-audit-store}"
WORK_DPATH="${WORK_DPATH:-/data/_transfer-package}"
PACKAGE_NAME="${PACKAGE_NAME:-eval-audit-analyses}"
DST_DPATH="${DST_DPATH:-\$HOME}"
INVENTORY_EDIT="${INVENTORY_EDIT:-0}"
KEEP_PACKAGE="${KEEP_PACKAGE:-1}"
DRY_RUN="${DRY_RUN:-0}"

INVENTORY_FPATH="$WORK_DPATH/analysis_inventory.jsonl"
PLAN_FPATH="$WORK_DPATH/plan.json"
PACKAGE_DPATH="$WORK_DPATH/$PACKAGE_NAME"
ARCHIVE_FPATH="$WORK_DPATH/$PACKAGE_NAME.tar.zst"

ALL_PHASES=(plan pack archive ship restore)

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

# --- remote resolution, matching pull-data-from-aiq-gpu.sh -------------------

resolve_remote() {  # resolve_remote HOST USER -> "" for local, else user@host
  local host="$1" user="$2"
  [[ -z "$host" ]] && return 0
  if [[ -n "$user" && "$host" != *@* ]]; then printf '%s@%s\n' "$user" "$host"
  else printf '%s\n' "$host"; fi
}

SRC_REMOTE="$(resolve_remote "${SRC_HOST:-}" "${SRC_USER:-}")"
DST_REMOTE="$(resolve_remote "${DST_HOST:-}" "${DST_USER:-}")"
SSH_ARGS=(); RSYNC_RSH=()
if [[ -n "${SSH_PORT:-}" ]]; then
  SSH_ARGS=(-p "$SSH_PORT")
  RSYNC_RSH=(-e "ssh -p $SSH_PORT")
fi

# Run a command on the source (or locally when SRC_HOST is unset).
# The command is passed as a single string and evaluated by the remote shell,
# so quote anything with spaces at the call site.
on_src() {
  if [[ "$DRY_RUN" == 1 ]]; then
    printf '\033[2m[src%s] %s\033[0m\n' "${SRC_REMOTE:+ $SRC_REMOTE}" "$1" >&2
    return 0
  fi
  if [[ -n "$SRC_REMOTE" ]]; then ssh "${SSH_ARGS[@]}" "$SRC_REMOTE" "$1"
  else bash -c "$1"; fi
}

on_dst() {
  if [[ "$DRY_RUN" == 1 ]]; then
    printf '\033[2m[dst%s] %s\033[0m\n' "${DST_REMOTE:+ $DST_REMOTE}" "$1" >&2
    return 0
  fi
  if [[ -n "$DST_REMOTE" ]]; then ssh "${SSH_ARGS[@]}" "$DST_REMOTE" "$1"
  else bash -c "$1"; fi
}

# --- the eval_audit entry points on the source ------------------------------
#
# Prefer an explicit interpreter (EVAL_AUDIT_PY) because a remote non-login
# shell often will not have the venv's bin/ on PATH, which is the single most
# common way this script fails on a machine that is otherwise fine.
if [[ -n "${EVAL_AUDIT_PY:-}" ]]; then
  CRAWL_CMD="$EVAL_AUDIT_PY -m eval_audit.cli.crawl_analyses"
  PACK_CMD="$EVAL_AUDIT_PY -m eval_audit.cli.package_analyses"
else
  CRAWL_CMD="eval-audit-crawl-analyses"
  PACK_CMD="eval-audit-package-analyses"
fi

ROOT_ARGS=""
if [[ -n "${SOURCE_ROOTS:-}" ]]; then
  for root in $SOURCE_ROOTS; do ROOT_ARGS+=" --source-root '$root'"; done
fi

preflight() {
  log "preflight: source${SRC_REMOTE:+ ($SRC_REMOTE)}"
  on_src "test -d '$STORE_DPATH'" \
    || die "store not found on source: $STORE_DPATH"
  on_src "$PACK_CMD --help >/dev/null 2>&1" \
    || die "eval_audit CLI not runnable on source. Set EVAL_AUDIT_PY to the \
python that has it installed, e.g. EVAL_AUDIT_PY=/path/.venv/bin/python"
  on_src "mkdir -p '$WORK_DPATH'"
}

# --- phases -----------------------------------------------------------------

phase_plan() {
  preflight
  log "crawling $STORE_DPATH"
  on_src "$CRAWL_CMD --store-dpath '$STORE_DPATH' --out-fpath '$INVENTORY_FPATH'"

  log "planning (dry run — nothing is copied)"
  on_src "$PACK_CMD --inventory-fpath '$INVENTORY_FPATH' \
--package-dpath '$PACKAGE_DPATH' --dry-run --plan-out '$PLAN_FPATH'$ROOT_ARGS"

  # Bring the plan back so it can be reviewed here. A packaging mistake shows
  # up as one implausibly large artifact at the top of this file, which is far
  # cheaper to notice now than after the copy.
  if [[ -n "$SRC_REMOTE" && "$DRY_RUN" != 1 ]]; then
    rsync -a "${RSYNC_RSH[@]}" "$SRC_REMOTE:$PLAN_FPATH" "./$(basename "$PLAN_FPATH")" \
      && log "plan fetched to ./$(basename "$PLAN_FPATH")"
  fi
  summarize_plan
  if [[ "$INVENTORY_EDIT" == 1 ]]; then
    log "INVENTORY_EDIT=1 — edit $INVENTORY_FPATH on the source, then run: $0 pack"
    exit 0
  fi
}

summarize_plan() {
  [[ "$DRY_RUN" == 1 ]] && return 0
  local local_plan="./$(basename "$PLAN_FPATH")"
  [[ -n "$SRC_REMOTE" ]] || local_plan="$PLAN_FPATH"
  [[ -f "$local_plan" ]] || return 0
  python3 - "$local_plan" <<'PY' || true
import json, sys
p = json.load(open(sys.argv[1]))
print(f"\n  artifacts {p['n_artifacts']}   {p['n_bytes']/1e9:.2f} GB"
      f"   containers skipped {p.get('containers_not_followed', 0)}"
      f"   catalog skipped {p.get('catalog_only_not_followed', 0)}")
print("  largest artifacts:")
for a in p["artifacts"][:5]:
    print(f"    {a['n_bytes']/1e6:9.1f} MB  {a['rule']:<12} {a['src'][:96]}")
if p.get("missing"):
    print(f"  unresolved references: {len(p['missing'])} (see missing.tsv after pack)")
print()
PY
}

phase_pack() {
  preflight
  on_src "test -f '$INVENTORY_FPATH'" \
    || die "no inventory at $INVENTORY_FPATH — run the 'plan' phase first"
  log "packing to $PACKAGE_DPATH (resumable; safe to re-run)"
  on_src "$PACK_CMD --inventory-fpath '$INVENTORY_FPATH' \
--package-dpath '$PACKAGE_DPATH'$ROOT_ARGS"
  on_src "du -sh '$PACKAGE_DPATH' 2>/dev/null || true"
}

phase_archive() {
  log "archiving $PACKAGE_DPATH"
  on_src "test -f '$PACKAGE_DPATH/MANIFEST.json'" \
    || die "no MANIFEST.json in $PACKAGE_DPATH — run the 'pack' phase first"
  # Deterministic: sorted entries, zeroed mtimes/ownership, pax format.
  # pax matters -- HELM run-spec directory names blow past tar's 100-char
  # legacy name limit. Symlinks are stored as symlinks (tar's default), which
  # is what keeps the package's relative components/ links intact.
  on_src "cd '$(dirname "$PACKAGE_DPATH")' && \
tar --sort=name --mtime='@0' --owner=0 --group=0 --numeric-owner \
    --format=pax -cf - '$(basename "$PACKAGE_DPATH")' \
  | zstd -19 --long -T0 -q -o '$ARCHIVE_FPATH' -f"
  on_src "cd '$(dirname "$ARCHIVE_FPATH")' && \
sha256sum '$(basename "$ARCHIVE_FPATH")' > '$ARCHIVE_FPATH.sha256'"
  on_src "ls -lh '$ARCHIVE_FPATH'; cat '$ARCHIVE_FPATH.sha256'"
  if [[ "$KEEP_PACKAGE" == 0 ]]; then
    warn "KEEP_PACKAGE=0 — removing $PACKAGE_DPATH"
    on_src "rm -rf '$PACKAGE_DPATH'"
  fi
}

phase_ship() {
  log "shipping archive to destination${DST_REMOTE:+ ($DST_REMOTE)}"
  on_dst "mkdir -p $DST_DPATH"
  # DST_DPATH is written for the *destination* shell to expand (it defaults
  # to $HOME). When the destination is local, rsync is invoked by this shell
  # instead, so expand it here or the archive lands in a directory literally
  # named '$HOME'.
  local dst_dir="$DST_DPATH"
  [[ -z "$DST_REMOTE" ]] && dst_dir="$(eval echo "$DST_DPATH")"

  local src_spec="$ARCHIVE_FPATH" dst_spec="$dst_dir/"
  [[ -n "$SRC_REMOTE" ]] && src_spec="$SRC_REMOTE:$ARCHIVE_FPATH"
  [[ -n "$DST_REMOTE" ]] && dst_spec="$DST_REMOTE:$DST_DPATH/"

  if [[ -n "$SRC_REMOTE" && -n "$DST_REMOTE" ]]; then
    # rsync cannot do remote-to-remote; stream it through here instead of
    # silently landing a 30 GB file in the local working directory.
    warn "both ends remote — streaming source -> here -> destination"
    if [[ "$DRY_RUN" == 1 ]]; then
      printf '\033[2m[pipe] ssh %s cat %s | ssh %s "cat > %s/%s"\033[0m\n' \
        "$SRC_REMOTE" "$ARCHIVE_FPATH" "$DST_REMOTE" "$DST_DPATH" \
        "$(basename "$ARCHIVE_FPATH")" >&2
    else
      ssh "${SSH_ARGS[@]}" "$SRC_REMOTE" "cat '$ARCHIVE_FPATH'" \
        | ssh "${SSH_ARGS[@]}" "$DST_REMOTE" \
            "cat > '$DST_DPATH/$(basename "$ARCHIVE_FPATH")'"
      ssh "${SSH_ARGS[@]}" "$SRC_REMOTE" "cat '$ARCHIVE_FPATH.sha256'" \
        | ssh "${SSH_ARGS[@]}" "$DST_REMOTE" \
            "cat > '$DST_DPATH/$(basename "$ARCHIVE_FPATH").sha256'"
    fi
  elif [[ "$DRY_RUN" == 1 ]]; then
    printf '\033[2m[rsync] %s -> %s\033[0m\n' "$src_spec" "$dst_spec" >&2
  else
    # --partial --append-verify: a dropped connection on a 30 GB transfer
    # resumes rather than restarting, and the resumed tail is checked.
    rsync -a --info=progress2 --partial --append-verify "${RSYNC_RSH[@]}" \
      "$src_spec" "$src_spec.sha256" "$dst_spec"
  fi

  log "verifying checksum on the destination"
  on_dst "cd $DST_DPATH && sha256sum -c '$(basename "$ARCHIVE_FPATH").sha256'" \
    || die "checksum mismatch after transfer — do not trust this archive"
}

phase_restore() {
  log "extracting on the destination"
  on_dst "command -v zstd >/dev/null 2>&1" \
    || die "zstd not installed on the destination"
  on_dst "cd $DST_DPATH && zstd -dc '$(basename "$ARCHIVE_FPATH")' | tar -xf -"
  # repoint.py ships inside the package and uses only the standard library,
  # so the receiving machine needs nothing installed to make the paths usable.
  log "repointing absolute paths at the new location"
  on_dst "cd $DST_DPATH/$PACKAGE_NAME && python3 repoint.py"
  on_dst "cd $DST_DPATH/$PACKAGE_NAME && \
echo && head -40 REPACK.md && echo && \
echo 'unresolved references:' && (wc -l < missing.tsv) && \
echo 'dropped files:' && (wc -l < drops.tsv)"
  log "package ready at $DST_DPATH/$PACKAGE_NAME"
}

# --- main -------------------------------------------------------------------

phases=("$@")
[[ ${#phases[@]} -eq 0 ]] && phases=("${ALL_PHASES[@]}")

for phase in "${phases[@]}"; do
  case "$phase" in
    plan|pack|archive|ship|restore) ;;
    *) die "unknown phase '$phase' (want: ${ALL_PHASES[*]})" ;;
  esac
done

[[ "$DRY_RUN" == 1 ]] && warn "DRY_RUN=1 — commands are printed, not executed"

for phase in "${phases[@]}"; do
  "phase_$phase"
done

log "done: ${phases[*]}"

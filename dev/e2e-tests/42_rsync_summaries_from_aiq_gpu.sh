#!/usr/bin/env bash
# 42_rsync_summaries_from_aiq_gpu.sh — pull the FINAL 40_build_summary results
# from the aiq-gpu GPU box.
#
# 40_build_summary.sh writes one aggregate publication surface per scenario at
#   <output.root>/reports/aggregate-summary
# where <output.root> is the per-scenario virtual-experiment manifest's
# output.root (e2e_vexp_manifest -> configs/virtual-experiments/e2e-phi2-*.yaml).
# When that build ran on aiq-gpu, this script fetches just those summary dirs
# back here. aiq-gpu's /data roots mirror this host (same absolute paths), so
# each pull is a straight mirrored rsync.
#
# This is the targeted counterpart to 17_rsync_from_aiq_gpu.sh: rather than the
# whole STORE_ROOT, it discovers each scenario's summary dir exactly as
# 40_build_summary.sh does (loop E2E_TARGETS, honor VEXP_MANIFEST) and pulls only
# those — small and fast.
#
# Usage:
#   ./42_rsync_summaries_from_aiq_gpu.sh
#   VEXP_MANIFEST=<path> ./42_rsync_summaries_from_aiq_gpu.sh   # one scenario only
#
# Knobs:
#   SYNC_FULL_OUTPUT    pull the whole <output.root> (indexes, analysis, reports).
#                       DEFAULT 1. Set SYNC_FULL_OUTPUT=0 to pull only
#                       reports/aggregate-summary.
#   DELETE              mirror with rsync --delete, removing stale local-only files
#                       under each synced path. DEFAULT 1 here (the whole point is a
#                       clean mirror so a previous run's leftovers don't linger).
#                       Set DELETE=0 to keep local-only files.
#   AIQ_GPU_HOST / AIQ_GPU_USER / SSH_PORT / DRY_RUN / RSYNC_EXTRA
#                       — shared fetch knobs (see _rsync_lib.sh).
#
# Pull-only (remote -> local). By DEFAULT it pulls each scenario's entire
# output.root and mirrors it with --delete, so the local copy exactly matches
# aiq-gpu and no stale files survive. Preview first with DRY_RUN=1 (rsync prints
# what it would transfer AND delete). Use DELETE=0 for an additive pull.
set -euo pipefail
# _lib.sh: E2E_TARGETS, e2e_vexp_manifest, _e2e_yaml_scalar, ROOT.
# _rsync_lib.sh: aiq_remote / aiq_rsync_pull / aiq_on_err + the AIQ_GPU_* knobs.
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_rsync_lib.sh"
cd "$ROOT"

# This script's defaults differ from 17's generic pull: fetch the whole
# output.root and mirror it (--delete) so a re-pull leaves no stale leftovers.
# Both stay overridable. DELETE is read by aiq_rsync_pull from this same shell.
SYNC_FULL_OUTPUT="${SYNC_FULL_OUTPUT:-1}"
export DELETE="${DELETE:-1}"

if [[ -n "${VEXP_MANIFEST:-}" ]]; then
  manifests=("$VEXP_MANIFEST")
else
  manifests=()
  for target in "${E2E_TARGETS[@]}"; do
    manifests+=("$(e2e_vexp_manifest "$target")")
  done
fi

echo "=================================================================="
echo "== rsync 40_build_summary results from aiq-gpu"
echo "=================================================================="
echo "  remote : $(aiq_remote)${SSH_PORT:+  (port ${SSH_PORT})}"
echo "  scope  : $([[ "$SYNC_FULL_OUTPUT" == 1 ]] && echo 'full output.root' || echo 'reports/aggregate-summary only')"
echo "  mirror : $([[ "$DELETE" == 1 ]] && echo '--delete ON — stale local-only files under each path are removed (DELETE=0 to keep)' || echo 'additive (DELETE=0) — local-only files kept')"
[[ "${DRY_RUN:-0}" == 1 ]] && echo "  *** DRY RUN — no files written or deleted; rsync shows what WOULD change ***"
echo

trap 'aiq_on_err $?' ERR

# Collect what we pulled for an end-of-run recap (parallel arrays).
pulled_names=()
pulled_paths=()

for manifest in "${manifests[@]}"; do
  # Discover name + output.root straight from the manifest (no yaml dependency):
  # each e2e manifest has a single top-level `name:` and a single `root:` (under
  # output:), so the shared scalar reader resolves both unambiguously.
  name="$(_e2e_yaml_scalar "$manifest" name)"
  root="$(_e2e_yaml_scalar "$manifest" root)"
  if [[ -z "$root" ]]; then
    echo "WARN: no output.root in $manifest — skipping" >&2
    continue
  fi

  if [[ "$SYNC_FULL_OUTPUT" == 1 ]]; then
    target="$root"
  else
    target="$root/reports/aggregate-summary"
  fi

  echo "-- ${name:-$manifest}"
  aiq_rsync_pull "$target"
  pulled_names+=("${name:-$manifest}")
  pulled_paths+=("$target")
done

trap - ERR

echo
echo "=================================================================="
echo "== pulled ${#pulled_paths[@]} summary surface(s)"
echo "=================================================================="
for i in "${!pulled_paths[@]}"; do
  echo "  ${pulled_names[$i]}"
  echo "    ${pulled_paths[$i]}"
done
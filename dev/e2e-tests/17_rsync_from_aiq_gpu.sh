#!/usr/bin/env bash
# 17_rsync_from_aiq_gpu.sh — pull audit results from the aiq-gpu GPU box.
#
# aiq-gpu is the heavy-iron GPU host where the audit / e2e grids actually run.
# Its /data roots MIRROR this analysis host (same absolute paths on both
# machines), so a pull is just an rsync of identical paths:
#
#     aiq-gpu:/data/<root>/  ->  /data/<root>/
#
# Use this when the run grid (10_run_smoke_grid.sh / 15_run_full_grid.sh)
# executed on aiq-gpu instead of locally: fetch the results back here, then
# resume the local pipeline at 20_index_local.sh -> 30_compose.sh -> 40_build_summary.sh.
#
# Roots pulled (each toggleable). Defaults mirror dev/e2e-tests/_lib.sh, and
# honor the same AUDIT_* overrides, so a custom root used for the run is pulled
# to the matching local path:
#   RESULTS_ROOT  ${AUDIT_RESULTS_ROOT:-/data/crfm-helm-audit}        per-run HELM outputs
#   STORE_ROOT    ${AUDIT_STORE_ROOT:-/data/crfm-helm-audit-store}    bundles + virtual-exp reports
#
# Usage:
#   ./17_rsync_from_aiq_gpu.sh [EXPERIMENT ...]
#
#   No args      pull the whole of each enabled root.
#   EXPERIMENT…  pull only those subdirectories under RESULTS_ROOT
#                (e.g. audit-historic-grid-gpt-oss-20b-vllm) — narrow + fast.
#                Narrowing forces SYNC_STORE off unless you set SYNC_STORE=1,
#                since an experiment name maps to a RESULTS_ROOT subdir.
#
# Env knobs:
#   AIQ_GPU_HOST   ssh host (default: aiq-gpu — define it in ~/.ssh/config, or
#                  pass a bare user@host here)
#   AIQ_GPU_USER   remote user (optional; prepended as USER@HOST when set and the
#                  host is not already user-qualified)
#   SSH_PORT       ssh port (optional)
#   SYNC_RESULTS   pull RESULTS_ROOT (default 1)
#   SYNC_STORE     pull STORE_ROOT   (default 1 for a full pull; 0 when narrowing)
#   DRY_RUN        rsync --dry-run preview, no writes (default 0)
#   DELETE         rsync --delete: make the local copy an EXACT mirror, deleting
#                  local-only files under the synced path. Default 0 (OFF).
#                  Opt-in and destructive — on a full-root pull this removes any
#                  experiment that exists locally but not on aiq-gpu.
#   RSYNC_EXTRA    extra rsync args, word-split (e.g. RSYNC_EXTRA="--exclude=*.log")
#
# This is a pull (remote -> local): it overwrites local files that also exist on
# aiq-gpu. Without DELETE it never removes local-only files, so it is safe to
# re-run and safe to interleave with locally-produced results.
set -euo pipefail

# Shared host-resolution + rsync-pull helpers (aiq_remote / aiq_rsync_pull /
# aiq_on_err) and the AIQ_GPU_* / DRY_RUN / DELETE / RSYNC_EXTRA knobs they read.
source "$(dirname "${BASH_SOURCE[0]}")/_rsync_lib.sh"

# Root defaults mirror _lib.sh (and its AUDIT_* overrides). We re-derive them
# here instead of sourcing _lib.sh so this stays a pure fetch utility, free of
# the infer-stack data-dir setup _lib.sh performs on source.
STORE_ROOT="${AUDIT_STORE_ROOT:-/data/crfm-helm-audit-store}"
RESULTS_ROOT="${AUDIT_RESULTS_ROOT:-/data/crfm-helm-audit}"

REMOTE="$(aiq_remote)"

experiments=("$@")
sync_results="${SYNC_RESULTS:-1}"
# When narrowing to specific experiments (RESULTS_ROOT subdirs), default the
# store pull off — opt back in with SYNC_STORE=1.
if [[ ${#experiments[@]} -gt 0 ]]; then
  sync_store="${SYNC_STORE:-0}"
else
  sync_store="${SYNC_STORE:-1}"
fi

echo "=================================================================="
echo "== rsync from aiq-gpu"
echo "=================================================================="
echo "  remote        : ${REMOTE}${SSH_PORT:+  (port ${SSH_PORT})}"
echo "  RESULTS_ROOT  : ${RESULTS_ROOT}   (pull=${sync_results})"
echo "  STORE_ROOT    : ${STORE_ROOT}   (pull=${sync_store})"
[[ ${#experiments[@]} -gt 0 ]] && echo "  experiments   : ${experiments[*]}"
[[ "${DRY_RUN:-0}" == 1 ]] && echo "  *** DRY RUN — no files will be written ***"
[[ "${DELETE:-0}"  == 1 ]] && echo "  *** DELETE on — local-only files under synced paths will be removed ***"
echo

trap 'aiq_on_err $?' ERR

if [[ "$sync_results" == 1 ]]; then
  if [[ ${#experiments[@]} -gt 0 ]]; then
    for exp in "${experiments[@]}"; do
      # Accept a bare experiment name or an already-RESULTS_ROOT-relative path.
      aiq_rsync_pull "${RESULTS_ROOT%/}/${exp#"${RESULTS_ROOT%/}/"}"
    done
  else
    aiq_rsync_pull "$RESULTS_ROOT"
  fi
fi

[[ "$sync_store" == 1 ]] && aiq_rsync_pull "$STORE_ROOT"

trap - ERR
echo
echo "Done. Resume the local pipeline with ./20_index_local.sh."

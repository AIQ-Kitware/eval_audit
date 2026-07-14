#!/usr/bin/env bash
# 50_rsync_from_aiq_gpu.sh — pull this runbook's outputs back from the aiq-gpu box.
#
# The 10/15 GPU steps and 20/30/40 usually run on aiq-gpu (the heavy-iron GPU
# host). aiq-gpu's /data roots MIRROR this analysis host at identical absolute
# paths, so bringing the finished outputs "back here" is a straight mirrored
# rsync of the same paths:  aiq-gpu:<abs>/  ->  <abs>/.
#
# Run this FROM the analysis host once the aiq-gpu run is done. It pulls the
# virtual-experiment output.root (the publication surface: synthesized indexes,
# analysis, reports/aggregate-summary). Optional knobs also fetch the raw HELM
# run dirs and the shared audit index.
#
# Usage:
#   ./50_rsync_from_aiq_gpu.sh                 # pull the vexp output.root
#   DRY_RUN=1 ./50_rsync_from_aiq_gpu.sh       # preview only, no writes
#   SYNC_RESULTS=1 ./50_rsync_from_aiq_gpu.sh  # also pull the raw run dirs + index
#
# Env knobs:
#   AIQ_GPU_HOST   ssh host (default: aiq-gpu — define it in ~/.ssh/config, or
#                  pass a bare user@host here)
#   AIQ_GPU_USER   remote user (optional; prepended as USER@HOST when set and the
#                  host is not already user-qualified)
#   SSH_PORT       ssh port (optional)
#   SYNC_OUTPUT    pull the vexp output.root (default 1)
#   SYNC_RESULTS   also pull this runbook's smoke+full run dirs under RESULTS_ROOT
#                  and the shared audit index under STORE_ROOT/indexes (default 0)
#   DRY_RUN        rsync --dry-run preview, no writes (default 0)
#   DELETE         rsync --delete: exact mirror, removing local-only files under
#                  each synced path (default 0 — OFF; opt-in, destructive)
#   RSYNC_EXTRA    extra rsync args, word-split (e.g. RSYNC_EXTRA="--exclude=*.log")
#
# Pull-only (remote -> local). Without DELETE it never removes local-only files,
# so it is safe to re-run and safe to interleave with locally-produced results.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

# This runbook's smoke+full experiment run dirs (used only when SYNC_RESULTS=1).
EXPS=(
  "$QWEN_COMBINED_EXPERIMENT_SMOKE"
  "$QWEN_COMBINED_EXPERIMENT_FULL"
)

# --- resolve the remote + a mirrored-path pull helper (inlined, self-contained) --
aiq_remote() {
  local host="${AIQ_GPU_HOST:-aiq-gpu}"
  if [[ -n "${AIQ_GPU_USER:-}" && "$host" != *@* ]]; then
    printf '%s@%s\n' "$AIQ_GPU_USER" "$host"
  else
    printf '%s\n' "$host"
  fi
}
aiq_ssh_cmd() {
  if [[ -n "${SSH_PORT:-}" ]]; then printf 'ssh -p %s\n' "$SSH_PORT"; else printf 'ssh\n'; fi
}
# Pull one mirrored absolute path: remote dir CONTENTS land in the identically
# named local dir (--mkpath creates it if absent). Archive mode keeps
# perms/times/symlinks (result trees carry DONE/report symlinks); -z compresses,
# --partial resumes.
aiq_rsync_pull() {  # $1 = absolute path mirrored on both hosts
  local p="$1" remote flags
  remote="$(aiq_remote)"
  flags=(-aHz --partial --mkpath --human-readable --info=progress2)
  [[ "${DRY_RUN:-0}" == 1 ]] && flags+=(--dry-run)
  [[ "${DELETE:-0}"  == 1 ]] && flags+=(--delete)
  # RSYNC_EXTRA is intentionally word-split so callers can pass multiple flags.
  [[ -n "${RSYNC_EXTRA:-}" ]] && flags+=(${RSYNC_EXTRA})
  echo "  + ${remote}:${p}/  ->  ${p}/"
  rsync "${flags[@]}" -e "$(aiq_ssh_cmd)" "${remote}:${p}/" "${p}/"
}
aiq_on_err() {
  local remote; remote="$(aiq_remote)"
  echo >&2
  echo "ERROR: rsync from '${remote}' failed (exit $1)." >&2
  echo "  - Is the host reachable?  $(aiq_ssh_cmd) ${remote} true" >&2
  echo "  - Define it in ~/.ssh/config, or pass AIQ_GPU_HOST=user@host (and SSH_PORT=...)." >&2
}

# Resolve the vexp output.root exactly as 40_build_summary.sh does.
read -r EXPERIMENT_NAME OUTPUT_ROOT <<<"$("$PYTHON_BIN" -c "
import yaml
data = yaml.safe_load(open('$VEXP_MANIFEST'))
print(data['name'], data['output']['root'])
")"

sync_output="${SYNC_OUTPUT:-1}"
sync_results="${SYNC_RESULTS:-0}"

echo "=================================================================="
echo "== rsync qwen-models-combined outputs from aiq-gpu"
echo "=================================================================="
echo "  remote       : $(aiq_remote)${SSH_PORT:+  (port ${SSH_PORT})}"
echo "  output.root  : ${OUTPUT_ROOT}   (pull=${sync_output})"
echo "  run dirs+idx : (pull=${sync_results})"
[[ "${DRY_RUN:-0}" == 1 ]] && echo "  *** DRY RUN — no files will be written ***"
[[ "${DELETE:-0}"  == 1 ]] && echo "  *** DELETE on — local-only files under synced paths will be removed ***"
echo

trap 'aiq_on_err $?' ERR

[[ "$sync_output" == 1 ]] && aiq_rsync_pull "$OUTPUT_ROOT"

if [[ "$sync_results" == 1 ]]; then
  for exp in "${EXPS[@]}"; do
    aiq_rsync_pull "$RESULTS_ROOT/$exp"
  done
  aiq_rsync_pull "$STORE_ROOT/indexes"
fi

trap - ERR
echo
echo "Done. Local publication surface: $OUTPUT_ROOT/reports/aggregate-summary"

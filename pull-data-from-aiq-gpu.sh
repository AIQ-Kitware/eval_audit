#!/usr/bin/env bash
# pull-data-from-aiq-gpu.sh — mirror the working data roots from aiq-gpu to this
# analysis host, preserving absolute layout via rsync -R and the /./ pivot.
#
# aiq-gpu's /data roots mirror this host at identical absolute paths, so a pull
# is just:  aiq-gpu:/data/./<dir>  ->  /data/<dir>.  The `/./` in the source is
# the rsync -R pivot: everything AFTER the dot is the relative path recreated
# under the destination (/data), so the remote and local trees stay identical.
#
# Pull-only (remote -> local). Without DELETE it never removes local-only files,
# so it is safe to re-run and safe to interleave with locally-produced results.
#
# Usage:
#   ./pull-data-from-aiq-gpu.sh                 # default set (audit-store + audit)
#   WITH_PUBLIC=1 ./pull-data-from-aiq-gpu.sh   # also the official HELM corpus (large)
#   DRY_RUN=1 ./pull-data-from-aiq-gpu.sh       # preview only, no writes
#   ./pull-data-from-aiq-gpu.sh crfm-helm-audit/audit-allenai-olmo-2-1124-13b-instruct-ifeval-fp32
#                                               # scoped: pull ONE subpath under /data
#
# Positional args, if any, REPLACE the default set: each is a path RELATIVE to
# /data to pull (so you can grab just one result dir instead of the whole tree).
#
# Env knobs:
#   AIQ_GPU_HOST  ssh host (default: aiq-gpu — define it in ~/.ssh/config, or
#                 pass a bare user@host here)
#   AIQ_GPU_USER  remote user (optional; prepended as USER@HOST when set and the
#                 host is not already user-qualified)
#   SSH_PORT      ssh port (optional)
#   DATA_ROOT     the /./ pivot base on BOTH hosts (default: /data)
#   WITH_PUBLIC   also pull $DATA_ROOT/crfm-helm-public — the official corpus,
#                 multi-TB and (here) not group-writable; default 0
#   DRY_RUN       rsync --dry-run preview, no writes (default 0)
#   DELETE        rsync --delete: exact mirror, removing local-only files under
#                 each synced path (default 0 — OFF; opt-in, destructive)
#   RSYNC_EXTRA   extra rsync args, word-split (e.g. RSYNC_EXTRA="--exclude=*.sqlite")
set -euo pipefail

DATA_ROOT="${DATA_ROOT:-/data}"

# The volatile working data — pipeline outputs that change every run:
#   crfm-helm-audit-store : deployment-match, virtual-experiments, analysis,
#                           local-bundles, open-judge, reports, indexes
#   crfm-helm-audit       : HELM run outputs (incl. the *-ifeval-fp32 result dirs)
DEFAULT_TARGETS=(
  crfm-helm-audit-store
  crfm-helm-audit
)

# --- resolve the remote (host/user/port), matching the runbook rsync scripts ---
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

# Assemble the target list: positional args (relative subpaths) override the
# default set; WITH_PUBLIC appends the corpus only when no explicit args given.
declare -a TARGETS
if [[ $# -gt 0 ]]; then
  TARGETS=("$@")
else
  TARGETS=("${DEFAULT_TARGETS[@]}")
  [[ "${WITH_PUBLIC:-0}" == 1 ]] && TARGETS+=(crfm-helm-public)
fi

REMOTE="$(aiq_remote)"

# rsync flags: -a archive, -v verbose, -P partial+progress (resumable), -R
# relative (the /./ pivot decides the recreated path), -z compress. We run as a
# non-root user pulling files owned by assorted remote users, so --no-owner
# --no-group avoids a wall of "not super-user" chown warnings (perms/times are
# still preserved). --mkpath creates the destination base if absent.
FLAGS=(-avPRz --human-readable --no-owner --no-group --mkpath)
[[ "${DRY_RUN:-0}" == 1 ]] && FLAGS+=(--dry-run)
[[ "${DELETE:-0}"  == 1 ]] && FLAGS+=(--delete)
[[ -n "${RSYNC_EXTRA:-}" ]] && FLAGS+=(${RSYNC_EXTRA})

echo "=================================================================="
echo "== pull data from aiq-gpu (mirrored /./ pivot under ${DATA_ROOT})"
echo "=================================================================="
echo "  remote  : ${REMOTE}${SSH_PORT:+  (port ${SSH_PORT})}"
echo "  targets : ${TARGETS[*]}"
[[ "${DRY_RUN:-0}" == 1 ]] && echo "  *** DRY RUN — no files will be written ***"
[[ "${DELETE:-0}"  == 1 ]] && echo "  *** DELETE on — local-only files under synced paths will be removed ***"
echo

# Pull each target independently so one failure (unreachable path, perms) does
# not abort the rest; collect failures and report at the end.
declare -a FAILED=()
for sub in "${TARGETS[@]}"; do
  sub="${sub#/}"; sub="${sub%/}"                      # normalize: no leading/trailing slash
  local_dir="${DATA_ROOT}/${sub}"
  if [[ "${DRY_RUN:-0}" != 1 && -e "$local_dir" && ! -w "$local_dir" ]]; then
    echo "  ! SKIP ${sub} — ${local_dir} exists but is not writable by $(whoami)" >&2
    FAILED+=("$sub (not writable)")
    continue
  fi
  echo "  + ${REMOTE}:${DATA_ROOT}/./${sub}  ->  ${DATA_ROOT}/${sub}"
  if rsync "${FLAGS[@]}" -e "$(aiq_ssh_cmd)" "${REMOTE}:${DATA_ROOT}/./${sub}" "${DATA_ROOT}/"; then
    echo "    ok: ${sub}"
  else
    rc=$?
    echo "  ! FAILED ${sub} (rsync exit ${rc})" >&2
    FAILED+=("$sub (rsync ${rc})")
  fi
  echo
done

if [[ ${#FAILED[@]} -gt 0 ]]; then
  echo "Completed with ${#FAILED[@]} failure(s):" >&2
  printf '  - %s\n' "${FAILED[@]}" >&2
  echo "Hints: is the host reachable ($(aiq_ssh_cmd) ${REMOTE} true)? Define it in" >&2
  echo "       ~/.ssh/config or pass AIQ_GPU_HOST=user@host (and SSH_PORT=...)." >&2
  exit 1
fi

echo "Done. Pulled: ${TARGETS[*]}"

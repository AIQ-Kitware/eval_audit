#!/usr/bin/env bash
# Shared rsync-pull helpers for fetching mirrored /data paths from the aiq-gpu
# GPU box. aiq-gpu's roots mirror this host at identical absolute paths, so every
# pull is `aiq-gpu:<abs>/  ->  <abs>/`.
#
# Sourced by:
#   17_rsync_from_aiq_gpu.sh            generic RESULTS_ROOT/STORE_ROOT (or per-experiment) pull
#   42_rsync_summaries_from_aiq_gpu.sh  just the per-scenario 40_build_summary outputs
#
# These are PURE helpers with no side effects on source, so a standalone script
# (17) and a _lib.sh-sourcing one (42) can both pull them in safely.
#
# Shared env knobs (read at call time, so callers can set them before sourcing
# or just before a call):
#   AIQ_GPU_HOST   ssh host (default: aiq-gpu — define in ~/.ssh/config, or pass user@host)
#   AIQ_GPU_USER   remote user (optional; prepended as USER@HOST unless host is already user@host)
#   SSH_PORT       ssh port (optional)
#   DRY_RUN        rsync --dry-run preview, no writes (default 0)
#   DELETE         rsync --delete: exact mirror, removing local-only files under the
#                  synced path (default 0 — OFF; opt-in, destructive)
#   RSYNC_EXTRA    extra rsync args, word-split (e.g. RSYNC_EXTRA="--exclude=*.log")

# Resolve the remote spec, qualifying with AIQ_GPU_USER only when set and the
# host isn't already user@host.
aiq_remote() {
  local host="${AIQ_GPU_HOST:-aiq-gpu}"
  if [[ -n "${AIQ_GPU_USER:-}" && "$host" != *@* ]]; then
    printf '%s@%s\n' "$AIQ_GPU_USER" "$host"
  else
    printf '%s\n' "$host"
  fi
}

# ssh transport for rsync -e; adds a port only when SSH_PORT is set.
aiq_ssh_cmd() {
  if [[ -n "${SSH_PORT:-}" ]]; then printf 'ssh -p %s\n' "$SSH_PORT"; else printf 'ssh\n'; fi
}

# Pull one mirrored absolute path: the remote directory's CONTENTS land in the
# identically-named local dir (which --mkpath creates if absent). Archive mode
# preserves perms/times/symlinks (result trees carry DONE/report symlinks — keep
# them as symlinks); -z compresses, --partial resumes.
# $1 = absolute path present (mirrored) on both hosts.
aiq_rsync_pull() {
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

# A friendlier failure than a bare rsync exit code: name the host and the usual
# fix. Install with: trap 'aiq_on_err $?' ERR
aiq_on_err() {
  local remote; remote="$(aiq_remote)"
  echo >&2
  echo "ERROR: rsync from '${remote}' failed (exit $1)." >&2
  echo "  - Is the host reachable?  $(aiq_ssh_cmd) ${remote} true" >&2
  echo "  - Define it in ~/.ssh/config, or pass AIQ_GPU_HOST=user@host (and SSH_PORT=...)." >&2
}

#!/usr/bin/env bash
#
# eval-audit HELM runner entrypoint.
#
# Responsibilities (in addition to running the wrapped command):
#   1. Write a `container_provenance.json` sidecar into the output directory so
#      an audit can later answer "which image produced this run?" even if the
#      experiment-level record is lost.
#   2. chown the output directory back to the invoking host user. The container
#      runs as root (so the HF cache + /root work), but kwdagger on the host
#      must own the outputs to read DONE, create symlinks, and rsync them.
#
# Both run via an EXIT trap so they happen even when the wrapped command fails,
# while the wrapped command's exit status is preserved and propagated (kwdagger
# relies on a real non-zero exit to detect failures).
#
# NOTE: we deliberately do NOT `exec "$@"` — exec would replace this shell and
# skip the EXIT trap (and thus the chown/provenance).

set -uo pipefail

# The node always passes `-w <out_dpath>`, so $PWD is the output dir. Allow an
# explicit override for robustness.
OUT_DPATH="${EVAL_AUDIT_OUT_DPATH:-$PWD}"

_write_provenance() {
    # Dependency-free w.r.t. the host: python is on PATH inside the venv.
    EVAL_AUDIT_OUT_DPATH="$OUT_DPATH" python - <<'PY' 2>/dev/null || true
import json
import os
import socket
import subprocess
from datetime import datetime, timezone

out_dpath = os.environ.get("EVAL_AUDIT_OUT_DPATH") or os.getcwd()


def _nvidia_driver():
    try:
        info = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        if info.returncode == 0:
            return [ln.strip() for ln in info.stdout.strip().splitlines() if ln.strip()]
    except Exception:
        pass
    return None


record = {
    "schema": "eval-audit/container-provenance/1",
    "requested_image": os.environ.get("EVAL_AUDIT_CONTAINER_IMAGE"),
    "resolved_digest": os.environ.get("EVAL_AUDIT_CONTAINER_DIGEST"),
    "container_hostname": socket.gethostname(),
    "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
    "nvidia_gpus": _nvidia_driver(),
    "recorded_at": datetime.now(timezone.utc).isoformat(),
}
try:
    with open(os.path.join(out_dpath, "container_provenance.json"), "w") as fp:
        json.dump(record, fp, indent=2)
        fp.write("\n")
except Exception:
    pass
PY
}

_finalize() {
    _write_provenance
    if [[ -n "${HOST_UID:-}" ]]; then
        # -R --no-dereference: change symlinks themselves and do not traverse
        # through them, so a read-only precomputed_root reachable via a reuse
        # symlink is never followed/altered.
        chown -R --no-dereference "${HOST_UID}:${HOST_GID:-$HOST_UID}" "$OUT_DPATH" 2>/dev/null || true
    fi
}
trap _finalize EXIT

"$@"
exit $?

#!/usr/bin/env bash
# Preflight for containerized HELM execution (MANDATORY): the smoke/full grids
# always pass `eval-audit-run --container-image "$GPTOSS_CONTAINER_IMAGE"`, so
# HELM runs inside the pinned eval-audit-helm-runner image (the "docker
# pipeline"). That image must be built first (./docker/build.sh). This verifies
# docker is present, the image exists locally, AND its python env is the
# corrected one — the latter catches a stale digest (built before a recipe fix)
# that would otherwise pass the presence check and then fail deep inside a run.
# The host-venv path has been removed, so this is a required preflight.
# See docs/container-execution.md.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

if ! command -v docker >/dev/null 2>&1; then
  echo "FAIL: docker not found on PATH, but containerized HELM is mandatory." >&2
  echo "  Install docker (there is no host-venv fallback)." >&2
  exit 1
fi

if docker image inspect "$GPTOSS_CONTAINER_IMAGE" >/dev/null 2>&1; then
  echo "OK: container image present: $GPTOSS_CONTAINER_IMAGE"
else
  echo "FAIL: container image not found: $GPTOSS_CONTAINER_IMAGE" >&2
  echo "  Build it first:  $ROOT/docker/build.sh" >&2
  echo "  (or push it and reference a digest via 'eval-audit-run --container-image'," >&2
  echo "   and set GPTOSS_CONTAINER_IMAGE to match; see docs/container-execution.md)" >&2
  exit 1
fi

# Beyond "image exists": verify the image's PYTHON ENVIRONMENT is the corrected
# one. docker/build.sh guards these invariants, but those guards only fire on a
# REBUILD — a stale digest built before the fix passes the presence check above
# and then dies mid-grid. Probe the real image so that surfaces here, cheaply,
# before any GPU work. Each check maps to a failure we have actually hit:
#   * langdetect importable    -> image built with crfm-helm[all], not [heim]
#       (langdetect lives in the [metrics]/[cleva] extras; a [heim] image dies
#        with "ModuleNotFoundError: langdetect" on the ifeval metric — which this
#        runbook's smoke set exercises as its canary).
#   * huggingface_hub==0.36.2  -> the pin HELM's dataset loaders are validated
#       against; a floated hub breaks old-style dataset ids.
# CPU-only import check: no --gpus, no network. --entrypoint python bypasses the
# provenance/chown wrapper and reads the probe from stdin (`python -`).
echo "Probing container python env (langdetect + huggingface_hub pin)…"
if ! docker run --rm -i --entrypoint python "$GPTOSS_CONTAINER_IMAGE" - <<'PY'
import sys

errs = []
try:
    import langdetect  # noqa: F401
except Exception as e:
    errs.append(
        "langdetect import failed -> image likely built with crfm-helm[heim] "
        f"instead of [all] ({e!r})"
    )
try:
    import huggingface_hub
    if huggingface_hub.__version__ != "0.36.2":
        errs.append(
            f"huggingface_hub=={huggingface_hub.__version__}, expected 0.36.2 "
            "-> a floated hub breaks dataset-id resolution"
        )
except Exception as e:
    errs.append(f"huggingface_hub import failed: {e!r}")

if errs:
    for m in errs:
        print("FAIL:", m, file=sys.stderr)
    sys.exit(1)
print(f"OK: container python env — langdetect ok; huggingface_hub {huggingface_hub.__version__}")
PY
then
  echo "FAIL: container image is present but its python env is stale/incorrect." >&2
  echo "  Rebuild the runner image:  $ROOT/docker/build.sh" >&2
  echo "  then re-pin GPTOSS_CONTAINER_IMAGE to the new build (docs/container-execution.md)." >&2
  exit 1
fi

echo "Note: the model is SERVED on the host (vLLM behind LiteLLM); the in-container"
echo "      HELM reaches the host endpoint via --network host (the preset's"
echo "      container_network: host)."

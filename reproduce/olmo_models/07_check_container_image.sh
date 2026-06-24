#!/usr/bin/env bash
# Preflight for containerized HELM execution (MANDATORY): the smoke/full grids
# always pass `eval-audit-run --container-image "$OLMO_CONTAINER_IMAGE"`, so HELM
# runs inside the pinned eval-audit-helm-runner image (the "docker pipeline").
# That image must be built first (./docker/build.sh). This verifies docker is
# present and the image exists locally, and points at the build script otherwise.
# The host-venv path has been removed, so this is a required preflight. See
# docs/container-execution.md.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

if ! command -v docker >/dev/null 2>&1; then
  echo "FAIL: docker not found on PATH, but containerized HELM is mandatory." >&2
  echo "  Install docker (there is no host-venv fallback)." >&2
  exit 1
fi

if docker image inspect "$OLMO_CONTAINER_IMAGE" >/dev/null 2>&1; then
  echo "OK: container image present: $OLMO_CONTAINER_IMAGE"
else
  echo "FAIL: container image not found: $OLMO_CONTAINER_IMAGE" >&2
  echo "  Build it first:  ./docker/build.sh" >&2
  echo "  (or push it and reference a digest via 'eval-audit-run --container-image'," >&2
  echo "   and set OLMO_CONTAINER_IMAGE to match; see docs/container-execution.md)" >&2
  exit 1
fi

echo "Note: the model is SERVED on the host (vLLM behind LiteLLM); the in-container"
echo "      HELM reaches the host endpoint via --network host (declared by the"
echo "      presets' container_network: host)."

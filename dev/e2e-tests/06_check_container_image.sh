#!/usr/bin/env bash
# Preflight for containerized HELM execution (MANDATORY): every e2e scenario runs
# HELM inside the pinned eval-audit-helm-runner image, which must be built first
# (./docker/build.sh). This verifies docker is present and the image exists
# locally, and points at the build script otherwise. The host-venv path has been
# removed, so this is a required preflight. See docs/container-execution.md.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

if ! command -v docker >/dev/null 2>&1; then
  echo "FAIL: docker not found on PATH, but containerized HELM is mandatory." >&2
  echo "  Install docker (there is no host-venv fallback)." >&2
  exit 1
fi

if docker image inspect "$E2E_CONTAINER_IMAGE" >/dev/null 2>&1; then
  echo "OK: container image present: $E2E_CONTAINER_IMAGE"
else
  echo "FAIL: container image not found: $E2E_CONTAINER_IMAGE" >&2
  echo "  Build it first:  ./docker/build.sh" >&2
  echo "  (or push it and reference a digest via 'eval-audit-run --container-image'," >&2
  echo "   and set E2E_CONTAINER_IMAGE to match; see docs/container-execution.md)" >&2
  exit 1
fi

echo "Note: the served (vLLM) scenarios reach the host LiteLLM endpoint via"
echo "      --network host (declared by their preset's container_network: host);"
echo "      the hf scenario loads the model in-process on a real GPU."

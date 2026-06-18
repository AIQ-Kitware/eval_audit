#!/usr/bin/env bash
# Preflight for the containerized example (ON BY DEFAULT; E2E_INCLUDE_CONTAINER=0
# to skip): the vllm-container target runs HELM inside the pinned
# eval-audit-helm-runner image, which must be built first (./docker/build.sh).
# This verifies docker is present and the image exists locally, and points at the
# build script otherwise. It is a no-op only when the container example is
# disabled (E2E_INCLUDE_CONTAINER=0).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

if [[ "${E2E_INCLUDE_CONTAINER:-1}" == "0" ]]; then
  echo "E2E_INCLUDE_CONTAINER=0: container example disabled; skipping."
  exit 0
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "FAIL: docker not found on PATH, but the container example is enabled by default." >&2
  echo "  Install docker, or set E2E_INCLUDE_CONTAINER=0 to skip the container example." >&2
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

echo "Note: the vllm-container example also reaches the host LiteLLM endpoint via"
echo "      --network host (declared by its preset's container_network: host)."

#!/usr/bin/env bash
# Preflight for the OPT-IN containerized example (E2E_INCLUDE_CONTAINER=1): the
# vllm-container target runs HELM inside the pinned eval-audit-helm-runner image,
# which must be built first (./docker/build.sh). This verifies docker is present
# and the image exists locally, and points at the build script otherwise. It is a
# no-op when the container example is not enabled.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

if [[ "${E2E_INCLUDE_CONTAINER:-0}" != "1" ]]; then
  echo "E2E_INCLUDE_CONTAINER != 1: container example not enabled; skipping."
  echo "  (set E2E_INCLUDE_CONTAINER=1 to add the vllm-container target)"
  exit 0
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "FAIL: docker not found on PATH, but the container example is enabled." >&2
  echo "  Install docker, or run without E2E_INCLUDE_CONTAINER." >&2
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

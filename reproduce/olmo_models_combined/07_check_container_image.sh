#!/usr/bin/env bash
# Container-image preflight — IDENTICAL to ../olmo_models/07_check_container_image.sh
# (same pinned eval-audit-helm-runner image, same python-env probe: langdetect +
# huggingface_hub==0.36.2). Target-independent, so delegate to the single-model
# runbook's implementation to avoid drift. Build the image first with ./docker/build.sh.
set -euo pipefail
_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$_here/../olmo_models/07_check_container_image.sh"

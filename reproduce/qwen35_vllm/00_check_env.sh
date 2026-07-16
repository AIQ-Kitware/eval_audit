#!/usr/bin/env bash
# Env preflight (eval-audit-check-env). Sourcing _lib also exercises the shared
# serving/leasing env setup (INFER_STACK_DATA_DIR resolution, etc.).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"
eval-audit-check-env

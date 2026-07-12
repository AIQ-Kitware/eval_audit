#!/usr/bin/env bash
# Preflight: the shared eval-audit environment check.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"
eval-audit-check-env
echo "Next: ./05_check_profiles.sh"

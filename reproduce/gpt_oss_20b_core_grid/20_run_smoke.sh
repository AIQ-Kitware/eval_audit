#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STORE_ROOT="${AUDIT_STORE_ROOT:-/data/crfm-helm-audit-store}"
BUNDLE_ROOT="$STORE_ROOT/local-bundles/gpt_oss_20b_core_grid"
cd "$ROOT"
bash reproduce/gpt_oss_20b_core_grid/05_write_bundle.sh >/dev/null
bash reproduce/gpt_oss_20b_core_grid/10_start_service.sh
bash reproduce/gpt_oss_20b_core_grid/15_validate_server.sh >/dev/null
eval-audit-run --run=1 "$BUNDLE_ROOT/smoke_manifest.yaml"

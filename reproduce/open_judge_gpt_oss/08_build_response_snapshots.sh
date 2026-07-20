#!/usr/bin/env bash
# Phase 2 (§7): freeze every SUPPORTED audited run into a content-addressed
# response snapshot. Idempotent (rebuilding an existing snapshot is a cache
# hit). Requires jq to select supported run_paths from the audit report.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

[[ -f "$OJ_AUDIT_JSON" ]] || { echo "FAIL: run 05 first ($OJ_AUDIT_JSON missing)." >&2; exit 1; }
command -v jq >/dev/null || { echo "FAIL: jq is required." >&2; exit 1; }

mapfile -t run_paths < <(jq -r '.records[] | select(.supported_for_rejudging) | .run_path' "$OJ_AUDIT_JSON")
[[ ${#run_paths[@]} -gt 0 ]] || { echo "FAIL: no supported runs in $OJ_AUDIT_JSON." >&2; exit 1; }

for run_path in "${run_paths[@]}"; do
  eval-audit-build-response-snapshot --run-dir "$run_path" --snapshot-root "$OJ_SNAPSHOT_ROOT"
done
echo
echo "OK: snapshots under $OJ_SNAPSHOT_ROOT"

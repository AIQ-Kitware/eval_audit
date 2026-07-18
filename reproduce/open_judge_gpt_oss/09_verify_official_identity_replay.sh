#!/usr/bin/env bash
# Phase 3 (§8) STOP GATE: reproduce each snapshot's published judge metrics
# from the ORIGINAL annotations before any judge request is sent. Exits
# nonzero if any snapshot fails to reproduce exactly.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

shopt -s nullglob
snapshots=("$OJ_SNAPSHOT_ROOT"/*/)
[[ ${#snapshots[@]} -gt 0 ]] || { echo "FAIL: no snapshots (run 08 first)." >&2; exit 1; }

eval-audit-verify-judge-replay "${snapshots[@]}" \
  --output "$OJ_ROOT/replay-report.json"
echo
echo "OK: identity replay passed — reconstruction is faithful, safe to rejudge."

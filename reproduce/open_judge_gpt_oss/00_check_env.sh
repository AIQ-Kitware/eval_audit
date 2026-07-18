#!/usr/bin/env bash
# Preflight: eval-audit env + resolved open-judge paths.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

echo "candidate model : $OJ_CANDIDATE_MODEL"
echo "benchmarks      : $OJ_BENCHMARKS"
echo "corpus          : $OJ_CORPUS"
echo "snapshot root   : $OJ_SNAPSHOT_ROOT"
echo "results root    : $OJ_RESULTS_ROOT"
echo "sidecar dir     : $OJ_SIDECAR_DIR"
echo "judge endpoints : $OJ_JUDGE_ENDPOINTS"
echo

eval-audit-check-env || {
  echo "FAIL: eval-audit-check-env reported problems." >&2
  exit 1
}
[[ -d "$OJ_CORPUS" ]] || {
  echo "WARN: corpus $OJ_CORPUS not present on this host; 05 will find 0 runs." >&2
}
echo "OK: environment looks sane."

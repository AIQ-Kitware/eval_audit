#!/usr/bin/env bash
# Phase 13 (§18): build the judge-substitution report for a benchmark's
# snapshot from all rejudge artifacts under the results root. Joins by
# response-set hash + display key; reports open-vs-official, the official
# GPT-vs-Llama baseline, open-vs-open, and replicate variance.
#
# Usage: ./30_analyze_judges.sh [benchmark]   (default: xstest)
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

benchmark="${1:-xstest}"
snapshot="$(oj_snapshot_for_benchmark "$benchmark")" || {
  echo "FAIL: no $benchmark snapshot under $OJ_SNAPSHOT_ROOT." >&2
  exit 1
}
mkdir -p "$OJ_ANALYSIS_ROOT"
hash="$(basename "$snapshot")"
eval-audit-analyze-judges \
  --snapshot "$snapshot" \
  --results-root "$OJ_RESULTS_ROOT" \
  --output "$OJ_ANALYSIS_ROOT/$benchmark-$hash.json" \
  --text "$OJ_ANALYSIS_ROOT/$benchmark-$hash.txt"

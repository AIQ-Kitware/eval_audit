#!/usr/bin/env bash
# Phase 1 (§6): audit the gpt-oss-20b closed-judge rows for rejudging
# suitability. Writes the JSON audit report; exits nonzero if nothing
# usable is found. Never modifies the corpus.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

mkdir -p "$(dirname "$OJ_AUDIT_JSON")"
eval-audit-audit-judge-sources "$OJ_CORPUS" \
  --model "$OJ_CANDIDATE_MODEL" \
  --benchmarks $OJ_BENCHMARKS \
  --output "$OJ_AUDIT_JSON"
echo
echo "OK: audit report at $OJ_AUDIT_JSON"

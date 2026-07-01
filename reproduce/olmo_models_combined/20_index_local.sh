#!/usr/bin/env bash
# Re-index the local audit results so the combined full experiment lands in
# $AUDIT_STORE_ROOT/indexes/audit_results_index.csv, which the virtual-experiment
# composer reads in the next step. Unlike the single-model runbook (seven
# experiments), the combined fan-out produces ONE experiment
# (audit-allenai-olmo-combined-full) whose run dirs span five models.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

echo "== verifying the combined full run dir exists =="
exp_dir="$RESULTS_ROOT/$OLMO_COMBINED_EXPERIMENT_FULL"
if [[ -d "$exp_dir" ]]; then
  echo "  found: $exp_dir"
else
  echo "  MISSING: $exp_dir" >&2
  echo "WARN: the combined full run dir is missing; did 15_run_full.sh complete?" >&2
fi

echo
echo "== eval-audit-index =="
eval-audit-index \
  --results-root "$RESULTS_ROOT" \
  --report-dpath "$STORE_ROOT/indexes"

echo
echo "OK: index refreshed at $STORE_ROOT/indexes/audit_results_index.csv"
echo "Next: ./30_compose.sh"

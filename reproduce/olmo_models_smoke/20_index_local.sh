#!/usr/bin/env bash
# Re-index the local audit results so all six OLMo smoke experiments land in
# $AUDIT_STORE_ROOT/indexes/audit_results_index.csv, which the virtual-experiment
# composer reads in the next step.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

echo "== verifying smoke run dirs exist =="
missing=0
for target in "${OLMO_TARGETS[@]}"; do
  exp="$(olmo_experiment "$target")"
  exp_dir="$RESULTS_ROOT/$exp"
  if [[ -d "$exp_dir" ]]; then
    echo "  found: $exp_dir"
  else
    echo "  MISSING: $exp_dir" >&2
    missing=1
  fi
done
if (( missing == 1 )); then
  echo "WARN: some smoke run dirs are missing; did 10_run_smoke_grid.sh complete?" >&2
fi

echo
echo "== eval-audit-index =="
eval-audit-index \
  --results-root "$RESULTS_ROOT" \
  --report-dpath "$STORE_ROOT/indexes"

echo
echo "OK: index refreshed at $STORE_ROOT/indexes/audit_results_index.csv"
echo "Next: ./30_compose.sh"

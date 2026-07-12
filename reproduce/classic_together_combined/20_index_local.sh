#!/usr/bin/env bash
# Re-index the local audit results so every full experiment lands in
# $AUDIT_STORE_ROOT/indexes/audit_results_index.csv, which the virtual-experiment
# composer reads. Per-era/model reports are built from the FULL runs; the smoke
# grid is a preflight only.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

echo "== verifying full run dirs exist =="
missing=0
for target in "${TARGETS[@]}"; do
  exp="$(t_experiment_full "$target")"
  exp_dir="$RESULTS_ROOT/$exp"
  if [[ -d "$exp_dir" ]]; then echo "  found: $exp_dir"
  else echo "  MISSING: $exp_dir" >&2; missing=1; fi
done
(( missing == 1 )) && echo "WARN: some full run dirs are missing; did 15_run_full.sh complete?" >&2

echo
echo "== eval-audit-index =="
eval-audit-index \
  --results-root "$RESULTS_ROOT" \
  --report-dpath "$STORE_ROOT/indexes"

echo
echo "OK: index refreshed at $STORE_ROOT/indexes/audit_results_index.csv"
echo "Next: ./25_index_official_classic.sh"

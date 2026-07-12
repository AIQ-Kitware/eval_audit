#!/usr/bin/env bash
# Re-index the local audit results so both era full experiments land in
# $AUDIT_STORE_ROOT/indexes/audit_results_index.csv, which the virtual-experiment
# composer reads. The per-era reports are built from the FULL runs; the smoke
# grid is a preflight only. Mirrors dev/e2e-tests/20_index_local.sh.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

echo "== verifying full run dirs exist =="
missing=0
for target in "${ERA_TARGETS[@]}"; do
  exp="$(era_experiment_full "$target")"
  exp_dir="$RESULTS_ROOT/$exp"
  if [[ -d "$exp_dir" ]]; then
    echo "  found: $exp_dir"
  else
    echo "  MISSING: $exp_dir" >&2
    missing=1
  fi
done
if (( missing == 1 )); then
  echo "WARN: some full run dirs are missing; did 15_run_full_grid.sh complete?" >&2
fi

echo
echo "== eval-audit-index =="
eval-audit-index \
  --results-root "$RESULTS_ROOT" \
  --report-dpath "$STORE_ROOT/indexes"

echo
echo "OK: index refreshed at $STORE_ROOT/indexes/audit_results_index.csv"
echo "Next: ./25_index_official_classic.sh"

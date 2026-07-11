#!/usr/bin/env bash
# Re-index the local audit results so the combined full experiment lands in
# $AUDIT_STORE_ROOT/indexes/audit_results_index.csv, which the virtual-experiment
# composer reads in the next step. The combined fan-out produces ONE experiment
# (audit-qwen-combined-full, eight models); any split-out members (empty by default)
# run as extra single-model experiments (audit-qwen-<preset>-full). All are folded
# into the one virtual experiment by 30_compose.sh.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

echo "== verifying the full run dirs exist =="
full_experiments=("$QWEN_COMBINED_EXPERIMENT_FULL")
for preset in "${QWEN_COMBINED_EXTRA_PRESETS[@]}"; do
  full_experiments+=("audit-${preset}-full")
done
for exp in "${full_experiments[@]}"; do
  exp_dir="$RESULTS_ROOT/$exp"
  if [[ -d "$exp_dir" ]]; then
    echo "  found: $exp_dir"
  else
    echo "  MISSING: $exp_dir" >&2
    echo "WARN: $exp is missing; did 15_run_full.sh complete?" >&2
  fi
done

echo
echo "== eval-audit-index =="
eval-audit-index \
  --results-root "$RESULTS_ROOT" \
  --report-dpath "$STORE_ROOT/indexes"

echo
echo "OK: index refreshed at $STORE_ROOT/indexes/audit_results_index.csv"
echo "Next: ./30_compose.sh"

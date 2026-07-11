#!/usr/bin/env bash
# Build the aggregate publication surface for the gpt-oss-20b-from-spec virtual
# experiment, running against the synthesized index slice produced by 30_compose.sh.
# VEXP_MANIFEST is set by _lib.sh to configs/virtual-experiments/gpt-oss-20b-from-spec.yaml.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

read -r EXPERIMENT_NAME OUTPUT_ROOT <<<"$("$PYTHON_BIN" -c "
import yaml
data = yaml.safe_load(open('$VEXP_MANIFEST'))
print(data['name'], data['output']['root'])
")"

INDEX_FPATH="$OUTPUT_ROOT/indexes/audit_results_index.csv"
SUMMARY_ROOT="$OUTPUT_ROOT/reports/aggregate-summary"
SCOPED_FILTER_INVENTORY="$OUTPUT_ROOT/scoped_filter_inventory.json"

if [[ ! -f "$INDEX_FPATH" ]]; then
  echo "synthesized index not found: $INDEX_FPATH" >&2
  echo "run ./30_compose.sh first." >&2
  exit 1
fi

# With the official_public_index source enabled, compose writes a scoped
# inventory; use it. If you comment that source out (local-only report), compose
# writes none and we fall back to --no-filter-inventory.
if [[ -f "$SCOPED_FILTER_INVENTORY" ]]; then
  INVENTORY_FLAGS=(--filter-inventory-json "$SCOPED_FILTER_INVENTORY")
else
  INVENTORY_FLAGS=(--no-filter-inventory)
fi

PYTHONPATH="$ROOT" "$PYTHON_BIN" -m eval_audit.workflows.build_reports_summary \
  --experiment-name "$EXPERIMENT_NAME" \
  --index-fpath "$INDEX_FPATH" \
  --summary-root "$SUMMARY_ROOT" \
  --analysis-root "$OUTPUT_ROOT" \
  --no-canonical-scan \
  "${INVENTORY_FLAGS[@]}" \
  "$@"

echo
echo "Aggregate publication surface: $SUMMARY_ROOT"

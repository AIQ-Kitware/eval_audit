#!/usr/bin/env bash
# Build the aggregate publication surface for EACH per-era virtual experiment
# composed by 30_compose.sh — one report per era, each pairing redpajama-3b's
# local replay against that era's official runs (same_deployment resolves
# 'unknown' for era pairs). Mirrors dev/e2e-tests/40_build_summary.sh.
#
# Loops over ERA_TARGETS by default; set VEXP_MANIFEST=<path> for a single one.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

if [[ -n "${VEXP_MANIFEST:-}" ]]; then
  manifests=("$VEXP_MANIFEST")
else
  manifests=()
  for target in "${ERA_TARGETS[@]}"; do
    manifests+=("$(era_vexp_manifest "$target")")
  done
fi

summary_names=()
summary_paths=()

for manifest in "${manifests[@]}"; do
  read -r EXPERIMENT_NAME OUTPUT_ROOT <<<"$("$PYTHON_BIN" -c "
import yaml
data = yaml.safe_load(open('$manifest'))
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

  if [[ -f "$SCOPED_FILTER_INVENTORY" ]]; then
    INVENTORY_FLAGS=(--filter-inventory-json "$SCOPED_FILTER_INVENTORY")
  else
    INVENTORY_FLAGS=(--no-filter-inventory)
  fi

  echo
  echo "==================================================================="
  echo "== summary for $EXPERIMENT_NAME"
  echo "==================================================================="
  PYTHONPATH="$ROOT" "$PYTHON_BIN" -m eval_audit.workflows.build_reports_summary \
    --experiment-name "$EXPERIMENT_NAME" \
    --index-fpath "$INDEX_FPATH" \
    --summary-root "$SUMMARY_ROOT" \
    --analysis-root "$OUTPUT_ROOT" \
    --no-canonical-scan \
    "${INVENTORY_FLAGS[@]}" \
    "$@"
  echo "Aggregate publication surface: $SUMMARY_ROOT"

  case "$SUMMARY_ROOT" in
    /*) summary_abspath="$SUMMARY_ROOT" ;;
    *)  summary_abspath="$ROOT/$SUMMARY_ROOT" ;;
  esac
  summary_names+=("$EXPERIMENT_NAME")
  summary_paths+=("$summary_abspath")
done

echo
echo "==================================================================="
echo "== era summaries complete — ${#summary_paths[@]} era report(s)"
echo "==================================================================="
for i in "${!summary_paths[@]}"; do
  echo "  ${summary_names[$i]}"
  echo "    ${summary_paths[$i]}"
done

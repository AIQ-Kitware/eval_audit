#!/usr/bin/env bash
# Build the aggregate publication surface for EACH per-scenario virtual
# experiment composed by 30_compose.sh — one report per phi-2 scenario, each
# pairing against the public run (comparable vLLM, incomparable vLLM, HF-direct,
# and the containerized scenario when enabled).
#
# By default this loops over the scenarios in E2E_TARGETS. Set VEXP_MANIFEST=<path>
# to summarize a single manifest only.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

if [[ -n "${VEXP_MANIFEST:-}" ]]; then
  manifests=("$VEXP_MANIFEST")
else
  manifests=()
  for target in "${E2E_TARGETS[@]}"; do
    manifests+=("$(e2e_vexp_manifest "$target")")
  done
fi

# Collect each scenario's report location so we can recap them all together at the
# end (parallel arrays: name[i] <-> report dir[i]) for easy copy/paste access.
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

  # With the official_public_index source's pre_filter, compose writes a scoped
  # inventory; use it for Sankey A. Otherwise fall back to --no-filter-inventory.
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

  # Record an absolute path (resolve against $ROOT, the cwd, when relative) for the
  # end-of-run recap so the printed paths are copy/paste-able from anywhere.
  case "$SUMMARY_ROOT" in
    /*) summary_abspath="$SUMMARY_ROOT" ;;
    *)  summary_abspath="$ROOT/$SUMMARY_ROOT" ;;
  esac
  summary_names+=("$EXPERIMENT_NAME")
  summary_paths+=("$summary_abspath")
done

# End-of-run recap: list every scenario's report so the results are easy to find
# without scrolling back through the per-scenario build output.
echo
echo "==================================================================="
echo "== e2e summaries complete — ${#summary_paths[@]} scenario report(s)"
echo "==================================================================="
for i in "${!summary_paths[@]}"; do
  echo "  ${summary_names[$i]}"
  echo "    ${summary_paths[$i]}"
done

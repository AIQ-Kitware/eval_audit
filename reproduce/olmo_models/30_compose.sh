#!/usr/bin/env bash
# Compose the OLMo-models virtual experiment: filter the seven full experiments
# out of the audit index, re-stamp them under a single experiment name
# (olmo-models), and run analyze_experiment per packet. This is the grouping
# step. Requires the local index produced by 20_index_local.sh; it does not
# re-run any benchmarks.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

required_inputs=(
  "$STORE_ROOT/indexes/audit_results_index.csv"
  "$VEXP_MANIFEST"
)
for path in "${required_inputs[@]}"; do
  if [[ ! -e "$path" ]]; then
    echo "missing required input: $path" >&2
    exit 1
  fi
done

# --allow-single-repeat: with EVAL_AUDIT_SKIP_LOCAL_REPEAT=1 each model has a
# single local attempt, which is the expected shape for this grid.
PYTHONPATH="$ROOT" "$PYTHON_BIN" -m eval_audit.cli.build_virtual_experiment \
  --manifest "$VEXP_MANIFEST" \
  --allow-single-repeat \
  "$@"

echo
echo "OK: composed virtual experiment from $VEXP_MANIFEST"
echo "Next: ./40_build_summary.sh"

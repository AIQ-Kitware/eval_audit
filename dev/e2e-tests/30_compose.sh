#!/usr/bin/env bash
# Compose each phi-2 scenario as its OWN virtual experiment, using a static
# per-scenario manifest (configs/virtual-experiments/e2e-phi2-<scenario>.yaml).
# Composing one scenario at a time means one local recipe per report, so each
# pairs cleanly with the public run instead of pooling all scenarios into a
# single packet. Requires the local index produced by 20_index_local.sh; it does
# not re-run any benchmarks.
#
# By default this loops over the scenarios in E2E_TARGETS. Set
# VEXP_MANIFEST=<path> to compose just one manifest.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

if [[ ! -e "$STORE_ROOT/indexes/audit_results_index.csv" ]]; then
  echo "missing required input: $STORE_ROOT/indexes/audit_results_index.csv" >&2
  exit 1
fi

if [[ -n "${VEXP_MANIFEST:-}" ]]; then
  manifests=("$VEXP_MANIFEST")
else
  manifests=()
  for target in "${E2E_TARGETS[@]}"; do
    manifests+=("$(e2e_vexp_manifest "$target")")
  done
fi

for manifest in "${manifests[@]}"; do
  if [[ ! -e "$manifest" ]]; then
    echo "missing manifest: $manifest" >&2
    exit 1
  fi
  echo
  echo "==================================================================="
  echo "== composing $manifest"
  echo "==================================================================="
  # --allow-single-repeat: with EVAL_AUDIT_SKIP_LOCAL_REPEAT=1 each scenario has
  # a single local attempt, which is the expected shape for this grid.
  PYTHONPATH="$ROOT" "$PYTHON_BIN" -m eval_audit.cli.build_virtual_experiment \
    --manifest "$manifest" \
    --allow-single-repeat \
    "$@"
done

echo
echo "OK: composed ${#manifests[@]} per-scenario virtual experiment(s)"
echo "Next: ./40_build_summary.sh"

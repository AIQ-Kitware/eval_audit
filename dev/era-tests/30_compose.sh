#!/usr/bin/env bash
# Compose each era as its OWN virtual experiment, using the per-era manifest
# (configs/virtual-experiments/era-redpajama-v{024,030}.yaml). One local recipe per
# report, paired against that era's official runs. Requires the local index
# (20_index_local.sh) and the per-era official index + inventory
# (25_index_official_classic.sh). Mirrors dev/e2e-tests/30_compose.sh.
#
# Loops over ERA_TARGETS by default; set VEXP_MANIFEST=<path> for a single one.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

if [[ ! -e "$STORE_ROOT/indexes/audit_results_index.csv" ]]; then
  echo "missing required input: $STORE_ROOT/indexes/audit_results_index.csv" >&2
  echo "run ./20_index_local.sh first." >&2
  exit 1
fi
if [[ ! -e "$STORE_ROOT/indexes/era-tests" ]]; then
  echo "missing per-era official indexes under $STORE_ROOT/indexes/era-tests" >&2
  echo "run ./25_index_official_classic.sh first." >&2
  exit 1
fi

if [[ -n "${VEXP_MANIFEST:-}" ]]; then
  manifests=("$VEXP_MANIFEST")
else
  manifests=()
  for target in "${ERA_TARGETS[@]}"; do
    manifests+=("$(era_vexp_manifest "$target")")
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
  PYTHONPATH="$ROOT" "$PYTHON_BIN" -m eval_audit.cli.build_virtual_experiment \
    --manifest "$manifest" \
    --allow-single-repeat \
    "$@"
done

echo
echo "OK: composed ${#manifests[@]} per-era virtual experiment(s)"
echo "Next: ./40_build_summary.sh"

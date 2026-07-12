#!/usr/bin/env bash
# Build the PER-ERA official/public index + Stage-1 filter inventory the vexp
# manifests pair against. The canonical official_public_index.csv is
# modern-tracks-only (ZERO classic rows), so the classic era comparison has
# nothing to pair with unless we index the classic corpus ourselves.
#
# One Stage-1 pass PER ERA SUITE (eval-audit-index-historic --suite_pattern
# <suite>), landing:
#   $STORE_ROOT/indexes/era-tests/<suite>/official_public_index.csv
#   $STORE_ROOT/analysis/era-tests/<suite>/filter_inventory.json
# Per-suite (not one combined index) so a v0.2.4 local run cannot pair against a
# v0.3.0 official (their logical keys are identical across suites).
#
# CRITICAL: --out_fpath / --out_detail_fpath default to the CURATED corpus
# catalog ($STORE_ROOT/configs/run_specs.yaml + run_details.yaml). We redirect
# BOTH to a scratch dir so this runbook never clobbers the curated catalog.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

if [[ ! -d "$PRECOMPUTED_ROOT/benchmark_output" ]]; then
  echo "FAIL: PRECOMPUTED_ROOT=$PRECOMPUTED_ROOT has no benchmark_output/ (classic corpus mirror expected)." >&2
  exit 1
fi

declare -A seen=()
for target in "${ERA_TARGETS[@]}"; do
  key="$(era_key "$target")"
  [[ -n "${seen[$key]:-}" ]] && continue
  seen["$key"]=1
  suite="$(era_suite_version "$key")"

  index_dpath="$STORE_ROOT/indexes/era-tests/$suite"
  inventory_fpath="$STORE_ROOT/analysis/era-tests/$suite/filter_inventory.json"
  scratch="$ERA_OUT/stage1/$suite"
  mkdir -p "$index_dpath" "$(dirname "$inventory_fpath")" "$scratch"

  echo
  echo "==================================================================="
  echo "== Stage-1 official index for suite $suite (era $key)"
  echo "==================================================================="
  eval-audit-index-historic "$PRECOMPUTED_ROOT" \
    --suite_pattern "$suite" \
    --out_official_index_dpath "$index_dpath" \
    --out_inventory_json "$inventory_fpath" \
    --out_fpath "$scratch/run_specs.yaml" \
    --out_detail_fpath "$scratch/run_details.yaml"

  echo "  official index: $index_dpath/official_public_index.csv"
  echo "  filter inventory: $inventory_fpath"
done

echo
echo "OK: per-era official indexes + inventories written under $STORE_ROOT/{indexes,analysis}/era-tests/"
echo "Next: ./30_compose.sh"

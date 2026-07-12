#!/usr/bin/env bash
# Compose each ERA as its own virtual experiment (configs/virtual-experiments/
# classic-together-v{024,030}.yaml) — each folds ALL THREE models for that suite,
# paired against that era's official runs. Requires the local index
# (20_index_local.sh) and the per-era official index + inventory
# (25_index_official_classic.sh).
#
# Loops over the two era manifests by default; set VEXP_MANIFEST=<path> for one.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

if [[ ! -e "$STORE_ROOT/indexes/audit_results_index.csv" ]]; then
  echo "missing required input: $STORE_ROOT/indexes/audit_results_index.csv" >&2
  echo "run ./20_index_local.sh first." >&2
  exit 1
fi
if [[ ! -e "$STORE_ROOT/indexes/classic-together" ]]; then
  echo "missing per-era official indexes under $STORE_ROOT/indexes/classic-together" >&2
  echo "run ./25_index_official_classic.sh first." >&2
  exit 1
fi

if [[ -n "${VEXP_MANIFEST:-}" ]]; then
  manifests=("$VEXP_MANIFEST")
else
  mapfile -t manifests < <(vexp_manifests)
fi

for manifest in "${manifests[@]}"; do
  [[ -e "$manifest" ]] || { echo "missing manifest: $manifest" >&2; exit 1; }
  echo
  echo "==================================================================="
  echo "== composing $manifest"
  echo "==================================================================="
  # One local attempt per scenario (grid exports EVAL_AUDIT_SKIP_LOCAL_REPEAT=1,
  # read by the planner). The old --allow-single-repeat flag was a no-op, deleted
  # in refactor D-3; the env var is the real control — do not re-add the flag.
  PYTHONPATH="$ROOT" "$PYTHON_BIN" -m eval_audit.cli.build_virtual_experiment \
    --manifest "$manifest" \
    "$@"
done

echo
echo "OK: composed ${#manifests[@]} per-era virtual experiment(s)"
echo "Next: ./40_build_summary.sh"

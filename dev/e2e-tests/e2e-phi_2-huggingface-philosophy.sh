#!/bin/bash 
set -euo pipefail

eval-audit-check-env

# Keep skipping local-repeat enabled and exported
export EVAL_AUDIT_SKIP_LOCAL_REPEAT=1
export EVAL_AUDIT_GROUP_STRIP=1

STORE_ROOT="${AUDIT_STORE_ROOT:-/data/crfm-helm-audit-store}"
RESULTS_ROOT="${AUDIT_RESULTS_ROOT:-/data/crfm-helm-audit}"
EXPERIMENT_NAME="${EXPERIMENT_NAME:-e2e-phi_2-huggingface-philosophy}"

eval-audit-run --run 1 ./manifests/hf-manifest.yaml
eval-audit-index \
  --results-root "$RESULTS_ROOT" \
  --report-dpath "$STORE_ROOT/indexes"

eval-audit-analyze-experiment \
  --experiment-name "$EXPERIMENT_NAME" \
  --index-fpath "$STORE_ROOT/indexes/audit_results_index.csv"

eval-audit-build-summary \
  --experiment-name "$EXPERIMENT_NAME" \
  --index-fpath "$STORE_ROOT/indexes/audit_results_index.csv"


#!/bin/bash 
set -euo pipefail

# Variables
export INFER_STACK_CONFIG_DIR="./config/infer_stack"
export EVAL_AUDIT_SKIP_LOCAL_REPEAT=1
export EVAL_AUDIT_GROUP_STRIP=1

STORE_ROOT="${AUDIT_STORE_ROOT:-/data/crfm-helm-audit-store}"
RESULTS_ROOT="${AUDIT_RESULTS_ROOT:-/data/crfm-helm-audit}"
EXPERIMENT_NAME="${EXPERIMENT_NAME:-e2e-phi_2-vllm-philosophy-full}"

LITELLM_PORT="$(infer-stack env --key INFER_STACK_LITELLM_PORT)"
LITELLM_BASE_URL="${LITELLM_BASE_URL:-http://localhost:$LITELLM_PORT}"

#BUNDLE_ROOT="$STORE_ROOT/local-bundles/e2e-phi_2-vllm"
BUNDLE_ROOT=./bundles
LITELLM_MASTER_KEY="$(infer-stack env --key LITELLM_MASTER_KEY)"

# Set up vLLM
infer-stack switch \
  --profile phi2-single \
  --apply

infer-stack wait-ready

python -m eval_audit.integrations.vllm_service export-benchmark-bundle \
  --preset e2e-phi_2-vllm-philosophy \
  --bundle-root "$BUNDLE_ROOT" \
  --base-url "${LITELLM_BASE_URL}/v1" \
  --api-key-value "$LITELLM_MASTER_KEY"

# Run eval-audit pipeline
eval-audit-check-env
eval-audit-run --run 1 "$BUNDLE_ROOT/full_manifest.yaml"

eval-audit-index \
  --results-root "$RESULTS_ROOT" \
  --report-dpath "$STORE_ROOT/indexes"
eval-audit-analyze-experiment \
  --experiment-name "$EXPERIMENT_NAME" \
  --index-fpath "$STORE_ROOT/indexes/audit_results_index.csv"

eval-audit-build-summary \
  --experiment-name "$EXPERIMENT_NAME" \
  --index-fpath "$STORE_ROOT/indexes/audit_results_index.csv"

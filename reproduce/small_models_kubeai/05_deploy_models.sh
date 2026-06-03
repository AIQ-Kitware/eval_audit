#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$ROOT/reproduce/small_models_kubeai/_lib.sh"

SERVICE_ROOT="${INFER_STACK_ROOT:-$ROOT/submodules/infer_stack}"
KUBEAI_NAMESPACE="$(resolve_kubeai_namespace)"
PYTHON_BIN="${INFER_STACK_PYTHON:-python3}"
APPLY_KUBEAI_TONIGHT_PATCHES="${APPLY_KUBEAI_TONIGHT_PATCHES:-1}"

cd "$SERVICE_ROOT"

"$PYTHON_BIN" manage.py setup \
  --backend kubeai \
  --profile qwen2-5-7b-instruct-turbo-default \
  --namespace "$KUBEAI_NAMESPACE"

"$PYTHON_BIN" manage.py validate
"$PYTHON_BIN" manage.py deploy

# Apply Vicuna as an additional KubeAI Model. `kubectl apply` is additive here,
# so the earlier Qwen Model remains live on the cluster.
"$PYTHON_BIN" manage.py switch vicuna-7b-v1-3-no-chat-template --apply --namespace "$KUBEAI_NAMESPACE"

# Tonight's cluster expects the explicit `:1` resource profile form and benefits
# from eager scheduling rather than scale-from-zero.
if [[ "$APPLY_KUBEAI_TONIGHT_PATCHES" == "1" ]]; then
  echo "Applying explicit tonight patches to both KubeAI Model objects"
  patch_model_for_tonight "$KUBEAI_NAMESPACE" qwen2-5-7b-instruct-turbo-default qwen2-5-7b-instruct-turbo-default
  patch_model_for_tonight "$KUBEAI_NAMESPACE" vicuna-7b-v1-3-no-chat-template vicuna-7b-v1-3-no-chat-template
  kubectl -n "$KUBEAI_NAMESPACE" get model qwen2-5-7b-instruct-turbo-default vicuna-7b-v1-3-no-chat-template -o yaml
  echo "Exact live args after patching:"
  for model in qwen2-5-7b-instruct-turbo-default vicuna-7b-v1-3-no-chat-template; do
    echo "--- $model"
    kubectl -n "$KUBEAI_NAMESPACE" get model "$model" -o jsonpath='{range .spec.args[*]}{.}{"\n"}{end}'
    echo
  done
fi

# Restore the default active profile locally without disturbing the already
# applied KubeAI Models so future one-model commands stay unsurprising.
"$PYTHON_BIN" manage.py switch qwen2-5-7b-instruct-turbo-default --namespace "$KUBEAI_NAMESPACE"
"$PYTHON_BIN" manage.py status

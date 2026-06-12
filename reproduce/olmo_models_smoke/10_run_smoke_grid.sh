#!/usr/bin/env bash
# Run the smoke manifest for each of the six OLMo presets, sequentially.
#
# Per model: bring the vLLM service up via its infer-stack profile, wait for it
# to be ready, materialize the benchmark bundle from the preset, and run the
# SMOKE manifest (eval-audit-run --run=1). Models are served one at a time
# (switching the active profile tears down the previous one) because the grid
# spans a 1B-active MoE up to a 32B dense model and they will not co-host.
#
# Transport: LiteLLM gateway (openai-compatible). The OLMo presets in adapter.py
# declare access_kind: vllm-direct, so we override it here with
# `--access-kind openai-compatible` and hand export-benchmark-bundle the LiteLLM
# base-url + master key (mirrors dev/e2e-tests/e2e-phi_2-vllm-philosophy.sh).
#
# HuggingFace auth: _lib.sh exports HF_TOKEN / HUGGING_FACE_HUB_TOKEN (from the
# env or a cached `huggingface-cli login`) into the environment eval-audit-run
# inherits, so HELM can pull gated datasets — gpqa is the smoke entry for
# allenai/olmo-2-1124-7b-instruct. Run ./06_check_hf_auth.sh first to confirm.
#
# Default is fail-fast. Set OLMO_KEEP_GOING=1 to attempt every model and report
# which ones failed at the end instead of stopping on the first error.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

KEEP_GOING="${OLMO_KEEP_GOING:-0}"
failed=()

# Resolve the LiteLLM gateway endpoint + master key from infer-stack.
LITELLM_PORT="$(infer-stack env --key INFER_STACK_LITELLM_PORT)"
LITELLM_BASE_URL="${LITELLM_BASE_URL:-http://localhost:$LITELLM_PORT}"
LITELLM_MASTER_KEY="$(infer-stack env --key LITELLM_MASTER_KEY)"

run_one() {
  local target="$1"
  local preset profile bundle_root
  preset="$(olmo_preset "$target")"
  profile="$(olmo_profile "$target")"
  bundle_root="$(olmo_bundle_root "$target")"

  echo
  echo "==================================================================="
  echo "== ${preset}  (profile: ${profile})"
  echo "==================================================================="

  # 1. Bring the model up and wait for readiness.
  infer-stack switch --profile "$profile" --apply --yes
  infer-stack wait-ready

  # 2. Materialize the bundle (smoke + full manifests) from the preset, routing
  #    through LiteLLM (override the preset's vllm-direct access kind).
  "$PYTHON_BIN" -m eval_audit.integrations.infer_stack export-benchmark-bundle \
    --preset "$preset" \
    --bundle-root "$bundle_root" \
    --access-kind openai-compatible \
    --base-url "${LITELLM_BASE_URL}/v1" \
    --api-key-value "$LITELLM_MASTER_KEY"

  # 3. Run the smoke manifest.
  eval-audit-run --run=1 "$bundle_root/smoke_manifest.yaml"
}

for target in "${OLMO_TARGETS[@]}"; do
  if [[ "$KEEP_GOING" == "1" ]]; then
    if ! run_one "$target"; then
      echo "WARN: $(olmo_preset "$target") smoke run failed; continuing." >&2
      failed+=("$(olmo_preset "$target")")
    fi
  else
    run_one "$target"
  fi
done

if (( ${#failed[@]} > 0 )); then
  echo >&2
  echo "Completed with ${#failed[@]} failed model(s):" >&2
  printf '  - %s\n' "${failed[@]}" >&2
  exit 1
fi

echo
echo "OK: all ${#OLMO_TARGETS[@]} OLMo smoke runs completed."
echo "Next: ./20_index_local.sh"

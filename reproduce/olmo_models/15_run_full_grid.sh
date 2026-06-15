#!/usr/bin/env bash
# Run the FULL manifest for each of the six OLMo presets, sequentially.
#
# This is the heavy counterpart to 10_run_smoke_grid.sh: where the smoke grid
# runs 1-4 cheap entries per model as a preflight, the full grid runs every
# candidate run-entry for the preset (sourced from candidate_runs.json, up to
# max_eval_instances=1000). The full runs are what the downstream
# index -> compose -> summary steps (20/30/40) operate on.
#
# Per model: bring the vLLM service up via its infer-stack profile, wait for it
# to be ready, materialize the benchmark bundle from the preset, and run the
# FULL manifest (eval-audit-run --run=1). Models are served one at a time
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
# inherits, so HELM can pull gated datasets — the full run-entry sets include
# gpqa on the OLMo-2 / OLMoE instruct models. Run ./06_check_hf_auth.sh first.
#
# Default is fail-fast. Set OLMO_KEEP_GOING=1 to attempt every model and report
# which ones failed at the end instead of stopping on the first error.
#
# eval-audit-run schedules through kwdagger with skip_existing=1, so a model
# whose previous full run already wrote its DONE sentinel
# ($AUDIT_RESULTS_ROOT/audit-<preset>-full/helm/.../DONE) is silently skipped on
# a re-invocation. Set OLMO_FORCE_RERUN=1 to clear each model's prior result dir
# before running so the full manifest re-executes from scratch.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

KEEP_GOING="${OLMO_KEEP_GOING:-0}"
FORCE_RERUN="${OLMO_FORCE_RERUN:-0}"
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

  # 3. Optionally clear a prior run so kwdagger's skip_existing doesn't no-op
  #    this model. The full experiment_name is "audit-<preset>-full" and its
  #    results (incl. the DONE sentinel) live under $RESULTS_ROOT/<experiment>.
  if [[ "$FORCE_RERUN" == "1" ]]; then
    local experiment result_dpath
    experiment="$(olmo_experiment_full "$target")"
    result_dpath="$RESULTS_ROOT/$experiment"
    if [[ -d "$result_dpath" ]]; then
      echo "OLMO_FORCE_RERUN=1: clearing prior results at $result_dpath"
      rm -rf "$result_dpath"
    fi
  fi

  # 4. Run the full manifest.
  eval-audit-run --run=1 "$bundle_root/full_manifest.yaml"
}

for target in "${OLMO_TARGETS[@]}"; do
  if [[ "$KEEP_GOING" == "1" ]]; then
    if ! run_one "$target"; then
      echo "WARN: $(olmo_preset "$target") full run failed; continuing." >&2
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
echo "OK: all ${#OLMO_TARGETS[@]} OLMo full runs completed."
echo "Next: ./20_index_local.sh"

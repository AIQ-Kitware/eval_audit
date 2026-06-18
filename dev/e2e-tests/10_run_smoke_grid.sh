#!/usr/bin/env bash
# Run the SMOKE manifest for each of the three phi-2 e2e scenarios, sequentially.
#
# Per scenario, the path depends on its transport:
#   * vllm — bring phi-2 up on vLLM via its infer-stack profile, wait for
#     readiness, materialize the benchmark bundle from the preset (routed through
#     the LiteLLM gateway), and run the SMOKE manifest. The phi-2 presets in
#     adapter.py already declare access_kind: openai-compatible, so the export
#     passes only the LiteLLM base-url + master key (no --access-kind override;
#     unlike reproduce/olmo_models, whose presets declare vllm-direct).
#   * hf — no infer-stack: HELM loads microsoft/phi-2 directly from HuggingFace
#     and runs the checked-in smoke manifest under manifests/.
#
# The two vLLM scenarios share the phi2-single profile, so switching between them
# is a no-op re-apply; the order still mirrors the full grid.
#
# Default is fail-fast. Set E2E_KEEP_GOING=1 to attempt every scenario and report
# which ones failed at the end instead of stopping on the first error.
#
# eval-audit-run schedules through kwdagger with skip_existing=1, so a scenario
# whose previous smoke run already wrote its DONE sentinel
# ($AUDIT_RESULTS_ROOT/<experiment>/helm/.../DONE) would be silently skipped on a
# re-invocation. Because the smoke grid is a cheap preflight whose whole job is to
# re-validate the recipe on every invocation, it FORCE-RERUNS by default (clears
# each scenario's prior result dir before running). Set E2E_FORCE_RERUN=0 to opt
# back into kwdagger's skip_existing no-op. (15_run_full_grid.sh defaults the
# other way.)
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

KEEP_GOING="${E2E_KEEP_GOING:-0}"
FORCE_RERUN="${E2E_FORCE_RERUN:-1}"
failed=()

# Start from a clean GPU: tear down any vLLM stack left up by a prior run so the
# hf scenario (first in the grid) has the full GPU to load phi-2 onto. Best-effort
# — `infer-stack down` (re-render + `docker compose down`) is a no-op when nothing
# is up, and a clean host shouldn't abort the grid just for having nothing to tear
# down.
echo "Spinning down any vLLM stack to free the GPU (infer-stack down)…"
infer-stack down || echo "WARN: 'infer-stack down' returned nonzero (nothing to tear down?); continuing." >&2

# Resolve the LiteLLM gateway endpoint + master key from infer-stack (used by the
# vLLM scenarios; harmless to resolve up front).
LITELLM_PORT="$(infer-stack env --key INFER_STACK_LITELLM_PORT)"
LITELLM_BASE_URL="${LITELLM_BASE_URL:-http://localhost:$LITELLM_PORT}"
LITELLM_MASTER_KEY="$(infer-stack env --key LITELLM_MASTER_KEY)"

run_one() {
  local target="$1"
  local name transport experiment serving bundle_root manifest
  name="$(e2e_name "$target")"
  transport="$(e2e_transport "$target")"
  experiment="$(e2e_experiment_smoke "$target")"

  echo
  echo "==================================================================="
  echo "== ${name}  (transport: ${transport})"
  echo "==================================================================="

  case "$transport" in
    vllm)
      serving="$(e2e_serving "$target")"
      bundle_root="$(e2e_bundle_root "$target")"
      # 1. Bring phi-2 up and wait for readiness.
      infer-stack switch --profile "$serving" --apply --yes
      infer-stack wait-ready
      # 2. Materialize the bundle (smoke + full manifests) from the preset,
      #    routing through the LiteLLM gateway.
      "$PYTHON_BIN" -m eval_audit.integrations.infer_stack export-benchmark-bundle \
        --preset "$name" \
        --bundle-root "$bundle_root" \
        --base-url "${LITELLM_BASE_URL}/v1" \
        --api-key-value "$LITELLM_MASTER_KEY"
      manifest="$bundle_root/smoke_manifest.yaml"
      ;;
    hf)
      # HELM loads microsoft/phi-2 directly from HuggingFace; no infer-stack.
      manifest="$(e2e_hf_manifest "$target" smoke)"
      ;;
    *)
      echo "FAIL: unknown transport '$transport' for scenario '$name'" >&2
      return 1
      ;;
  esac

  # 3. Optionally clear a prior run so kwdagger's skip_existing doesn't no-op it.
  if [[ "$FORCE_RERUN" == "1" ]]; then
    e2e_clear_results "$experiment"
  fi

  # 4. Run the smoke manifest.
  eval-audit-run --run=1 "$manifest"
}

for target in "${E2E_TARGETS[@]}"; do
  if [[ "$KEEP_GOING" == "1" ]]; then
    if ! run_one "$target"; then
      echo "WARN: $(e2e_name "$target") smoke run failed; continuing." >&2
      failed+=("$(e2e_name "$target")")
    fi
  else
    run_one "$target"
  fi
done

if (( ${#failed[@]} > 0 )); then
  echo >&2
  echo "Completed with ${#failed[@]} failed scenario(s):" >&2
  printf '  - %s\n' "${failed[@]}" >&2
  exit 1
fi

echo
echo "OK: all ${#E2E_TARGETS[@]} phi-2 smoke runs completed."
echo "Next: ./15_run_full_grid.sh"

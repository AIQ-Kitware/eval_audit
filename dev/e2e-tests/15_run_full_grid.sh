#!/usr/bin/env bash
# Run the FULL manifest for each of the three phi-2 e2e scenarios, sequentially.
#
# This is the counterpart to 10_run_smoke_grid.sh: where the smoke grid runs a
# handful of instances (max_eval_instances=5) as a preflight, the full grid runs
# the full batch (max_eval_instances=1000). The full runs are what the downstream
# index -> compose -> summary steps (20/30/40) operate on.
#
# Per scenario, the path depends on its transport:
#   * vllm — bring phi-2 up on vLLM via its infer-stack profile, wait for
#     readiness, materialize the benchmark bundle from the preset (routed through
#     the LiteLLM gateway), and run the FULL manifest. The phi-2 presets in
#     adapter.py already declare access_kind: openai-compatible, so the export
#     passes only the LiteLLM base-url + master key (no --access-kind override).
#   * hf — no infer-stack: HELM loads microsoft/phi-2 directly from HuggingFace
#     and runs the checked-in full manifest under manifests/.
#
# Default is fail-fast. Set E2E_KEEP_GOING=1 to attempt every scenario and report
# which ones failed at the end instead of stopping on the first error.
#
# eval-audit-run schedules through kwdagger with skip_existing=1, so a scenario
# whose previous full run already wrote its DONE sentinel
# ($AUDIT_RESULTS_ROOT/<experiment>/helm/.../DONE) is silently skipped on a
# re-invocation. Set E2E_FORCE_RERUN=1 to clear each scenario's prior result dir
# before running so the full manifest re-executes from scratch. Unlike the smoke
# grid (10_run_smoke_grid.sh), which force-reruns by default, the full grid keeps
# force-rerun opt-in (matching reproduce/olmo_models).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

KEEP_GOING="${E2E_KEEP_GOING:-0}"
FORCE_RERUN="${E2E_FORCE_RERUN:-0}"
failed=()

# Start from a clean GPU: release any standing leases + evict idle models left up
# by a prior run so the hf scenario (first in the grid) has the full GPU to load
# phi-2 onto. Best-effort — `release --all --evict` is a no-op when nothing is
# leased, and a clean host shouldn't abort the grid just for having nothing to
# free. (The leasing CLI replaced the old `infer-stack down`.)
echo "Releasing leases + evicting idle models to free the GPU (infer-stack release --all --evict)…"
infer-stack release --all --evict || echo "WARN: 'infer-stack release --all --evict' returned nonzero (nothing to free?); continuing." >&2

# The LiteLLM gateway host port is a fixed default in the new CLI (14042;
# override via LITELLM_PORT). The master key lives in the managed .env, which
# does not exist until the first `acquire` brings the gateway up — so it is read
# per-scenario inside run_one (after serve), NOT up front.
LITELLM_PORT="${LITELLM_PORT:-14042}"
LITELLM_BASE_URL="${LITELLM_BASE_URL:-http://localhost:$LITELLM_PORT}"

run_one() {
  local target="$1"
  local name transport experiment endpoint bundle_root manifest master_key
  name="$(e2e_name "$target")"
  transport="$(e2e_transport "$target")"
  experiment="$(e2e_experiment_full "$target")"

  echo
  echo "==================================================================="
  echo "== ${name}  (transport: ${transport})"
  echo "==================================================================="

  case "$transport" in
    vllm)
      endpoint="$(e2e_serving "$target")"
      bundle_root="$(e2e_bundle_root "$target")"
      # 1. Bring phi-2 up as a standing lease and wait for readiness. `acquire`
      #    renders + applies + waits; the explicit `wait` is belt-and-suspenders.
      infer-stack acquire "$endpoint" --yes
      infer-stack wait "$endpoint"
      # acquire writes the managed LiteLLM master key into the .env on first
      # bring-up; read it now (positional `env KEY`) for the export below.
      master_key="$(infer-stack env LITELLM_MASTER_KEY)"
      # 2. Materialize the bundle (smoke + full manifests) from the preset,
      #    routing through the LiteLLM gateway.
      "$PYTHON_BIN" -m eval_audit.integrations.infer_stack export-benchmark-bundle \
        --preset "$name" \
        --bundle-root "$bundle_root" \
        --base-url "${LITELLM_BASE_URL}/v1" \
        --api-key-value "$master_key"
      manifest="$bundle_root/full_manifest.yaml"
      ;;
    hf)
      # HELM loads microsoft/phi-2 directly from HuggingFace; no infer-stack.
      manifest="$(e2e_hf_manifest "$target" full)"
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

  # 4. Run the full manifest.
  eval-audit-run --run=1 "$manifest"
}

for target in "${E2E_TARGETS[@]}"; do
  if [[ "$KEEP_GOING" == "1" ]]; then
    if ! run_one "$target"; then
      echo "WARN: $(e2e_name "$target") full run failed; continuing." >&2
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
echo "OK: all ${#E2E_TARGETS[@]} phi-2 full runs completed."
echo "Next: ./20_index_local.sh"

#!/usr/bin/env bash
# Run the FULL manifest for each of the three phi-2 e2e scenarios, sequentially.
#
# This is the counterpart to 10_run_smoke_grid.sh: where the smoke grid runs a
# handful of instances (max_eval_instances=5) as a preflight, the full grid runs
# the full batch (max_eval_instances=1000). The full runs are what the downstream
# index -> compose -> summary steps (20/30/40) operate on.
#
# Every scenario runs HELM inside the pinned eval-audit-helm-runner image
# (containerization is mandatory — the grid always passes --container-image). The
# ONLY difference between the transports is leasing (and the GPU config that
# follows from it):
#   * vllm — phi-2 is SERVED on the host (vLLM behind LiteLLM). Each scheduled
#     HELM run self-acquires phi-2's GPU lease (`acquire --queue` before, release
#     after) via `eval-audit-run --lease`; the bundle's baked-in
#     lease_endpoint/ttl/catalog name the endpoint. The in-container client is an
#     HTTP caller (container_gpus: none) reaching the host via --network host. No
#     per-scenario pre-serve and no blunt `release --all` (which tore down the
#     shared docker-compose project, killing co-tenants' models).
#   * hf — no infer-stack: HELM loads microsoft/phi-2 IN-PROCESS from HuggingFace,
#     inside its container, on a real GPU (no --lease => no container_gpus: none).
#     It cannot lease (no served endpoint), so it runs first, while the GPU is
#     clear (a started vLLM model would hold the memory and OOM the HF load).
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

# The LiteLLM gateway host port is a fixed default in the new CLI (14042;
# override via LITELLM_PORT). The master key lives in the managed .env, read once
# at bootstrap below.
LITELLM_PORT="${LITELLM_PORT:-14042}"
LITELLM_BASE_URL="${LITELLM_BASE_URL:-http://localhost:$LITELLM_PORT}"

# Reclaim any lease a prior hard-killed run leaked (TTL-expired), freeing its GPU
# so the hf scenario (first) loads phi-2 onto a clear card. `gc` is scoped to
# leaked/expired demand in THIS data_dir's ledger — it never tears down another
# user's active leases (unlike the old `release --all --evict`).
echo "Reclaiming any leaked leases before start (infer-stack gc)…"
infer-stack gc --yes || echo "WARN: 'infer-stack gc' returned nonzero; continuing." >&2

# Bootstrap the no-blip gateway ONCE so the vLLM scenarios' export-benchmark-bundle
# can read the managed LiteLLM master key, then release just the bootstrap model
# (scoped by env-file, NOT `--all`). The gateway is a CPU container (no GPU) and
# stays up; the bootstrap model is evicted so the hf scenario keeps the GPU clear.
bootstrap_ep=""
for _t in "${E2E_TARGETS[@]}"; do
  if [[ "$(e2e_transport "$_t")" == "vllm" ]]; then bootstrap_ep="$(e2e_serving "$_t")"; break; fi
done
LEASE_MASTER_KEY=""
if [[ -n "$bootstrap_ep" ]]; then
  bootstrap_env="$(mktemp)"
  echo "Bootstrapping the gateway via ${bootstrap_ep} to read the LiteLLM master key…"
  infer-stack acquire "$bootstrap_ep" --no-wait --yes --env-file "$bootstrap_env"
  LEASE_MASTER_KEY="$(infer-stack env LITELLM_MASTER_KEY)"
  infer-stack release --env-file "$bootstrap_env" --evict \
    || echo "WARN: bootstrap 'release --env-file --evict' returned nonzero; continuing." >&2
  rm -f "$bootstrap_env"
fi

run_one() {
  local target="$1"
  local name transport experiment endpoint bundle_root manifest
  name="$(e2e_name "$target")"
  transport="$(e2e_transport "$target")"
  experiment="$(e2e_experiment_full "$target")"

  echo
  echo "==================================================================="
  echo "== ${name}  (transport: ${transport})"
  echo "==================================================================="

  # Containerization is mandatory for every scenario; the image is supplied here.
  local run_args=(--run=1 --container-image "$E2E_CONTAINER_IMAGE")
  case "$transport" in
    vllm)
      endpoint="$(e2e_serving "$target")"
      bundle_root="$(e2e_bundle_root "$target")"
      # 1. Materialize the bundle (smoke + full manifests) from the preset,
      #    routing through the LiteLLM gateway (master key read once at bootstrap).
      #    No pre-serve: the scheduled run self-acquires "$endpoint" via --lease.
      "$PYTHON_BIN" -m eval_audit.integrations.infer_stack export-benchmark-bundle \
        --preset "$name" \
        --bundle-root "$bundle_root" \
        --base-url "${LITELLM_BASE_URL}/v1" \
        --api-key-value "$LEASE_MASTER_KEY"
      manifest="$bundle_root/full_manifest.yaml"
      # 2. Bracket the run with phi-2's GPU lease (served on the host; the
      #    in-container client is an HTTP caller with container_gpus: none).
      run_args+=("$manifest" --lease)
      ;;
    hf)
      # HELM loads microsoft/phi-2 IN-PROCESS from HuggingFace; no infer-stack, no
      # lease (so the container gets a real GPU). Runs first while the GPU is clear.
      manifest="$(e2e_hf_manifest "$target" full)"
      run_args+=("$manifest")
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

  # 4. Run the manifest (leased for vLLM, bare for hf).
  eval-audit-run "${run_args[@]}"
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

# Final backstop: reclaim any lease a hard-killed vLLM run leaked (its release
# teardown never ran). Safe no-op when nothing leaked; runs even on partial
# failure.
echo "Reclaiming any leaked leases (infer-stack gc)…"
infer-stack gc --yes || echo "WARN: 'infer-stack gc' returned nonzero; continuing." >&2

if (( ${#failed[@]} > 0 )); then
  echo >&2
  echo "Completed with ${#failed[@]} failed scenario(s):" >&2
  printf '  - %s\n' "${failed[@]}" >&2
  exit 1
fi

echo
echo "OK: all ${#E2E_TARGETS[@]} phi-2 full runs completed."
echo "Next: ./20_index_local.sh"

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
#
# eval-audit-run schedules through kwdagger with skip_existing=1, so a model
# whose previous smoke run already wrote its DONE sentinel
# ($AUDIT_RESULTS_ROOT/audit-<preset>-smoke/helm/.../DONE) would be silently
# skipped on a re-invocation. Because the smoke grid is a cheap preflight whose
# whole job is to re-validate the recipe on every invocation, it FORCE-RERUNS by
# default (clears each model's prior result dir before running). Set
# OLMO_FORCE_RERUN=0 to opt back into kwdagger's skip_existing no-op. (The full
# grid in 15_run_full_grid.sh defaults the other way — expensive, opt-in only.)
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

KEEP_GOING="${OLMO_KEEP_GOING:-0}"
FORCE_RERUN="${OLMO_FORCE_RERUN:-1}"
failed=()

# The LiteLLM gateway host port is a fixed default in the new CLI (14042;
# override via LITELLM_PORT). The master key lives in the managed .env, which
# does not exist until the first `serve` brings the gateway up — so it is read
# per-model inside run_one (after serve), NOT up front.
LITELLM_PORT="${LITELLM_PORT:-14042}"
LITELLM_BASE_URL="${LITELLM_BASE_URL:-http://localhost:$LITELLM_PORT}"

run_one() {
  local target="$1"
  local preset endpoint bundle_root master_key
  preset="$(olmo_preset "$target")"
  endpoint="$(olmo_profile "$target")"
  bundle_root="$(olmo_bundle_root "$target")"

  echo
  echo "==================================================================="
  echo "== ${preset}  (endpoint: ${endpoint})"
  echo "==================================================================="

  # 1. C-1: serve/acquire ACCUMULATE (demand is ref-counted) — unlike the old
  #    `switch`, which replaced. The six models span a 1B-active MoE up to a 32B
  #    dense model and will not co-host, so release the previous model's GPUs
  #    before standing up the next or they pile up and OOM. release --all --evict
  #    frees idle deployments; the standing LiteLLM gateway stays up.
  infer-stack release --all --evict || echo "WARN: 'infer-stack release --all --evict' returned nonzero (nothing to free?); continuing." >&2

  # 2. Bring this model up as a standing lease and wait for readiness. `serve`
  #    renders + applies + waits; the explicit `wait` is belt-and-suspenders.
  infer-stack serve "$endpoint" --yes
  infer-stack wait "$endpoint"

  # serve writes the managed LiteLLM master key into the .env on first bring-up;
  # read it now (positional `env KEY`) for the export below.
  master_key="$(infer-stack env LITELLM_MASTER_KEY)"

  # 3. Materialize the bundle (smoke + full manifests) from the preset, routing
  #    through LiteLLM (override the preset's vllm-direct access kind).
  "$PYTHON_BIN" -m eval_audit.integrations.infer_stack export-benchmark-bundle \
    --preset "$preset" \
    --bundle-root "$bundle_root" \
    --access-kind openai-compatible \
    --base-url "${LITELLM_BASE_URL}/v1" \
    --api-key-value "$master_key"

  # 4. Optionally clear a prior run so kwdagger's skip_existing doesn't no-op
  #    this model. The smoke experiment_name is "audit-<preset>-smoke" and its
  #    results (incl. the DONE sentinel) live under $RESULTS_ROOT/<experiment>.
  if [[ "$FORCE_RERUN" == "1" ]]; then
    local experiment result_dpath
    experiment="$(olmo_experiment_smoke "$target")"
    result_dpath="$RESULTS_ROOT/$experiment"
    if [[ -d "$result_dpath" ]]; then
      echo "OLMO_FORCE_RERUN=1: clearing prior results at $result_dpath"
      rm -rf "$result_dpath"
    fi
  fi

  # 5. Run the smoke manifest. With OLMO_CONTAINER=1 (default) append
  #    --container-image to route HELM through the pinned container ("docker
  #    pipeline"); with OLMO_CONTAINER=0 omit it for the host-venv fallback
  #    (the presets' container fields stay inert). Built as an args array, like
  #    the export call above.
  local run_args=(--run=1 "$bundle_root/smoke_manifest.yaml")
  if [[ "$OLMO_CONTAINER" != "0" ]]; then
    run_args+=(--container-image "$OLMO_CONTAINER_IMAGE")
  fi
  eval-audit-run "${run_args[@]}"
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
echo "Next: ./15_run_full_grid.sh"

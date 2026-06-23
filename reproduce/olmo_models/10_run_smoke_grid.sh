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
# OLMO_LEASE=1 switches to the high-throughput per-run-lease fan-out (handoff
# §13): instead of pre-serving each model serially (release/acquire/wait), each
# HELM run self-acquires its model's GPU lease (`acquire --queue`, queue-and-wait
# when the fleet is busy) and releases it after — so kwdagger fans the runs out
# and infer-stack's admission queue serializes models that can't co-host. The
# catalog's `reclaim: stop` frees a model's GPU on its last release, and a final
# `infer-stack gc` reclaims any lease a hard-killed job leaked. Requires
# OLMO_CONTAINER=1 (the lease bracket lives on the containerized client, which
# runs with NO GPU since infer-stack owns them). Default OLMO_LEASE=0 keeps the
# known-good per-model serve loop. NOTE: the lease fan-out is rendering-tested
# only; its end-to-end behavior is a docker/GPU-box gate-check (see the handoff).
LEASE="${OLMO_LEASE:-0}"
if [[ "$LEASE" == "1" && "$OLMO_CONTAINER" == "0" ]]; then
  echo "FAIL: OLMO_LEASE=1 requires OLMO_CONTAINER=1 (per-run --lease needs the containerized client)." >&2
  exit 2
fi
failed=()

# The LiteLLM gateway host port is a fixed default in the new CLI (14042;
# override via LITELLM_PORT). The master key lives in the managed .env, which
# does not exist until the first `acquire` brings the gateway up — so it is read
# per-model inside run_one (after serve), NOT up front.
LITELLM_PORT="${LITELLM_PORT:-14042}"
LITELLM_BASE_URL="${LITELLM_BASE_URL:-http://localhost:$LITELLM_PORT}"

# OLMO_LEASE bootstrap: bring the no-blip gateway up ONCE so export-benchmark-bundle
# can read the master key, then release so per-run leasing owns every model's
# lifecycle. `acquire --no-wait` renders the gateway + writes the key without
# blocking on the model load; `release --all --evict` frees the half-loaded
# bootstrap model (reclaim: stop frees its GPU) while the standing LiteLLM gateway
# stays up (no-blip). The key persists in the managed .env for every bundle.
LEASE_MASTER_KEY=""
if [[ "$LEASE" == "1" ]]; then
  bootstrap_ep="$(olmo_profile "${OLMO_TARGETS[0]}")"
  echo "OLMO_LEASE=1: bootstrapping the gateway via ${bootstrap_ep} to read the master key…"
  infer-stack acquire "$bootstrap_ep" --no-wait --yes
  LEASE_MASTER_KEY="$(infer-stack env LITELLM_MASTER_KEY)"
  infer-stack release --all --evict || echo "WARN: bootstrap 'release --all --evict' returned nonzero; continuing." >&2
fi

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

  if [[ "$LEASE" == "1" ]]; then
    # Per-run-lease fan-out: do NOT pre-serve. Each scheduled HELM run
    # self-acquires this endpoint (`acquire --queue`); ref-counting coalesces a
    # model's run_entries onto one deployment, and the queue serializes models
    # that can't co-host. The master key was read once at bootstrap.
    master_key="$LEASE_MASTER_KEY"
  else
    # 1. C-1: acquire ACCUMULATES (demand is ref-counted) — unlike the old
    #    `switch`, which replaced. The six models span a 1B-active MoE up to a 32B
    #    dense model and will not co-host, so release the previous model's GPUs
    #    before standing up the next or they pile up and OOM. release --all --evict
    #    frees idle deployments; the standing LiteLLM gateway stays up.
    infer-stack release --all --evict || echo "WARN: 'infer-stack release --all --evict' returned nonzero (nothing to free?); continuing." >&2

    # 2. Bring this model up as a standing lease and wait for readiness. `acquire`
    #    renders + applies + waits; the explicit `wait` is belt-and-suspenders.
    infer-stack acquire "$endpoint" --yes
    infer-stack wait "$endpoint"

    # acquire writes the managed LiteLLM master key into the .env on first bring-up;
    # read it now (positional `env KEY`) for the export below.
    master_key="$(infer-stack env LITELLM_MASTER_KEY)"
  fi

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
  # OLMO_LEASE=1: bracket each scheduled run with its model's GPU lease. The
  # bundle's baked-in lease facts (lease_endpoint/ttl/catalog) tell eval-audit-run
  # which endpoint to acquire; the client runs with no GPU (infer-stack owns them).
  if [[ "$LEASE" == "1" ]]; then
    run_args+=(--lease)
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

# Final backstop (OLMO_LEASE=1): reclaim any lease a hard-killed job leaked (its
# `release` teardown never ran), tearing down the stop-policy deployment and
# freeing its GPU. The per-run admission queue already sweeps expired leases
# while waiting, so this is the last-job sweep; run it even on partial failure.
if [[ "$LEASE" == "1" ]]; then
  echo "Reclaiming any leaked leases (infer-stack gc)…"
  infer-stack gc --yes || echo "WARN: 'infer-stack gc' returned nonzero; continuing." >&2
fi

if (( ${#failed[@]} > 0 )); then
  echo >&2
  echo "Completed with ${#failed[@]} failed model(s):" >&2
  printf '  - %s\n' "${failed[@]}" >&2
  exit 1
fi

echo
echo "OK: all ${#OLMO_TARGETS[@]} OLMo smoke runs completed."
echo "Next: ./15_run_full_grid.sh"

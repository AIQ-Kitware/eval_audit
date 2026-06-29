#!/usr/bin/env bash
# Run the smoke manifest for each of the six OLMo presets via per-run GPU leasing.
#
# High-throughput per-run-lease fan-out (handoff §13): each scheduled HELM run
# self-acquires its model's GPU lease (`acquire --queue`, queue-and-wait when the
# fleet is busy) and releases it after, so kwdagger fans the runs out and
# infer-stack's admission queue serializes the models that can't co-host (the grid
# spans a 1B-active MoE up to a 32B dense model). The catalog's `reclaim: stop`
# frees a model's GPU on its last release; `infer-stack gc` reclaims any lease a
# hard-killed job leaked. There is no per-model serial serve loop and no blunt
# `release --all --evict` (which tore down the shared docker-compose project,
# killing co-tenants' models) — only the scoped, leaked-lease `gc`.
#
# Containerization is mandatory (the grid always passes --container-image
# "$OLMO_CONTAINER_IMAGE"), and leasing is the ORTHOGONAL axis
# (eval_audit.pipelines.lease_bracket): the lease acquires the model server's GPU,
# while the container pins where the HELM *client* runs. The client is just an
# HTTP caller to the served LiteLLM endpoint and uses no GPU (container_gpus:
# none). See docs/container-execution.md.
#
# Transport: LiteLLM gateway (openai-compatible). The OLMo presets in adapter.py
# declare access_kind: vllm-direct, so we override it here with
# `--access-kind openai-compatible` and hand export-benchmark-bundle the LiteLLM
# base-url + master key (mirrors dev/e2e-tests/).
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
# override via LITELLM_PORT). The master key lives in the managed .env, read once
# at bootstrap below.
LITELLM_PORT="${LITELLM_PORT:-14042}"
LITELLM_BASE_URL="${LITELLM_BASE_URL:-http://localhost:$LITELLM_PORT}"

# Reclaim any lease a prior hard-killed run leaked (TTL-expired) before we start,
# freeing its GPU. `gc` is scoped to leaked/expired demand in THIS data_dir's
# ledger — it never touches another user's active leases (unlike the old
# `release --all --evict`, which tore down the shared docker-compose project).
echo "Reclaiming any leaked leases before start (infer-stack gc)…"
infer-stack gc --yes || echo "WARN: 'infer-stack gc' returned nonzero; continuing." >&2

# Bootstrap the no-blip gateway ONCE so export-benchmark-bundle can read the
# managed LiteLLM master key, then release just the bootstrap model — scoped by
# env-file, NOT `--all` — so per-run leasing owns every model's lifecycle.
# `acquire --no-wait` renders the gateway + writes the key without blocking on the
# model load; the standing LiteLLM gateway stays up (no-blip) and the key persists
# in the managed .env for every bundle.
bootstrap_ep="$(olmo_profile "${OLMO_TARGETS[0]}")"
bootstrap_env="$(mktemp)"
echo "Bootstrapping the gateway via ${bootstrap_ep} to read the LiteLLM master key…"
infer-stack acquire "$bootstrap_ep" --no-wait --yes --env-file "$bootstrap_env"
LEASE_MASTER_KEY="$(infer-stack env LITELLM_MASTER_KEY)"
infer-stack release --env-file "$bootstrap_env" --evict --yes \
  || echo "WARN: bootstrap 'release --env-file --evict' returned nonzero; continuing." >&2
rm -f "$bootstrap_env"

run_one() {
  local target="$1"
  local preset endpoint bundle_root
  preset="$(olmo_preset "$target")"
  endpoint="$(olmo_profile "$target")"
  bundle_root="$(olmo_bundle_root "$target")"

  echo
  echo "==================================================================="
  echo "== ${preset}  (endpoint: ${endpoint})"
  echo "==================================================================="

  # 1. Materialize the bundle (smoke + full manifests) from the preset, routing
  #    through LiteLLM (override the preset's vllm-direct access kind). No
  #    pre-serve: each scheduled HELM run self-acquires "$endpoint" via --lease
  #    below; ref-counting coalesces a model's run_entries onto one deployment and
  #    the admission queue serializes models that can't co-host.
  #
  #    --from-spec is UNCONDITIONAL for OLMo (no e2e-style `e2e_uses_from_spec`
  #    carve-out): every OLMo preset is comparable — there is no temperature
  #    negative control whose deviation faithful replay would erase. The exporter
  #    reads each preset's `precomputed_root` + emits `from_run_spec: true` and the
  #    native `vllm/allenai-<model>` rewrite target (migration plan Change 2/3); the
  #    bridge then selects the replay pipeline from `manifest['from_run_spec']`, so
  #    the `eval-audit-run` line below is unchanged.
  "$PYTHON_BIN" -m eval_audit.integrations.infer_stack export-benchmark-bundle \
    --preset "$preset" \
    --bundle-root "$bundle_root" \
    --access-kind openai-compatible \
    --base-url "${LITELLM_BASE_URL}/v1" \
    --api-key-value "$LEASE_MASTER_KEY" \
    --from-spec

  # 2. Optionally clear a prior run so kwdagger's skip_existing doesn't no-op this
  #    model. The smoke experiment_name is "audit-<preset>-smoke" and its results
  #    (incl. the DONE sentinel) live under $RESULTS_ROOT/<experiment>.
  if [[ "$FORCE_RERUN" == "1" ]]; then
    local experiment result_dpath
    experiment="$(olmo_experiment_smoke "$target")"
    result_dpath="$RESULTS_ROOT/$experiment"
    if [[ -d "$result_dpath" ]]; then
      echo "OLMO_FORCE_RERUN=1: clearing prior results at $result_dpath"
      rm -rf "$result_dpath"
    fi
  fi

  # 3. Run the smoke manifest. Containerization is mandatory (the pinned image
  #    pins the software env), and each scheduled run is bracketed with its
  #    model's GPU lease (--lease; the bundle's baked-in lease_endpoint/ttl/catalog
  #    tell eval-audit-run which endpoint to acquire). Container and lease are
  #    orthogonal: the image says where the HELM client runs, the lease acquires
  #    the served model's GPU (client runs with container_gpus: none).
  eval-audit-run --run=1 "$bundle_root/smoke_manifest.yaml" \
    --container-image "$OLMO_CONTAINER_IMAGE" --lease
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

# Final backstop: reclaim any lease a hard-killed job leaked (its `release`
# teardown never ran), tearing down the stop-policy deployment and freeing its
# GPU. The per-run admission queue already sweeps expired leases while waiting, so
# this is the last-job sweep; run it even on partial failure.
echo "Reclaiming any leaked leases (infer-stack gc)…"
infer-stack gc --yes || echo "WARN: 'infer-stack gc' returned nonzero; continuing." >&2

if (( ${#failed[@]} > 0 )); then
  echo >&2
  echo "Completed with ${#failed[@]} failed model(s):" >&2
  printf '  - %s\n' "${failed[@]}" >&2
  exit 1
fi

echo
echo "OK: all ${#OLMO_TARGETS[@]} OLMo smoke runs completed."
echo "Next: ./15_run_full_grid.sh"

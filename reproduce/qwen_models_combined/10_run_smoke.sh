#!/usr/bin/env bash
# Smoke preflight for the COMBINED fan-out bundle: gc -> gateway bootstrap ->
# export (--from-spec --freeze-rel-paths) -> run the smoke_manifest with
# `--lease --tmux-workers N`. One schedule; the eight models fan out across GPUs.
#
# Rather than looping over per-model presets serially, this exports ONE
# multi-deployment bundle and lets cmd_queue drive N concurrent leased runs
# (QWEN_TMUX_WORKERS). Each scheduled HELM run self-acquires its model's GPU lease
# (`acquire --queue`, queue-and-wait when busy); infer-stack co-hosts what fits on
# INFER_STACK_ALLOWED_GPUS and serializes the rest (the 72Bs tp=2 / 110B tp=4 can't
# co-host). The catalog's `reclaim: stop` frees a model's GPU on last release;
# `infer-stack gc` reclaims any leaked lease.
#
# The smoke manifest carries 1–2 entries per model (all eight endpoints exercised,
# incl. the ifeval/langdetect container canary on the turbo models), so this is a
# cheap end-to-end exercise of the fan-out path before the full run. Force-rerun is
# ON by default (cheap preflight); set QWEN_FORCE_RERUN=0 to reuse a prior smoke.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

FORCE_RERUN="${QWEN_FORCE_RERUN:-1}"
LITELLM_PORT="${LITELLM_PORT:-14042}"
LITELLM_BASE_URL="${LITELLM_BASE_URL:-http://localhost:$LITELLM_PORT}"

# Reclaim any lease a prior hard-killed run leaked (scoped to THIS data_dir's
# ledger — never touches another user's active leases).
echo "Reclaiming any leaked leases before start (infer-stack gc)…"
infer-stack gc --yes || echo "WARN: 'infer-stack gc' returned nonzero; continuing." >&2

# Bootstrap the no-blip gateway ONCE so export-benchmark-bundle can read the
# managed LiteLLM master key, then release just the bootstrap model (scoped by
# env-file, NOT --all) so per-run leasing owns every model's lifecycle.
bootstrap_ep="${QWEN_COMBINED_ENDPOINTS[0]}"
bootstrap_env="$(mktemp)"
echo "Bootstrapping the gateway via ${bootstrap_ep} to read the LiteLLM master key…"
infer-stack acquire "$bootstrap_ep" --no-wait --yes --env-file "$bootstrap_env"
LEASE_MASTER_KEY="$(infer-stack env LITELLM_MASTER_KEY)"
infer-stack release --env-file "$bootstrap_env" --evict --yes \
  || echo "WARN: bootstrap 'release --env-file --evict' returned nonzero; continuing." >&2
rm -f "$bootstrap_env"

# Export the combined bundle. --freeze-rel-paths is MANDATORY for a multi-
# deployment bundle: it pins each run's official rel-path + per-run rewrite target
# + lease endpoint. Route through LiteLLM (override the preset's declared vllm-direct).
echo
echo "== exporting the combined bundle (exact-path freeze) =="
"$PYTHON_BIN" -m eval_audit.integrations.infer_stack export-benchmark-bundle \
  --preset "$QWEN_COMBINED_PRESET" \
  --bundle-root "$QWEN_COMBINED_BUNDLE_ROOT" \
  --access-kind openai-compatible \
  --base-url "${LITELLM_BASE_URL}/v1" \
  --api-key-value "$LEASE_MASTER_KEY" \
  --from-spec --freeze-rel-paths

# Optionally clear the prior smoke result so kwdagger's skip_existing doesn't no-op
# this run. The smoke experiment_name is "audit-qwen-combined-smoke".
if [[ "$FORCE_RERUN" == "1" ]]; then
  result_dpath="$RESULTS_ROOT/$QWEN_COMBINED_EXPERIMENT_SMOKE"
  if [[ -d "$result_dpath" ]]; then
    echo "QWEN_FORCE_RERUN=1: clearing prior results at $result_dpath"
    rm -rf "$result_dpath"
  fi
fi

# Run the smoke manifest, fanning out N concurrent leased runs across GPUs.
# Containerization is mandatory; leasing is the orthogonal axis (the container pins
# where the HELM client runs, the lease acquires each model's serving GPU).
echo
echo "== scheduling the smoke fan-out (tmux_workers=$QWEN_TMUX_WORKERS) =="
eval-audit-run --run=1 "$QWEN_COMBINED_BUNDLE_ROOT/smoke_manifest.yaml" \
  --container-image "$QWEN_CONTAINER_IMAGE" --lease --tmux-workers "$QWEN_TMUX_WORKERS"

# Fold in any extra split-out suites (empty by default — see _lib.sh).
for preset in "${QWEN_COMBINED_EXTRA_PRESETS[@]}"; do
  qwen_run_extra_preset "$preset" smoke
done

# Final backstop: reclaim any lease a hard-killed job leaked.
echo
echo "Reclaiming any leaked leases (infer-stack gc)…"
infer-stack gc --yes || echo "WARN: 'infer-stack gc' returned nonzero; continuing." >&2

echo
echo "OK: combined smoke fan-out scheduled (tmux_workers=$QWEN_TMUX_WORKERS)."
echo "Next: ./15_run_full.sh"

#!/usr/bin/env bash
# Run the FULL combined manifest via a single fan-out schedule.
#
# Heavy counterpart to 10_run_smoke.sh: one export of the combined multi-deployment
# bundle (--from-spec --freeze-rel-paths), then `eval-audit-run --lease
# --tmux-workers N` over the full_manifest — the union of the eight members' full
# run_entries (775 reproducible-whitelist rows: classic core + capabilities), each
# replayed from its official run_spec.json. The produced experiment
# (audit-qwen-combined-full) is what 20/30/40 index, compose, and summarize.
#
# Fan-out: cmd_queue drives QWEN_TMUX_WORKERS concurrent HELM client runs; each
# self-acquires its model's GPU lease. infer-stack co-hosts what fits on
# INFER_STACK_ALLOWED_GPUS and serializes the rest (the 72Bs tp=2 / 110B tp=4 can't
# co-host). Within a model, its run_entries share one deployment via ref-counting,
# so raising QWEN_TMUX_WORKERS mostly parallelizes ACROSS models.
#
# HuggingFace auth: the sibling _lib exports HF_TOKEN / HUGGING_FACE_HUB_TOKEN;
# eval-audit-run's scheduler writes it into the mounted HF cache so the in-container
# HELM reads it at $HF_HOME/token (the full set includes gpqa on the turbo models —
# a gated dataset). Run ./06_check_hf_auth.sh first.
#
# Force-rerun is OPT-IN here (the full run is expensive): kwdagger schedules with
# skip_existing=1, so runs whose DONE sentinel already exists are skipped. Set
# QWEN_FORCE_RERUN=1 to clear the prior full result dir before running.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

FORCE_RERUN="${QWEN_FORCE_RERUN:-0}"
LITELLM_PORT="${LITELLM_PORT:-14042}"
LITELLM_BASE_URL="${LITELLM_BASE_URL:-http://localhost:$LITELLM_PORT}"

echo "Reclaiming any leaked leases before start (infer-stack gc)…"
infer-stack gc --yes || echo "WARN: 'infer-stack gc' returned nonzero; continuing." >&2

bootstrap_ep="${QWEN_COMBINED_ENDPOINTS[0]}"
bootstrap_env="$(mktemp)"
echo "Bootstrapping the gateway via ${bootstrap_ep} to read the LiteLLM master key…"
infer-stack acquire "$bootstrap_ep" --no-wait --yes --env-file "$bootstrap_env"
LEASE_MASTER_KEY="$(infer-stack env LITELLM_MASTER_KEY)"
infer-stack release --env-file "$bootstrap_env" --evict --yes \
  || echo "WARN: bootstrap 'release --env-file --evict' returned nonzero; continuing." >&2
rm -f "$bootstrap_env"

echo
echo "== exporting the combined bundle (exact-path freeze) =="
"$PYTHON_BIN" -m eval_audit.integrations.infer_stack export-benchmark-bundle \
  --preset "$QWEN_COMBINED_PRESET" \
  --bundle-root "$QWEN_COMBINED_BUNDLE_ROOT" \
  --access-kind openai-compatible \
  --base-url "${LITELLM_BASE_URL}/v1" \
  --api-key-value "$LEASE_MASTER_KEY" \
  --from-spec --freeze-rel-paths

if [[ "$FORCE_RERUN" == "1" ]]; then
  result_dpath="$RESULTS_ROOT/$QWEN_COMBINED_EXPERIMENT_FULL"
  if [[ -d "$result_dpath" ]]; then
    echo "QWEN_FORCE_RERUN=1: clearing prior results at $result_dpath"
    rm -rf "$result_dpath"
  fi
fi

echo
echo "== scheduling the full fan-out (tmux_workers=$QWEN_TMUX_WORKERS) =="
eval-audit-run --run=1 "$QWEN_COMBINED_BUNDLE_ROOT/full_manifest.yaml" \
  --container-image "$QWEN_CONTAINER_IMAGE" --lease --tmux-workers "$QWEN_TMUX_WORKERS"

# Fold in any extra split-out suites (empty by default — see _lib.sh). Each is its
# own single-model fan-out bundle against a narrow per-suite root, landing in the
# same virtual experiment. Scheduled after the combined bundle (eval-audit-run
# blocks); the fan-out still spreads each suite's runs.
for preset in "${QWEN_COMBINED_EXTRA_PRESETS[@]}"; do
  qwen_run_extra_preset "$preset" full
done

echo
echo "Reclaiming any leaked leases (infer-stack gc)…"
infer-stack gc --yes || echo "WARN: 'infer-stack gc' returned nonzero; continuing." >&2

echo
echo "OK: combined full fan-out scheduled (tmux_workers=$QWEN_TMUX_WORKERS)."
echo "Next: ./20_index_local.sh"

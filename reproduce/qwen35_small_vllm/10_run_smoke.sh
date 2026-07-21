#!/usr/bin/env bash
# Smoke run: gc -> gateway bootstrap -> export the COMPUTE-FROM-SPEC bundle
# (--compute-from-spec: none of the small base ids has a public HELM run to
# replay, so we EXPAND the authored keys ONCE here under the pinned HELM into a
# frozen run_spec.json per run and replay THOSE — the key string is a transient
# authoring input, the frozen spec is the durable identity; see
# docs/planning/compute-run-spec-freeze-plan.md) -> run the smoke_manifest with
# `--lease`. Each scheduled HELM run self-acquires ITS OWN model's GPU lease
# (the inline model_deployment= token is the per-run lease key); infer-stack's
# VRAM-aware planner places every lease on whichever eligible GPU is free — no
# GPU indices anywhere.
#
# Force-rerun is ON by default (cheap preflight); set QWEN35S_FORCE_RERUN=0 to
# reuse a prior smoke.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

FORCE_RERUN="${QWEN35S_FORCE_RERUN:-1}"
LITELLM_PORT="${LITELLM_PORT:-14042}"
LITELLM_BASE_URL="${LITELLM_BASE_URL:-http://localhost:$LITELLM_PORT}"

echo "Reclaiming any leaked leases before start (infer-stack gc)…"
infer-stack gc --yes || echo "WARN: 'infer-stack gc' returned nonzero; continuing." >&2

# Bootstrap the gateway ONCE so export-benchmark-bundle can read the managed
# LiteLLM master key, then release the bootstrap lease (scoped by env-file) so
# per-run leasing owns the serving lifecycle. Any family endpoint works; use
# the smallest (fastest cold start).
bootstrap_env="$(mktemp)"
echo "Bootstrapping the gateway via ${QWEN35S_BOOTSTRAP_ENDPOINT} to read the LiteLLM master key…"
infer-stack acquire "$QWEN35S_BOOTSTRAP_ENDPOINT" --no-wait --yes --env-file "$bootstrap_env"
LEASE_MASTER_KEY="$(infer-stack env LITELLM_MASTER_KEY)"
infer-stack release --env-file "$bootstrap_env" --evict --yes \
  || echo "WARN: bootstrap 'release --env-file --evict' returned nonzero; continuing." >&2
rm -f "$bootstrap_env"

echo
echo "== exporting the compute-from-spec bundle =="
# --compute-from-spec expands the authored run_entries ONCE now (offline, no
# inference — needs HELM importable) into synthesized_specs/<run>/run_spec.json
# and freezes them (with the preset's model_metadata/tokenizer sidecars
# registered so the qwen3.5 ids resolve). Multi-deployment: each frozen source
# keeps its own inline model_deployment= token. Route through LiteLLM.
"$PYTHON_BIN" -m eval_audit.integrations.infer_stack export-benchmark-bundle \
  --preset "$QWEN35S_PRESET" \
  --bundle-root "$QWEN35S_BUNDLE_ROOT" \
  --access-kind openai-compatible \
  --compute-from-spec \
  --base-url "${LITELLM_BASE_URL}/v1" \
  --api-key-value "$LEASE_MASTER_KEY"

if [[ "$FORCE_RERUN" == "1" ]]; then
  result_dpath="$RESULTS_ROOT/$QWEN35S_EXPERIMENT_SMOKE"
  if [[ -d "$result_dpath" ]]; then
    echo "QWEN35S_FORCE_RERUN=1: clearing prior results at $result_dpath"
    rm -rf "$result_dpath"
  fi
fi

echo
echo "== scheduling the smoke run (tmux_workers=$QWEN35S_TMUX_WORKERS) =="
eval-audit-run --run=1 "$QWEN35S_BUNDLE_ROOT/smoke_manifest.yaml" \
  --container-image "$QWEN35S_CONTAINER_IMAGE" --lease --tmux-workers "$QWEN35S_TMUX_WORKERS"

echo
echo "Reclaiming any leaked leases (infer-stack gc)…"
infer-stack gc --yes || echo "WARN: 'infer-stack gc' returned nonzero; continuing." >&2

echo
echo "OK: smoke scheduled. Verify produced run dirs with:"
echo "  ./40_verify_artifacts.sh $RESULTS_ROOT/$QWEN35S_EXPERIMENT_SMOKE"

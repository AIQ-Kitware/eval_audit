#!/usr/bin/env bash
# Full run: same shape as 10_run_smoke.sh against the full_manifest — 3 x the
# authored 72-entry classic/Lite COMPUTE core (the 9B grid token-swapped;
# mmlu in canonical compute form, math/natural_qa dropped as data-access
# barriers), grouped by model so reclaim: stop + ref-count coalescing gives
# one vLLM cold start per model block. Placement is VRAM-aware and unpinned;
# safe to run concurrently with the 9B runbook's full run (its 24 GiB
# declaration is eligible only on the 48 GiB card). Force-rerun is OFF by
# default (expensive); set QWEN35S_FORCE_RERUN=1 to clear a prior result.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

FORCE_RERUN="${QWEN35S_FORCE_RERUN:-0}"
LITELLM_PORT="${LITELLM_PORT:-14042}"
LITELLM_BASE_URL="${LITELLM_BASE_URL:-http://localhost:$LITELLM_PORT}"

echo "Reclaiming any leaked leases before start (infer-stack gc)…"
infer-stack gc --yes || echo "WARN: 'infer-stack gc' returned nonzero; continuing." >&2

bootstrap_env="$(mktemp)"
echo "Bootstrapping the gateway via ${QWEN35S_BOOTSTRAP_ENDPOINT} to read the LiteLLM master key…"
infer-stack acquire "$QWEN35S_BOOTSTRAP_ENDPOINT" --no-wait --yes --env-file "$bootstrap_env"
LEASE_MASTER_KEY="$(infer-stack env LITELLM_MASTER_KEY)"
infer-stack release --env-file "$bootstrap_env" --evict --yes \
  || echo "WARN: bootstrap 'release --env-file --evict' returned nonzero; continuing." >&2
rm -f "$bootstrap_env"

echo
echo "== exporting the compute-from-spec bundle =="
# --compute-from-spec: expand the authored run_entries ONCE here (offline; needs
# HELM importable) into frozen synthesized_specs/<run>/run_spec.json and replay
# THOSE — the frozen spec is the durable identity, not the run-key string (see
# docs/planning/compute-run-spec-freeze-plan.md).
"$PYTHON_BIN" -m eval_audit.integrations.infer_stack export-benchmark-bundle \
  --preset "$QWEN35S_PRESET" \
  --bundle-root "$QWEN35S_BUNDLE_ROOT" \
  --access-kind openai-compatible \
  --compute-from-spec \
  --base-url "${LITELLM_BASE_URL}/v1" \
  --api-key-value "$LEASE_MASTER_KEY"

if [[ "$FORCE_RERUN" == "1" ]]; then
  result_dpath="$RESULTS_ROOT/$QWEN35S_EXPERIMENT_FULL"
  if [[ -d "$result_dpath" ]]; then
    echo "QWEN35S_FORCE_RERUN=1: clearing prior results at $result_dpath"
    rm -rf "$result_dpath"
  fi
fi

echo
echo "== scheduling the full run (tmux_workers=$QWEN35S_TMUX_WORKERS) =="
eval-audit-run --run=1 "$QWEN35S_BUNDLE_ROOT/full_manifest.yaml" \
  --container-image "$QWEN35S_CONTAINER_IMAGE" --lease --tmux-workers "$QWEN35S_TMUX_WORKERS"

echo
echo "Reclaiming any leaked leases (infer-stack gc)…"
infer-stack gc --yes || echo "WARN: 'infer-stack gc' returned nonzero; continuing." >&2

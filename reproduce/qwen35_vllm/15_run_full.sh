#!/usr/bin/env bash
# Full run: same shape as 10_run_smoke.sh against the full_manifest — the
# authored 72-entry classic/Lite COMPUTE core (docs/planning/qwen36-core-new-results-plan.md
# §6.1; run keys mirror the reproduced Qwen 1.5/2/2.5 grids, mmlu in canonical
# compute form, math+natural_qa dropped as data-access barriers) plus boolq at
# full instance count as the '<think>'-leakage probe. Expect several hours of
# wall-clock on a single RTX 8000. Force-rerun is OFF by default (expensive);
# set QWEN35_FORCE_RERUN=1 to clear a prior result.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

FORCE_RERUN="${QWEN35_FORCE_RERUN:-0}"
LITELLM_PORT="${LITELLM_PORT:-14042}"
LITELLM_BASE_URL="${LITELLM_BASE_URL:-http://localhost:$LITELLM_PORT}"

echo "Reclaiming any leaked leases before start (infer-stack gc)…"
infer-stack gc --yes || echo "WARN: 'infer-stack gc' returned nonzero; continuing." >&2

bootstrap_env="$(mktemp)"
echo "Bootstrapping the gateway via ${QWEN35_ENDPOINT} to read the LiteLLM master key…"
infer-stack acquire "$QWEN35_ENDPOINT" --no-wait --yes --env-file "$bootstrap_env"
LEASE_MASTER_KEY="$(infer-stack env LITELLM_MASTER_KEY)"
infer-stack release --env-file "$bootstrap_env" --evict --yes \
  || echo "WARN: bootstrap 'release --env-file --evict' returned nonzero; continuing." >&2
rm -f "$bootstrap_env"

echo
echo "== exporting the compute bundle =="
"$PYTHON_BIN" -m eval_audit.integrations.infer_stack export-benchmark-bundle \
  --preset "$QWEN35_PRESET" \
  --bundle-root "$QWEN35_BUNDLE_ROOT" \
  --access-kind openai-compatible \
  --base-url "${LITELLM_BASE_URL}/v1" \
  --api-key-value "$LEASE_MASTER_KEY"

if [[ "$FORCE_RERUN" == "1" ]]; then
  result_dpath="$RESULTS_ROOT/$QWEN35_EXPERIMENT_FULL"
  if [[ -d "$result_dpath" ]]; then
    echo "QWEN35_FORCE_RERUN=1: clearing prior results at $result_dpath"
    rm -rf "$result_dpath"
  fi
fi

echo
echo "== scheduling the full run (tmux_workers=$QWEN35_TMUX_WORKERS) =="
eval-audit-run --run=1 "$QWEN35_BUNDLE_ROOT/full_manifest.yaml" \
  --container-image "$QWEN35_CONTAINER_IMAGE" --lease --tmux-workers "$QWEN35_TMUX_WORKERS"

echo
echo "Reclaiming any leaked leases (infer-stack gc)…"
infer-stack gc --yes || echo "WARN: 'infer-stack gc' returned nonzero; continuing." >&2

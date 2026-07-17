#!/usr/bin/env bash
# Smoke run: gc -> gateway bootstrap -> export the COMPUTE bundle (NO
# --from-spec: qwen/qwen3.5-9b-base has no public HELM run to replay) -> run the
# smoke_manifest with `--lease` (the scheduled HELM run self-acquires the
# model's GPU lease; infer-stack serves vLLM behind LiteLLM and reclaims the
# GPU on release).
#
# Force-rerun is ON by default (cheap preflight); set QWEN35_FORCE_RERUN=0 to
# reuse a prior smoke.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

FORCE_RERUN="${QWEN35_FORCE_RERUN:-1}"
LITELLM_PORT="${LITELLM_PORT:-14042}"
LITELLM_BASE_URL="${LITELLM_BASE_URL:-http://localhost:$LITELLM_PORT}"

echo "Reclaiming any leaked leases before start (infer-stack gc)…"
infer-stack gc --yes || echo "WARN: 'infer-stack gc' returned nonzero; continuing." >&2

# Bootstrap the gateway ONCE so export-benchmark-bundle can read the managed
# LiteLLM master key, then release the bootstrap lease (scoped by env-file) so
# per-run leasing owns the serving lifecycle.
bootstrap_env="$(mktemp)"
echo "Bootstrapping the gateway via ${QWEN35_ENDPOINT} to read the LiteLLM master key…"
infer-stack acquire "$QWEN35_ENDPOINT" --no-wait --yes --env-file "$bootstrap_env"
LEASE_MASTER_KEY="$(infer-stack env LITELLM_MASTER_KEY)"
infer-stack release --env-file "$bootstrap_env" --evict --yes \
  || echo "WARN: bootstrap 'release --env-file --evict' returned nonzero; continuing." >&2
rm -f "$bootstrap_env"

# Export the compute bundle. NO --from-spec / --freeze-rel-paths — this is a
# net-new result, not a reproduction; the manifest's precomputed_root stays
# null and the run_entries are authored (see the preset). Route through LiteLLM.
echo
echo "== exporting the compute bundle =="
"$PYTHON_BIN" -m eval_audit.integrations.infer_stack export-benchmark-bundle \
  --preset "$QWEN35_PRESET" \
  --bundle-root "$QWEN35_BUNDLE_ROOT" \
  --access-kind openai-compatible \
  --base-url "${LITELLM_BASE_URL}/v1" \
  --api-key-value "$LEASE_MASTER_KEY"

if [[ "$FORCE_RERUN" == "1" ]]; then
  result_dpath="$RESULTS_ROOT/$QWEN35_EXPERIMENT_SMOKE"
  if [[ -d "$result_dpath" ]]; then
    echo "QWEN35_FORCE_RERUN=1: clearing prior results at $result_dpath"
    rm -rf "$result_dpath"
  fi
fi

echo
echo "== scheduling the smoke run (tmux_workers=$QWEN35_TMUX_WORKERS) =="
eval-audit-run --run=1 "$QWEN35_BUNDLE_ROOT/smoke_manifest.yaml" \
  --container-image "$QWEN35_CONTAINER_IMAGE" --lease --tmux-workers "$QWEN35_TMUX_WORKERS"

echo
echo "Reclaiming any leaked leases (infer-stack gc)…"
infer-stack gc --yes || echo "WARN: 'infer-stack gc' returned nonzero; continuing." >&2

echo
echo "OK: smoke scheduled. Verify a produced run dir with:"
echo "  ./40_verify_artifacts.sh $RESULTS_ROOT/$QWEN35_EXPERIMENT_SMOKE/<...>/benchmark_output/runs/audit-qwen35-vllm-smoke/<run>"

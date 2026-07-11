#!/usr/bin/env bash
# Smoke preflight: gc -> gateway bootstrap -> export (--from-spec
# --freeze-rel-paths) -> run the smoke_manifest with `--lease --tmux-workers N`.
#
# The smoke manifest carries ifeval (the langdetect/[metrics] container canary)
# + bbq — the two LOW-risk-for-null-content rows — so this is a cheap end-to-end
# exercise of the from-spec + serving + container path before the full run (which
# adds the CoT rows mmlu_pro/gpqa). Force-rerun is ON by default (cheap
# preflight); set GPTOSS_FORCE_RERUN=0 to reuse a prior smoke result.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

FORCE_RERUN="${GPTOSS_FORCE_RERUN:-1}"
LITELLM_PORT="${LITELLM_PORT:-14042}"
LITELLM_BASE_URL="${LITELLM_BASE_URL:-http://localhost:$LITELLM_PORT}"

# Reclaim any lease a prior hard-killed run leaked (scoped to THIS data_dir's
# ledger — never touches another user's active leases).
echo "Reclaiming any leaked leases before start (infer-stack gc)…"
infer-stack gc --yes || echo "WARN: 'infer-stack gc' returned nonzero; continuing." >&2

# Bootstrap the no-blip gateway ONCE so export-benchmark-bundle can read the
# managed LiteLLM master key, then release just the bootstrap model (scoped by
# env-file, NOT --all) so per-run leasing owns the model's lifecycle.
bootstrap_env="$(mktemp)"
echo "Bootstrapping the gateway via ${GPTOSS_ENDPOINT} to read the LiteLLM master key…"
infer-stack acquire "$GPTOSS_ENDPOINT" --no-wait --yes --env-file "$bootstrap_env"
LEASE_MASTER_KEY="$(infer-stack env LITELLM_MASTER_KEY)"
infer-stack release --env-file "$bootstrap_env" --evict --yes \
  || echo "WARN: bootstrap 'release --env-file --evict' returned nonzero; continuing." >&2
rm -f "$bootstrap_env"

# Export the from-spec bundle (exact-path freeze), routed through LiteLLM
# (override the preset's declared vllm-direct with openai-compatible).
echo
echo "== exporting the bundle (exact-path freeze) =="
"$PYTHON_BIN" -m eval_audit.integrations.infer_stack export-benchmark-bundle \
  --preset "$GPTOSS_PRESET" \
  --bundle-root "$GPTOSS_BUNDLE_ROOT" \
  --access-kind openai-compatible \
  --base-url "${LITELLM_BASE_URL}/v1" \
  --api-key-value "$LEASE_MASTER_KEY" \
  --from-spec --freeze-rel-paths

# Optionally clear the prior smoke result so kwdagger's skip_existing doesn't
# no-op this run.
if [[ "$FORCE_RERUN" == "1" ]]; then
  result_dpath="$RESULTS_ROOT/$GPTOSS_EXPERIMENT_SMOKE"
  if [[ -d "$result_dpath" ]]; then
    echo "GPTOSS_FORCE_RERUN=1: clearing prior results at $result_dpath"
    rm -rf "$result_dpath"
  fi
fi

# Run the smoke manifest under leasing. Containerization is mandatory; leasing is
# the orthogonal axis (the container pins where the HELM client runs, the lease
# acquires the serving GPU).
echo
echo "== scheduling the smoke run (tmux_workers=$GPTOSS_TMUX_WORKERS) =="
eval-audit-run --run=1 "$GPTOSS_BUNDLE_ROOT/smoke_manifest.yaml" \
  --container-image "$GPTOSS_CONTAINER_IMAGE" --lease --tmux-workers "$GPTOSS_TMUX_WORKERS"

# Final backstop: reclaim any lease a hard-killed job leaked.
echo
echo "Reclaiming any leaked leases (infer-stack gc)…"
infer-stack gc --yes || echo "WARN: 'infer-stack gc' returned nonzero; continuing." >&2

echo
echo "OK: smoke run scheduled. Next: ./15_run_full.sh"

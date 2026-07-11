#!/usr/bin/env bash
# Run the FULL from-spec manifest: gc -> gateway bootstrap -> export (--from-spec
# --freeze-rel-paths) -> `eval-audit-run --lease --tmux-workers N` over the
# full_manifest — bbq, ifeval, mmlu_pro, gpqa, each replayed from its official
# run_spec.json. The produced experiment (audit-openai-gpt-oss-20b-from-spec-full)
# is what 20/30/40 index, compose, and summarize.
#
# HuggingFace auth: _lib exports HF_TOKEN / HUGGING_FACE_HUB_TOKEN;
# eval-audit-run's scheduler writes it into the mounted HF cache so the
# in-container HELM reads it at $HF_HOME/token (needed for gpqa, gated). Run
# ./06_check_hf_auth.sh first.
#
# NULL-CONTENT CAVEAT: the CoT rows (mmlu_pro, gpqa) can hit the reasoning-only
# `message.content=null` chat response that un-patched HELM crashes on. If a run
# dies with "AttributeError: 'NoneType' object has no attribute 'strip'", see the
# README "Caveats" for the completions fallback.
#
# Force-rerun is OPT-IN here (the full run is expensive): kwdagger schedules with
# skip_existing=1. Set GPTOSS_FORCE_RERUN=1 to clear the prior full result dir.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

FORCE_RERUN="${GPTOSS_FORCE_RERUN:-0}"
LITELLM_PORT="${LITELLM_PORT:-14042}"
LITELLM_BASE_URL="${LITELLM_BASE_URL:-http://localhost:$LITELLM_PORT}"

echo "Reclaiming any leaked leases before start (infer-stack gc)…"
infer-stack gc --yes || echo "WARN: 'infer-stack gc' returned nonzero; continuing." >&2

bootstrap_env="$(mktemp)"
echo "Bootstrapping the gateway via ${GPTOSS_ENDPOINT} to read the LiteLLM master key…"
infer-stack acquire "$GPTOSS_ENDPOINT" --no-wait --yes --env-file "$bootstrap_env"
LEASE_MASTER_KEY="$(infer-stack env LITELLM_MASTER_KEY)"
infer-stack release --env-file "$bootstrap_env" --evict --yes \
  || echo "WARN: bootstrap 'release --env-file --evict' returned nonzero; continuing." >&2
rm -f "$bootstrap_env"

echo
echo "== exporting the bundle (exact-path freeze) =="
"$PYTHON_BIN" -m eval_audit.integrations.infer_stack export-benchmark-bundle \
  --preset "$GPTOSS_PRESET" \
  --bundle-root "$GPTOSS_BUNDLE_ROOT" \
  --access-kind openai-compatible \
  --base-url "${LITELLM_BASE_URL}/v1" \
  --api-key-value "$LEASE_MASTER_KEY" \
  --from-spec --freeze-rel-paths

if [[ "$FORCE_RERUN" == "1" ]]; then
  result_dpath="$RESULTS_ROOT/$GPTOSS_EXPERIMENT_FULL"
  if [[ -d "$result_dpath" ]]; then
    echo "GPTOSS_FORCE_RERUN=1: clearing prior results at $result_dpath"
    rm -rf "$result_dpath"
  fi
fi

echo
echo "== scheduling the full run (tmux_workers=$GPTOSS_TMUX_WORKERS) =="
eval-audit-run --run=1 "$GPTOSS_BUNDLE_ROOT/full_manifest.yaml" \
  --container-image "$GPTOSS_CONTAINER_IMAGE" --lease --tmux-workers "$GPTOSS_TMUX_WORKERS"

echo
echo "Reclaiming any leaked leases (infer-stack gc)…"
infer-stack gc --yes || echo "WARN: 'infer-stack gc' returned nonzero; continuing." >&2

echo
echo "OK: full run scheduled. Next: ./20_index_local.sh"

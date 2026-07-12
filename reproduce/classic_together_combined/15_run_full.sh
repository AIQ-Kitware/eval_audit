#!/usr/bin/env bash
# Run the FULL manifest for each (model x era) target — ALL ~226 official runs per
# model per suite, at the official 1000-instance cap. This is the batch that feeds
# 20->40; 10_run_smoke.sh is a fast preflight. Same era-pinned mechanics as 10.
#
# ⚠️ Large: 6 targets x ~226 runs. OPT-66B needs a multi-GPU host (TP=4). Set
# KEEP_GOING=1 to attempt every target and report failures at the end. Narrow with
# TARGETS_OVERRIDE="era-gptj_6b-v0_2_4:helm-v0.2.4:gptj6b-single ..." to run a subset.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

KEEP_GOING="${KEEP_GOING:-0}"
if [[ -n "${TARGETS_OVERRIDE:-}" ]]; then read -r -a TARGETS <<<"$TARGETS_OVERRIDE"; fi
failed=()
LITELLM_PORT="${LITELLM_PORT:-14042}"
LITELLM_BASE_URL="${LITELLM_BASE_URL:-http://localhost:$LITELLM_PORT}"

echo "Reclaiming any leaked leases before start (infer-stack gc)…"
infer-stack gc --yes || echo "WARN: 'infer-stack gc' returned nonzero; continuing." >&2

bootstrap_ep="$(t_endpoint "${TARGETS[0]}")"
LEASE_MASTER_KEY=""
if [[ -n "$bootstrap_ep" ]]; then
  bootstrap_env="$(mktemp)"
  echo "Bootstrapping the gateway via ${bootstrap_ep} to read the LiteLLM master key…"
  infer-stack acquire "$bootstrap_ep" --no-wait --yes --env-file "$bootstrap_env"
  LEASE_MASTER_KEY="$(infer-stack env LITELLM_MASTER_KEY)"
  infer-stack release --env-file "$bootstrap_env" --evict --yes \
    || echo "WARN: bootstrap 'release --env-file --evict' returned nonzero; continuing." >&2
  rm -f "$bootstrap_env"
fi

run_one() {
  local target="$1"
  local preset key endpoint experiment bundle_root view manifest image
  preset="$(t_preset "$target")"; key="$(t_key "$target")"; endpoint="$(t_endpoint "$target")"
  experiment="$(t_experiment_full "$target")"; bundle_root="$(t_bundle_root "$target")"
  image="$(era_image "$key")"

  echo
  echo "==================================================================="
  echo "== ${preset}  (era: ${key}, endpoint: ${endpoint})  [FULL]"
  echo "==================================================================="

  view="$(era_corpus_view "$key")"

  "$PYTHON_BIN" -m eval_audit.integrations.infer_stack export-benchmark-bundle \
    --preset "$preset" \
    --bundle-root "$bundle_root" \
    --freeze-rel-paths \
    --precomputed-root "$view" \
    --base-url "${LITELLM_BASE_URL}/v1" \
    --api-key-value "$LEASE_MASTER_KEY"
  manifest="$bundle_root/full_manifest.yaml"

  clear_results "$experiment"
  export EVAL_AUDIT_ERA_API_KEY="${LEASE_MASTER_KEY:-$EVAL_AUDIT_ERA_API_KEY}"

  eval-audit-run "$manifest" --lease --run=1 --container-image "$image"
}

for target in "${TARGETS[@]}"; do
  if [[ "$KEEP_GOING" == "1" ]]; then
    if ! run_one "$target"; then
      echo "WARN: $(t_preset "$target") full run failed; continuing." >&2
      failed+=("$(t_preset "$target")")
    fi
  else
    run_one "$target"
  fi
done

echo "Reclaiming any leaked leases (infer-stack gc)…"
infer-stack gc --yes || echo "WARN: 'infer-stack gc' returned nonzero; continuing." >&2

if (( ${#failed[@]} > 0 )); then
  echo >&2; echo "Completed with ${#failed[@]} failed target(s):" >&2
  printf '  - %s\n' "${failed[@]}" >&2
  exit 1
fi

echo
echo "OK: all ${#TARGETS[@]} full runs completed."
echo "Next: ./20_index_local.sh"

#!/usr/bin/env bash
# Run the SMOKE manifest for each (model x era) target, sequentially. Each target
# replays inside its own era-pinned CPU-only image; inference is served on the
# host by modern vLLM (the <model>-single endpoint behind LiteLLM); the era
# container is an HTTP client (container_gpus: none, --network host) that
# self-acquires the model's GPU lease per run (eval-audit-run --lease).
#
# export-benchmark-bundle --freeze-rel-paths bakes from_run_spec + frozen
# run_spec_sources + era: into runnable smoke/full manifests. The broad classic
# root is AMBIGUOUS (these models' runs exist at both v0.2.4 and v0.3.0 with
# identical names), so we override --precomputed-root with a per-era suite-scoped
# VIEW (era_corpus_view).
#
# Default is fail-fast; set KEEP_GOING=1 to attempt every target and report which
# failed. Always force-reruns (clears each experiment's prior result dir).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

KEEP_GOING="${KEEP_GOING:-0}"
failed=()
LITELLM_PORT="${LITELLM_PORT:-14042}"
LITELLM_BASE_URL="${LITELLM_BASE_URL:-http://localhost:$LITELLM_PORT}"

echo "Reclaiming any leaked leases before start (infer-stack gc)…"
infer-stack gc --yes || echo "WARN: 'infer-stack gc' returned nonzero; continuing." >&2

# Bootstrap the no-blip gateway ONCE so export-benchmark-bundle can read the
# managed LiteLLM master key, then release just the bootstrap model.
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
  preset="$(t_preset "$target")"
  key="$(t_key "$target")"
  endpoint="$(t_endpoint "$target")"
  experiment="$(t_experiment_smoke "$target")"
  bundle_root="$(t_bundle_root "$target")"
  image="$(era_image "$key")"

  echo
  echo "==================================================================="
  echo "== ${preset}  (era: ${key}, endpoint: ${endpoint})"
  echo "==================================================================="

  view="$(era_corpus_view "$key")"

  "$PYTHON_BIN" -m eval_audit.integrations.infer_stack export-benchmark-bundle \
    --preset "$preset" \
    --bundle-root "$bundle_root" \
    --freeze-rel-paths \
    --precomputed-root "$view" \
    --base-url "${LITELLM_BASE_URL}/v1" \
    --api-key-value "$LEASE_MASTER_KEY"
  manifest="$bundle_root/smoke_manifest.yaml"

  clear_results "$experiment"

  # The master key must ALSO ride EVAL_AUDIT_ERA_API_KEY (forwarded into the
  # container -> the shim's credentials.conf): at v0.2.4, AutoClient's
  # additional_args api_key OVERRIDES the client_spec.args key, so with the
  # default EMPTY the v0.2.4 client would 401 at the gateway. Harmless at v0.3.0.
  export EVAL_AUDIT_ERA_API_KEY="${LEASE_MASTER_KEY:-$EVAL_AUDIT_ERA_API_KEY}"

  eval-audit-run "$manifest" --lease --run=1 --container-image "$image"
}

for target in "${TARGETS[@]}"; do
  if [[ "$KEEP_GOING" == "1" ]]; then
    if ! run_one "$target"; then
      echo "WARN: $(t_preset "$target") smoke run failed; continuing." >&2
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
echo "OK: all ${#TARGETS[@]} smoke runs completed."
echo "Next: ./15_run_full.sh"

#!/usr/bin/env bash
# Run the FULL manifest for each classic-era target (one per era) — the batch the
# downstream index -> compose -> summary steps operate on. Same structure as
# 10_run_smoke_grid.sh (see its header), but selects the full_manifest (official
# 1000-cap) and the <name>-full experiments.
#
# Default is fail-fast. Set ERA_KEEP_GOING=1 to attempt every era and report
# which failed at the end. Always force-reruns.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

KEEP_GOING="${ERA_KEEP_GOING:-0}"
failed=()

LITELLM_PORT="${LITELLM_PORT:-14042}"
LITELLM_BASE_URL="${LITELLM_BASE_URL:-http://localhost:$LITELLM_PORT}"

echo "Reclaiming any leaked leases before start (infer-stack gc)…"
infer-stack gc --yes || echo "WARN: 'infer-stack gc' returned nonzero; continuing." >&2

bootstrap_ep="$(era_endpoint "${ERA_TARGETS[0]}")"
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
  local name key endpoint experiment bundle_root view manifest image
  name="$(era_name "$target")"
  key="$(era_key "$target")"
  endpoint="$(era_endpoint "$target")"
  experiment="$(era_experiment_full "$target")"
  bundle_root="$(era_bundle_root "$target")"
  image="$(era_image "$key")"

  echo
  echo "==================================================================="
  echo "== ${name}  (era: ${key}, endpoint: ${endpoint})"
  echo "==================================================================="

  view="$(era_corpus_view "$key")"

  "$PYTHON_BIN" -m eval_audit.integrations.infer_stack export-benchmark-bundle \
    --preset "$name" \
    --bundle-root "$bundle_root" \
    --freeze-rel-paths \
    --precomputed-root "$view" \
    --base-url "${LITELLM_BASE_URL}/v1" \
    --api-key-value "$LEASE_MASTER_KEY"
  manifest="$bundle_root/full_manifest.yaml"

  era_clear_results "$experiment"

  eval-audit-run "$manifest" --lease --run=1 --container-image "$image"
}

for target in "${ERA_TARGETS[@]}"; do
  if [[ "$KEEP_GOING" == "1" ]]; then
    if ! run_one "$target"; then
      echo "WARN: $(era_name "$target") full run failed; continuing." >&2
      failed+=("$(era_name "$target")")
    fi
  else
    run_one "$target"
  fi
done

echo "Reclaiming any leaked leases (infer-stack gc)…"
infer-stack gc --yes || echo "WARN: 'infer-stack gc' returned nonzero; continuing." >&2

if (( ${#failed[@]} > 0 )); then
  echo >&2
  echo "Completed with ${#failed[@]} failed era(s):" >&2
  printf '  - %s\n' "${failed[@]}" >&2
  exit 1
fi

echo
echo "OK: all ${#ERA_TARGETS[@]} era full runs completed."
echo "Next: ./20_index_local.sh"

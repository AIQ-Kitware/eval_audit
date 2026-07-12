#!/usr/bin/env bash
# Run the SMOKE manifest for each classic-era target (one per era), sequentially.
#
# Each era runs its replay inside its OWN era-pinned CPU-only image (the bridge
# selects the era shim pipeline and guards the image's org.aiq.era label). Model
# inference is served on the host by modern vLLM (redpajama3b-single behind
# LiteLLM); the era container is an HTTP client (container_gpus: none,
# --network host) that self-acquires redpajama-3b's GPU lease per run
# (eval-audit-run --lease).
#
# Unlike the phi-2 e2e, there is NO from-spec CLI flag to pass and no separate
# make-manifest step: export-benchmark-bundle --freeze-rel-paths bakes
# from_run_spec + the frozen run_spec_sources + era: into directly runnable
# smoke/full manifests (era replay is exact-path only; the preset declares the
# era). The broad classic root is AMBIGUOUS for redpajama-3b (its runs exist at
# both v0.2.4 and v0.3.0 with identical names), so we override --precomputed-root
# with a per-era suite-scoped VIEW (era_corpus_view) that exposes exactly this
# era's suite while preserving the classic/benchmark_output/... layout era
# resolution needs.
#
# Default is fail-fast. Set ERA_KEEP_GOING=1 to attempt every era and report
# which failed at the end. Always force-reruns (clears each experiment's prior
# result dir) so a stale DONE sentinel can't mask a regression.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

KEEP_GOING="${ERA_KEEP_GOING:-0}"
failed=()

LITELLM_PORT="${LITELLM_PORT:-14042}"
LITELLM_BASE_URL="${LITELLM_BASE_URL:-http://localhost:$LITELLM_PORT}"

echo "Reclaiming any leaked leases before start (infer-stack gc)…"
infer-stack gc --yes || echo "WARN: 'infer-stack gc' returned nonzero; continuing." >&2

# Bootstrap the no-blip gateway ONCE so export-benchmark-bundle can read the
# managed LiteLLM master key, then release just the bootstrap model. All era
# targets are leased vLLM, so the first endpoint bootstraps the gateway.
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
  experiment="$(era_experiment_smoke "$target")"
  bundle_root="$(era_bundle_root "$target")"
  image="$(era_image "$key")"

  echo
  echo "==================================================================="
  echo "== ${name}  (era: ${key}, endpoint: ${endpoint})"
  echo "==================================================================="

  # Per-era suite-scoped corpus view so --freeze-rel-paths resolves unambiguously.
  view="$(era_corpus_view "$key")"

  # Materialize the era bundle (smoke + full manifests) from the per-era preset.
  # --freeze-rel-paths implies --from-spec and freezes exact-path sources; --era
  # comes from the preset. The generated deployment binds the OFFICIAL model name
  # to the era shim client (verbatim by-name) with the gateway base-url + master
  # key.
  "$PYTHON_BIN" -m eval_audit.integrations.infer_stack export-benchmark-bundle \
    --preset "$name" \
    --bundle-root "$bundle_root" \
    --freeze-rel-paths \
    --precomputed-root "$view" \
    --base-url "${LITELLM_BASE_URL}/v1" \
    --api-key-value "$LEASE_MASTER_KEY"
  manifest="$bundle_root/smoke_manifest.yaml"

  # Always clear prior results so kwdagger's skip_existing can't no-op the rerun.
  era_clear_results "$experiment"

  # The master key must ALSO ride EVAL_AUDIT_ERA_API_KEY (forwarded into the
  # container -> the shim's credentials.conf): at v0.2.4, AutoClient constructs
  # the client with additional_args={"api_key": <credentials.conf value>}, which
  # OVERRIDES the client_spec.args api_key the export baked in — so with the
  # default EMPTY the v0.2.4 client would send no Authorization header and every
  # gateway request would 401. v0.3.0 is unaffected (api_key-in-args wins there),
  # so setting it for both eras is harmless.
  export EVAL_AUDIT_ERA_API_KEY="${LEASE_MASTER_KEY:-$EVAL_AUDIT_ERA_API_KEY}"

  # Run inside the ERA image (the bridge guards org.aiq.era against the manifest
  # era), leased per run.
  eval-audit-run "$manifest" --lease --run=1 --container-image "$image"
}

for target in "${ERA_TARGETS[@]}"; do
  if [[ "$KEEP_GOING" == "1" ]]; then
    if ! run_one "$target"; then
      echo "WARN: $(era_name "$target") smoke run failed; continuing." >&2
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
echo "OK: all ${#ERA_TARGETS[@]} era smoke runs completed."
echo "Next: ./15_run_full_grid.sh"

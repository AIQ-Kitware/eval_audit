#!/usr/bin/env bash
# 08_check_discovery.sh — from-spec discovery preflight for the COMBINED preset.
#
# This is the DECISION POINT for §2.1 splits: the combined preset's run_entries
# carry an inline `model_deployment=<local>` token (the multi-deployment convention),
# so we EXERCISE THE FREEZE rather than run `check_precomputed_discovery --preset`
# with bare keys. `export-benchmark-bundle --freeze-rel-paths` strips each local
# token for discovery, resolves each entry to EXACTLY ONE official run dir, and
# HARD-FAILS on NO_MATCH/AMBIGUOUS — freezing a per-run rel-path + rewrite target.
# We then validate every frozen rel-path actually exists via
# `check-precomputed-discovery --manifest`.
#
# Expectation: all eight members freeze 1:1 under the shared parent root
# /data/crfm-helm-public (0 AMBIGUOUS — pre-verified against the corpus: 775
# whitelisted run dirs, all distinct basenames). If a corpus refresh introduces an
# ambiguity, this hard-fails; move the offending member into
# QWEN_COMBINED_EXTRA_PRESETS (own narrow root) — the loop below freeze-checks those
# too.
#
# CPU-only: no GPU, no serving, no gateway. We export with the preset's NATIVE
# vllm-direct access (no --access-kind override) so no LiteLLM master key is needed.
# The bundle is written to a scratch dir and discarded; 10/15 re-export the real one.
#
# vllm-direct exports fail fast without an explicit --base-url (b127aa48, P2:
# the client must never silently default to the auth-protected gateway). For
# this freeze-only preflight the transport is irrelevant and the bundle is
# discarded, so we pass a loopback placeholder — it never receives a request.
#
# Env:
#   PRECOMPUTED_ROOT  override the corpus root the freeze resolves against
#                     (default: the preset's own precomputed_root, the parent
#                     /data/crfm-helm-public that spans every model's runs).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

PREFLIGHT_BASE_URL="${PREFLIGHT_BASE_URL:-http://127.0.0.1:8000/v1}"

root_override=()
if [[ -n "${PRECOMPUTED_ROOT:-}" ]]; then
  [[ -d "$PRECOMPUTED_ROOT" ]] || { echo "FAIL: PRECOMPUTED_ROOT not found: $PRECOMPUTED_ROOT" >&2; exit 1; }
  root_override=(--precomputed-root "$PRECOMPUTED_ROOT")
fi

scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT

echo "=================================================================="
echo "== from-spec discovery preflight (combined preset, exact-path freeze)"
echo "==   preset=$QWEN_COMBINED_PRESET"
echo "==   root=${PRECOMPUTED_ROOT:-<preset precomputed_root>}"
echo "=================================================================="
echo
echo "-- freezing the bundle (each entry resolves 1:1 or this hard-fails) --"
"$PYTHON_BIN" -m eval_audit.integrations.infer_stack export-benchmark-bundle \
  --preset "$QWEN_COMBINED_PRESET" \
  --bundle-root "$scratch" \
  --from-spec --freeze-rel-paths \
  --base-url "$PREFLIGHT_BASE_URL" \
  "${root_override[@]}"

for mode in smoke full; do
  echo
  echo "-- existence check: $mode frozen run_spec_sources --"
  "$PYTHON_BIN" -m eval_audit.cli.check_precomputed_discovery \
    --manifest "$scratch/${mode}_manifest.yaml"
done

# Also freeze-check any extra split-out suites (empty by default; each carries its
# OWN narrow precomputed_root that disambiguates, so no PRECOMPUTED_ROOT override).
for preset in "${QWEN_COMBINED_EXTRA_PRESETS[@]}"; do
  echo
  echo "-- extra suite: freezing ${preset} (own narrow root) --"
  "$PYTHON_BIN" -m eval_audit.integrations.infer_stack export-benchmark-bundle \
    --preset "$preset" --bundle-root "$scratch/$preset" --from-spec --freeze-rel-paths \
    --base-url "$PREFLIGHT_BASE_URL"
  for mode in smoke full; do
    "$PYTHON_BIN" -m eval_audit.cli.check_precomputed_discovery \
      --manifest "$scratch/$preset/${mode}_manifest.yaml"
  done
done

echo
echo "=================================================================="
echo "OK: combined preset freezes cleanly (0 NO_MATCH / 0 AMBIGUOUS) and every"
echo "    frozen run_spec.json exists. Next: ./10_run_smoke.sh"

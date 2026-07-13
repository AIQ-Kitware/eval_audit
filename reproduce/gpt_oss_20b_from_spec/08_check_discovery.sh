#!/usr/bin/env bash
# 08_check_discovery.sh — from-spec discovery preflight for the single-model preset.
#
# EXERCISE THE FREEZE, which is the actual discovery gate for the exact-path
# bundle this runbook exports. `export-benchmark-bundle --from-spec
# --freeze-rel-paths` resolves each run_entry (bbq, ifeval, mmlu_pro, gpqa) to
# EXACTLY ONE official gpt-oss-20b run dir under precomputed_root, HARD-FAILS on
# NO_MATCH/AMBIGUOUS, and freezes a per-run rel-path + rewrite target. We then
# validate every frozen rel-path actually exists via
# `check-precomputed-discovery --manifest`.
#
# This is where a partial local corpus mirror surfaces: if the official gpt-oss
# run dirs (capabilities/v1.12.0 for ifeval/mmlu_pro/gpqa, safety/v1.14.0 for
# bbq) are not present under PRECOMPUTED_ROOT, the freeze reports NO_MATCH here
# — CPU-only, before any GPU work.
#
# CPU-only: no GPU, no serving, no gateway. We export with the preset's NATIVE
# vllm-direct access (no --access-kind override) so no LiteLLM master key is
# needed — the frozen run_spec_sources are independent of the client transport.
# The bundle is written to a scratch dir and discarded; 10/15 re-export the real
# one (routed through LiteLLM).
#
# vllm-direct exports fail fast without an explicit --base-url (b127aa48, P2:
# the client must never silently default to the auth-protected gateway). For
# this freeze-only preflight the transport is irrelevant and the bundle is
# discarded, so we pass a loopback placeholder — it never receives a request.
#
# Env:
#   PRECOMPUTED_ROOT  override the corpus root the freeze resolves against
#                     (default: the preset's own precomputed_root, /data/crfm-helm-public).
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
echo "== from-spec discovery preflight (single-model exact-path freeze)"
echo "==   preset=$GPTOSS_PRESET"
echo "==   root=${PRECOMPUTED_ROOT:-<preset precomputed_root>}"
echo "=================================================================="
echo
echo "-- freezing the bundle (each entry resolves 1:1 or this hard-fails) --"
"$PYTHON_BIN" -m eval_audit.integrations.infer_stack export-benchmark-bundle \
  --preset "$GPTOSS_PRESET" \
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

echo
echo "=================================================================="
echo "OK: preset freezes cleanly (0 NO_MATCH / 0 AMBIGUOUS) and every frozen"
echo "    run_spec.json exists. Next: ./10_run_smoke.sh"

#!/usr/bin/env bash
# Milestone B: the first LIVE judge smoke — XSTest, 20 instances, Qwen3.5-27B,
# one replicate. Leases the judge endpoint (model stays up for the whole
# pass), exports the sidecar bundle against the live gateway, rejudges a
# 20-instance subset in-process, then releases. Validates serving, output
# shape, parser behavior, raw-response retention, metrics, and artifact
# writing before any full run.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

N="${OJ_SMOKE_INSTANCES:-20}"
ENDPOINT="qwen3.5-27b-judge"

snapshot="$(oj_snapshot_for_benchmark xstest)" || {
  echo "FAIL: no xstest snapshot under $OJ_SNAPSHOT_ROOT (run 05+08 first)." >&2
  exit 1
}
echo "xstest snapshot: $snapshot"

echo "Reclaiming any leaked leases (infer-stack gc)…"
infer-stack gc --yes || echo "WARN: 'infer-stack gc' returned nonzero; continuing." >&2

lease_env="$(mktemp)"
cleanup() {
  # Only release if the acquire actually wrote a lease (env-file non-empty).
  if [[ -s "$lease_env" ]]; then
    infer-stack release --env-file "$lease_env" --evict --yes \
      || echo "WARN: release returned nonzero; run 'infer-stack gc --yes'." >&2
  fi
  rm -f "$lease_env"
}
trap cleanup EXIT

# Acquire with --no-wait so the lease is HELD while the model loads (the
# default wait-mode RELEASES the lease if a slow model misses its 600s
# timeout — a first-time 27B weight download does). Then block on the
# companion `wait` up to OJ_LEASE_WAIT_TIMEOUT.
echo "Acquiring $ENDPOINT (--no-wait: holds the lease while weights load)…"
infer-stack acquire "$ENDPOINT" --no-wait --yes --env-file "$lease_env"
MASTER_KEY="$(infer-stack env LITELLM_MASTER_KEY)"

echo "Waiting up to ${OJ_LEASE_WAIT_TIMEOUT}s for $ENDPOINT to become READY…"
echo "  (first acquire downloads the judge weights from HF — this can take a while)"
infer-stack wait "$ENDPOINT" --timeout "$OJ_LEASE_WAIT_TIMEOUT" || {
  echo "FAIL: $ENDPOINT not ready within ${OJ_LEASE_WAIT_TIMEOUT}s." >&2
  echo "  Check the vLLM container logs; if it is still downloading weights," >&2
  echo "  re-run (the lease is released on exit) or raise OJ_LEASE_WAIT_TIMEOUT." >&2
  exit 1
}

echo "Exporting the judge sidecar bundle against the live gateway…"
LITELLM_MASTER_KEY="$MASTER_KEY" eval-audit-export-judge-bundle \
  --judge-json "$OJ_JUDGE_JSON_QWEN35" \
  --config-dir "$INFER_STACK_CONFIG_DIR" \
  --base-url "${LITELLM_BASE_URL}/v1" \
  --infer-stack-revision "$(git -C "$ROOT/submodules/infer_stack" rev-parse --short HEAD 2>/dev/null || echo unknown)" \
  --out "$OJ_SIDECAR_DIR"

echo "Rejudging XSTest (first $N instances) with Qwen3.5-27B…"
eval-audit-rejudge-helm \
  --snapshot "$snapshot" \
  --judge-json "$OJ_JUDGE_JSON_QWEN35" \
  --replicate 0 \
  --experiment-name "$OJ_EXPERIMENT" \
  --out-root "$OJ_RESULTS_ROOT" \
  --cache-root "$OJ_CACHE_ROOT" \
  --sidecar-config "$OJ_SIDECAR_DIR" \
  --max-instances "$N"

echo
echo "OK: smoke complete. Inspect judgments + parser status with:"
echo "  ls $OJ_RESULTS_ROOT/*/ ; jq . \$(ls -d $OJ_RESULTS_ROOT/*/ | tail -1)/rejudge_manifest.json"

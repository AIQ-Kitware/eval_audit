#!/usr/bin/env bash
# LIVE judge smoke, parameterized: BENCHMARK x JUDGE, N instances, one replicate.
#
#   ./20_smoke.sh [BENCHMARK] [JUDGE]
#     BENCHMARK  xstest | wildbench   (default: xstest)
#     JUDGE      qwen35 | qwen36      (default: qwen35)
#
# Leases the judge endpoint (the model stays up for the whole pass), exports the
# sidecar bundle against the live gateway, rejudges an N-instance subset
# in-process, then releases. Validates serving, output shape, parser behavior,
# raw-response retention, metrics, and artifact writing before any full run.
#
# The lease uses `acquire --no-wait` so the lease is HELD while the model loads
# (the default wait-mode RELEASES the lease if a slow model misses its 600s
# timeout — a first-time 27B/35B weight download does), then blocks on the
# companion `wait` up to OJ_LEASE_WAIT_TIMEOUT.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

BENCHMARK="${1:-xstest}"
JUDGE_KEY="${2:-qwen35}"
N="${OJ_SMOKE_INSTANCES:-20}"

spec="$(oj_judge_spec "$JUDGE_KEY")" || {
  echo "FAIL: unknown judge '$JUDGE_KEY' (want: qwen35 | qwen36)." >&2
  exit 1
}
read -r ENDPOINT JUDGE_JSON <<<"$spec"

snapshot="$(oj_snapshot_for_benchmark "$BENCHMARK")" || {
  echo "FAIL: no $BENCHMARK snapshot under $OJ_SNAPSHOT_ROOT (run 05+08 first)." >&2
  exit 1
}
echo "benchmark : $BENCHMARK"
echo "snapshot  : $snapshot"
echo "judge     : $JUDGE_KEY -> endpoint=$ENDPOINT json=$JUDGE_JSON"

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
  --judge-json "$JUDGE_JSON" \
  --config-dir "$INFER_STACK_CONFIG_DIR" \
  --base-url "${LITELLM_BASE_URL}/v1" \
  --infer-stack-revision "$(git -C "$ROOT/submodules/infer_stack" rev-parse --short HEAD 2>/dev/null || echo unknown)" \
  --out "$OJ_SIDECAR_DIR"

echo "Rejudging $BENCHMARK (first $N instances) with $JUDGE_KEY…"
eval-audit-rejudge-helm \
  --snapshot "$snapshot" \
  --judge-json "$JUDGE_JSON" \
  --replicate 0 \
  --experiment-name "$OJ_EXPERIMENT" \
  --out-root "$OJ_RESULTS_ROOT" \
  --cache-root "$OJ_CACHE_ROOT" \
  --sidecar-config "$OJ_SIDECAR_DIR" \
  --max-instances "$N"

echo
echo "OK: smoke complete. Inspect judgments + parser status with:"
echo "  d=\$(ls -dt $OJ_RESULTS_ROOT/*/ | head -1); f=\"\$d/judgments.jsonl\""
echo "  jq -r '.annotation.parse_status'  \"\$f\" | sort | uniq -c"
echo "  jq -r '.annotation.finish_reason' \"\$f\" | sort | uniq -c"

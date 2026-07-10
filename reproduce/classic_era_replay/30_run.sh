#!/usr/bin/env bash
# Ladder steps 3-4: run one full era packet through Stages 3-6.
#
# The bridge selects the era pipeline (helm_era_shim.replay) because the manifest
# pins an era with capability era-shim-from-spec, and it guards the resolved
# image's org.aiq.era label against the manifest era at SCHEDULE time (a
# mismatched image fails on the host, not the GPU). The era shim forwards
# EVAL_AUDIT_ERA_API_KEY into the container for v0.2.4's credentials.conf.
#
# Preview first (no execution) to confirm the pipeline + image guard, then run.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

: "${OUT_MANIFEST:?set OUT_MANIFEST (from 20_make_manifest.sh)}"
# Forwarded into the container as the per-deployment credential (vLLM ignores it;
# v0.2.4 merely requires it to exist). Default EMPTY.
export EVAL_AUDIT_ERA_API_KEY="${EVAL_AUDIT_ERA_API_KEY:-EMPTY}"

echo "[era-replay] preview (resolve + pin image, era<->image guard, no execution)"
eval-audit-run "${OUT_MANIFEST}"

echo
echo "[era-replay] executing"
eval-audit-run "${OUT_MANIFEST}" --run

cat <<EOF

Done. Now build the reproducibility report (Stages 4-6) as usual:
  eval-audit-index ...      # Stage 4
  analyze-experiment ...    # Stage 5
  build-reports-summary ... # Stage 6

For an era pair, same_deployment correctly resolves 'unknown' (both sides lack
the model_deployment field) — that is the expected behavior, not a bug. The era
key + the image's org.aiq.era label are recorded in container_provenance.json.
EOF

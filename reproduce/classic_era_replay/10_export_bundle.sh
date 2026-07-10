#!/usr/bin/env bash
# Export an ERA bundle: an era-schema model_deployments.yaml (the official model
# name bound to the era shim client, by-name, no api_key) + frozen exact-path
# run_spec_sources (rel-paths resolved against the corpus snapshot NOW).
#
# --era switches to the era schema + shim client and skips the modern HELM-alias
# assertion; --freeze-rel-paths pins each run's exact rel-path (era is exact-path
# only). No model_deployment rewrite target is emitted (era replay is verbatim).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

: "${ERA:?set ERA (e.g. helm-v0.3.0)}"
: "${ERA_PRESET:?set ERA_PRESET (an infer-stack preset declaring protocol_mode + helm_model_name)}"
: "${PRECOMPUTED_ROOT:?set PRECOMPUTED_ROOT (the public HELM corpus mirror)}"

python -m eval_audit.integrations.infer_stack export-benchmark-bundle \
  --preset "${ERA_PRESET}" \
  --era "${ERA}" \
  --freeze-rel-paths \
  --precomputed-root "${PRECOMPUTED_ROOT}"

echo
echo "[era-replay] bundle written. The generated model_deployments.<hash>.yaml"
echo "binds the OFFICIAL model name to ${ERA} shim client (verbatim by-name)."
echo "Next: 20_make_manifest.sh (point --run-spec-sources-fpath at the frozen"
echo "benchmark_full_manifest.yaml's run_spec_sources)."

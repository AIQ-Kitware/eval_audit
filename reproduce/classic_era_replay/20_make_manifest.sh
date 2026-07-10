#!/usr/bin/env bash
# Build the era execution manifest. --era auto resolves the era from the sources'
# rel-paths (one manifest = one era; a mixed-era set is a hard error). The era
# image (org.aiq.era=<ERA>) is pinned via --container-image; the bridge guards
# that label against the manifest era at schedule time.
#
# Inputs:
#   SOURCES_FPATH  a YAML list of {run_entry, rel_path} (the frozen exact-path
#                  sources from the exported bundle; era sources omit
#                  model_deployment).
#   IMAGE_REF      the era image reference (tag or digest) with org.aiq.era=<ERA>.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

: "${ERA:?set ERA (e.g. helm-v0.3.0)}"
: "${PRECOMPUTED_ROOT:?set PRECOMPUTED_ROOT}"
: "${SOURCES_FPATH:?set SOURCES_FPATH (frozen run_spec_sources YAML)}"
: "${IMAGE_REF:?set IMAGE_REF (era image with org.aiq.era=${ERA})}"
: "${OUT_MANIFEST:=configs/era_${ERA//./_}_manifest.yaml}"
: "${EXPERIMENT_NAME:=era_${ERA//./_}_replay}"
: "${SUITE:=era-${ERA}}"

eval-audit-make-manifest \
  --output "${OUT_MANIFEST}" \
  --experiment-name "${EXPERIMENT_NAME}" \
  --suite "${SUITE}" \
  --from-run-spec \
  --era auto \
  --run-spec-sources-fpath "${SOURCES_FPATH}" \
  --precomputed-root "${PRECOMPUTED_ROOT}" \
  --max-eval-instances official \
  --container-image "${IMAGE_REF}"

echo
echo "[era-replay] manifest: ${OUT_MANIFEST} (era: ${ERA}). Next: 30_run.sh"

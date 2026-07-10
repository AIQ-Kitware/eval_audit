#!/usr/bin/env bash
# Ladder step 1 (image sanity): build the era image + smoke-check the shim.
#
# The era harness is the pinned release commit from docker/eras.yaml (git
# archive), staged with docker/era_shim/ + the era constraints file into a
# CPU-only ubuntu:22.04 image. After the first green build, freeze the full
# environment (docker/README.md :: "Era constraints freeze workflow") and
# rebuild so the image is reproducible.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

: "${ERA:?set ERA to an era key from docker/eras.yaml (e.g. helm-v0.3.0)}"

echo "[era-replay] building era image for ERA=${ERA}"
ERA="${ERA}" ./docker/build.sh

# The build tags <image_name>:dev; resolve it for the smoke checks.
IMAGE_NAME="$(./docker/read_eras.py docker/eras.yaml "${ERA}" image_name \
  2>/dev/null || echo "")"
[[ -n "${IMAGE_NAME}" ]] || { echo "could not resolve image_name for ${ERA}"; exit 1; }
IMAGE="${IMAGE_NAME}:dev"

echo "[era-replay] shim --help in-container"
docker run --rm "${IMAGE}" python -m helm_era_shim.replay --help

echo "[era-replay] org.aiq.era label"
docker image inspect --format '{{index .Config.Labels "org.aiq.era"}}' "${IMAGE}"

cat <<EOF

Next:
  * Freeze the environment (docker/README.md), commit the constraints file,
    and re-run this script so the image is reproducible.
  * Ladder step 2 (instrument fidelity, no model): dry-run scenario-state
    construction for a pandas-sensitive entity_matching run and diff instance
    identity against the official artifacts (must be byte-for-byte).
  * Then 10_export_bundle.sh.
EOF

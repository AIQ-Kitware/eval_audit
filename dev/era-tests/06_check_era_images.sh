#!/usr/bin/env bash
# Preflight for containerized era HELM execution (MANDATORY): each era runs its
# replay inside its own era-pinned image (docker/eras.yaml -> ERA=<key>
# ./docker/build.sh). This verifies docker is present and, per era, that the
# image exists locally AND is the real era image: its org.aiq.era label matches,
# the shim CLI resolves, and the era identity is in the RUNTIME env (Finding 6 —
# EVAL_AUDIT_ERA_KEY must be an ENV, not just a build ARG, or replay.py records a
# null era in provenance). Does NOT auto-build: an image build invalidates
# caching and must be a deliberate act. Mirrors dev/e2e-tests/06.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

if ! command -v docker >/dev/null 2>&1; then
  echo "FAIL: docker not found on PATH, but containerized era HELM is mandatory." >&2
  echo "  Install docker (era images are CPU-only — no GPU needed to build them)." >&2
  exit 1
fi

declare -A seen=()
for target in "${ERA_TARGETS[@]}"; do
  key="$(era_key "$target")"
  [[ -n "${seen[$key]:-}" ]] && continue
  seen["$key"]=1

  image="$(era_image "$key")" || exit 1
  echo
  echo "== era ${key}: ${image} =="

  if ! docker image inspect "$image" >/dev/null 2>&1; then
    echo "FAIL: era image not found: $image" >&2
    echo "  Build it first:  ERA=${key} ./docker/build.sh" >&2
    echo "  (or push it and set ERA_IMAGE_${key//[.-]/_}=<repo@sha256:...> for a" >&2
    echo "   digest-pinned cross-machine run)." >&2
    exit 1
  fi
  echo "OK: image present."

  # Deep probe 1: the org.aiq.era label must equal the era key (the bridge's
  # era<->image guard reads it at schedule time).
  label="$(docker image inspect --format '{{index .Config.Labels "org.aiq.era"}}' "$image" 2>/dev/null || true)"
  if [[ "$label" != "$key" ]]; then
    echo "FAIL: image org.aiq.era label is '${label}', expected '${key}'." >&2
    echo "  This image is not the ${key} era image; rebuild: ERA=${key} ./docker/build.sh" >&2
    exit 1
  fi
  echo "OK: org.aiq.era=$label"

  # Deep probe 2: the shim replay CLI resolves inside the image.
  if ! docker run --rm "$image" python -m helm_era_shim.replay --help >/dev/null 2>&1; then
    echo "FAIL: 'python -m helm_era_shim.replay --help' failed inside $image." >&2
    echo "  The era shim is missing/broken; rebuild: ERA=${key} ./docker/build.sh" >&2
    exit 1
  fi
  echo "OK: helm_era_shim.replay resolves."

  # Deep probe 3 (Finding 6 regression guard): the era identity is in the runtime
  # ENV, so replay.py stamps it into adapter_manifest.json / process_context.
  if ! docker run --rm --entrypoint python "$image" - <<'PY'
import os, sys
missing = [k for k in ("EVAL_AUDIT_ERA_KEY", "EVAL_AUDIT_ERA_HELM_REF") if not os.environ.get(k)]
if missing:
    print("FAIL: era env not set in image:", missing, file=sys.stderr)
    sys.exit(1)
print(f"OK: EVAL_AUDIT_ERA_KEY={os.environ['EVAL_AUDIT_ERA_KEY']}")
PY
  then
    echo "FAIL: era identity env vars are absent in $image (ENV missing?)." >&2
    echo "  Rebuild with the fixed dockerfile: ERA=${key} ./docker/build.sh" >&2
    exit 1
  fi
done

echo
echo "OK: all era images present and valid."
echo "Next: ./07_run_gate.sh   (pre-v0.5 validation gates: tier 0 pytest + rungs 2/5)"

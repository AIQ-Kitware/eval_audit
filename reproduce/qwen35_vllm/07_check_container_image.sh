#!/usr/bin/env bash
# Preflight for containerized HELM execution (MANDATORY — the host-venv path was
# removed): verifies docker, the pinned image, and that the image's python env
# is the corrected one (stale-digest guard). Port of
# reproduce/qwen_models_combined/07_check_container_image.sh; see that file and
# docs/container-execution.md for the failure archaeology behind each probe.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

if ! command -v docker >/dev/null 2>&1; then
  echo "FAIL: docker not found on PATH, but containerized HELM is mandatory." >&2
  echo "  Install docker (there is no host-venv fallback)." >&2
  exit 1
fi

if docker image inspect "$QWEN35_CONTAINER_IMAGE" >/dev/null 2>&1; then
  echo "OK: container image present: $QWEN35_CONTAINER_IMAGE"
else
  echo "FAIL: container image not found: $QWEN35_CONTAINER_IMAGE" >&2
  echo "  Build it first:  $ROOT/docker/build.sh" >&2
  echo "  (or push it and set QWEN35_CONTAINER_IMAGE to the digest)" >&2
  exit 1
fi

echo "Probing container python env (langdetect + huggingface_hub pin)…"
if ! docker run --rm -i --entrypoint python "$QWEN35_CONTAINER_IMAGE" - <<'PY'
import sys

errs = []
try:
    import langdetect  # noqa: F401
except Exception as e:
    errs.append(
        "langdetect import failed -> image likely built with crfm-helm[heim] "
        f"instead of [all] ({e!r})"
    )
try:
    import huggingface_hub
    if huggingface_hub.__version__ != "0.36.2":
        errs.append(
            f"huggingface_hub=={huggingface_hub.__version__}, expected 0.36.2 "
            "-> a floated hub breaks dataset-id resolution (e.g. wmt14)"
        )
except Exception as e:
    errs.append(f"huggingface_hub import failed: {e!r}")

if errs:
    for m in errs:
        print("FAIL:", m, file=sys.stderr)
    sys.exit(1)
print(f"OK: container python env — langdetect ok; huggingface_hub {huggingface_hub.__version__}")
PY
then
  echo "FAIL: container image is present but its python env is stale/incorrect." >&2
  echo "  Rebuild the runner image:  $ROOT/docker/build.sh" >&2
  exit 1
fi

echo "Note: the model is SERVED on the host (vLLM behind LiteLLM via infer-stack);"
echo "      the in-container HELM reaches it via --network host (preset-declared)."

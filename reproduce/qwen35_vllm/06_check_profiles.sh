#!/usr/bin/env bash
# Preflight: confirm the serving endpoint the preset's profile references exists
# in the active infer-stack catalog (shipped by this runbook via
# INFER_STACK_CONFIG_DIR, set in _lib.sh) before we try to serve it.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

available="$(infer-stack catalog endpoint list 2>/dev/null || true)"
if [[ -z "$available" ]]; then
  echo "WARN: 'infer-stack catalog endpoint list' produced no output; cannot validate." >&2
  echo "      Ensure infer-stack is installed and INFER_STACK_CONFIG_DIR points" >&2
  echo "      at your config. Continuing without validation." >&2
  exit 0
fi

if ! grep -qw -- "$QWEN35_ENDPOINT" <<<"$available"; then
  echo "FAIL: infer-stack endpoint not defined: $QWEN35_ENDPOINT" >&2
  echo "The preset's profile references it; define the model + endpoint in your" >&2
  echo "infer-stack catalog (see config/infer_stack/catalog.yaml) or point" >&2
  echo "INFER_STACK_CONFIG_DIR at a config that has it." >&2
  exit 1
fi

echo "OK: serving endpoint defined: $QWEN35_ENDPOINT"

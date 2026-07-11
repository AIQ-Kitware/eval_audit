#!/usr/bin/env bash
# Preflight: confirm the single serving endpoint the preset's `profile` references
# (gpt-oss-20b-single) exists in the active infer-stack catalog before we try to
# serve it. The endpoint is shipped by this runbook in
# config/infer_stack/catalog.yaml (the active catalog via INFER_STACK_CONFIG_DIR,
# set by _lib.sh). Fails fast with guidance rather than letting `infer-stack
# acquire` error mid-schedule.
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

if ! grep -qw -- "$GPTOSS_ENDPOINT" <<<"$available"; then
  echo "FAIL: the gpt-oss serving endpoint '$GPTOSS_ENDPOINT' is not defined." >&2
  echo >&2
  echo "The 'openai-gpt-oss-20b' preset's profile references it, but it is not in" >&2
  echo "the active catalog. Define the model + a 'gpt-oss-20b-single' endpoint in" >&2
  echo "your infer-stack catalog (see config/infer_stack/catalog.yaml), or point" >&2
  echo "INFER_STACK_CONFIG_DIR at a config that has it." >&2
  exit 1
fi

echo "OK: gpt-oss serving endpoint is defined: $GPTOSS_ENDPOINT"

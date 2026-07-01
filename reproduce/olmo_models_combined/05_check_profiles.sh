#!/usr/bin/env bash
# Preflight: confirm the FIVE serving endpoints the combined preset's `profiles`
# reference exist in the active infer-stack catalog before we try to serve them.
# The endpoints (<model>-single) are the same ones the single-model runbook ships
# in ../olmo_models/config/infer_stack/catalog.yaml (reused via INFER_STACK_CONFIG_DIR
# inherited from the sibling _lib). Fails fast with guidance if any are missing,
# rather than letting `infer-stack acquire` error mid-schedule.
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

missing=()
for endpoint in "${OLMO_COMBINED_ENDPOINTS[@]}"; do
  if ! grep -qw -- "$endpoint" <<<"$available"; then
    missing+=("$endpoint")
  fi
done

if (( ${#missing[@]} > 0 )); then
  echo "FAIL: the following OLMo infer-stack endpoints are not defined:" >&2
  printf '  - %s\n' "${missing[@]}" >&2
  echo >&2
  echo "The combined preset's profiles reference these, but their serving endpoints" >&2
  echo "are not in the active catalog. Define each model + a '<name>-single' endpoint" >&2
  echo "in your infer-stack catalog (see ../olmo_models/config/infer_stack/catalog.yaml)," >&2
  echo "or point INFER_STACK_CONFIG_DIR at a config that has them." >&2
  exit 1
fi

echo "OK: all ${#OLMO_COMBINED_ENDPOINTS[@]} combined-preset serving endpoints are defined."

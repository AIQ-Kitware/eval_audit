#!/usr/bin/env bash
# Preflight: confirm the six OLMo infer-stack serving endpoints exist before we
# try to serve them. The OLMo presets in
# eval_audit/integrations/infer_stack/adapter.py reference endpoints named
# "<preset>-single", shipped here in config/infer_stack/catalog.yaml (not in the
# infer_stack submodule). They must be provided by the operator's infer-stack
# config. This script fails fast with guidance if any are missing, rather than
# letting `infer-stack acquire` error mid-grid.
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
for target in "${OLMO_TARGETS[@]}"; do
  endpoint="$(olmo_profile "$target")"
  if ! grep -qw -- "$endpoint" <<<"$available"; then
    missing+=("$endpoint")
  fi
done

if (( ${#missing[@]} > 0 )); then
  echo "FAIL: the following OLMo infer-stack endpoints are not defined:" >&2
  printf '  - %s\n' "${missing[@]}" >&2
  echo >&2
  echo "These presets exist in adapter.py but their serving endpoints are not in" >&2
  echo "the active catalog. Define each model + a '<name>-single' endpoint in your" >&2
  echo "infer-stack catalog (see config/infer_stack/catalog.yaml for the schema)," >&2
  echo "or point INFER_STACK_CONFIG_DIR at a config that has them." >&2
  exit 1
fi

echo "OK: all ${#OLMO_TARGETS[@]} OLMo serving endpoints are defined."

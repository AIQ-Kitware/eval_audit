#!/usr/bin/env bash
# Preflight: confirm the six OLMo infer-stack serving profiles exist before we
# try to switch into them. The OLMo presets in
# eval_audit/integrations/infer_stack/adapter.py reference profiles named
# "<preset>-single", but those profiles are NOT shipped in
# this repo's infer_stack submodule builtin catalog. They must be provided by
# the operator's infer-stack config (models.yaml + profiles). This script fails
# fast with guidance if any are missing, rather than letting `infer-stack
# switch` error mid-grid.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

available="$(infer-stack list-profiles 2>/dev/null || true)"
if [[ -z "$available" ]]; then
  echo "WARN: 'infer-stack list-profiles' produced no output; cannot validate." >&2
  echo "      Ensure infer-stack is installed and INFER_STACK_CONFIG_DIR points" >&2
  echo "      at your config. Continuing without validation." >&2
  exit 0
fi

missing=()
for target in "${OLMO_TARGETS[@]}"; do
  profile="$(olmo_profile "$target")"
  if ! grep -qw -- "$profile" <<<"$available"; then
    missing+=("$profile")
  fi
done

if (( ${#missing[@]} > 0 )); then
  echo "FAIL: the following OLMo infer-stack profiles are not defined:" >&2
  printf '  - %s\n' "${missing[@]}" >&2
  echo >&2
  echo "These presets exist in adapter.py but their serving profiles do not ship" >&2
  echo "in submodules/infer_stack. Define each model + a '<name>-single' profile" >&2
  echo "in your infer-stack config (see dev/e2e-tests/config/infer_stack/models.yaml" >&2
  echo "for the schema), or point INFER_STACK_CONFIG_DIR at a config that has them." >&2
  exit 1
fi

echo "OK: all ${#OLMO_TARGETS[@]} OLMo serving profiles are defined."

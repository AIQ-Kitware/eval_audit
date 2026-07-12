#!/usr/bin/env bash
# Preflight: confirm the infer-stack serving endpoints the targets lease exist
# (gptj6b-single / gptneox20b-single / opt66b-single, shipped in
# config/infer_stack/catalog.yaml) before the grid tries to serve them.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

available="$(infer-stack catalog endpoint list 2>/dev/null || true)"
if [[ -z "$available" ]]; then
  echo "WARN: 'infer-stack catalog endpoint list' produced no output; cannot validate." >&2
  echo "      Ensure infer-stack is installed and INFER_STACK_CONFIG_DIR points at" >&2
  echo "      config/infer_stack. Continuing without validation." >&2
  exit 0
fi

missing=()
while read -r endpoint; do
  [[ -z "$endpoint" ]] && continue
  grep -qw -- "$endpoint" <<<"$available" || missing+=("$endpoint")
done < <(_endpoints_from_targets)

if (( ${#missing[@]} > 0 )); then
  echo "FAIL: the following infer-stack endpoints are not defined:" >&2
  printf '  - %s\n' "${missing[@]}" >&2
  echo >&2
  echo "Define each model + endpoint in your infer-stack catalog (the shipped" >&2
  echo "config/infer_stack/catalog.yaml is the reference), or point" >&2
  echo "INFER_STACK_CONFIG_DIR at a config that has them." >&2
  exit 1
fi

echo "OK: all required serving endpoints are defined ($(_endpoints_from_targets | tr '\n' ' '))."
echo "Next: ./06_check_era_images.sh"

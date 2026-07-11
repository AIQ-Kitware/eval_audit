#!/usr/bin/env bash
# Preflight: confirm the infer-stack serving endpoint the era targets lease
# exists before the grid tries to serve it. Both eras share the redpajama3b-single
# endpoint (shipped here in config/infer_stack/catalog.yaml). Fails fast with
# guidance if it is missing, rather than letting `infer-stack acquire` error
# mid-grid. Mirrors dev/e2e-tests/05_check_profiles.sh.
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

declare -A seen=()
missing=()
for target in "${ERA_TARGETS[@]}"; do
  endpoint="$(era_endpoint "$target")"
  [[ -n "${seen[$endpoint]:-}" ]] && continue
  seen["$endpoint"]=1
  if ! grep -qw -- "$endpoint" <<<"$available"; then
    missing+=("$endpoint")
  fi
done

if (( ${#missing[@]} > 0 )); then
  echo "FAIL: the following infer-stack endpoints are not defined:" >&2
  printf '  - %s\n' "${missing[@]}" >&2
  echo >&2
  echo "Define each model + endpoint in your infer-stack catalog (the shipped" >&2
  echo "config/infer_stack/catalog.yaml is the reference), or point" >&2
  echo "INFER_STACK_CONFIG_DIR at a config that has them." >&2
  exit 1
fi

echo "OK: all required serving endpoints are defined."
echo "Next: ./06_check_era_images.sh"

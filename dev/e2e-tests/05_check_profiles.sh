#!/usr/bin/env bash
# Preflight: confirm the infer-stack serving endpoints the vLLM targets serve
# exist before we try to serve them. The phi-2 vLLM presets in
# eval_audit/integrations/infer_stack/adapter.py reference the "phi2-single"
# endpoint, which is shipped here in config/infer_stack/catalog.yaml (not in the
# infer_stack submodule). This script fails fast with guidance if it is missing,
# rather than letting `infer-stack serve` error mid-grid.
#
# The huggingface target needs no serving endpoint (HELM loads microsoft/phi-2
# directly from HuggingFace), so it is skipped here.
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
for target in "${E2E_TARGETS[@]}"; do
  [[ "$(e2e_transport "$target")" == "vllm" ]] || continue
  endpoint="$(e2e_serving "$target")"
  [[ -n "${seen[$endpoint]:-}" ]] && continue
  seen["$endpoint"]=1
  if ! grep -qw -- "$endpoint" <<<"$available"; then
    missing+=("$endpoint")
  fi
done

if (( ${#missing[@]} > 0 )); then
  echo "FAIL: the following phi-2 infer-stack endpoints are not defined:" >&2
  printf '  - %s\n' "${missing[@]}" >&2
  echo >&2
  echo "Define each model + endpoint in your infer-stack catalog (the shipped" >&2
  echo "config/infer_stack/catalog.yaml is the reference), or point" >&2
  echo "INFER_STACK_CONFIG_DIR at a config that has them." >&2
  exit 1
fi

echo "OK: all required phi-2 serving endpoints are defined."

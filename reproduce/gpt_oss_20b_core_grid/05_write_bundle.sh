#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STORE_ROOT="${AUDIT_STORE_ROOT:-/data/crfm-helm-audit-store}"
BUNDLE_ROOT="${GPT_OSS_BUNDLE_ROOT:-$STORE_ROOT/local-bundles/gpt_oss_20b_core_grid}"
ENV_FPATH="${LITELLM_ENV_FPATH:-$ROOT/submodules/vllm_service/generated/.env}"
LITELLM_BASE_URL="${LITELLM_BASE_URL:-http://localhost:14000}"

if [[ -f "$ENV_FPATH" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FPATH"
  set +a
fi
if [[ -z "${LITELLM_MASTER_KEY:-}" ]]; then
  echo "FAIL: LITELLM_MASTER_KEY not set after sourcing $ENV_FPATH." >&2
  echo "      Set LITELLM_MASTER_KEY=... in your shell or override LITELLM_ENV_FPATH." >&2
  exit 1
fi

cd "$ROOT"
python -m eval_audit.integrations.vllm_service export-benchmark-bundle \
  --preset gpt_oss_20b_core_grid \
  --bundle-root "$BUNDLE_ROOT" \
  --base-url "${LITELLM_BASE_URL}/v1"

echo "$BUNDLE_ROOT"

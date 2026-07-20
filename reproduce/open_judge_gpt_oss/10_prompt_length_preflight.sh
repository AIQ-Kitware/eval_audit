#!/usr/bin/env bash
# §14.3: size max_model_len from the ACTUAL judge prompts before serving.
# Reports a recommended max_model_len; if it exceeds the catalog's value,
# raise catalog.yaml's runtime.max_model_len for the affected endpoints.
# Uses the real Qwen tokenizer when reachable, else a conservative estimate.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

shopt -s nullglob
snapshots=("$OJ_SNAPSHOT_ROOT"/*/)
[[ ${#snapshots[@]} -gt 0 ]] || { echo "FAIL: no snapshots (run 08 first)." >&2; exit 1; }

TOKENIZER="${OJ_PROMPT_TOKENIZER:-Qwen/Qwen3.5-27B}"
eval-audit-judge-prompt-lengths "${snapshots[@]}" \
  --tokenizer "$TOKENIZER" \
  --output "$OJ_ROOT/prompt-lengths.json"
echo
echo "If 'recommended max_model_len' exceeds catalog.yaml's runtime.max_model_len"
echo "(currently 32768) for either endpoint, raise it there before serving."

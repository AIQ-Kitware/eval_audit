#!/usr/bin/env bash
# Drive the OLMo-7B deployment matrix: bring each endpoint up one at a time,
# query it with the fixed prompt set, release it, then print the comparison.
#
# Each variant takes a whole GPU (catalog `reclaim: stop`) and they will not
# co-host, so this is strictly serial: acquire -> query -> release --evict, with
# `infer-stack gc` bracketing the loop to reclaim any leaked lease.
#
# Usage (on a GPU host with the infer-stack CLI + docker):
#   ./run_matrix.sh                       # all matrix endpoints
#   ./run_matrix.sh olmo7b-dbg-auto olmo7b-dbg-bf16   # a subset
#
# Knobs (env):
#   INFER_STACK_CONFIG_DIR  default: this dir (loads the debug catalog+settings)
#   INFER_STACK_ALLOWED_GPUS default: 0
#   LITELLM_PORT / LITELLM_BASE_URL  default: 14042 / http://localhost:14042
#   MAX_TOKENS              default: 60
#   RESULTS_DIR            default: ./results
#   REFERENCE             default: hf-bf16 if results/hf-bf16.json exists, else olmo7b-dbg-bf16
#   PY                     default: python3   (only needs the stdlib)
#
# Ground-truth reference (optional but recommended): before/after this run,
# generate the HF-transformers oracle row(s) so the report can score each vLLM
# variant against them:
#   python olmo_hf_reference.py --dtype bfloat16 --out results/hf-bf16.json
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export INFER_STACK_CONFIG_DIR="${INFER_STACK_CONFIG_DIR:-$HERE}"
export INFER_STACK_ALLOWED_GPUS="${INFER_STACK_ALLOWED_GPUS:-0}"
LITELLM_PORT="${LITELLM_PORT:-14042}"
LITELLM_BASE_URL="${LITELLM_BASE_URL:-http://localhost:$LITELLM_PORT}"
MAX_TOKENS="${MAX_TOKENS:-60}"
RESULTS_DIR="${RESULTS_DIR:-$HERE/results}"
PROMPTS="${PROMPTS:-$HERE/prompts.jsonl}"
PY="${PY:-python3}"

ENDPOINTS=(
  olmo7b-dbg-auto
  olmo7b-dbg-fp16
  olmo7b-dbg-bf16
  olmo7b-dbg-fp32
  olmo7b-dbg-chat-bf16
  olmo7b-dbg-orig-bf16
  olmo7b-dbg-0724-bf16
)
if [ "$#" -gt 0 ]; then ENDPOINTS=("$@"); fi

if ! command -v infer-stack >/dev/null 2>&1; then
  echo "FATAL: 'infer-stack' CLI not found on PATH." >&2
  echo "Install infer-stack (submodules/infer_stack) on this GPU host, or query" >&2
  echo "already-running endpoints manually with compare_deployments.py query." >&2
  exit 1
fi

protocol_for() { case "$1" in *chat*) echo chat ;; *) echo completions ;; esac; }

mkdir -p "$RESULTS_DIR"
ENVFILE="$(mktemp "${TMPDIR:-/tmp}/olmo-matrix.env.XXXXXX")"
trap 'rm -f "$ENVFILE"' EXIT

echo "[matrix] config dir : $INFER_STACK_CONFIG_DIR"
echo "[matrix] gateway     : $LITELLM_BASE_URL"
echo "[matrix] results dir : $RESULTS_DIR"
echo "[matrix] endpoints   : ${ENDPOINTS[*]}"
echo

echo "[matrix] reclaiming any leaked leases (infer-stack gc)…"
infer-stack gc --yes || echo "WARN: 'infer-stack gc' returned nonzero; continuing." >&2

MASTER_KEY=""
for ep in "${ENDPOINTS[@]}"; do
  proto="$(protocol_for "$ep")"
  echo
  echo "==================== $ep  (protocol=$proto) ===================="
  if infer-stack acquire "$ep" --yes --env-file "$ENVFILE"; then
    if [ -z "$MASTER_KEY" ]; then
      MASTER_KEY="$(infer-stack env LITELLM_MASTER_KEY 2>/dev/null || true)"
    fi
  else
    echo "WARN: 'infer-stack acquire $ep' failed; querying anyway (records errors)." >&2
  fi

  # Always query: if acquire failed / the model never became routable, the probe
  # records per-prompt errors and the report shows the variant as NO_DATA rather
  # than silently dropping it.
  "$PY" "$HERE/compare_deployments.py" query \
    --base-url "$LITELLM_BASE_URL/v1" --model "$ep" --label "$ep" \
    --protocol "$proto" --prompts "$PROMPTS" --max-tokens "$MAX_TOKENS" \
    ${MASTER_KEY:+--api-key "$MASTER_KEY"} \
    --out "$RESULTS_DIR/$ep.json" \
    || echo "WARN: query $ep failed." >&2

  infer-stack release --env-file "$ENVFILE" --evict --yes \
    || echo "WARN: 'infer-stack release $ep --evict' returned nonzero; continuing." >&2
done

echo
echo "[matrix] reclaiming leases (infer-stack gc)…"
infer-stack gc --yes || echo "WARN: 'infer-stack gc' returned nonzero; continuing." >&2

# Reference for the agreement column: prefer the HF-transformers oracle if it was
# generated, else the healthy vLLM bf16 variant.
REFERENCE="${REFERENCE:-}"
if [ -z "$REFERENCE" ]; then
  if [ -f "$RESULTS_DIR/hf-bf16.json" ]; then REFERENCE="hf-bf16"; else REFERENCE="olmo7b-dbg-bf16"; fi
fi

echo
echo "[matrix] building comparison report (reference=$REFERENCE)…"
"$PY" "$HERE/compare_deployments.py" report "$RESULTS_DIR" \
  --reference "$REFERENCE" --out "$RESULTS_DIR/report.json"

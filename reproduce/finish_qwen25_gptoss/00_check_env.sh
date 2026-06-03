#!/usr/bin/env bash
# Preflight for the finish_qwen25_gptoss audit batch.
#
# Verifies:
#   - eval_audit CLIs on $PATH
#   - GPU layout: 4 visible GPUs, each with enough free VRAM
#   - $AUDIT_STORE_ROOT / $AUDIT_RESULTS_ROOT exist + writable
#   - HuggingFace cache reachable (we don't download here, but later
#     steps need it for the gpt-oss tokenizer)
#   - infer_stack repo present at submodules/infer_stack and
#     manage.py invokable
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STORE_ROOT="${AUDIT_STORE_ROOT:-/data/crfm-helm-audit-store}"
RESULTS_ROOT="${AUDIT_RESULTS_ROOT:-/data/crfm-helm-audit}"
HF_CACHE_DIR="${HF_HOME:-$HOME/.cache/huggingface}"

cd "$ROOT"

echo "== eval_audit CLIs =="
which eval-audit-check-env || { echo "FAIL: eval-audit-check-env not on PATH; run 'uv pip install -e .' first" >&2; exit 1; }
which eval-audit-run || { echo "FAIL: eval-audit-run not on PATH" >&2; exit 1; }
which eval-audit-index || { echo "FAIL: eval-audit-index not on PATH" >&2; exit 1; }
eval-audit-check-env

echo
echo "== GPU layout =="
if ! command -v nvidia-smi >/dev/null 2>&1; then
  echo "FAIL: nvidia-smi not found" >&2
  exit 1
fi
nvidia-smi --query-gpu=index,name,memory.total,memory.free --format=csv,noheader
N_GPUS="$(nvidia-smi --query-gpu=index --format=csv,noheader | wc -l)"
if [[ "$N_GPUS" -lt 4 ]]; then
  echo "WARN: only $N_GPUS GPUs visible; the pythia-qwen25-gptoss-mixed-4x96 profile expects 4." >&2
fi

# gpt-oss-20b needs ~40 GiB VRAM headroom on its GPU.
GPU0_FREE_MIB="$(nvidia-smi --id=0 --query-gpu=memory.free --format=csv,noheader,nounits)"
if [[ "$GPU0_FREE_MIB" -lt 41000 ]]; then
  echo "WARN: GPU 0 has ${GPU0_FREE_MIB} MiB free; gpt-oss-20b expects ~40 GiB. The profile will warn or fail at fit-validation time." >&2
fi

echo
echo "== filesystem =="
for d in "$STORE_ROOT" "$RESULTS_ROOT" "$HF_CACHE_DIR"; do
  if [[ ! -d "$d" ]]; then
    echo "FAIL: $d does not exist" >&2
    exit 1
  fi
  if [[ ! -w "$d" ]]; then
    echo "FAIL: $d is not writable by $(whoami)" >&2
    exit 1
  fi
  echo "  OK: $d"
done

echo
echo "== infer_stack submodule =="
INFER_STACK_ROOT="$ROOT/submodules/infer_stack"
if [[ ! -f "$INFER_STACK_ROOT/manage.py" ]]; then
  echo "FAIL: $INFER_STACK_ROOT/manage.py missing — submodule not checked out?" >&2
  exit 1
fi
echo "  OK: $INFER_STACK_ROOT/manage.py"

# Verify our profile is registered.
if (cd "$INFER_STACK_ROOT" && python manage.py list-profiles 2>/dev/null | grep -q pythia-qwen25-gptoss-mixed-4x96); then
  echo "  OK: pythia-qwen25-gptoss-mixed-4x96 is registered"
else
  echo "FAIL: profile pythia-qwen25-gptoss-mixed-4x96 not registered with infer_stack. Did the submodule update apply?" >&2
  exit 1
fi

echo
echo "preflight OK; next: ./05_write_bundle.sh"

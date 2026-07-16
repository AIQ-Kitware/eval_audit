#!/usr/bin/env bash
set -euo pipefail

MODEL_NAME="${MODEL_NAME:-Qwen/Qwen3.5-9B-Base}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
TP_SIZE="${TP_SIZE:-1}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-32768}"
GPU_MEM_UTIL="${GPU_MEM_UTIL:-0.9}"
# Quadro RTX 8000 (Turing, sm_75) has no native bf16; Qwen3.5 ships bf16
# weights. vLLM usually downcasts automatically, but pin float16 explicitly
# so the effective precision is recorded here rather than decided silently.
DTYPE="${DTYPE:-float16}"
# Default to the 48GB RTX 8000 (GPU 0 on yardrat); override to move cards.
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"

# HELM's vLLM client sends api_key="EMPTY".
# The simplest compatible path is to omit --api-key or set it to EMPTY.
exec vllm serve "${MODEL_NAME}" \
  --host "${HOST}" \
  --port "${PORT}" \
  --tensor-parallel-size "${TP_SIZE}" \
  --max-model-len "${MAX_MODEL_LEN}" \
  --gpu-memory-utilization "${GPU_MEM_UTIL}" \
  --dtype "${DTYPE}"

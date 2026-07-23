#!/usr/bin/env bash
# 70_hf_fp32_probe.sh — find the HF forward-pass config that reproduces a HELM
# HuggingFaceClient official, to test whether the vLLM-fp32 e2e residual
# (+0.067/+0.082 on ifeval_strict_accuracy) is the vLLM<->HF engine gap.
#
# For OLMo-2 DENSE the HF probe does NOT reproduce the official out of the box:
# the 2026-07-10 "residual puzzle" found first-token agreement 0.42 on
# byte-identical prompts, traced to the fp32 FORWARD PASS — `device_map=auto`
# shards a fits-on-one-GPU model and changes the reduction order. So this is a
# CONFIG SEARCH, not a single run: at the known-correct axes (fp32, decode=helm
# matching HELM's do_sample/temp->1e-7, agp0 = the effective old-template
# behavior, ast1 = a non-factor for OLMo-2) it sweeps the two forward-pass axes
# that actually move greedy fp32 logits — attention impl {eager,sdpa} x device
# map {auto,single} = 4 cells — and scores each against the official.
#
# SMALL-N BY DESIGN: config-matching needs a sample, not the full set (the
# 07-10 probes used n=12; OLMoE matched at quasi 1.0 there). A cell hitting
# quasi ~1.0 means HF-fp32-<that config> reproduces the official => the official
# IS HF-fp32 and the vLLM e2e residual is the engine gap. Then CONFIRM the
# winner at full n:  DM_HF_ATTN=<w> DM_HF_DEVMAPS=<w> DM_N=541 ./70... <size>
#
# Self-contained: pure transformers on one GPU. No infer-stack, lease, vLLM,
# chat-template file, or litellm.
#
#   ./70_hf_fp32_probe.sh 7b [GPU]          # 4-cell sweep, n=32 (~20-40 min)
#   ./70_hf_fp32_probe.sh 13b 1
#   DM_HF_DEVMAPS=single DM_N=541 ./70_hf_fp32_probe.sh 7b   # full-n confirm of one config
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../_lib.sh"

SIZE="${1:-}"
GPU="${2:-0}"
case "$SIZE" in
  7b)  RUN="/data/crfm-helm-public/capabilities/benchmark_output/runs/v1.8.0/ifeval:num_output_tokens=2048,model=allenai_olmo-2-1124-7b-instruct" ;;
  13b) RUN="/data/crfm-helm-public/capabilities/benchmark_output/runs/v1.8.0/ifeval:num_output_tokens=2048,model=allenai_olmo-2-1124-13b-instruct" ;;
  *)   echo "usage: $0 <7b|13b> [gpu]" >&2; exit 1 ;;
esac

export DM_RUN="$RUN"
export DM_OUT="$STORE_ROOT/deployment-match/olmo-2-1124-${SIZE}-instruct--ifeval-hf-fp32"
export DM_HF_FP32=1
export DM_HF_DTYPES=float32
export DM_HF_DECODE=helm                    # match the official's decode (NOT true-greedy)
export DM_HF_AGP="${DM_HF_AGP:-0}"          # PIN agp0 — agp1 is known NOT to match OLMo-2
export DM_HF_AST="${DM_HF_AST:-1}"          # PIN ast1 — a non-factor for OLMo-2
# DM_HF_ATTN / DM_HF_DEVMAPS left to the tool's sweep ({eager,sdpa}x{auto,single})
# unless the caller narrows them (e.g. a full-n confirm of the winning cell).
export DM_N="${DM_N:-32}"                    # config-finding sample, not the full set
export DM_ALLOWED_GPUS="$GPU"

echo "== HF-fp32 config probe: olmo-2-1124-${SIZE}-instruct ifeval (GPU ${GPU}, n=${DM_N})"
echo "   sweeping attn x device_map at fp32/decode=helm/agp0/ast1; official: $DM_RUN"
echo "   out: $DM_OUT"
exec "$HERE/run_deployment_match.sh"

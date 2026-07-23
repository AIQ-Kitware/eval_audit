#!/usr/bin/env bash
# 70_hf_fp32_fullN_probe.sh — reproduce a HELM HuggingFaceClient official the
# SAME way it was produced and score it across the FULL instance set.
#
# WHY: the vLLM fp32+agp0 e2e (60_confirm_fp32_e2e.sh) left a systematic
# residual — local scored ABOVE official by +0.067 (7B) / +0.082 (13B) on
# ifeval_strict_accuracy. The officials were made by HELM's HuggingFaceClient
# (transformers.generate at fp32); our e2e used vLLM. Leading hypothesis: the
# residual is the vLLM<->HF engine gap. This probe removes that variable —
# transformers.generate at fp32 with HELM's own decode (do_sample=True,
# temp->1e-7) — and measures completion agreement vs the official across ALL
# instances. High agreement => the official is faithfully HF-fp32 and the vLLM
# e2e residual is the engine gap; low agreement => a deeper unrecovered factor.
#
# SELF-CONTAINED and low-risk: a thin wrapper over the PROVEN hf-probe path
# (run_deployment_match.sh DM_HF_FP32=1, already run at n=12). Pure transformers
# on one GPU — NO infer-stack, NO lease, NO vLLM, NO chat-template file, NO
# litellm — so the serving/contention/template failure modes of the vLLM path
# cannot occur here.
#
#   ./70_hf_fp32_fullN_probe.sh 7b [GPU]     # default GPU 0, all 541 instances
#   ./70_hf_fp32_fullN_probe.sh 13b 1
#   DM_N=50 ./70_hf_fp32_fullN_probe.sh 7b   # smaller sample (smoke)
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

# DM_N defaults to 541 (the full ifeval eval set); the oracle takes all when N
# equals the instance count. Override DM_N for a quick smoke.
export DM_RUN="$RUN"
export DM_OUT="$STORE_ROOT/deployment-match/olmo-2-1124-${SIZE}-instruct--ifeval-hf-fp32-fullN"
export DM_HF_FP32=1
export DM_HF_DTYPES=float32
export DM_HF_DECODE=helm          # match the official's decode exactly (not true-greedy)
export DM_N="${DM_N:-541}"
export DM_ALLOWED_GPUS="$GPU"

echo "== HF-fp32 full-N probe: olmo-2-1124-${SIZE}-instruct ifeval (GPU ${GPU}, n=${DM_N}, decode=helm)"
echo "   official : $DM_RUN"
echo "   out      : $DM_OUT"
exec "$HERE/run_deployment_match.sh"

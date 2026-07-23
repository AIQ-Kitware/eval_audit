#!/usr/bin/env bash
# 72_hf_fp32_family.sh — HF-fp32 substrate probe for ANY HELM HuggingFaceClient
# official. Tests whether the OLMo-2 result generalizes across families:
#   * TREATMENT (official pinned NO dtype -> ran fp32 by default): does HF-fp32
#     reproduce the official? (predict: yes, byte-exact, like OLMo-2.)
#   * CONTROL (official PINNED a dtype, e.g. bf16): does the PINNED dtype win and
#     fp32 lose? (predict: yes — the method tracks the recorded substrate.)
#
# Sweeps dtype {float32,bfloat16} by default so the winning dtype is read straight
# off results/ranking.txt. Pure transformers on one GPU — self-contained (no
# infer-stack / lease / vLLM / template file), so no serving/contention risk.
#
#   ./72_hf_fp32_family.sh <label> <official_run_dir> [gpu]
#
# Env (all optional):
#   DM_PROTOCOL   completions|chat — set completions for BASE models / known
#                 non-chat officials (avoids the resolver's silent chat default,
#                 the marin trap). Omit to let the tool resolve.
#   DM_HF_DTYPES  default 'float32,bfloat16' (the substrate axis under test)
#   DM_HF_ATTN    default 'eager'   (OLMo-2 showed attn is a non-factor)
#   DM_HF_DEVMAPS default 'single'  (deterministic 1-GPU reduction)
#   DM_HF_AGP     true|false|both — chat models only; set 'both' when the
#                 template's add_generation_prompt behavior is unknown
#   DM_N          default 32 (config-finding sample)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../_lib.sh"

LABEL="${1:?usage: $0 <label> <official_run_dir> [gpu]}"
RUN="${2:?usage: $0 <label> <official_run_dir> [gpu]}"
GPU="${3:-0}"
[[ -f "$RUN/scenario_state.json" ]] || {
  echo "FAIL: no scenario_state.json in $RUN — need the official completions (oracle)." >&2; exit 1; }

export DM_RUN="$RUN"
export DM_OUT="$STORE_ROOT/deployment-match/family--${LABEL}--hf-fp32"
export DM_HF_FP32=1
export DM_HF_DTYPES="${DM_HF_DTYPES:-float32,bfloat16}"
export DM_HF_ATTN="${DM_HF_ATTN:-eager}"
export DM_HF_DEVMAPS="${DM_HF_DEVMAPS:-single}"
export DM_HF_DECODE="${DM_HF_DECODE:-helm}"
export DM_N="${DM_N:-32}"
export DM_ALLOWED_GPUS="$GPU"
# Pass through the optional per-family knobs only when the caller set them, so
# they keep run_deployment_match.sh's own defaults otherwise.
[[ -n "${DM_PROTOCOL:-}" ]] && export DM_PROTOCOL
[[ -n "${DM_HF_AGP:-}" ]] && export DM_HF_AGP
[[ -n "${DM_HF_AST:-}" ]] && export DM_HF_AST

echo "== HF-fp32 family probe: ${LABEL} (GPU ${GPU})"
echo "   dtypes=${DM_HF_DTYPES} protocol=${DM_PROTOCOL:-auto} attn=${DM_HF_ATTN} device=${DM_HF_DEVMAPS} n=${DM_N}"
echo "   official: ${RUN}"
echo "   out     : ${DM_OUT}"
exec "$HERE/run_deployment_match.sh"

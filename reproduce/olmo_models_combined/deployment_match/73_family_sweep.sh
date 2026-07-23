#!/usr/bin/env bash
# 73_family_sweep.sh — fan the HF-fp32 substrate probe across model families, one
# per GPU, concurrent. Tests whether the OLMo-2 fp32 result generalizes.
#
#   TREATMENT (official pinned NO dtype -> ran fp32): pythia-6.9b (GPT-NeoX,base),
#             vicuna-7b (Llama-1,instruct), granite-4.0-micro (Granite,instruct).
#             Predict: HF-fp32 reproduces the official; bf16 does not.
#   CONTROL   (official PINNED bf16): gemma-2-9b-it. Predict: bf16 reproduces,
#             fp32 does NOT — the method tracks the RECORDED substrate.
#
# Each probe sweeps dtype {float32,bfloat16} (eager/single, decode=helm, n=32) and
# writes results/ranking.txt. Read the winning dtype per family to fill the
# generality table. Pure transformers, one GPU each, no infra — fully independent.
#
#   ./73_family_sweep.sh                 # all 4 across GPUs 0-3
#   FAMILIES="pythia" ./73_family_sweep.sh   # just one (validate first)
#
# NOTE gemma-2-9b-it is a GATED HF repo — needs a token with Gemma access; if it
# fails to download, the 3 treatment families still stand.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PUB=/data/crfm-helm-public

# label | gpu | protocol | agp | official-run-dir
# (protocol/agp chosen per family: base+classic-narrative_qa run as completions;
#  ifeval is chat with unknown add_generation_prompt -> sweep agp=both.)
declare -A RUN PROTO AGP GPU
RUN[pythia]="$PUB/classic/benchmark_output/runs/v0.2.4/narrative_qa:model=eleutherai_pythia-6.9b,data_augmentation=canonical"
PROTO[pythia]=completions; AGP[pythia]=""; GPU[pythia]=0
RUN[vicuna]="$PUB/classic/benchmark_output/runs/v0.3.0/narrative_qa:model=lmsys_vicuna-7b-v1.3,data_augmentation=canonical"
PROTO[vicuna]=completions; AGP[vicuna]=""; GPU[vicuna]=1
RUN[granite]="$PUB/capabilities/benchmark_output/runs/v1.13.0-granite-preview/ifeval:model=ibm_granite-4.0-micro"
PROTO[granite]=chat; AGP[granite]=both; GPU[granite]=2
RUN[gemma]="$PUB/lite/benchmark_output/runs/v1.6.0/narrative_qa:model=google_gemma-2-9b-it"
PROTO[gemma]=completions; AGP[gemma]=""; GPU[gemma]=3

read -r -a FAMS <<<"${FAMILIES:-pythia vicuna granite gemma}"

declare -a PIDS=() NAMES=()
for fam in "${FAMS[@]}"; do
  rd="${RUN[$fam]:-}"
  [[ -n "$rd" ]] || { echo "unknown family: $fam" >&2; continue; }
  log="$HERE/family_${fam}.log"
  echo ">>> $(date '+%F %T')  ${fam} on GPU ${GPU[$fam]} (protocol=${PROTO[$fam]}) -> ${log}"
  (
    export DM_PROTOCOL="${PROTO[$fam]}"
    [[ -n "${AGP[$fam]}" ]] && export DM_HF_AGP="${AGP[$fam]}"
    "$HERE/72_hf_fp32_family.sh" "$fam" "$rd" "${GPU[$fam]}"
  ) >"$log" 2>&1 &
  PIDS+=($!); NAMES+=("$fam")
done

echo
echo "watch:  tail -f ${HERE}/family_*.log"
echo
rc=0
for i in "${!PIDS[@]}"; do
  wait "${PIDS[$i]}" && echo ">>> done: ${NAMES[$i]}" || { echo ">>> FAILED: ${NAMES[$i]}" >&2; rc=1; }
done

echo
echo "Read the winning dtype+config per family:"
for fam in "${FAMS[@]}"; do
  echo "  ${fam}: <STORE>/deployment-match/family--${fam}--hf-fp32/results/ranking.txt"
done
exit "$rc"

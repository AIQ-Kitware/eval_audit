#!/usr/bin/env bash
# 71_hf_fp32_both.sh — run the HF-fp32 config probe for 7B and 13B CONCURRENTLY,
# one model per GPU.
#
# Not serial: the HF path holds NO lease and never converges infer-stack, so
# there is no shared mutable state and no contention (the thing that killed the
# vLLM 13B e2e). Two disjoint CUDA_VISIBLE_DEVICES run fully independently.
# One GPU per model is enough — a 7B/13B fp32 fits on one 96GB card, so
# device_map=auto won't shard it anyway; the auto/single arm still tests the
# accelerate dispatch-path difference.
#
# Default: 7b -> GPU 0, 13b -> GPU 1 (GPUs 2,3 free for the marin sweep or a 32B
# probe). Each model's 4-cell sweep (attn x device_map at fp32/decode=helm/agp0/
# ast1, n=32) takes ~20-40 min; both finish together. Per-model logs because
# concurrent stdout would interleave — tail them.
#
#   ./71_hf_fp32_both.sh                       # 7b@0, 13b@1, concurrent
#   GPU_7B=0 GPU_13B=2 ./71_hf_fp32_both.sh    # pick cards
#   SIZES="7b" ./71_hf_fp32_both.sh            # one model
#
# Prefer live output in your own tmux panes? Just run 70 twice, one per pane:
#   ./70_hf_fp32_probe.sh 7b 0        # pane 1
#   ./70_hf_fp32_probe.sh 13b 1       # pane 2
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

declare -A GPU=( [7b]="${GPU_7B:-0}" [13b]="${GPU_13B:-1}" )
read -r -a SIZES <<<"${SIZES:-7b 13b}"

declare -a PIDS=() LOGS=() NAMES=()
for size in "${SIZES[@]}"; do
  log="$HERE/hf_fp32_${size}.log"
  echo ">>> $(date '+%F %T')  launching ${size} on GPU ${GPU[$size]} -> ${log}"
  ( "$HERE/70_hf_fp32_probe.sh" "$size" "${GPU[$size]}" ) >"$log" 2>&1 &
  PIDS+=($!); LOGS+=("$log"); NAMES+=("$size")
done

echo
echo "watch:  tail -f ${LOGS[*]}"
echo
rc=0
for i in "${!PIDS[@]}"; do
  if wait "${PIDS[$i]}"; then
    echo ">>> $(date '+%F %T')  done: ${NAMES[$i]}"
  else
    echo ">>> $(date '+%F %T')  FAILED: ${NAMES[$i]} (see ${LOGS[$i]})" >&2
    rc=1
  fi
done

echo
echo "Read each ranking (a cell at quasi ~1.0 = HF-fp32 reproduces the official):"
for size in "${SIZES[@]}"; do
  echo "  olmo-2-1124-${size}: <STORE>/deployment-match/olmo-2-1124-${size}-instruct--ifeval-hf-fp32/results/ranking.txt"
done
exit "$rc"

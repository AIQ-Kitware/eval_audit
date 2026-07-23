#!/usr/bin/env bash
# 71_overnight_hf_fp32.sh — the whole overnight in one command.
#
# Runs the HF-fp32 full-N probes for 7B then 13B SERIALLY on a single GPU. Serial
# by design: the vLLM 13B e2e died because a concurrent infer-stack converge tore
# down its leased endpoint (2026-07-22). This path holds no lease at all, and
# running one model at a time removes even the possibility of GPU contention.
#
# Walk away. Each model writes results/ranking.txt + scored.json under its own
# out dir; 7B (~a few hours at fp32 batch-1) will finish for sure, 13B likely by
# morning. Re-runnable: hf-probe is deterministic-ish and idempotent per out dir.
#
#   ./71_overnight_hf_fp32.sh              # 7b then 13b on GPU 0
#   OJ_GPU=1 ./71_overnight_hf_fp32.sh     # pin to a different card
#   SIZES="7b" ./71_overnight_hf_fp32.sh   # just one model
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

GPU="${OJ_GPU:-0}"
read -r -a SIZES <<<"${SIZES:-7b 13b}"

echo "=================================================================="
echo "== overnight HF-fp32 full-N probes: [${SIZES[*]}] on GPU ${GPU}"
echo "=================================================================="
rc_any=0
for size in "${SIZES[@]}"; do
  echo
  echo ">>> $(date '+%F %T')  starting HF-fp32 full-N probe: ${size}"
  if "$HERE/70_hf_fp32_fullN_probe.sh" "$size" "$GPU"; then
    echo ">>> $(date '+%F %T')  done: ${size}"
  else
    rc=$?
    echo ">>> $(date '+%F %T')  FAILED ${size} (exit ${rc}); continuing" >&2
    rc_any=1
  fi
done

echo
echo "All requested probes attempted. Read each: "
for size in "${SIZES[@]}"; do
  echo "  olmo-2-1124-${size}: <STORE>/deployment-match/olmo-2-1124-${size}-instruct--ifeval-hf-fp32-fullN/results/ranking.txt"
done
exit "$rc_any"

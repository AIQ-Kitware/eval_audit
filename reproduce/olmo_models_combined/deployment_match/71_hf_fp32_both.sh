#!/usr/bin/env bash
# 71_hf_fp32_both.sh — run the HF-fp32 config probe for 7B then 13B, SERIAL on
# one GPU. Fast now (each ~20-40 min at n=32), not an overnight: it finds which
# HF forward-pass config reproduces the official for each model.
#
# Serial single-GPU by design — no lease, no concurrent converge, so none of the
# contention that killed the vLLM 13B e2e. Each model writes results/ranking.txt
# + scored.json under its own out dir; read those to see which cell (if any)
# hits quasi ~1.0.
#
#   ./71_hf_fp32_both.sh              # 7b then 13b on GPU 0
#   OJ_GPU=1 ./71_hf_fp32_both.sh     # pin to a different card
#   SIZES="7b" ./71_hf_fp32_both.sh   # just one model
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

GPU="${OJ_GPU:-0}"
read -r -a SIZES <<<"${SIZES:-7b 13b}"

echo "=================================================================="
echo "== HF-fp32 config probes: [${SIZES[*]}] on GPU ${GPU}"
echo "=================================================================="
rc_any=0
for size in "${SIZES[@]}"; do
  echo
  echo ">>> $(date '+%F %T')  starting HF-fp32 config probe: ${size}"
  if "$HERE/70_hf_fp32_probe.sh" "$size" "$GPU"; then
    echo ">>> $(date '+%F %T')  done: ${size}"
  else
    rc=$?
    echo ">>> $(date '+%F %T')  FAILED ${size} (exit ${rc}); continuing" >&2
    rc_any=1
  fi
done

echo
echo "Read each ranking (look for a cell at quasi ~1.0 = HF-fp32 reproduces the official):"
for size in "${SIZES[@]}"; do
  echo "  olmo-2-1124-${size}: <STORE>/deployment-match/olmo-2-1124-${size}-instruct--ifeval-hf-fp32/results/ranking.txt"
done
exit "$rc_any"

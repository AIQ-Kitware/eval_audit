#!/usr/bin/env bash
# Run the FULL manifest for every (model x era) target — ALL ~226 official runs per
# model per suite, at the official 1000-instance cap. This is the batch that feeds
# 20->40; 10_run_smoke.sh is the breadth-complete depth-5 preflight.
#
# All targets are launched CONCURRENTLY and the infer-stack lease system arbitrates
# GPUs (run_grid_parallel in _lib.sh): the two eras of one model COALESCE onto a
# single served endpoint (one vLLM container, demand-refcounted), while different
# models QUEUE for GPU residency. Neither the era image nor the endpoint identity
# forces serialization. Per-target output -> out/logs/<experiment>.log; failures
# are reported at the end (nonzero exit).
#
# ⚠️ Large: 6 targets x ~226 runs. OPT-66B needs a multi-GPU host (TP=4). Narrow
# with TARGETS_OVERRIDE="era-gptj_6b-v0_2_4:helm-v0.2.4:gptj6b-single ..." to run a
# subset (see _lib.sh :: TARGETS).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

if [[ -n "${TARGETS_OVERRIDE:-}" ]]; then read -r -a TARGETS <<<"$TARGETS_OVERRIDE"; fi

run_grid_parallel full

echo "Next: ./20_index_local.sh"

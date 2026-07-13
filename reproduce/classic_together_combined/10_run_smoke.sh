#!/usr/bin/env bash
# Run the SMOKE manifest for every (model x era) target, CONCURRENTLY. Each target
# replays inside its own era-pinned CPU-only image; inference is served on the host
# by modern vLLM (the <model>-single endpoint behind LiteLLM); the era container is
# an HTTP client (container_gpus: none, --network host) that self-acquires the
# model's GPU lease per run (eval-audit-run --lease).
#
# All targets are launched in parallel and the infer-stack lease system arbitrates
# GPUs (run_grid_parallel in _lib.sh): the two eras of one model COALESCE onto a
# single served endpoint (one vLLM container, demand-refcounted), while different
# models QUEUE for GPU residency. Neither the era image nor the endpoint identity
# forces serialization. Per-target output -> out/logs/<experiment>.log; failures
# are reported at the end (nonzero exit).
#
# NB the smoke here is breadth-complete, depth-5: the SAME ~226 run_entries as the
# full run, capped at 5 eval instances each (SMOKE_CAP in gen_presets.py). It is a
# full-coverage dry-run of the freeze + era-shim paths, not a couple of probes.
#
# export-benchmark-bundle --freeze-rel-paths bakes from_run_spec + frozen
# run_spec_sources + era: into the runnable manifest. The broad classic root is
# AMBIGUOUS (these models' runs exist at both v0.2.4 and v0.3.0 with identical
# names), so run_grid_parallel overrides --precomputed-root with a per-era
# suite-scoped VIEW (era_corpus_view), pre-created once to avoid concurrent races.
#
# Narrow the set with TARGETS_OVERRIDE="<row> <row>" (see _lib.sh :: TARGETS).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

if [[ -n "${TARGETS_OVERRIDE:-}" ]]; then read -r -a TARGETS <<<"$TARGETS_OVERRIDE"; fi

run_grid_parallel smoke

echo "Next: ./15_run_full.sh"

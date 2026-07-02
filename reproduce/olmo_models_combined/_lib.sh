#!/usr/bin/env bash
# Shared definitions for the COMBINED multi-model OLMo fan-out runbook.
# Source this from the numbered scripts: `source "$(dirname "$0")/_lib.sh"`.
#
# This runbook is the sibling of ../olmo_models. The single-model runbook runs the
# seven OLMo presets one at a time (a serial bash loop over OLMO_TARGETS, each its
# own export + `eval-audit-run` schedule). This one instead runs a SINGLE
# multi-deployment preset — `allenai-olmo-combined`
# (adapter.PRESET_CONFIGS) — exported with `--freeze-rel-paths` and scheduled with
# `eval-audit-run --tmux-workers N`, so cmd_queue issues N concurrent per-run
# leases and infer-stack co-hosts what fits on INFER_STACK_ALLOWED_GPUS /
# serializes the rest. Five OLMo models fan out across GPUs under ONE schedule.
# See docs/planning/olmo-multi-model-from-spec-plan.md §4.4/§4.7.
#
# The serving / leasing / container / HuggingFace-auth / infer-stack-config setup
# is IDENTICAL to the single-model runbook, so we inherit it verbatim by sourcing
# the sibling `_lib.sh` (one source of truth — no drift), then override only the
# combined-specific bits below. In particular this reuses the sibling's
# INFER_STACK_CONFIG_DIR (the shipped OLMo catalog with the <preset>-single
# endpoints), OLMO_CONTAINER_IMAGE, HF token resolution, INFER_STACK_ALLOWED_GPUS,
# INFER_STACK_DATA_DIR resolution, and the EVAL_AUDIT_* group-strip conventions.

_combined_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../olmo_models/_lib.sh
source "$_combined_here/../olmo_models/_lib.sh"

# --- combined-specific overrides ------------------------------------------------

# Group the single combined full experiment (not the sibling's seven). Override
# with OLMO_COMBINED_VEXP_MANIFEST; VEXP_MANIFEST (set by the sibling _lib to the
# seven-experiment olmo-models.yaml) is repointed here.
VEXP_MANIFEST="${OLMO_COMBINED_VEXP_MANIFEST:-$ROOT/configs/virtual-experiments/olmo-models-combined.yaml}"

# The one multi-deployment preset and its per-mode experiment names / bundle root
# (mirrors adapter.PRESET_CONFIGS["allenai-olmo-combined"]'s smoke/full blocks).
OLMO_COMBINED_PRESET="allenai-olmo-combined"
OLMO_COMBINED_EXPERIMENT_SMOKE="audit-allenai-olmo-combined-smoke"
OLMO_COMBINED_EXPERIMENT_FULL="audit-allenai-olmo-combined-full"
OLMO_COMBINED_BUNDLE_ROOT="$STORE_ROOT/local-bundles/$OLMO_COMBINED_PRESET"

# The five serving endpoints the combined preset's `profiles` reference — a subset
# of the sibling catalog. The base olmo-7b is NOT in this bundle: its per-subject
# MMLU runs exist under both the /mmlu and /lite suites, so they are AMBIGUOUS
# under the shared parent root this bundle freezes against. It therefore runs as
# two SEPARATE single-model suites (narrow per-suite roots) that are folded into
# the SAME virtual experiment — see OLMO_COMBINED_EXTRA_PRESETS below.
OLMO_COMBINED_ENDPOINTS=(
  allenai-olmo-1-7-7b-single
  allenai-olmo-2-1124-7b-instruct-single
  allenai-olmo-2-1124-13b-instruct-single
  allenai-olmoe-1b-7b-0125-instruct-single
  allenai-olmo-2-0325-32b-instruct-single
)

# Fan-out width: the MAX number of concurrent HELM client runs cmd_queue drives.
# Each run self-acquires ITS model's GPU lease (acquire --queue); infer-stack
# co-hosts what fits on INFER_STACK_ALLOWED_GPUS and QUEUES the rest — so this is
# not a GPU count and may exceed the number of cards. The 32B (tensor_parallel=2)
# can't co-host, so it serializes against the smaller models. Override per host;
# with the default 2 allowed GPUs, 4 keeps a couple of small models busy while the
# 32B waits its turn. (Within a model, its run_entries also share one lease via
# ref-counting, so raising this mostly parallelizes ACROSS models.)
OLMO_TMUX_WORKERS="${OLMO_TMUX_WORKERS:-4}"

# The base OLMo-7B can't join the combined bundle (its MMLU is ambiguous under the
# shared parent root), so the runbook also runs its two official suites as
# single-model exact-path bundles against their narrow per-suite roots, and folds
# them into the SAME virtual experiment (olmo-models-combined.yaml lists all three
# experiments). Both serve the same base model via the one olmo-7b endpoint. They
# are exported + scheduled AFTER the combined bundle by 10/15 (olmo_run_extra_preset).
OLMO_COMBINED_EXTRA_PRESETS=(
  allenai-olmo-7b-mmlu
  allenai-olmo-7b-lite
)
OLMO_COMBINED_EXTRA_ENDPOINT="allenai-olmo-7b-single"

# Export one extra single-model preset's exact-path bundle and schedule its <mode>
# manifest (smoke|full) with per-run leasing + fan-out. Single-deployment freeze
# against the preset's OWN narrow precomputed_root (baked into its manifest block);
# no inline model_deployment token, so the locator run-entry is a bare discovery
# key. Expects the gateway already bootstrapped by 10/15: LEASE_MASTER_KEY,
# LITELLM_BASE_URL, OLMO_CONTAINER_IMAGE, OLMO_TMUX_WORKERS in the environment.
# Honors FORCE_RERUN (the caller's OLMO_FORCE_RERUN).
olmo_run_extra_preset() {
  local preset="$1" mode="$2"   # mode = smoke | full
  local bundle_root="$STORE_ROOT/local-bundles/$preset"
  local experiment="audit-${preset}-${mode}"
  echo
  echo "==================================================================="
  echo "== extra single-model suite: ${preset} (${mode})"
  echo "==================================================================="
  "$PYTHON_BIN" -m eval_audit.integrations.infer_stack export-benchmark-bundle \
    --preset "$preset" \
    --bundle-root "$bundle_root" \
    --access-kind openai-compatible \
    --base-url "${LITELLM_BASE_URL}/v1" \
    --api-key-value "$LEASE_MASTER_KEY" \
    --from-spec --freeze-rel-paths
  if [[ "${FORCE_RERUN:-0}" == "1" && -d "$RESULTS_ROOT/$experiment" ]]; then
    echo "OLMO_FORCE_RERUN=1: clearing prior results at $RESULTS_ROOT/$experiment"
    rm -rf "$RESULTS_ROOT/$experiment"
  fi
  eval-audit-run --run=1 "$bundle_root/${mode}_manifest.yaml" \
    --container-image "$OLMO_CONTAINER_IMAGE" --lease --tmux-workers "$OLMO_TMUX_WORKERS"
}

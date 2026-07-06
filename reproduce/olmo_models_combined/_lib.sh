#!/usr/bin/env bash
# Shared definitions for the COMBINED multi-model OLMo fan-out runbook.
# Source this from the numbered scripts: `source "$(dirname "$0")/_lib.sh"`.
#
# This runbook runs a SINGLE multi-deployment preset — `allenai-olmo-combined`
# (adapter.PRESET_CONFIGS) — exported with `--freeze-rel-paths` and scheduled with
# `eval-audit-run --tmux-workers N`, so cmd_queue issues N concurrent per-run
# leases and infer-stack co-hosts what fits on INFER_STACK_ALLOWED_GPUS /
# serializes the rest. Five OLMo models fan out across GPUs under ONE schedule.
# See docs/planning/olmo-multi-model-from-spec-plan.md §4.4/§4.7.
#
# Self-contained: this runbook ships its own infer-stack config
# (config/infer_stack/{catalog,settings}.yaml), its own preflights, and this
# _lib.sh with no dependency on any sibling runbook. The base environment setup
# below (repo root, store/results roots, the shipped OLMo catalog via
# INFER_STACK_CONFIG_DIR, the docker-mountable INFER_STACK_DATA_DIR resolution,
# OLMO_CONTAINER_IMAGE, HuggingFace-auth, and the EVAL_AUDIT_* group-strip
# conventions) is followed by the combined-specific definitions.

# Repo root (two levels up from reproduce/olmo_models_combined/).
olmo_root() {
  cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd
}

ROOT="$(olmo_root)"
STORE_ROOT="${AUDIT_STORE_ROOT:-/data/crfm-helm-audit-store}"
RESULTS_ROOT="${AUDIT_RESULTS_ROOT:-/data/crfm-helm-audit}"
PYTHON_BIN="${PYTHON_BIN:-python}"

# GPU placement: NO restriction by default — infer-stack serves across ALL
# detected GPUs, so the per-run-lease fan-out can spread models over every card
# (first_fit preserves real indices). On a SHARED machine, export
# INFER_STACK_ALLOWED_GPUS=<csv> to pin serving to specific physical indices (keep
# >=2 free for the 32B tp=2 profile). infer-stack reads that env var directly, so
# we intentionally set no default here (unset ⇒ all detected GPUs).

# infer-stack catalog providing the OLMo models + <preset>-single endpoints.
# Defaults to the config dir shipped alongside this runbook (settings.yaml +
# catalog.yaml); override to point at your own infer-stack config if the OLMo
# endpoints already live there.
export INFER_STACK_CONFIG_DIR="${INFER_STACK_CONFIG_DIR:-$ROOT/reproduce/olmo_models_combined/config/infer_stack}"

# C-2: config_root and data_root are SEPARATE in the leasing world. The managed
# LiteLLM .env + the lease ledger live under data_root()/leasing/, and the grid
# runners read the master key via `infer-stack env LITELLM_MASTER_KEY` — that
# read and the serve-time write must resolve the SAME data_root. We guarantee that
# by resolving it ONCE here and exporting it, so every infer-stack call in the
# runbook agrees (including the bracket's `infer-stack acquire`, which inherits the
# exported value rather than re-resolving from its own possibly-CONFIG_DIR-less env).
#
# The data dir is also BIND-MOUNTED into the containers: the vLLM upstream gets
# the HF weight cache (compose.py: `<hf_cache>:/root/.cache/huggingface`) and the
# gateway gets its static route table. It must therefore live on a path the docker
# daemon can bind-mount — never an NFS $HOME (the vLLM mount fails, the model never
# attaches behind the gateway's *static* route, and HELM sees LiteLLM up but every
# request 500s with "Connection error" — the "works here, not there" footgun).
#
# Resolution order (highest first), matching infer-stack's own env > settings >
# default precedence, but resolved HERE and exported so it travels to subprocesses:
#   1. an explicit INFER_STACK_DATA_DIR in the environment  (operator override)
#   2. a `data_dir:` pinned in the resolved settings.yaml    (durable, committed)
#   3. ${INFER_STACK_DATA_ROOT:-/data/service}/infer-stack   (big-disk fallback)
_olmo_yaml_scalar() {  # $1=file $2=key -> value with quotes/inline-comment stripped
  local v
  v="$(sed -n -E "s/^[[:space:]]*$2:[[:space:]]*(.*)$/\1/p" "$1" 2>/dev/null | head -n1)"
  v="${v%%#*}"                          # strip inline comment
  v="${v%"${v##*[![:space:]]}"}"        # rstrip whitespace
  v="${v#\"}"; v="${v%\"}"              # strip double quotes
  v="${v#\'}"; v="${v%\'}"              # strip single quotes
  printf '%s' "$v"
}
: "${INFER_STACK_DATA_ROOT:=/data/service}"
_olmo_pinned_data_dir="$(_olmo_yaml_scalar "$INFER_STACK_CONFIG_DIR/settings.yaml" data_dir)"
if [[ -n "${INFER_STACK_DATA_DIR:-}" ]]; then
  _olmo_data_src="env"                                          # 1. explicit override wins
elif [[ -n "$_olmo_pinned_data_dir" ]]; then
  INFER_STACK_DATA_DIR="$_olmo_pinned_data_dir"; _olmo_data_src="settings.yaml"  # 2. yaml pin
else
  INFER_STACK_DATA_DIR="${INFER_STACK_DATA_ROOT}/infer-stack"; _olmo_data_src="default"  # 3. fallback
fi
export INFER_STACK_DATA_DIR

# Best-effort legibility: warn (don't silently relocate) when the chosen dir can't
# be created/written or is on NFS, so a later container-mount failure is
# self-explanatory. The remedy is to point INFER_STACK_DATA_DIR at a local big disk.
if ! { mkdir -p "$INFER_STACK_DATA_DIR" 2>/dev/null && [[ -w "$INFER_STACK_DATA_DIR" ]]; }; then
  echo "WARN: INFER_STACK_DATA_DIR=$INFER_STACK_DATA_DIR is not writable;" \
       "docker bind-mounts into the vLLM/LiteLLM containers will fail." \
       "Set INFER_STACK_DATA_DIR to a writable local big disk." >&2
else
  _olmo_fstype="$(stat -f -c %T "$INFER_STACK_DATA_DIR" 2>/dev/null || true)"
  if [[ "$_olmo_fstype" == nfs* || "$_olmo_fstype" == autofs ]]; then
    echo "WARN: INFER_STACK_DATA_DIR=$INFER_STACK_DATA_DIR is on $_olmo_fstype" \
         "(not docker-mountable); the vLLM HF-cache mount will fail." \
         "Set INFER_STACK_DATA_DIR to a local big disk." >&2
  fi
fi
echo "[olmo] infer-stack data dir: $INFER_STACK_DATA_DIR (source: $_olmo_data_src)" >&2

# Carry the e2e-test conventions: one local attempt per model (no repeat),
# strip the run-group prefix so smoke rows pair cleanly.
export EVAL_AUDIT_SKIP_LOCAL_REPEAT="${EVAL_AUDIT_SKIP_LOCAL_REPEAT:-1}"
export EVAL_AUDIT_GROUP_STRIP="${EVAL_AUDIT_GROUP_STRIP:-1}"

# Containerized HELM execution (the "docker pipeline"; see
# docs/container-execution.md) is MANDATORY: the grid scripts always pass
# `eval-audit-run --container-image "$OLMO_CONTAINER_IMAGE"`, so HELM runs inside
# the pinned eval-audit-helm-runner image — pinning the software environment so it
# stops being a confounding variable in the reproducibility comparison. The model
# is still SERVED ON THE HOST (vLLM behind LiteLLM); only WHERE HELM runs is the
# container. The in-container HELM reaches the host's LiteLLM endpoint via
# --network host (declared by the presets' container_network: host in
# eval_audit/integrations/infer_stack/adapter.py). The host-venv path has been
# removed (build_schedule_params now requires a container image). Leasing is the
# ORTHOGONAL axis (always on via --lease; see lease_bracket.py).
#
# OLMO_CONTAINER_IMAGE is the local tag built by ./docker/build.sh; override with a
# pushed digest for cross-machine pinning. 07_check_container_image.sh verifies it
# exists before the grid runs.
export OLMO_CONTAINER_IMAGE="${OLMO_CONTAINER_IMAGE:-eval-audit-helm-runner:dev}"

# HuggingFace auth. Some candidate runs (e.g. gpqa on the OLMo-2 / OLMoE
# instruct models) pull a GATED HF dataset; HELM's dataset loader needs a token
# whose account has accepted that dataset's terms. Resolve a token from the env
# or the cached `huggingface-cli login`, and export it under BOTH names the
# downstream libs read (huggingface_hub -> HF_TOKEN, older datasets ->
# HUGGING_FACE_HUB_TOKEN). Empty if none is available — 06_check_hf_auth.sh
# reports that before the grid runs.
olmo_resolve_hf_token() {
  if [[ -n "${HF_TOKEN:-}" ]]; then printf '%s' "$HF_TOKEN"; return; fi
  if [[ -n "${HUGGING_FACE_HUB_TOKEN:-}" ]]; then printf '%s' "$HUGGING_FACE_HUB_TOKEN"; return; fi
  local cached="${HF_HOME:-$HOME/.cache/huggingface}/token"
  if [[ -s "$cached" ]]; then tr -d '\n' <"$cached"; return; fi
  printf ''
}
HF_TOKEN_VALUE="$(olmo_resolve_hf_token)"
if [[ -n "$HF_TOKEN_VALUE" ]]; then
  export HF_TOKEN="$HF_TOKEN_VALUE"
  export HUGGING_FACE_HUB_TOKEN="$HF_TOKEN_VALUE"
fi
unset HF_TOKEN_VALUE

# --- combined-specific definitions ----------------------------------------------

# Group the single combined full experiment. Override with
# OLMO_COMBINED_VEXP_MANIFEST.
VEXP_MANIFEST="${OLMO_COMBINED_VEXP_MANIFEST:-$ROOT/configs/virtual-experiments/olmo-models-combined.yaml}"

# The one multi-deployment preset and its per-mode experiment names / bundle root
# (mirrors adapter.PRESET_CONFIGS["allenai-olmo-combined"]'s smoke/full blocks).
OLMO_COMBINED_PRESET="allenai-olmo-combined"
OLMO_COMBINED_EXPERIMENT_SMOKE="audit-allenai-olmo-combined-smoke"
OLMO_COMBINED_EXPERIMENT_FULL="audit-allenai-olmo-combined-full"
OLMO_COMBINED_BUNDLE_ROOT="$STORE_ROOT/local-bundles/$OLMO_COMBINED_PRESET"

# The five serving endpoints the combined preset's `profiles` reference — a subset
# of the shipped catalog. The base olmo-7b is NOT in this bundle: its per-subject
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

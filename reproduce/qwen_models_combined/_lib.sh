#!/usr/bin/env bash
# Shared definitions for the COMBINED multi-model Qwen text-family fan-out runbook.
# Source this from the numbered scripts: `source "$(dirname "$0")/_lib.sh"`.
#
# This runbook runs a SINGLE multi-deployment preset — `qwen-combined`
# (adapter.PRESET_CONFIGS) — exported with `--freeze-rel-paths` and scheduled with
# `eval-audit-run --tmux-workers N`, so cmd_queue issues N concurrent per-run
# leases and infer-stack co-hosts what fits on INFER_STACK_ALLOWED_GPUS /
# serializes the rest. Eight Qwen models fan out across GPUs under ONE schedule.
# See docs/planning/qwen-models-combined-fanout-plan.md.
#
# Self-contained: this runbook ships its own infer-stack config
# (config/infer_stack/{catalog,settings}.yaml), its own preflights, and this
# _lib.sh with no dependency on any sibling runbook. It is a direct port of
# reproduce/olmo_models_combined/_lib.sh (QWEN_* names / endpoints / workers).

# Repo root (two levels up from reproduce/qwen_models_combined/).
qwen_root() {
  cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd
}

ROOT="$(qwen_root)"
STORE_ROOT="${AUDIT_STORE_ROOT:-/data/crfm-helm-audit-store}"
RESULTS_ROOT="${AUDIT_RESULTS_ROOT:-/data/crfm-helm-audit}"
PYTHON_BIN="${PYTHON_BIN:-python}"

# GPU placement: NO restriction by default — infer-stack serves across ALL
# detected GPUs, so the per-run-lease fan-out can spread models over every card.
# On a SHARED machine, export INFER_STACK_ALLOWED_GPUS=<csv> to pin serving to
# specific physical indices (keep >=2 free for the tp=2 profiles, >=4 for the 110B
# tp=4). infer-stack reads that env var directly, so we set no default here.

# infer-stack catalog providing the Qwen models + <preset>-single endpoints.
# Defaults to the config dir shipped alongside this runbook; override to point at
# your own infer-stack config if the Qwen endpoints already live there.
export INFER_STACK_CONFIG_DIR="${INFER_STACK_CONFIG_DIR:-$ROOT/reproduce/qwen_models_combined/config/infer_stack}"

# config_root and data_root are SEPARATE in the leasing world. The managed LiteLLM
# .env + the lease ledger live under data_root()/leasing/, and the grid runners
# read the master key via `infer-stack env LITELLM_MASTER_KEY` — that read and the
# serve-time write must resolve the SAME data_root. Resolve it ONCE here and export
# it so every infer-stack call in the runbook agrees.
#
# The data dir is also BIND-MOUNTED into the containers (vLLM HF weight cache +
# gateway route table). It must live on a path the docker daemon can bind-mount —
# never an NFS $HOME (the vLLM mount fails, the model never attaches behind the
# gateway's static route, and HELM sees LiteLLM up but every request 500s).
#
# Resolution order (highest first): explicit env override > settings.yaml pin >
# ${INFER_STACK_DATA_ROOT:-/data/service}/infer-stack fallback.
_qwen_yaml_scalar() {  # $1=file $2=key -> value with quotes/inline-comment stripped
  local v
  v="$(sed -n -E "s/^[[:space:]]*$2:[[:space:]]*(.*)$/\1/p" "$1" 2>/dev/null | head -n1)"
  v="${v%%#*}"                          # strip inline comment
  v="${v%"${v##*[![:space:]]}"}"        # rstrip whitespace
  v="${v#\"}"; v="${v%\"}"              # strip double quotes
  v="${v#\'}"; v="${v%\'}"              # strip single quotes
  printf '%s' "$v"
}
: "${INFER_STACK_DATA_ROOT:=/data/service}"
_qwen_pinned_data_dir="$(_qwen_yaml_scalar "$INFER_STACK_CONFIG_DIR/settings.yaml" data_dir)"
if [[ -n "${INFER_STACK_DATA_DIR:-}" ]]; then
  _qwen_data_src="env"                                          # 1. explicit override wins
elif [[ -n "$_qwen_pinned_data_dir" ]]; then
  INFER_STACK_DATA_DIR="$_qwen_pinned_data_dir"; _qwen_data_src="settings.yaml"  # 2. yaml pin
else
  INFER_STACK_DATA_DIR="${INFER_STACK_DATA_ROOT}/infer-stack"; _qwen_data_src="default"  # 3. fallback
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
  _qwen_fstype="$(stat -f -c %T "$INFER_STACK_DATA_DIR" 2>/dev/null || true)"
  if [[ "$_qwen_fstype" == nfs* || "$_qwen_fstype" == autofs ]]; then
    echo "WARN: INFER_STACK_DATA_DIR=$INFER_STACK_DATA_DIR is on $_qwen_fstype" \
         "(not docker-mountable); the vLLM HF-cache mount will fail." \
         "Set INFER_STACK_DATA_DIR to a local big disk." >&2
  fi
fi
echo "[qwen] infer-stack data dir: $INFER_STACK_DATA_DIR (source: $_qwen_data_src)" >&2

# Carry the e2e-test conventions: one local attempt per model (no repeat), strip
# the run-group prefix so smoke rows pair cleanly.
export EVAL_AUDIT_SKIP_LOCAL_REPEAT="${EVAL_AUDIT_SKIP_LOCAL_REPEAT:-1}"
export EVAL_AUDIT_GROUP_STRIP="${EVAL_AUDIT_GROUP_STRIP:-1}"

# Containerized HELM execution (the "docker pipeline"; see
# docs/container-execution.md) is MANDATORY: the grid scripts always pass
# `eval-audit-run --container-image "$QWEN_CONTAINER_IMAGE"`, so HELM runs inside
# the pinned eval-audit-helm-runner image — pinning the software environment so it
# stops being a confounding variable in the reproducibility comparison. The model
# is still SERVED ON THE HOST (vLLM behind LiteLLM); only WHERE HELM runs is the
# container. The in-container HELM reaches the host's LiteLLM endpoint via
# --network host (declared by the presets' container_network: host). Leasing is the
# ORTHOGONAL axis (always on via --lease).
#
# QWEN_CONTAINER_IMAGE is the local tag built by ./docker/build.sh; override with a
# pushed digest for cross-machine pinning. 07_check_container_image.sh verifies it
# exists before the grid runs.
export QWEN_CONTAINER_IMAGE="${QWEN_CONTAINER_IMAGE:-eval-audit-helm-runner:dev}"

# HuggingFace auth. Some candidate runs (the turbo models' gpqa) pull a GATED HF
# dataset (Idavidrein/gpqa); HELM's dataset loader needs a token whose account has
# accepted that dataset's terms. Resolve a token from the env or the cached
# `huggingface-cli login`, and export it under BOTH names the downstream libs read
# (HF_TOKEN + HUGGING_FACE_HUB_TOKEN). Empty if none — 06_check_hf_auth.sh reports
# that before the grid runs. (The non-turbo classic-lite members don't gate.)
qwen_resolve_hf_token() {
  if [[ -n "${HF_TOKEN:-}" ]]; then printf '%s' "$HF_TOKEN"; return; fi
  if [[ -n "${HUGGING_FACE_HUB_TOKEN:-}" ]]; then printf '%s' "$HUGGING_FACE_HUB_TOKEN"; return; fi
  local cached="${HF_HOME:-$HOME/.cache/huggingface}/token"
  if [[ -s "$cached" ]]; then tr -d '\n' <"$cached"; return; fi
  printf ''
}
HF_TOKEN_VALUE="$(qwen_resolve_hf_token)"
if [[ -n "$HF_TOKEN_VALUE" ]]; then
  export HF_TOKEN="$HF_TOKEN_VALUE"
  export HUGGING_FACE_HUB_TOKEN="$HF_TOKEN_VALUE"
fi
unset HF_TOKEN_VALUE

# --- combined-specific definitions ----------------------------------------------

# Group the single combined full experiment. Override with QWEN_COMBINED_VEXP_MANIFEST.
VEXP_MANIFEST="${QWEN_COMBINED_VEXP_MANIFEST:-$ROOT/configs/virtual-experiments/qwen-models-combined.yaml}"

# The one multi-deployment preset and its per-mode experiment names / bundle root
# (mirrors adapter.PRESET_CONFIGS["qwen-combined"]'s smoke/full blocks).
QWEN_COMBINED_PRESET="qwen-combined"
QWEN_COMBINED_EXPERIMENT_SMOKE="audit-qwen-combined-smoke"
QWEN_COMBINED_EXPERIMENT_FULL="audit-qwen-combined-full"
QWEN_COMBINED_BUNDLE_ROOT="$STORE_ROOT/local-bundles/$QWEN_COMBINED_PRESET"

# The eight serving endpoints the combined preset's `profiles` reference — the full
# shipped catalog. All eight freeze 1:1 under the shared parent root (verified: 775
# whitelisted run dirs, 0 AMBIGUOUS), so none splits out. Order mirrors the preset
# profiles (base completions models first, then chat models).
QWEN_COMBINED_ENDPOINTS=(
  qwen-1-5-7b-single
  qwen-1-5-14b-single
  qwen-1-5-32b-single
  qwen-1-5-72b-single
  qwen-1-5-110b-chat-single
  qwen-2-72b-instruct-single
  qwen-2-5-7b-instruct-turbo-single
  qwen-2-5-72b-instruct-turbo-single
)

# Fan-out width: the MAX number of concurrent HELM client runs cmd_queue drives.
# Each run self-acquires ITS model's GPU lease (acquire --queue); infer-stack
# co-hosts what fits on INFER_STACK_ALLOWED_GPUS and QUEUES the rest — so this is
# not a GPU count and may exceed the number of cards. The 72Bs (tp=2) and the 110B
# (tp=4) can't co-host, so they serialize against the smaller models. Override per
# host. (Within a model, its run_entries also share one lease via ref-counting, so
# raising this mostly parallelizes ACROSS models.)
QWEN_TMUX_WORKERS="${QWEN_TMUX_WORKERS:-4}"

# Extra single-model suites folded into the SAME virtual experiment (the olmo-7b
# pattern). EMPTY by default: all eight Qwen models freeze cleanly in the combined
# bundle. If a future corpus refresh makes a member AMBIGUOUS under the shared root,
# 08_check_discovery.sh hard-fails; move that member here (as its own preset with a
# narrow per-suite root) + add its endpoint to QWEN_COMBINED_EXTRA_ENDPOINTS + list
# audit-<preset>-full in the virtual experiment. The 10/15/20/08 loops already
# handle a non-empty array (no-op when empty).
QWEN_COMBINED_EXTRA_PRESETS=()
QWEN_COMBINED_EXTRA_ENDPOINTS=()

# Export one extra single-model preset's exact-path bundle and schedule its <mode>
# manifest (smoke|full) with per-run leasing + fan-out. Single-deployment freeze
# against the preset's OWN narrow precomputed_root (baked into its manifest block);
# no inline model_deployment token. Expects the gateway already bootstrapped by
# 10/15: LEASE_MASTER_KEY, LITELLM_BASE_URL, QWEN_CONTAINER_IMAGE, QWEN_TMUX_WORKERS
# in the environment. Honors FORCE_RERUN (the caller's QWEN_FORCE_RERUN).
qwen_run_extra_preset() {
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
    echo "QWEN_FORCE_RERUN=1: clearing prior results at $RESULTS_ROOT/$experiment"
    rm -rf "$RESULTS_ROOT/$experiment"
  fi
  eval-audit-run --run=1 "$bundle_root/${mode}_manifest.yaml" \
    --container-image "$QWEN_CONTAINER_IMAGE" --lease --tmux-workers "$QWEN_TMUX_WORKERS"
}

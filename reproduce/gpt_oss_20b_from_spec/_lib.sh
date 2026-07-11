#!/usr/bin/env bash
# Shared definitions for the gpt-oss-20b FROM-SPEC reproduction runbook.
# Source this from the numbered scripts: `source "$(dirname "$0")/_lib.sh"`.
#
# This runbook replays the four ungated-judge public HELM gpt-oss-20b rows (bbq,
# ifeval, mmlu_pro, gpqa) from their official run_spec.json — a single-model
# exact-path freeze (`export-benchmark-bundle --from-spec --freeze-rel-paths`)
# scheduled with `eval-audit-run --lease --tmux-workers N`. It mirrors the OLMo
# single-model from-spec presets and the olmo_models_combined runbook, minus the
# multi-model fan-out (gpt-oss is one model, one serving endpoint).
#
# Self-contained: this runbook ships its own infer-stack config
# (config/infer_stack/{catalog,settings}.yaml), its own preflights, and this
# _lib.sh with no dependency on any sibling runbook.

# Repo root (two levels up from reproduce/gpt_oss_20b_from_spec/).
gptoss_root() {
  cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd
}

ROOT="$(gptoss_root)"
STORE_ROOT="${AUDIT_STORE_ROOT:-/data/crfm-helm-audit-store}"
RESULTS_ROOT="${AUDIT_RESULTS_ROOT:-/data/crfm-helm-audit}"
PYTHON_BIN="${PYTHON_BIN:-python}"

# GPU placement: NO restriction by default — infer-stack serves across ALL
# detected GPUs. On a SHARED machine, export INFER_STACK_ALLOWED_GPUS=<csv> to
# pin serving to specific physical indices (>=2 free if you switch the endpoint
# to tensor_parallel_size: 2). infer-stack reads that env var directly.

# infer-stack catalog providing the gpt-oss-20b model + the gpt-oss-20b-single
# endpoint. Defaults to the config dir shipped alongside this runbook; override
# to point at your own infer-stack config if the endpoint already lives there.
export INFER_STACK_CONFIG_DIR="${INFER_STACK_CONFIG_DIR:-$ROOT/reproduce/gpt_oss_20b_from_spec/config/infer_stack}"

# config_root and data_root are SEPARATE in the leasing world. The managed
# LiteLLM .env + the lease ledger live under data_root/leasing/, and the runners
# read the master key via `infer-stack env LITELLM_MASTER_KEY` — that read and
# the serve-time write must resolve the SAME data_root. Resolve it ONCE here and
# export it so every infer-stack call in the runbook agrees.
#
# The data dir is also BIND-MOUNTED into the containers (vLLM HF weight cache +
# the gateway's static route table), so it must live on a docker-mountable path —
# never an NFS $HOME (the vLLM mount fails, the model never attaches behind the
# gateway's static route, and HELM sees LiteLLM up but every request 500s).
#
# Resolution order (highest first): env override > settings.yaml pin > big-disk
# default (${INFER_STACK_DATA_ROOT:-/data/service}/infer-stack).
_gptoss_yaml_scalar() {  # $1=file $2=key -> value with quotes/inline-comment stripped
  local v
  v="$(sed -n -E "s/^[[:space:]]*$2:[[:space:]]*(.*)$/\1/p" "$1" 2>/dev/null | head -n1)"
  v="${v%%#*}"                          # strip inline comment
  v="${v%"${v##*[![:space:]]}"}"        # rstrip whitespace
  v="${v#\"}"; v="${v%\"}"              # strip double quotes
  v="${v#\'}"; v="${v%\'}"              # strip single quotes
  printf '%s' "$v"
}
: "${INFER_STACK_DATA_ROOT:=/data/service}"
_gptoss_pinned_data_dir="$(_gptoss_yaml_scalar "$INFER_STACK_CONFIG_DIR/settings.yaml" data_dir)"
if [[ -n "${INFER_STACK_DATA_DIR:-}" ]]; then
  _gptoss_data_src="env"                                          # 1. explicit override wins
elif [[ -n "$_gptoss_pinned_data_dir" ]]; then
  INFER_STACK_DATA_DIR="$_gptoss_pinned_data_dir"; _gptoss_data_src="settings.yaml"  # 2. yaml pin
else
  INFER_STACK_DATA_DIR="${INFER_STACK_DATA_ROOT}/infer-stack"; _gptoss_data_src="default"  # 3. fallback
fi
export INFER_STACK_DATA_DIR

# Best-effort legibility: warn (don't silently relocate) when the chosen dir
# can't be created/written or is on NFS, so a later container-mount failure is
# self-explanatory. The remedy is to point INFER_STACK_DATA_DIR at a local big disk.
if ! { mkdir -p "$INFER_STACK_DATA_DIR" 2>/dev/null && [[ -w "$INFER_STACK_DATA_DIR" ]]; }; then
  echo "WARN: INFER_STACK_DATA_DIR=$INFER_STACK_DATA_DIR is not writable;" \
       "docker bind-mounts into the vLLM/LiteLLM containers will fail." \
       "Set INFER_STACK_DATA_DIR to a writable local big disk." >&2
else
  _gptoss_fstype="$(stat -f -c %T "$INFER_STACK_DATA_DIR" 2>/dev/null || true)"
  if [[ "$_gptoss_fstype" == nfs* || "$_gptoss_fstype" == autofs ]]; then
    echo "WARN: INFER_STACK_DATA_DIR=$INFER_STACK_DATA_DIR is on $_gptoss_fstype" \
         "(not docker-mountable); the vLLM HF-cache mount will fail." \
         "Set INFER_STACK_DATA_DIR to a local big disk." >&2
  fi
fi
echo "[gpt-oss] infer-stack data dir: $INFER_STACK_DATA_DIR (source: $_gptoss_data_src)" >&2

# One local attempt per run (no repeat), strip the run-group prefix so rows pair
# cleanly — the from-spec / e2e conventions.
export EVAL_AUDIT_SKIP_LOCAL_REPEAT="${EVAL_AUDIT_SKIP_LOCAL_REPEAT:-1}"
export EVAL_AUDIT_GROUP_STRIP="${EVAL_AUDIT_GROUP_STRIP:-1}"

# Containerized HELM execution (the "docker pipeline"; see
# docs/container-execution.md) is MANDATORY: the grid scripts always pass
# `eval-audit-run --container-image "$GPTOSS_CONTAINER_IMAGE"`, so HELM runs
# inside the pinned eval-audit-helm-runner image, pinning the software
# environment so it stops being a confounding variable. The model is still SERVED
# ON THE HOST (vLLM behind LiteLLM); only WHERE HELM runs is the container. The
# in-container HELM reaches the host's LiteLLM endpoint via --network host
# (the preset's container_network: host). Leasing is the ORTHOGONAL axis (--lease).
#
# GPTOSS_CONTAINER_IMAGE is the local tag built by ./docker/build.sh; override
# with a pushed digest for cross-machine pinning. 07_check_container_image.sh
# verifies it exists before the grid runs.
export GPTOSS_CONTAINER_IMAGE="${GPTOSS_CONTAINER_IMAGE:-eval-audit-helm-runner:dev}"

# HuggingFace auth. gpqa pulls the GATED `Idavidrein/gpqa` dataset; HELM's loader
# needs a token whose account has accepted that dataset's terms. Resolve a token
# from the env or the cached `huggingface-cli login`, and export it under BOTH
# names the downstream libs read (HF_TOKEN + HUGGING_FACE_HUB_TOKEN). Empty if
# none is available — 06_check_hf_auth.sh reports that before the grid runs.
gptoss_resolve_hf_token() {
  if [[ -n "${HF_TOKEN:-}" ]]; then printf '%s' "$HF_TOKEN"; return; fi
  if [[ -n "${HUGGING_FACE_HUB_TOKEN:-}" ]]; then printf '%s' "$HUGGING_FACE_HUB_TOKEN"; return; fi
  local cached="${HF_HOME:-$HOME/.cache/huggingface}/token"
  if [[ -s "$cached" ]]; then tr -d '\n' <"$cached"; return; fi
  printf ''
}
HF_TOKEN_VALUE="$(gptoss_resolve_hf_token)"
if [[ -n "$HF_TOKEN_VALUE" ]]; then
  export HF_TOKEN="$HF_TOKEN_VALUE"
  export HUGGING_FACE_HUB_TOKEN="$HF_TOKEN_VALUE"
fi
unset HF_TOKEN_VALUE

# --- gpt-oss-specific definitions ------------------------------------------------

# Group the single from-spec full experiment. Override with GPTOSS_VEXP_MANIFEST.
VEXP_MANIFEST="${GPTOSS_VEXP_MANIFEST:-$ROOT/configs/virtual-experiments/gpt-oss-20b-from-spec.yaml}"

# The single-model from-spec preset and its per-mode experiment names / bundle root
# (mirrors preset_configs.yaml 'openai-gpt-oss-20b' smoke/full blocks).
GPTOSS_PRESET="openai-gpt-oss-20b"
GPTOSS_EXPERIMENT_SMOKE="audit-openai-gpt-oss-20b-from-spec-smoke"
GPTOSS_EXPERIMENT_FULL="audit-openai-gpt-oss-20b-from-spec-full"
GPTOSS_BUNDLE_ROOT="$STORE_ROOT/local-bundles/$GPTOSS_PRESET"

# The one serving endpoint the preset's `profile` references (shipped in
# config/infer_stack/catalog.yaml).
GPTOSS_ENDPOINT="gpt-oss-20b-single"

# Fan-out width: the MAX number of concurrent HELM client runs cmd_queue drives.
# All four run_entries hit the SAME single served model, so they share one lease
# via ref-counting and this just sets how many run concurrently against that one
# vLLM endpoint (its max_num_seqs bounds real concurrency). Default 2 keeps a
# couple of runs in flight without over-subscribing the single GPU.
GPTOSS_TMUX_WORKERS="${GPTOSS_TMUX_WORKERS:-2}"

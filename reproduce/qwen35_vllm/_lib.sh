#!/usr/bin/env bash
# Shared definitions for the Qwen3.5-9B-Base EXTENSION runbook (compute, not
# reproduction). Source from the numbered scripts:
# `source "$(dirname "$0")/_lib.sh"`.
#
# Single-model port of reproduce/qwen_models_combined/_lib.sh (QWEN35_* names,
# one endpoint, workers=1). The model is SERVED on the host via infer-stack
# (vLLM behind LiteLLM, GPU acquired per-run via --lease); HELM runs inside the
# pinned eval-audit-helm-runner container and reaches the gateway via
# --network host. There is NO hand-rolled `vllm serve` here.

qwen35_root() {
  cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd
}

ROOT="$(qwen35_root)"
STORE_ROOT="${AUDIT_STORE_ROOT:-/data/crfm-helm-audit-store}"
RESULTS_ROOT="${AUDIT_RESULTS_ROOT:-/data/crfm-helm-audit}"
# Prefer the activated env's `python`; fall back to python3 (bare hosts).
PYTHON_BIN="${PYTHON_BIN:-$(command -v python || command -v python3)}"

# infer-stack catalog providing the model + endpoint. Defaults to the config dir
# shipped alongside this runbook; override to point at your own infer-stack
# config if the endpoint already lives there.
export INFER_STACK_CONFIG_DIR="${INFER_STACK_CONFIG_DIR:-$ROOT/reproduce/qwen35_vllm/config/infer_stack}"

# data_root resolution (env > settings.yaml pin > /data/service default) — same
# contract as qwen_models_combined/_lib.sh; the dir is BIND-MOUNTED into the
# vLLM/LiteLLM containers, so it must be a docker-mountable local big disk
# (never an NFS $HOME).
_qwen35_yaml_scalar() {  # $1=file $2=key -> value with quotes/inline-comment stripped
  local v
  v="$(sed -n -E "s/^[[:space:]]*$2:[[:space:]]*(.*)$/\1/p" "$1" 2>/dev/null | head -n1)"
  v="${v%%#*}"
  v="${v%"${v##*[![:space:]]}"}"
  v="${v#\"}"; v="${v%\"}"
  v="${v#\'}"; v="${v%\'}"
  printf '%s' "$v"
}
: "${INFER_STACK_DATA_ROOT:=/data/service}"
_qwen35_pinned_data_dir="$(_qwen35_yaml_scalar "$INFER_STACK_CONFIG_DIR/settings.yaml" data_dir)"
if [[ -n "${INFER_STACK_DATA_DIR:-}" ]]; then
  _qwen35_data_src="env"
elif [[ -n "$_qwen35_pinned_data_dir" ]]; then
  INFER_STACK_DATA_DIR="$_qwen35_pinned_data_dir"; _qwen35_data_src="settings.yaml"
else
  INFER_STACK_DATA_DIR="${INFER_STACK_DATA_ROOT}/infer-stack"; _qwen35_data_src="default"
fi
export INFER_STACK_DATA_DIR

if ! { mkdir -p "$INFER_STACK_DATA_DIR" 2>/dev/null && [[ -w "$INFER_STACK_DATA_DIR" ]]; }; then
  echo "WARN: INFER_STACK_DATA_DIR=$INFER_STACK_DATA_DIR is not writable;" \
       "docker bind-mounts into the vLLM/LiteLLM containers will fail." \
       "Set INFER_STACK_DATA_DIR to a writable local big disk." >&2
else
  _qwen35_fstype="$(stat -f -c %T "$INFER_STACK_DATA_DIR" 2>/dev/null || true)"
  if [[ "$_qwen35_fstype" == nfs* || "$_qwen35_fstype" == autofs ]]; then
    echo "WARN: INFER_STACK_DATA_DIR=$INFER_STACK_DATA_DIR is on $_qwen35_fstype" \
         "(not docker-mountable); the vLLM HF-cache mount will fail." \
         "Set INFER_STACK_DATA_DIR to a local big disk." >&2
  fi
fi
echo "[qwen35] infer-stack data dir: $INFER_STACK_DATA_DIR (source: $_qwen35_data_src)" >&2

# Containerized HELM execution is MANDATORY (see qwen_models_combined/_lib.sh
# for the full rationale). Local tag from ./docker/build.sh; override with a
# pushed digest for cross-machine pinning.
export QWEN35_CONTAINER_IMAGE="${QWEN35_CONTAINER_IMAGE:-eval-audit-helm-runner:dev}"

# --- runbook-specific definitions ------------------------------------------------

QWEN35_PRESET="qwen35_9b_base_vllm"
QWEN35_ENDPOINT="qwen3-5-9b-base-single"
QWEN35_EXPERIMENT_SMOKE="audit-qwen35-9b-base-vllm-smoke"
QWEN35_EXPERIMENT_FULL="audit-qwen35-9b-base-vllm-full"
QWEN35_BUNDLE_ROOT="$STORE_ROOT/local-bundles/$QWEN35_PRESET"

# One model, one endpoint -> one worker. Raising this only parallelizes the two
# smoke run_entries against the same leased server (they ref-count one lease).
QWEN35_TMUX_WORKERS="${QWEN35_TMUX_WORKERS:-1}"

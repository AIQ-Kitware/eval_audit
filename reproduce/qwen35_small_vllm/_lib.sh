#!/usr/bin/env bash
# Shared definitions for the Qwen3.5 SMALL base-family EXTENSION runbook
# (0.8B / 2B / 4B, compute not reproduction). Source from the numbered
# scripts: `source "$(dirname "$0")/_lib.sh"`.
#
# Multi-model port of reproduce/qwen35_vllm/_lib.sh (QWEN35S_* names, THREE
# endpoints, one combined preset). Serving rides infer-stack (vLLM behind
# LiteLLM, GPU acquired per-run via --lease); HELM runs inside the pinned
# eval-audit-helm-runner container and reaches the gateway via --network host.
#
# GPU placement is deliberately UNPINNED (vram-aware-placement.md Phase 4):
# every endpoint in config/infer_stack/catalog.yaml declares
# placement.min_vram_gib and infer-stack's eligibility-aware planner puts each
# lease on whichever eligible GPU is free. On yardrat all three smalls fit
# BOTH cards, so leases spread across the full pool; run the 9B runbook
# concurrently and its 24GiB declaration keeps it on the 48GiB card without
# either runbook naming a GPU index. Do NOT set INFER_STACK_ALLOWED_GPUS here
# — it is an operator restriction for shared machines, not a scheduling tool.

qwen35s_root() {
  cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd
}

ROOT="$(qwen35s_root)"
STORE_ROOT="${AUDIT_STORE_ROOT:-/data/crfm-helm-audit-store}"
RESULTS_ROOT="${AUDIT_RESULTS_ROOT:-/data/crfm-helm-audit}"
# Prefer the activated env's `python`; fall back to python3 (bare hosts).
PYTHON_BIN="${PYTHON_BIN:-$(command -v python || command -v python3)}"

# infer-stack catalog providing the three models + endpoints. Defaults to the
# config dir shipped alongside this runbook; override to point at your own
# infer-stack config if the endpoints already live there.
export INFER_STACK_CONFIG_DIR="${INFER_STACK_CONFIG_DIR:-$ROOT/reproduce/qwen35_small_vllm/config/infer_stack}"

# data_root resolution (env > settings.yaml pin > /data/service default) — same
# contract as qwen35_vllm/_lib.sh; the dir is BIND-MOUNTED into the
# vLLM/LiteLLM containers, so it must be a docker-mountable local big disk
# (never an NFS $HOME).
_qwen35s_yaml_scalar() {  # $1=file $2=key -> value with quotes/inline-comment stripped
  local v
  v="$(sed -n -E "s/^[[:space:]]*$2:[[:space:]]*(.*)$/\1/p" "$1" 2>/dev/null | head -n1)"
  v="${v%%#*}"
  v="${v%"${v##*[![:space:]]}"}"
  v="${v#\"}"; v="${v%\"}"
  v="${v#\'}"; v="${v%\'}"
  printf '%s' "$v"
}
: "${INFER_STACK_DATA_ROOT:=/data/service}"
_qwen35s_pinned_data_dir="$(_qwen35s_yaml_scalar "$INFER_STACK_CONFIG_DIR/settings.yaml" data_dir)"
if [[ -n "${INFER_STACK_DATA_DIR:-}" ]]; then
  _qwen35s_data_src="env"
elif [[ -n "$_qwen35s_pinned_data_dir" ]]; then
  INFER_STACK_DATA_DIR="$_qwen35s_pinned_data_dir"; _qwen35s_data_src="settings.yaml"
else
  INFER_STACK_DATA_DIR="${INFER_STACK_DATA_ROOT}/infer-stack"; _qwen35s_data_src="default"
fi
export INFER_STACK_DATA_DIR

if ! { mkdir -p "$INFER_STACK_DATA_DIR" 2>/dev/null && [[ -w "$INFER_STACK_DATA_DIR" ]]; }; then
  echo "WARN: INFER_STACK_DATA_DIR=$INFER_STACK_DATA_DIR is not writable;" \
       "docker bind-mounts into the vLLM/LiteLLM containers will fail." \
       "Set INFER_STACK_DATA_DIR to a writable local big disk." >&2
else
  _qwen35s_fstype="$(stat -f -c %T "$INFER_STACK_DATA_DIR" 2>/dev/null || true)"
  if [[ "$_qwen35s_fstype" == nfs* || "$_qwen35s_fstype" == autofs ]]; then
    echo "WARN: INFER_STACK_DATA_DIR=$INFER_STACK_DATA_DIR is on $_qwen35s_fstype" \
         "(not docker-mountable); the vLLM HF-cache mount will fail." \
         "Set INFER_STACK_DATA_DIR to a local big disk." >&2
  fi
fi
echo "[qwen35s] infer-stack data dir: $INFER_STACK_DATA_DIR (source: $_qwen35s_data_src)" >&2

# Containerized HELM execution is MANDATORY (see qwen_models_combined/_lib.sh
# for the full rationale). Local tag from ./docker/build.sh; override with a
# pushed digest for cross-machine pinning.
export QWEN35S_CONTAINER_IMAGE="${QWEN35S_CONTAINER_IMAGE:-eval-audit-helm-runner:dev}"

# --- runbook-specific definitions ------------------------------------------------

QWEN35S_PRESET="qwen35_small_vllm"
# Space-separated; order matches the preset's profiles list (0.8B, 2B, 4B).
QWEN35S_ENDPOINTS="qwen3-5-0-8b-base-single qwen3-5-2b-base-single qwen3-5-4b-base-single"
QWEN35S_BOOTSTRAP_ENDPOINT="qwen3-5-0-8b-base-single"
QWEN35S_EXPERIMENT_SMOKE="audit-qwen35-small-vllm-smoke"
QWEN35S_EXPERIMENT_FULL="audit-qwen35-small-vllm-full"
QWEN35S_BUNDLE_ROOT="$STORE_ROOT/local-bundles/$QWEN35S_PRESET"

# Two workers: run_entries are GROUPED BY MODEL, so both workers usually chew
# the same model's block — ref-count coalescing (reclaim: stop) keeps that
# block's vLLM up while refcount >= 1, and vLLM batches the two concurrent
# HELM runs. At block boundaries the workers briefly straddle two models and
# the planner places the second model on the other free GPU — that is the
# unpinned multi-GPU case working, not a config accident.
QWEN35S_TMUX_WORKERS="${QWEN35S_TMUX_WORKERS:-2}"

#!/usr/bin/env bash
# Shared definitions for the OLMo smoke + full grid and grouping runbook.
# Source this from the numbered scripts: `source "$(dirname "$0")/_lib.sh"`.

# Repo root (two levels up from reproduce/olmo_models/).
olmo_root() {
  cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd
}

ROOT="$(olmo_root)"
STORE_ROOT="${AUDIT_STORE_ROOT:-/data/crfm-helm-audit-store}"
RESULTS_ROOT="${AUDIT_RESULTS_ROOT:-/data/crfm-helm-audit}"
# Grouping manifest for the downstream index -> compose -> summary steps. It
# scopes to the FULL run experiments (audit-<preset>-full); the smoke grid is a
# preflight only and is not folded into the grouped report.
VEXP_MANIFEST="${VEXP_MANIFEST:-$ROOT/configs/virtual-experiments/olmo-models.yaml}"
PYTHON_BIN="${PYTHON_BIN:-python}"

# infer-stack config providing the six OLMo models + <preset>-single profiles.
# Defaults to the config dir shipped alongside this runbook; override to point
# at your own infer-stack config if the OLMo profiles already live there.
export INFER_STACK_CONFIG_DIR="${INFER_STACK_CONFIG_DIR:-$ROOT/reproduce/olmo_models/config/infer_stack}"

# Carry the e2e-test conventions: one local attempt per model (no repeat),
# strip the run-group prefix so smoke rows pair cleanly.
export EVAL_AUDIT_SKIP_LOCAL_REPEAT="${EVAL_AUDIT_SKIP_LOCAL_REPEAT:-1}"
export EVAL_AUDIT_GROUP_STRIP="${EVAL_AUDIT_GROUP_STRIP:-1}"

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

# The six OLMo presets, ordered smallest -> largest so a cheap model surfaces a
# pipeline failure before the expensive 32B load. Each row is "preset:profile".
# The infer-stack profile is the preset name with a "-single" suffix, and the
# per-mode experiment_name is "audit-<preset>-smoke" / "audit-<preset>-full"
# (smoke is a preflight; the full runs feed indexing/grouping).
OLMO_TARGETS=(
  "allenai-olmoe-1b-7b-0125-instruct:allenai-olmoe-1b-7b-0125-instruct-single"
  "allenai-olmo-7b:allenai-olmo-7b-single"
  "allenai-olmo-1-7-7b:allenai-olmo-1-7-7b-single"
  "allenai-olmo-2-1124-7b-instruct:allenai-olmo-2-1124-7b-instruct-single"
  "allenai-olmo-2-1124-13b-instruct:allenai-olmo-2-1124-13b-instruct-single"
  "allenai-olmo-2-0325-32b-instruct:allenai-olmo-2-0325-32b-instruct-single"
)

olmo_preset()     { printf '%s\n' "${1%%:*}"; }
olmo_profile()    { printf '%s\n' "${1##*:}"; }
# Per-mode experiment names, matching the smoke_manifest / full_manifest blocks
# in eval_audit/integrations/infer_stack/adapter.py.
olmo_experiment_smoke() { printf 'audit-%s-smoke\n' "${1%%:*}"; }
olmo_experiment_full()  { printf 'audit-%s-full\n' "${1%%:*}"; }
olmo_bundle_root() { printf '%s\n' "$STORE_ROOT/local-bundles/${1%%:*}"; }

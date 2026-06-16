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

# Restrict serving to specific physical GPUs on a shared machine. first_fit will
# only ever place vLLM on these indices (real indices preserved). Need >=2 for
# the 32B tp=2 profile. Override per-host; unset = use whatever infer-stack detects.
export INFER_STACK_ALLOWED_GPUS="${INFER_STACK_ALLOWED_GPUS:-2,3}"

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

# natural_qa data staging (script-only; no submodule edits).
#
# HELM's natural_qa scenario downloads the NaturalQuestions dev shards from a
# public GCS bucket that now refuses ANONYMOUS reads (HTTP 403); HELM fetches
# anonymously, so the run can't get them itself. We can't pre-seed the run's
# benchmark_output because out_dpath is a per-run kwdagger hash dir created at
# run time. Instead we ship a `helm-run` PATH shim (bin/helm-run): materialize
# invokes `helm-run` with cwd=out_dpath, so the shim — running in that cwd —
# seeds the pre-staged shards into ./benchmark_output/scenarios/natural_qa/data
# (exactly where get_instances() looks) and then exec's the real helm-run.
# HELM's ensure_file_downloaded skips the network when the target file exists.
#
# Stage location (the shim's source); 07_check_gcloud_auth.sh fills it.
export EVAL_AUDIT_NQ_STAGE_DIR="${EVAL_AUDIT_NQ_STAGE_DIR:-$STORE_ROOT/scenario-cache/natural_qa/data}"

# The natural_questions dev shards HELM's natural_qa scenario fetches — mirrors
# the scenario's own base_url/file_list (helm .../scenarios/natural_qa_scenario.py).
# natural_qa appears in the allenai-olmo-7b FULL manifest (mode=closedbook and
# mode=openbook_longans), so only that preset depends on these.
NQ_GCS_BUCKET="natural_questions"
NQ_GCS_PREFIX="v1.0/dev"
NQ_FILES=(nq-dev-00.jsonl.gz nq-dev-01.jsonl.gz nq-dev-02.jsonl.gz nq-dev-03.jsonl.gz nq-dev-04.jsonl.gz)
# Where 07 stages the shards (== the shim's source dir).
NQ_CACHE_DATA_DIR="$EVAL_AUDIT_NQ_STAGE_DIR"

# Put the `helm-run` shim first on PATH so the run path hits it instead of the
# real entry point. Resolve the REAL helm-run BEFORE prepending so the shim can
# delegate to it without recursing. Both are exported so they survive into the
# materialize subprocess eval-audit-run schedules. If the real helm-run isn't on
# PATH yet (env not fully set up), skip the shim — the run would fail anyway and
# 00_check_env.sh covers entry-point presence.
OLMO_SHIM_DIR="$ROOT/reproduce/olmo_models/bin"
if [[ -z "${EVAL_AUDIT_REAL_HELM_RUN:-}" ]]; then
  _real_helm_run="$(command -v helm-run 2>/dev/null || true)"
  if [[ -n "$_real_helm_run" && "$_real_helm_run" != "$OLMO_SHIM_DIR/helm-run" ]]; then
    export EVAL_AUDIT_REAL_HELM_RUN="$_real_helm_run"
  fi
  unset _real_helm_run
fi
if [[ -n "${EVAL_AUDIT_REAL_HELM_RUN:-}" && ":$PATH:" != *":$OLMO_SHIM_DIR:"* ]]; then
  export PATH="$OLMO_SHIM_DIR:$PATH"
fi

# Resolve a Google OAuth access token for authenticated GCS reads, without
# requiring gsutil. Order: explicit env -> gcloud user creds -> gcloud ADC.
# Empty if none — 07_check_gcloud_auth.sh reports that before staging.
olmo_resolve_gcloud_token() {
  if [[ -n "${GOOGLE_OAUTH_ACCESS_TOKEN:-}" ]]; then printf '%s' "$GOOGLE_OAUTH_ACCESS_TOKEN"; return; fi
  if command -v gcloud >/dev/null 2>&1; then
    local tok
    if tok="$(gcloud auth print-access-token 2>/dev/null)" && [[ -n "$tok" ]]; then
      printf '%s' "$tok"; return
    fi
    if tok="$(gcloud auth application-default print-access-token 2>/dev/null)" && [[ -n "$tok" ]]; then
      printf '%s' "$tok"; return
    fi
  fi
  printf ''
}

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

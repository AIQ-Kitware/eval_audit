#!/usr/bin/env bash
# Shared definitions for the phi-2 e2e smoke + full grid and grouping runbook.
# Source this from the numbered scripts: `source "$(dirname "$0")/_lib.sh"`.
#
# This is the original dev/e2e-tests suite restructured into the same shape as
# reproduce/olmo_models/ — which was itself derived from these scripts: a
# TARGETS array + helpers in _lib.sh, numbered stage scripts, and an olmo-style
# virtual-experiment grouping (index -> compose -> summary). The three monolithic
# e2e-phi_2-*.sh scripts this replaces are folded into the grid below.

# Repo root (two levels up from dev/e2e-tests/).
e2e_root() {
  cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd
}

ROOT="$(e2e_root)"
E2E_DIR="$ROOT/dev/e2e-tests"
STORE_ROOT="${AUDIT_STORE_ROOT:-/data/crfm-helm-audit-store}"
RESULTS_ROOT="${AUDIT_RESULTS_ROOT:-/data/crfm-helm-audit}"
# Grouping manifest for the downstream index -> compose -> summary steps. It
# scopes to the FULL run experiments (<name>-full); the smoke grid is a
# preflight only and is not folded into the grouped report. Point VEXP_MANIFEST
# at e2e-phi2-smoke.yaml to group the smoke preflight instead.
VEXP_MANIFEST="${VEXP_MANIFEST:-$ROOT/configs/virtual-experiments/e2e-phi2.yaml}"
PYTHON_BIN="${PYTHON_BIN:-python}"

# infer-stack config providing the phi2 model + the phi2-single profile (the
# vLLM targets need it; the huggingface target does not). Defaults to the config
# dir shipped alongside this runbook; override to point at your own config.
export INFER_STACK_CONFIG_DIR="${INFER_STACK_CONFIG_DIR:-$E2E_DIR/config/infer_stack}"

# e2e-test conventions, carried verbatim from the original scripts: one local
# attempt per scenario (no repeat), strip the run-group prefix so rows pair
# cleanly.
export EVAL_AUDIT_SKIP_LOCAL_REPEAT="${EVAL_AUDIT_SKIP_LOCAL_REPEAT:-1}"
export EVAL_AUDIT_GROUP_STRIP="${EVAL_AUDIT_GROUP_STRIP:-1}"

# The three phi-2 e2e scenarios. Each row is "name:transport:serving":
#   * name      — the preset/experiment stem. The per-mode experiments are
#                 <name>-smoke / <name>-full, matching the smoke_manifest /
#                 full_manifest blocks in
#                 eval_audit/integrations/infer_stack/adapter.py for the vLLM
#                 presets, and the checked-in HF manifests under manifests/.
#   * transport — "vllm": served via infer-stack and fronted by the LiteLLM
#                 gateway (openai-compatible); the bundle is materialized from
#                 the preset. "hf": HELM loads microsoft/phi-2 directly from
#                 HuggingFace (no infer-stack); the run is a checked-in manifest.
#   * serving   — vllm: the infer-stack profile to switch into.
#                 hf:   unused ("-"); the manifest path is derived from the
#                       experiment name (manifests/<experiment>.yaml).
# Ordered HF-direct FIRST, then the vLLM scenarios. The hf target makes HELM load
# microsoft/phi-2 onto the GPU itself (no infer-stack), so it must run while the
# GPU is free — before any vLLM stack is up holding the memory (otherwise the HF
# load can OOM). The grid scripts run `infer-stack down` at the start to
# guarantee that clean state, and running hf first keeps the GPU clear for it.
# After hf, the vLLM comparable baseline and its temperature=1 "incomparable"
# negative control (a deliberate recipe deviation the planner should flag) run
# against the served LiteLLM endpoint. The container scenario (appended below) is
# vLLM-served too and runs last, reusing the already-up stack.
E2E_TARGETS=(
  "e2e-phi_2-huggingface-philosophy:hf:-"
  "e2e-phi_2-vllm-philosophy:vllm:phi2-single"
  "e2e-phi_2-vllm-philosophy-incomparable:vllm:phi2-single"
)

# Containerized-execution example (ON BY DEFAULT; set E2E_INCLUDE_CONTAINER=0 to
# skip). Runs HELM inside the pinned eval-audit-helm-runner image (build with
# ./docker/build.sh first) instead of the host venv — the "docker pipeline"
# (Stage 3 containerized execution; see docs/container-execution.md). It needs
# the built image + a working docker, so on hosts without them set
# E2E_INCLUDE_CONTAINER=0 (otherwise 06_check_container_image.sh fails the
# preflight with a build/skip hint).
#
# This is the intended containerized workflow: the model is SERVED on the host
# (phi-2 on vLLM behind LiteLLM) and HELM runs in the container, reaching the host
# endpoint via --network host. It reuses transport "vllm" unchanged — the
# container settings (incl. container_network: host) are declared by the
# e2e-phi_2-vllm-philosophy-container PRESET, so the generated bundle manifest
# carries it. (An in-process HF-in-container flavor — the model loaded inside the
# container — is intentionally not included: it is self-contained and needs no
# host endpoint, which is not the served workflow this exercises.)
if [[ "${E2E_INCLUDE_CONTAINER:-1}" != "0" ]]; then
  E2E_TARGETS+=(
    "e2e-phi_2-vllm-philosophy-container:vllm:phi2-single"
  )
fi

# The pinned HELM-runner image + a dedicated audit HF cache (the container runs
# as root, so a dedicated dir keeps downloads consistently owned). These match
# the e2e-phi_2-*-container preset / manifest defaults and are used by
# 06_check_container_image.sh; override to point at a pushed digest / other cache.
export E2E_CONTAINER_IMAGE="${E2E_CONTAINER_IMAGE:-eval-audit-helm-runner:dev}"
export E2E_HF_CACHE_DIR="${E2E_HF_CACHE_DIR:-$HOME/.cache/eval-audit-hf}"

e2e_name()       { printf '%s\n' "${1%%:*}"; }
e2e_transport()  { local rest="${1#*:}"; printf '%s\n' "${rest%%:*}"; }
e2e_serving()    { printf '%s\n' "${1##*:}"; }
# Per-mode experiment names, matching the adapter smoke_manifest/full_manifest
# blocks (vLLM) and the manifests/<name>-{smoke,full}.yaml files (HF).
e2e_experiment_smoke() { printf '%s-smoke\n' "${1%%:*}"; }
e2e_experiment_full()  { printf '%s-full\n' "${1%%:*}"; }
e2e_bundle_root()      { printf '%s\n' "$STORE_ROOT/local-bundles/${1%%:*}"; }
# HF manifest path for a given mode ("smoke" | "full").
e2e_hf_manifest()      { printf '%s\n' "$E2E_DIR/manifests/${1%%:*}-$2.yaml"; }

# Clear a prior experiment's result dir so kwdagger's skip_existing doesn't
# no-op a re-invocation. eval-audit-run schedules with skip_existing=1, so a
# scenario whose previous run already wrote its DONE sentinel
# ($RESULTS_ROOT/<experiment>/helm/.../DONE) would otherwise be skipped. Callers
# gate this on their own FORCE_RERUN flag. $1 = experiment name.
e2e_clear_results() {
  local experiment="$1" result_dpath
  result_dpath="$RESULTS_ROOT/$experiment"
  if [[ -d "$result_dpath" ]]; then
    echo "force-rerun: clearing prior results at $result_dpath"
    rm -rf "$result_dpath"
  fi
}

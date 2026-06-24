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
# Each scenario composes as its OWN virtual experiment via a static per-scenario
# manifest (configs/virtual-experiments/e2e-phi2-<scenario>.yaml; resolved by
# e2e_vexp_manifest below). 30_compose.sh / 40_build_summary.sh loop over the
# scenarios in E2E_TARGETS by default, producing one report per scenario — each
# pairs cleanly with the public run instead of pooling all scenarios into one
# packet. Set VEXP_MANIFEST=<path> to compose/summarize a single manifest only.
PYTHON_BIN="${PYTHON_BIN:-python}"

# infer-stack catalog providing the phi-2 model + the phi2-single endpoint (the
# vLLM targets need it; the huggingface target does not). Defaults to the config
# dir shipped alongside this runbook (settings.yaml + catalog.yaml); override to
# point at your own config.
export INFER_STACK_CONFIG_DIR="${INFER_STACK_CONFIG_DIR:-$E2E_DIR/config/infer_stack}"

# C-2: config_root and data_root are SEPARATE in the leasing world. The managed
# LiteLLM .env + the lease ledger live under data_root()/leasing/, and the grid
# runners read the master key via `infer-stack env LITELLM_MASTER_KEY` — that
# read and the serve-time write must resolve the same data_root, so pin it here
# (every infer-stack call in the runbook then agrees). Defaults to the XDG
# location; override to a big-disk path on hosts where weights/state shouldn't
# land under $HOME.
export INFER_STACK_DATA_DIR="${INFER_STACK_DATA_DIR:-$HOME/.local/share/infer_stack}"

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
#   * serving   — vllm: the infer-stack catalog endpoint to serve.
#                 hf:   unused ("-"); the manifest path is derived from the
#                       experiment name (manifests/<experiment>.yaml).
# Ordered HF-direct FIRST, then the vLLM scenarios. Every scenario runs HELM
# inside the pinned eval-audit-helm-runner image (containerization is mandatory;
# the grid passes `eval-audit-run --container-image "$E2E_CONTAINER_IMAGE"`). The
# hf target loads microsoft/phi-2 IN-PROCESS in its container (a real GPU, no
# lease, no --network host), so it must run while the GPU is free — before any
# vLLM scenario self-acquires phi-2 and holds the memory (otherwise the HF load
# can OOM). The grid scripts reclaim leaked leases (`infer-stack gc`) and evict
# the one-time gateway-bootstrap model at the start, and run hf first, to keep the
# GPU clear (the per-run leasing path has no standing pre-served stack). After hf,
# the vLLM comparable baseline and its temperature=1 "incomparable" negative
# control (a deliberate recipe deviation the planner should flag) run against the
# served LiteLLM endpoint, each self-acquiring phi-2's lease for the run
# (`eval-audit-run --lease`, container_gpus: none). The only hf-vs-vLLM difference
# is the lease (and the GPU config that follows from it).
E2E_TARGETS=(
  "e2e-phi_2-huggingface-philosophy:hf:-"
  "e2e-phi_2-vllm-philosophy:vllm:phi2-single"
  "e2e-phi_2-vllm-philosophy-incomparable:vllm:phi2-single"
)

# The pinned HELM-runner image (always used — containerization is mandatory) + a
# dedicated audit HF cache (the container runs as root, so a dedicated dir keeps
# downloads consistently owned). Build the image first with ./docker/build.sh;
# 06_check_container_image.sh verifies it. Override E2E_CONTAINER_IMAGE with a
# pushed digest for cross-machine pinning.
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

# Resolve a target's per-scenario virtual-experiment manifest. Each scenario is
# composed as its own virtual experiment (one local recipe) so it pairs cleanly
# with the public run instead of pooling with the others. Keep this in sync with
# E2E_TARGETS. $1 = an E2E_TARGETS row (or bare scenario name).
e2e_vexp_manifest() {
  local d="$ROOT/configs/virtual-experiments"
  case "$(e2e_name "$1")" in
    e2e-phi_2-vllm-philosophy)              printf '%s\n' "$d/e2e-phi2-vllm.yaml" ;;
    e2e-phi_2-vllm-philosophy-incomparable) printf '%s\n' "$d/e2e-phi2-incomparable.yaml" ;;
    e2e-phi_2-huggingface-philosophy)       printf '%s\n' "$d/e2e-phi2-hf.yaml" ;;
    *) echo "e2e_vexp_manifest: no manifest mapped for '$(e2e_name "$1")'" >&2; return 1 ;;
  esac
}

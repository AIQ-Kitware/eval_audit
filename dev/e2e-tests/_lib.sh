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
_e2e_yaml_scalar() {  # $1=file $2=key -> value with quotes/inline-comment stripped
  local v
  v="$(sed -n -E "s/^[[:space:]]*$2:[[:space:]]*(.*)$/\1/p" "$1" 2>/dev/null | head -n1)"
  v="${v%%#*}"                          # strip inline comment
  v="${v%"${v##*[![:space:]]}"}"        # rstrip whitespace
  v="${v#\"}"; v="${v%\"}"              # strip double quotes
  v="${v#\'}"; v="${v%\'}"              # strip single quotes
  printf '%s' "$v"
}
: "${INFER_STACK_DATA_ROOT:=/data/service}"
_e2e_pinned_data_dir="$(_e2e_yaml_scalar "$INFER_STACK_CONFIG_DIR/settings.yaml" data_dir)"
if [[ -n "${INFER_STACK_DATA_DIR:-}" ]]; then
  _e2e_src="env"                                             # 1. explicit override wins
elif [[ -n "$_e2e_pinned_data_dir" ]]; then
  INFER_STACK_DATA_DIR="$_e2e_pinned_data_dir"; _e2e_src="settings.yaml"   # 2. yaml pin
else
  INFER_STACK_DATA_DIR="${INFER_STACK_DATA_ROOT}/infer-stack"; _e2e_src="default"  # 3. fallback
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
  _e2e_fstype="$(stat -f -c %T "$INFER_STACK_DATA_DIR" 2>/dev/null || true)"
  if [[ "$_e2e_fstype" == nfs* || "$_e2e_fstype" == autofs ]]; then
    echo "WARN: INFER_STACK_DATA_DIR=$INFER_STACK_DATA_DIR is on $_e2e_fstype" \
         "(not docker-mountable); the vLLM HF-cache mount will fail." \
         "Set INFER_STACK_DATA_DIR to a local big disk." >&2
  fi
fi
echo "[e2e] infer-stack data dir: $INFER_STACK_DATA_DIR (source: $_e2e_src)" >&2

# e2e-test conventions, carried verbatim from the original scripts: one local
# attempt per scenario (no repeat), strip the run-group prefix so rows pair
# cleanly.
export EVAL_AUDIT_SKIP_LOCAL_REPEAT="${EVAL_AUDIT_SKIP_LOCAL_REPEAT:-1}"
export EVAL_AUDIT_GROUP_STRIP="${EVAL_AUDIT_GROUP_STRIP:-1}"

# Faithful replay is the DEFAULT and only path for the comparable scenarios — the
# e2e always replays the OFFICIAL microsoft/phi-2 run_spec.json verbatim rather
# than reconstructing the recipe from the run-entry string (migration plan
# docs/planning/e2e-from-run-spec-migration-plan.md). There is no run-entry
# opt-out: the hf manifests are from-spec and the grids always pass `--from-spec`
# to export-benchmark-bundle for the vLLM baseline. The incomparable negative
# control is the SOLE carve-out (see e2e_uses_from_spec) — it stays on the
# run-entry path because from-spec replays the official recipe verbatim and would
# erase the temperature=1 deviation it exists to flag (§7).

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

# Whether a scenario runs the FROM-SPEC (faithful-replay) path. From-spec is the
# default for every scenario EXCEPT the incomparable negative control, which is the
# sole carve-out: from-spec replays the official recipe verbatim and would erase
# its temperature=1 deviation (§7). This is structural, not a toggle. $1 = an
# E2E_TARGETS row (or bare scenario name).
e2e_uses_from_spec() {
  case "$(e2e_name "$1")" in
    *-incomparable) return 1 ;;
    *)              return 0 ;;
  esac
}

# HF manifest path for a given mode ("smoke" | "full"). The hf scenario is
# comparable, so this checked-in manifest replays the official run_spec.json
# (from-spec is the default; see e2e_uses_from_spec). $1 = target/name, $2 = mode.
e2e_hf_manifest() { printf '%s\n' "$E2E_DIR/manifests/${1%%:*}-$2.yaml"; }

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

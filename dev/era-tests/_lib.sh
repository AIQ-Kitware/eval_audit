#!/usr/bin/env bash
# Shared definitions for the classic-era (pre-v0.5) replay runbook.
# Source this from the numbered scripts: `source "$(dirname "$0")/_lib.sh"`.
#
# Mirrors dev/e2e-tests/_lib.sh (the phi-2 e2e). The generic machinery — repo
# root, store/results roots, the INFER_STACK_DATA_DIR resolution, the
# SKIP_LOCAL_REPEAT/GROUP_STRIP exports, and the per-scenario compose/summary
# grouping — is carried over verbatim. What differs is the SUBJECT: this runbook
# replays eleutherai/pythia-6.9b through BOTH classic eras (helm-v0.2.4 and
# helm-v0.3.0), each inside its own era-pinned CPU-only HELM image, with model
# inference served out-of-process on modern vLLM. See
# docs/planning/era-tests-dev-runbook-plan.md and
# docs/planning/era-pinned-helm-containers-plan.md.

# Repo root (two levels up from dev/era-tests/).
era_root() {
  cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd
}

ROOT="$(era_root)"
ERA_DIR="$ROOT/dev/era-tests"
STORE_ROOT="${AUDIT_STORE_ROOT:-/data/crfm-helm-audit-store}"
RESULTS_ROOT="${AUDIT_RESULTS_ROOT:-/data/crfm-helm-audit}"
PYTHON_BIN="${PYTHON_BIN:-python}"

# The public HELM corpus mirror. The classic track (pre-v0.5) lives under
# <root>/benchmark_output/runs/{v0.2.2..v0.4.0}. Discovery/era-resolution derive
# (public_track, suite_version) from the run-dir path, so this must contain the
# <track>/benchmark_output/... layout.
PRECOMPUTED_ROOT="${PRECOMPUTED_ROOT:-/data/crfm-helm-public/classic}"
# Prefix the configs/run_details.yaml run_dir paths carry (the fidelity/fetch
# rungs strip it to build corpus-relative paths).
CANONICAL_CORPUS_PREFIX="${CANONICAL_CORPUS_PREFIX:-/data/crfm-helm-public}"
# Output root for gate/ladder artifacts (per-rung logs, corpus views, bundles).
ERA_OUT="${ERA_OUT:-$ROOT/ladder-out}"

# infer-stack catalog providing the pythia-6.9b model + the pythia69b-single
# endpoint. Defaults to the config dir shipped alongside this runbook.
export INFER_STACK_CONFIG_DIR="${INFER_STACK_CONFIG_DIR:-$ERA_DIR/config/infer_stack}"

# C-2 (verbatim from e2e): resolve INFER_STACK_DATA_DIR ONCE and export it so the
# master-key read and the serve-time write agree, and so it is bind-mountable
# into the vLLM/LiteLLM containers (never NFS $HOME). Resolution order:
#   1. explicit INFER_STACK_DATA_DIR in env   2. data_dir: in settings.yaml
#   3. ${INFER_STACK_DATA_ROOT:-/data/service}/infer-stack
_era_yaml_scalar() {  # $1=file $2=key -> value with quotes/inline-comment stripped
  local v
  v="$(sed -n -E "s/^[[:space:]]*$2:[[:space:]]*(.*)$/\1/p" "$1" 2>/dev/null | head -n1)"
  v="${v%%#*}"
  v="${v%"${v##*[![:space:]]}"}"
  v="${v#\"}"; v="${v%\"}"
  v="${v#\'}"; v="${v%\'}"
  printf '%s' "$v"
}
: "${INFER_STACK_DATA_ROOT:=/data/service}"
_era_pinned_data_dir="$(_era_yaml_scalar "$INFER_STACK_CONFIG_DIR/settings.yaml" data_dir)"
if [[ -n "${INFER_STACK_DATA_DIR:-}" ]]; then
  _era_src="env"
elif [[ -n "$_era_pinned_data_dir" ]]; then
  INFER_STACK_DATA_DIR="$_era_pinned_data_dir"; _era_src="settings.yaml"
else
  INFER_STACK_DATA_DIR="${INFER_STACK_DATA_ROOT}/infer-stack"; _era_src="default"
fi
export INFER_STACK_DATA_DIR
if ! { mkdir -p "$INFER_STACK_DATA_DIR" 2>/dev/null && [[ -w "$INFER_STACK_DATA_DIR" ]]; }; then
  echo "WARN: INFER_STACK_DATA_DIR=$INFER_STACK_DATA_DIR is not writable;" \
       "docker bind-mounts into the vLLM/LiteLLM containers will fail." >&2
else
  _era_fstype="$(stat -f -c %T "$INFER_STACK_DATA_DIR" 2>/dev/null || true)"
  if [[ "$_era_fstype" == nfs* || "$_era_fstype" == autofs ]]; then
    echo "WARN: INFER_STACK_DATA_DIR=$INFER_STACK_DATA_DIR is on $_era_fstype" \
         "(not docker-mountable); the vLLM HF-cache mount will fail." >&2
  fi
fi
echo "[era] infer-stack data dir: $INFER_STACK_DATA_DIR (source: $_era_src)" >&2

# One local attempt per scenario; strip the run-group prefix so rows pair cleanly.
export EVAL_AUDIT_SKIP_LOCAL_REPEAT="${EVAL_AUDIT_SKIP_LOCAL_REPEAT:-1}"
export EVAL_AUDIT_GROUP_STRIP="${EVAL_AUDIT_GROUP_STRIP:-1}"

# Forwarded into the era container as the per-deployment credential (vLLM ignores
# it; v0.2.4's AutoClient merely requires it to exist). Default EMPTY.
export EVAL_AUDIT_ERA_API_KEY="${EVAL_AUDIT_ERA_API_KEY:-EMPTY}"

# The classic-era replay TARGETS. Each row is "name:era:endpoint":
#   * name     — the preset/experiment stem (per-era preset in
#                eval_audit/integrations/infer_stack/preset_configs.yaml). The
#                per-mode experiments are <name>-smoke / <name>-full.
#   * era      — the docker/eras.yaml key. Selects the era image + shim pipeline;
#                the bridge guards the image's org.aiq.era label against it.
#   * endpoint — the infer-stack catalog endpoint the run leases (same served
#                pythia-6.9b vLLM backend for both eras).
# The grid loops one row per ERA (not per scenario): each per-era preset carries
# BOTH scenarios (synthetic_reasoning_natural + mmlu), which have distinct logical
# run keys, so a single per-era experiment composes cleanly.
ERA_TARGETS=(
  "era-pythia_6_9b-v0_2_4:helm-v0.2.4:pythia69b-single"
  "era-pythia_6_9b-v0_3_0:helm-v0.3.0:pythia69b-single"
)

# Space-separated era keys the gate validates (derived from ERA_TARGETS unless
# overridden — e.g. LADDER_ERAS="helm-v0.3.0" to gate a single era).
_era_keys_from_targets() { local t; for t in "${ERA_TARGETS[@]}"; do era_key "$t"; done; }

era_name()             { printf '%s\n' "${1%%:*}"; }
era_key()              { local rest="${1#*:}"; printf '%s\n' "${rest%%:*}"; }
era_endpoint()         { printf '%s\n' "${1##*:}"; }
era_experiment_smoke() { printf '%s-smoke\n' "${1%%:*}"; }
era_experiment_full()  { printf '%s-full\n' "${1%%:*}"; }
era_bundle_root()      { printf '%s\n' "$STORE_ROOT/local-bundles/${1%%:*}"; }

# Resolve an era key -> image ref. Override per-era via ERA_IMAGE_<key> (with
# '.'/'-' -> '_'), e.g. ERA_IMAGE_helm_v0_2_4=repo@sha256:... for a digest-pinned
# cross-machine run. Default: <image_name from docker/eras.yaml>:dev (built by
# `ERA=<key> ./docker/build.sh`; see 06_check_era_images.sh). $1 = era key.
era_image() {
  local key="$1" override_var
  override_var="ERA_IMAGE_${key//[.-]/_}"
  if [[ -n "${!override_var:-}" ]]; then printf '%s\n' "${!override_var}"; return; fi
  local name
  name="$("$PYTHON_BIN" "$ROOT/docker/read_eras.py" "$ROOT/docker/eras.yaml" "$key" image_name 2>/dev/null)" \
    || { echo "era_image: cannot resolve image_name for '$key' from docker/eras.yaml" >&2; return 1; }
  printf '%s:dev\n' "$name"
}

# The suite_version dir name for an era key (helm-v0.2.4 -> v0.2.4).
era_suite_version() { printf '%s\n' "${1#helm-}"; }

# The corpus MIRROR root (the parent of the track dirs, e.g.
# /data/crfm-helm-public) — the root that configs/run_details.yaml run_dir paths
# are relative to once CANONICAL_CORPUS_PREFIX is stripped. Distinct from
# PRECOMPUTED_ROOT, which this runbook points at the TRACK root
# (.../classic) for the grid's freeze/discovery: joining a
# classic/benchmark_output/... rel against the track root would double the
# 'classic' component. Detect which convention PRECOMPUTED_ROOT follows so an
# operator using the old mirror-root convention still resolves correctly;
# ERA_MIRROR_ROOT overrides.
era_mirror_root() {
  if [[ -n "${ERA_MIRROR_ROOT:-}" ]]; then
    printf '%s\n' "$ERA_MIRROR_ROOT"
  elif [[ -d "$PRECOMPUTED_ROOT/benchmark_output" ]]; then
    # Track root (the runbook default): the mirror is its parent.
    dirname "$PRECOMPUTED_ROOT"
  else
    # Already a mirror root (tracks underneath), or unknown — use it as-is; the
    # rungs' per-run existence checks surface a wrong guess loudly.
    printf '%s\n' "$PRECOMPUTED_ROOT"
  fi
}

# Build (idempotently) a per-era suite-scoped VIEW of the corpus and echo its
# path, for use as --precomputed-root. Why: pythia-6.9b's runs exist under BOTH
# v0.2.4 and v0.3.0 with identical run-dir names, so freezing against the broad
# classic root is AMBIGUOUS. The view is a real <view>/classic/benchmark_output/
# runs/ dir whose only suite is a symlink to this era's suite — so discovery sees
# exactly one candidate, AND the run-dir path still contains classic/
# benchmark_output/runs/<suite>/... so era resolution yields (classic, <suite>).
# $1 = era key.
era_corpus_view() {
  local key="$1" suite view runs_dir
  suite="$(era_suite_version "$key")"
  view="$ERA_OUT/corpus-view/$key"
  runs_dir="$view/classic/benchmark_output/runs"
  mkdir -p "$runs_dir"
  # Refresh the single suite symlink (idempotent).
  ln -sfn "$PRECOMPUTED_ROOT/benchmark_output/runs/$suite" "$runs_dir/$suite"
  printf '%s\n' "$view"
}

# Clear a prior experiment's result dir so kwdagger's skip_existing doesn't no-op
# a re-invocation. $1 = experiment name.
era_clear_results() {
  local experiment="$1" result_dpath
  result_dpath="$RESULTS_ROOT/$experiment"
  if [[ -d "$result_dpath" ]]; then
    echo "force-rerun: clearing prior results at $result_dpath"
    rm -rf "$result_dpath"
  fi
}

# Resolve a target's per-era virtual-experiment manifest. Keep in sync with
# ERA_TARGETS. $1 = an ERA_TARGETS row (or bare scenario name).
era_vexp_manifest() {
  local d="$ROOT/configs/virtual-experiments"
  case "$(era_name "$1")" in
    era-pythia_6_9b-v0_2_4) printf '%s\n' "$d/era-pythia-v024.yaml" ;;
    era-pythia_6_9b-v0_3_0) printf '%s\n' "$d/era-pythia-v030.yaml" ;;
    *) echo "era_vexp_manifest: no manifest mapped for '$(era_name "$1")'" >&2; return 1 ;;
  esac
}

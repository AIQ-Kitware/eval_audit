#!/usr/bin/env bash
# Shared definitions for the classic Together open-weight COMBINED runbook.
# Source from the numbered scripts: `source "$(dirname "$0")/_lib.sh"`.
#
# Subject: GPT-J 6B, GPT-NeoX 20B, OPT 66B — all their official runs — replayed
# ERA-PINNED through the two era-supported classic suites (helm-v0.2.4 and
# helm-v0.3.0), each inside its own era-pinned CPU-only HELM image, with model
# inference served out-of-process on modern vLLM. Structurally this is
# dev/era-tests generalized to 3 models x all-runs, in the combined multi-model
# reproduce shape of reproduce/olmo_models_combined. See README.md.

# Repo root (three levels up from reproduce/classic_together_combined/).
_here() { cd "$(dirname "${BASH_SOURCE[0]}")" && pwd; }
LIB_DIR="$(_here)"
ROOT="$(cd "$LIB_DIR/../.." && pwd)"

STORE_ROOT="${AUDIT_STORE_ROOT:-/data/crfm-helm-audit-store}"
RESULTS_ROOT="${AUDIT_RESULTS_ROOT:-/data/crfm-helm-audit}"
PYTHON_BIN="${PYTHON_BIN:-python}"

# Public HELM corpus mirror, CLASSIC TRACK ROOT (pre-v0.5 lives under
# <root>/benchmark_output/runs/{v0.2.2..v0.4.0}). Discovery/era-resolution derive
# (public_track, suite_version) from the run-dir path.
PRECOMPUTED_ROOT="${PRECOMPUTED_ROOT:-/data/crfm-helm-public/classic}"
# Prefix the configs/run_details.yaml run_dir paths carry (unused here directly,
# kept for parity with the shared helpers).
CANONICAL_CORPUS_PREFIX="${CANONICAL_CORPUS_PREFIX:-/data/crfm-helm-public}"
# Output root for grid artifacts (corpus views, bundles, logs).
ERA_OUT="${ERA_OUT:-$ROOT/reproduce/classic_together_combined/out}"

# The ~1.3k from-spec run_entries live in a GENERATED, runbook-local preset file
# (gen_presets.py), merged into PRESET_CONFIGS via this env var rather than
# bloating the shared preset_configs.yaml.
export INFER_STACK_EXTRA_PRESET_FILES="${INFER_STACK_EXTRA_PRESET_FILES:-$LIB_DIR/config/presets.yaml}"

# infer-stack serving catalog (3 models + <model>-single endpoints).
export INFER_STACK_CONFIG_DIR="${INFER_STACK_CONFIG_DIR:-$LIB_DIR/config/infer_stack}"

# C-2 (verbatim from dev/era-tests): resolve INFER_STACK_DATA_DIR ONCE and export
# it so the master-key read and the serve-time write agree, and so it is
# bind-mountable into the vLLM/LiteLLM containers (never NFS $HOME).
_yaml_scalar() {  # $1=file $2=key
  local v
  v="$(sed -n -E "s/^[[:space:]]*$2:[[:space:]]*(.*)$/\1/p" "$1" 2>/dev/null | head -n1)"
  v="${v%%#*}"; v="${v%"${v##*[![:space:]]}"}"
  v="${v#\"}"; v="${v%\"}"; v="${v#\'}"; v="${v%\'}"
  printf '%s' "$v"
}
: "${INFER_STACK_DATA_ROOT:=/data/service}"
_pinned_data_dir="$(_yaml_scalar "$INFER_STACK_CONFIG_DIR/settings.yaml" data_dir)"
if [[ -n "${INFER_STACK_DATA_DIR:-}" ]]; then
  _data_src="env"
elif [[ -n "$_pinned_data_dir" ]]; then
  INFER_STACK_DATA_DIR="$_pinned_data_dir"; _data_src="settings.yaml"
else
  INFER_STACK_DATA_DIR="${INFER_STACK_DATA_ROOT}/infer-stack"; _data_src="default"
fi
export INFER_STACK_DATA_DIR
if ! { mkdir -p "$INFER_STACK_DATA_DIR" 2>/dev/null && [[ -w "$INFER_STACK_DATA_DIR" ]]; }; then
  echo "WARN: INFER_STACK_DATA_DIR=$INFER_STACK_DATA_DIR is not writable;" \
       "docker bind-mounts into the vLLM/LiteLLM containers will fail." >&2
fi
echo "[classic-together] infer-stack data dir: $INFER_STACK_DATA_DIR (source: $_data_src)" >&2

# One local attempt per scenario; strip the run-group prefix so rows pair cleanly.
export EVAL_AUDIT_SKIP_LOCAL_REPEAT="${EVAL_AUDIT_SKIP_LOCAL_REPEAT:-1}"
export EVAL_AUDIT_GROUP_STRIP="${EVAL_AUDIT_GROUP_STRIP:-1}"

# Forwarded into the era container as the per-deployment credential (vLLM ignores
# it; v0.2.4's AutoClient merely requires it to exist). Default EMPTY.
export EVAL_AUDIT_ERA_API_KEY="${EVAL_AUDIT_ERA_API_KEY:-EMPTY}"

# The replay TARGETS: one row per (model x era). Each is "preset:era:endpoint":
#   * preset   — the per-model-per-era preset key (config/presets.yaml); the
#                per-mode experiments are <preset>-smoke / <preset>-full.
#   * era      — the docker/eras.yaml key (selects the era image + shim pipeline;
#                the bridge guards the image's org.aiq.era label against it).
#   * endpoint — the infer-stack catalog endpoint the run leases (one served
#                model, shared across both eras).
# The grid loops one row per target; each preset carries ALL of that model's
# official run_entries for that suite (~226), which have distinct logical run
# keys, so a single per-target experiment composes cleanly.
TARGETS=(
  "era-gptj_6b-v0_2_4:helm-v0.2.4:gptj6b-single"
  "era-gptneox_20b-v0_2_4:helm-v0.2.4:gptneox20b-single"
  "era-opt_66b-v0_2_4:helm-v0.2.4:opt66b-single"
  "era-gptj_6b-v0_3_0:helm-v0.3.0:gptj6b-single"
  "era-gptneox_20b-v0_3_0:helm-v0.3.0:gptneox20b-single"
  "era-opt_66b-v0_3_0:helm-v0.3.0:opt66b-single"
)

# The era keys the grids/gate touch (derived from TARGETS unless overridden).
_era_keys_from_targets() { local t; for t in "${TARGETS[@]}"; do t_key "$t"; done | sort -u; }
# The catalog endpoints (deduped) — 05_check_profiles verifies each exists.
_endpoints_from_targets() { local t; for t in "${TARGETS[@]}"; do t_endpoint "$t"; done | sort -u; }

t_preset()           { printf '%s\n' "${1%%:*}"; }
t_key()              { local rest="${1#*:}"; printf '%s\n' "${rest%%:*}"; }
t_endpoint()         { printf '%s\n' "${1##*:}"; }
t_experiment_smoke() { printf '%s-smoke\n' "${1%%:*}"; }
t_experiment_full()  { printf '%s-full\n' "${1%%:*}"; }
t_bundle_root()      { printf '%s\n' "$STORE_ROOT/local-bundles/${1%%:*}"; }

# Resolve an era key -> image ref. Override per-era via ERA_IMAGE_<key> (with
# '.'/'-' -> '_'). Default: <image_name from docker/eras.yaml>:dev. $1 = era key.
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

# Build (idempotently) a per-era suite-scoped VIEW of the corpus and echo its
# path, for use as --precomputed-root. Why: these models' runs exist under BOTH
# v0.2.4 and v0.3.0 with identical run-dir names, so freezing against the broad
# classic root is AMBIGUOUS. The view is a real <view>/classic/benchmark_output/
# runs/ dir whose only suite is a symlink to this era's suite. $1 = era key.
era_corpus_view() {
  local key="$1" suite view runs_dir
  suite="$(era_suite_version "$key")"
  view="$ERA_OUT/corpus-view/$key"
  runs_dir="$view/classic/benchmark_output/runs"
  mkdir -p "$runs_dir"
  ln -sfn "$PRECOMPUTED_ROOT/benchmark_output/runs/$suite" "$runs_dir/$suite"
  printf '%s\n' "$view"
}

# Clear a prior experiment's result dir so kwdagger's skip_existing doesn't no-op
# a re-invocation. $1 = experiment name.
clear_results() {
  local experiment="$1" result_dpath="$RESULTS_ROOT/$1"
  if [[ -d "$result_dpath" ]]; then
    echo "force-rerun: clearing prior results at $result_dpath"
    rm -rf "$result_dpath"
  fi
}

# The per-ERA virtual-experiment manifests (compose is per-era: each folds all
# three models for that suite). Echoes one manifest path per line.
vexp_manifests() {
  local d="$LIB_DIR/configs/virtual-experiments"
  printf '%s\n' "$d/classic-together-v024.yaml" "$d/classic-together-v030.yaml"
}

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
# The experiment name for a mode ($1=smoke|full, $2=target).
t_experiment()       { if [[ "$1" == smoke ]]; then t_experiment_smoke "$2"; else t_experiment_full "$2"; fi; }
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

# The path era_corpus_view builds for an era key, with NO side effects. Concurrent
# readers use this after the view has been created (see run_grid_parallel), so they
# never race era_corpus_view's non-atomic `ln -sfn`. $1 = era key.
era_corpus_view_path() { printf '%s\n' "$ERA_OUT/corpus-view/$1"; }

# Build (idempotently) a per-era suite-scoped VIEW of the corpus and echo its
# path, for use as --precomputed-root. Why: these models' runs exist under BOTH
# v0.2.4 and v0.3.0 with identical run-dir names, so freezing against the broad
# classic root is AMBIGUOUS. The view is a real <view>/classic/benchmark_output/
# runs/ dir whose only suite is a symlink to this era's suite. $1 = era key.
era_corpus_view() {
  local key="$1" suite view runs_dir
  suite="$(era_suite_version "$key")"
  view="$(era_corpus_view_path "$key")"
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

# Export + run ONE target for a mode. Fully isolated per target (distinct
# bundle_root, experiment result dir, and read-only corpus view), so it is safe to
# run concurrently with the other targets. Reads a PRE-CREATED corpus view (path
# only) to avoid racing era_corpus_view's symlink write.
#   $1=mode(smoke|full) $2=target $3=litellm_base_url $4=lease_master_key
run_one_grid() {
  local mode="$1" target="$2" base_url="$3" master_key="$4"
  local preset key endpoint experiment bundle_root view manifest image
  preset="$(t_preset "$target")"; key="$(t_key "$target")"; endpoint="$(t_endpoint "$target")"
  experiment="$(t_experiment "$mode" "$target")"; bundle_root="$(t_bundle_root "$target")"
  image="$(era_image "$key")"; view="$(era_corpus_view_path "$key")"

  echo "== ${preset}  (era: ${key}, endpoint: ${endpoint})  [${mode^^}]"

  "$PYTHON_BIN" -m eval_audit.integrations.infer_stack export-benchmark-bundle \
    --preset "$preset" \
    --bundle-root "$bundle_root" \
    --freeze-rel-paths \
    --precomputed-root "$view" \
    --base-url "${base_url}/v1" \
    --api-key-value "$master_key"
  manifest="$bundle_root/${mode}_manifest.yaml"

  clear_results "$experiment"

  # The master key must ALSO ride EVAL_AUDIT_ERA_API_KEY (forwarded into the
  # container -> the shim's credentials.conf): at v0.2.4, AutoClient's
  # additional_args api_key OVERRIDES the client_spec.args key, so with the
  # default EMPTY the v0.2.4 client would 401 at the gateway. Harmless at v0.3.0.
  # Exported inside this backgrounded subshell only — it does not leak to siblings.
  export EVAL_AUDIT_ERA_API_KEY="${master_key:-$EVAL_AUDIT_ERA_API_KEY}"

  eval-audit-run "$manifest" --lease --run=1 --container-image "$image"
}

# Run EVERY TARGETS row CONCURRENTLY for the given mode, letting the infer-stack
# lease system arbitrate GPUs rather than serializing in bash. Neither the era
# image (a container_gpus:none HTTP client) nor the endpoint identity is a reason
# to serialize: the two eras of one model COALESCE onto a single served endpoint
# (one vLLM container, demand-refcounted), and different models QUEUE for GPU
# residency. Per-target output is redirected to $ERA_OUT/logs/<experiment>.log;
# failures are collected and reported at the end (nonzero return on any failure).
#   $1 = smoke | full
run_grid_parallel() {
  local mode="$1"
  local litellm_port="${LITELLM_PORT:-14042}"
  local base_url="${LITELLM_BASE_URL:-http://localhost:$litellm_port}"
  local log_dir="$ERA_OUT/logs"
  mkdir -p "$log_dir"

  echo "Reclaiming any leaked leases before start (infer-stack gc)…"
  infer-stack gc --yes || echo "WARN: 'infer-stack gc' returned nonzero; continuing." >&2

  # Bootstrap the no-blip gateway ONCE so export-benchmark-bundle can read the
  # managed LiteLLM master key, then release just the bootstrap model.
  local bootstrap_ep bootstrap_env master_key=""
  bootstrap_ep="$(t_endpoint "${TARGETS[0]}")"
  if [[ -n "$bootstrap_ep" ]]; then
    bootstrap_env="$(mktemp)"
    echo "Bootstrapping the gateway via ${bootstrap_ep} to read the LiteLLM master key…"
    infer-stack acquire "$bootstrap_ep" --no-wait --yes --env-file "$bootstrap_env"
    master_key="$(infer-stack env LITELLM_MASTER_KEY)"
    infer-stack release --env-file "$bootstrap_env" --evict --yes \
      || echo "WARN: bootstrap 'release --env-file --evict' returned nonzero; continuing." >&2
    rm -f "$bootstrap_env"
  fi

  # Pre-create each era's suite-scoped corpus view ONCE, serially, so the
  # concurrent exports below never race era_corpus_view's non-atomic symlink write.
  local key
  for key in $(_era_keys_from_targets); do era_corpus_view "$key" >/dev/null; done

  # Launch every target concurrently; the lease system arbitrates GPUs.
  local target experiment log pids=() logs=() names=()
  for target in "${TARGETS[@]}"; do
    experiment="$(t_experiment "$mode" "$target")"
    log="$log_dir/$experiment.log"
    echo "launch: $(t_preset "$target")  (era $(t_key "$target"), endpoint $(t_endpoint "$target")) -> $log"
    run_one_grid "$mode" "$target" "$base_url" "$master_key" >"$log" 2>&1 &
    pids+=("$!"); logs+=("$log"); names+=("$(t_preset "$target")")
  done
  echo "Launched ${#pids[@]} target(s) in parallel; tail a log to watch, e.g.: tail -f ${logs[0]}"

  # Join every target (regardless of individual failures) and collect the failed.
  local i rc failed=()
  for i in "${!pids[@]}"; do
    if wait "${pids[$i]}"; then
      echo "OK:   ${names[$i]}  (${logs[$i]})"
    else
      rc=$?
      echo "FAIL: ${names[$i]}  rc=${rc}  (${logs[$i]})" >&2
      failed+=("${names[$i]}")
    fi
  done

  echo "Reclaiming any leaked leases (infer-stack gc)…"
  infer-stack gc --yes || echo "WARN: 'infer-stack gc' returned nonzero; continuing." >&2

  if (( ${#failed[@]} > 0 )); then
    echo >&2; echo "Completed with ${#failed[@]} failed target(s):" >&2
    printf '  - %s\n' "${failed[@]}" >&2
    return 1
  fi
  echo; echo "OK: all ${#TARGETS[@]} ${mode} runs completed."
}

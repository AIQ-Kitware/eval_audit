#!/usr/bin/env bash
# Shared definitions for the reproduce/ runbooks.
#
# This is the single source of truth merged from the three previously-duplicated
# per-scenario _lib.sh files (olmo_models/, olmo_models_combined/,
# small_models_kubeai/). Each scenario's _lib.sh is now a thin shim that sources
# THIS file and then, for the OLMo runbooks, calls `olmo_setup` to run the
# serving/leasing/container/HF-auth setup. The kubeai runbook only needs the
# function definitions and calls nothing here at source time (it has, and must
# keep, zero side effects when sourced).
#
# IMPORTANT (BASH_SOURCE / path depth): the *_root helpers below resolve the repo
# root RELATIVE TO THIS FILE'S LOCATION. Because this file lives at
# reproduce/_lib.sh (one level under the repo root), the root is `dirname(..)/..`
# — one `..`, not two. The original per-scenario copies lived at
# reproduce/<scenario>/_lib.sh (two levels down) and used `../..`. Moving the
# definition up one directory means BASH_SOURCE[0] now points one level higher,
# so the compensating path is one `..` shorter. The effective repo-root value is
# unchanged.

# ---------------------------------------------------------------------------
# Repo-root helpers (identical bodies; kept under both historical names).
# ---------------------------------------------------------------------------

# Repo root, resolved from this file's location (reproduce/_lib.sh -> .. = root).
olmo_root() {
  cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd
}

small_models_root() {
  cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd
}

# ---------------------------------------------------------------------------
# OLMo helpers (used by olmo_models/ and olmo_models_combined/).
# ---------------------------------------------------------------------------

_olmo_yaml_scalar() {  # $1=file $2=key -> value with quotes/inline-comment stripped
  local v
  v="$(sed -n -E "s/^[[:space:]]*$2:[[:space:]]*(.*)$/\1/p" "$1" 2>/dev/null | head -n1)"
  v="${v%%#*}"                          # strip inline comment
  v="${v%"${v##*[![:space:]]}"}"        # rstrip whitespace
  v="${v#\"}"; v="${v%\"}"              # strip double quotes
  v="${v#\'}"; v="${v%\'}"              # strip single quotes
  printf '%s' "$v"
}

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

olmo_preset()     { printf '%s\n' "${1%%:*}"; }
olmo_profile()    { printf '%s\n' "${1##*:}"; }
# Per-mode experiment names, matching the smoke_manifest / full_manifest blocks
# in eval_audit/integrations/infer_stack/adapter.py.
olmo_experiment_smoke() { printf 'audit-%s-smoke\n' "${1%%:*}"; }
olmo_experiment_full()  { printf 'audit-%s-full\n' "${1%%:*}"; }
olmo_bundle_root() { printf '%s\n' "$STORE_ROOT/local-bundles/${1%%:*}"; }

# Export one extra single-model preset's exact-path bundle and schedule its <mode>
# manifest (smoke|full) with per-run leasing + fan-out. Single-deployment freeze
# against the preset's OWN narrow precomputed_root (baked into its manifest block);
# no inline model_deployment token, so the locator run-entry is a bare discovery
# key. Expects the gateway already bootstrapped by 10/15: LEASE_MASTER_KEY,
# LITELLM_BASE_URL, OLMO_CONTAINER_IMAGE, OLMO_TMUX_WORKERS in the environment.
# Honors FORCE_RERUN (the caller's OLMO_FORCE_RERUN).
# (Only the olmo_models_combined runbook uses this; defined here for a single
# source of truth — harmless when unused.)
olmo_run_extra_preset() {
  local preset="$1" mode="$2"   # mode = smoke | full
  local bundle_root="$STORE_ROOT/local-bundles/$preset"
  local experiment="audit-${preset}-${mode}"
  echo
  echo "==================================================================="
  echo "== extra single-model suite: ${preset} (${mode})"
  echo "==================================================================="
  "$PYTHON_BIN" -m eval_audit.integrations.infer_stack export-benchmark-bundle \
    --preset "$preset" \
    --bundle-root "$bundle_root" \
    --access-kind openai-compatible \
    --base-url "${LITELLM_BASE_URL}/v1" \
    --api-key-value "$LEASE_MASTER_KEY" \
    --from-spec --freeze-rel-paths
  if [[ "${FORCE_RERUN:-0}" == "1" && -d "$RESULTS_ROOT/$experiment" ]]; then
    echo "OLMO_FORCE_RERUN=1: clearing prior results at $RESULTS_ROOT/$experiment"
    rm -rf "$RESULTS_ROOT/$experiment"
  fi
  eval-audit-run --run=1 "$bundle_root/${mode}_manifest.yaml" \
    --container-image "$OLMO_CONTAINER_IMAGE" --lease --tmux-workers "$OLMO_TMUX_WORKERS"
}

# ---------------------------------------------------------------------------
# olmo_setup: the serving / leasing / container / HuggingFace-auth /
# infer-stack-config environment for the OLMo runbooks. Formerly the top-level
# body of reproduce/olmo_models/_lib.sh; wrapped in a function so that sourcing
# the shared lib is side-effect-free (the kubeai runbook must not trigger any of
# this). The OLMo shims call `olmo_setup` immediately after sourcing, reproducing
# the original source-time behavior exactly. All assignments are intentionally
# unqualified (global), so the effective shell state is identical to the old
# top-level execution.
# ---------------------------------------------------------------------------
olmo_setup() {
  ROOT="$(olmo_root)"
  STORE_ROOT="${AUDIT_STORE_ROOT:-/data/crfm-helm-audit-store}"
  RESULTS_ROOT="${AUDIT_RESULTS_ROOT:-/data/crfm-helm-audit}"
  # Grouping manifest for the downstream index -> compose -> summary steps. It
  # scopes to the FULL run experiments (audit-<preset>-full); the smoke grid is a
  # preflight only and is not folded into the grouped report.
  VEXP_MANIFEST="${VEXP_MANIFEST:-$ROOT/configs/virtual-experiments/olmo-models.yaml}"
  PYTHON_BIN="${PYTHON_BIN:-python}"

  # GPU placement: NO restriction by default — infer-stack serves across ALL
  # detected GPUs, so the per-run-lease fan-out can spread models over every card
  # (first_fit preserves real indices). On a SHARED machine, export
  # INFER_STACK_ALLOWED_GPUS=<csv> to pin serving to specific physical indices (keep
  # >=2 free for the 32B tp=2 profile). infer-stack reads that env var directly, so
  # we intentionally set no default here (unset ⇒ all detected GPUs).

  # infer-stack catalog providing the six OLMo models + <preset>-single endpoints.
  # Defaults to the config dir shipped alongside this runbook (settings.yaml +
  # catalog.yaml); override to point at your own infer-stack config if the OLMo
  # endpoints already live there.
  export INFER_STACK_CONFIG_DIR="${INFER_STACK_CONFIG_DIR:-$ROOT/reproduce/olmo_models/config/infer_stack}"

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
  : "${INFER_STACK_DATA_ROOT:=/data/service}"
  _olmo_pinned_data_dir="$(_olmo_yaml_scalar "$INFER_STACK_CONFIG_DIR/settings.yaml" data_dir)"
  if [[ -n "${INFER_STACK_DATA_DIR:-}" ]]; then
    _olmo_data_src="env"                                          # 1. explicit override wins
  elif [[ -n "$_olmo_pinned_data_dir" ]]; then
    INFER_STACK_DATA_DIR="$_olmo_pinned_data_dir"; _olmo_data_src="settings.yaml"  # 2. yaml pin
  else
    INFER_STACK_DATA_DIR="${INFER_STACK_DATA_ROOT}/infer-stack"; _olmo_data_src="default"  # 3. fallback
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
    _olmo_fstype="$(stat -f -c %T "$INFER_STACK_DATA_DIR" 2>/dev/null || true)"
    if [[ "$_olmo_fstype" == nfs* || "$_olmo_fstype" == autofs ]]; then
      echo "WARN: INFER_STACK_DATA_DIR=$INFER_STACK_DATA_DIR is on $_olmo_fstype" \
           "(not docker-mountable); the vLLM HF-cache mount will fail." \
           "Set INFER_STACK_DATA_DIR to a local big disk." >&2
    fi
  fi
  echo "[olmo] infer-stack data dir: $INFER_STACK_DATA_DIR (source: $_olmo_data_src)" >&2

  # Carry the e2e-test conventions: one local attempt per model (no repeat),
  # strip the run-group prefix so smoke rows pair cleanly.
  export EVAL_AUDIT_SKIP_LOCAL_REPEAT="${EVAL_AUDIT_SKIP_LOCAL_REPEAT:-1}"
  export EVAL_AUDIT_GROUP_STRIP="${EVAL_AUDIT_GROUP_STRIP:-1}"

  # Containerized HELM execution (the "docker pipeline"; see
  # docs/container-execution.md) is MANDATORY: the grid scripts always pass
  # `eval-audit-run --container-image "$OLMO_CONTAINER_IMAGE"`, so HELM runs inside
  # the pinned eval-audit-helm-runner image — pinning the software environment so it
  # stops being a confounding variable in the reproducibility comparison. The model
  # is still SERVED ON THE HOST (vLLM behind LiteLLM); only WHERE HELM runs is the
  # container. The in-container HELM reaches the host's LiteLLM endpoint via
  # --network host (declared by the presets' container_network: host in
  # eval_audit/integrations/infer_stack/adapter.py). The host-venv path has been
  # removed (build_schedule_params now requires a container image). Leasing is the
  # ORTHOGONAL axis (always on via --lease; see lease_bracket.py).
  #
  # OLMO_CONTAINER_IMAGE is the local tag built by ./docker/build.sh; override with a
  # pushed digest for cross-machine pinning. 07_check_container_image.sh verifies it
  # exists before the grid runs.
  export OLMO_CONTAINER_IMAGE="${OLMO_CONTAINER_IMAGE:-eval-audit-helm-runner:dev}"

  local HF_TOKEN_VALUE
  HF_TOKEN_VALUE="$(olmo_resolve_hf_token)"
  if [[ -n "$HF_TOKEN_VALUE" ]]; then
    export HF_TOKEN="$HF_TOKEN_VALUE"
    export HUGGING_FACE_HUB_TOKEN="$HF_TOKEN_VALUE"
  fi

  # The seven OLMo from-spec experiments (six models; olmo-7b is split into -mmlu /
  # -lite, see below), ordered smallest -> largest so a cheap model surfaces a
  # pipeline failure before the expensive 32B load. Each row is "preset:endpoint".
  # The infer-stack catalog endpoint is the preset name with a "-single" suffix,
  # and the per-mode experiment_name is "audit-<preset>-smoke" /
  # "audit-<preset>-full" (smoke is a preflight; the full runs feed indexing).
  OLMO_TARGETS=(
    "allenai-olmoe-1b-7b-0125-instruct:allenai-olmoe-1b-7b-0125-instruct-single"
    # olmo-7b is split into two from-spec experiments (-mmlu / -lite) reproducing its
    # full-MMLU and HELM-Lite official runs from per-suite precomputed_roots; both
    # serve the same model, so both point at the one allenai-olmo-7b-single endpoint.
    "allenai-olmo-7b-mmlu:allenai-olmo-7b-single"
    "allenai-olmo-7b-lite:allenai-olmo-7b-single"
    "allenai-olmo-1-7-7b:allenai-olmo-1-7-7b-single"
    "allenai-olmo-2-1124-7b-instruct:allenai-olmo-2-1124-7b-instruct-single"
    "allenai-olmo-2-1124-13b-instruct:allenai-olmo-2-1124-13b-instruct-single"
    "allenai-olmo-2-0325-32b-instruct:allenai-olmo-2-0325-32b-instruct-single"
  )
}

# ---------------------------------------------------------------------------
# KubeAI helpers (used by small_models_kubeai/).
# ---------------------------------------------------------------------------

resolve_kubeai_namespace() {
  if [[ -n "${KUBEAI_NAMESPACE:-}" ]]; then
    printf '%s\n' "$KUBEAI_NAMESPACE"
    return
  fi
  if command -v helm >/dev/null 2>&1; then
    local detected
    detected="$(helm list -A 2>/dev/null | awk 'NR>1 && $1=="kubeai" {print $2; exit}')"
    if [[ -n "$detected" ]]; then
      printf '%s\n' "$detected"
      return
    fi
  fi
  printf '%s\n' "default"
}

print_kubeai_diagnostics() {
  local namespace="$1"
  echo "=== kubectl -n $namespace get model -o wide ==="
  kubectl -n "$namespace" get model -o wide || true
  echo

  echo "=== kubectl -n $namespace get model -o yaml ==="
  kubectl -n "$namespace" get model -o yaml || true
  echo

  echo "=== effective KubeAI model args ==="
  for model in qwen2-5-7b-instruct-turbo-default vicuna-7b-v1-3-no-chat-template; do
    echo "--- $model"
    kubectl -n "$namespace" get model "$model" -o jsonpath='{range .spec.args[*]}{.}{"\n"}{end}' || true
    echo
  done

  echo "=== kubectl -n $namespace describe model qwen2-5-7b-instruct-turbo-default ==="
  kubectl -n "$namespace" describe model qwen2-5-7b-instruct-turbo-default || true
  echo

  echo "=== kubectl -n $namespace describe model vicuna-7b-v1-3-no-chat-template ==="
  kubectl -n "$namespace" describe model vicuna-7b-v1-3-no-chat-template || true
  echo

  echo "=== kubectl -n $namespace get pods -o wide ==="
  kubectl -n "$namespace" get pods -o wide || true
  echo

  local serving_pods
  serving_pods="$(kubectl -n "$namespace" get pods --no-headers 2>/dev/null | awk '/qwen2-5-7b-instruct-turbo-default|vicuna-7b-v1-3-no-chat-template/ {print $1}')"
  if [[ -n "$serving_pods" ]]; then
    local pod
    for pod in $serving_pods; do
      echo "=== kubectl -n $namespace logs $pod --tail=200 ==="
      kubectl -n "$namespace" logs "$pod" --tail=200 || true
      echo
    done
  fi

  local kubeai_pods
  kubeai_pods="$(kubectl -n "$namespace" get pods --no-headers 2>/dev/null | awk '/kubeai/ {print $1}')"
  if [[ -n "$kubeai_pods" ]]; then
    local pod
    for pod in $kubeai_pods; do
      echo "=== kubectl -n $namespace logs $pod --tail=200 ==="
      kubectl -n "$namespace" logs "$pod" --tail=200 || true
      echo
    done
  elif kubectl -n "$namespace" get deploy kubeai >/dev/null 2>&1; then
    echo "=== kubectl -n $namespace logs deploy/kubeai --tail=200 ==="
    kubectl -n "$namespace" logs deploy/kubeai --tail=200 || true
    echo
  fi

  echo "=== kubectl -n $namespace get events --sort-by=.metadata.creationTimestamp | tail -n 40 ==="
  kubectl -n "$namespace" get events --sort-by=.metadata.creationTimestamp | tail -n 40 || true
}

patch_model_for_tonight() {
  local namespace="$1"
  local model_name="$2"
  local public_served_name="$3"
  local tmp
  tmp="$(mktemp)"
  kubectl -n "$namespace" get model "$model_name" -o json >"$tmp"
  python3 - "$tmp" "$public_served_name" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
public_name = sys.argv[2]
doc = json.loads(path.read_text())
spec = doc.setdefault("spec", {})
args = list(spec.get("args") or [])
rewritten = []
seen = False
for arg in args:
    if isinstance(arg, str) and arg.startswith("--served-model-name="):
        rewritten.append(f"--served-model-name={public_name}")
        seen = True
    else:
        rewritten.append(arg)
if not seen:
    rewritten.insert(0, f"--served-model-name={public_name}")
spec["args"] = rewritten
spec["resourceProfile"] = "gpu-single-default:1"
spec["minReplicas"] = 1
doc.pop("status", None)
metadata = doc.setdefault("metadata", {})
for field in (
    "creationTimestamp",
    "generation",
    "resourceVersion",
    "uid",
    "managedFields",
    "selfLink",
):
    metadata.pop(field, None)
path.write_text(json.dumps(doc))
PY
  kubectl -n "$namespace" apply -f "$tmp"
  rm -f "$tmp"
}

wait_for_model_objects() {
  local namespace="$1"
  local attempts="${2:-60}"
  local sleep_s="${3:-10}"
  local i
  for ((i=1; i<=attempts; i++)); do
    if kubectl -n "$namespace" get model qwen2-5-7b-instruct-turbo-default >/dev/null 2>&1 && \
       kubectl -n "$namespace" get model vicuna-7b-v1-3-no-chat-template >/dev/null 2>&1; then
      echo "Both KubeAI Model objects exist in namespace $namespace"
      return 0
    fi
    echo "Waiting for KubeAI Model objects ($i/$attempts)..."
    sleep "$sleep_s"
  done
  return 1
}

wait_for_model_pods_ready() {
  local namespace="$1"
  local attempts="${2:-60}"
  local sleep_s="${3:-10}"
  local patterns='qwen2-5-7b-instruct-turbo-default|vicuna-7b-v1-3-no-chat-template'
  local i
  for ((i=1; i<=attempts; i++)); do
    local pod_lines
    pod_lines="$(kubectl -n "$namespace" get pods --no-headers 2>/dev/null | grep -E "$patterns" || true)"
    if [[ -n "$pod_lines" ]]; then
      local qwen_ready=0
      local vicuna_ready=0
      while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        local pod ready status
        pod="$(awk '{print $1}' <<<"$line")"
        ready="$(awk '{print $2}' <<<"$line")"
        status="$(awk '{print $3}' <<<"$line")"
        if [[ "$pod" == *qwen2-5-7b-instruct-turbo-default* && "$status" == "Running" && "${ready%/*}" == "${ready#*/}" ]]; then
          qwen_ready=1
        fi
        if [[ "$pod" == *vicuna-7b-v1-3-no-chat-template* && "$status" == "Running" && "${ready%/*}" == "${ready#*/}" ]]; then
          vicuna_ready=1
        fi
      done <<<"$pod_lines"
      if [[ "$qwen_ready" == "1" && "$vicuna_ready" == "1" ]]; then
        echo "Serving pods for both models are Ready"
        return 0
      fi
    fi
    echo "Waiting for serving pods to become Ready ($i/$attempts)..."
    sleep "$sleep_s"
  done
  return 1
}

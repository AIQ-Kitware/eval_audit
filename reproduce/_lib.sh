#!/usr/bin/env bash
# Shared definitions for the small_models_kubeai runbook.
#
# History: this file once carried the shared setup for three runbooks (olmo_models/,
# olmo_models_combined/, small_models_kubeai/). The single-model olmo_models runbook
# has since been removed and olmo_models_combined is self-contained (it ships its own
# _lib.sh with no dependency on this file), so the OLMo half — `olmo_root` and
# `olmo_setup` — was dropped. What remains is the KubeAI runbook's definitions; its
# _lib.sh is a thin shim that sources THIS file. Sourcing is side-effect-free — it
# only defines functions — matching the kubeai path's original behavior (no
# source-time environment setup).
#
# IMPORTANT (BASH_SOURCE / path depth): the small_models_root helper below resolves
# the repo root RELATIVE TO THIS FILE'S LOCATION. Because this file lives at
# reproduce/_lib.sh (one level under the repo root), the root is `dirname(..)/..`
# — one `..`, not two. The original per-scenario copy lived at
# reproduce/small_models_kubeai/_lib.sh (two levels down) and used `../..`.

# ---------------------------------------------------------------------------
# Repo-root helper.
# ---------------------------------------------------------------------------

small_models_root() {
  cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd
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

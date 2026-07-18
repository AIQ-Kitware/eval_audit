#!/usr/bin/env bash
# Shared definitions for the OPEN-JUDGE experiment runbook (aiq-gpu).
# Source from the numbered scripts: `source "$(dirname "$0")/_lib.sh"`.
#
# Rejudges frozen HELM candidate responses (gpt-oss-20b closed-judge rows)
# with open-weight judges (Qwen3.5-27B, Qwen3.6-35B-A3B) served via
# infer-stack + LiteLLM. Unlike the candidate runbooks this does NOT use
# kwdagger per-run leasing: a judge endpoint is leased for the duration of
# a rejudge pass (the model must stay up across many judge requests), the
# sidecar bundle is exported against the live gateway, the rejudge CLI runs
# in-process against the gateway, then the lease is released.
#
# GPU placement is UNPINNED (vram-aware-placement): each judge endpoint
# declares placement.min_vram_gib; on aiq-gpu's homogeneous 4x96 GiB pool
# eligibility is trivially satisfied. Do NOT set INFER_STACK_ALLOWED_GPUS.

oj_root() {
  cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd
}

ROOT="$(oj_root)"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python || command -v python3)}"

# infer-stack catalog shipped by this runbook (the two judge endpoints).
# Announce whether it was inherited from the environment (a leftover
# INFER_STACK_CONFIG_DIR from a sibling runbook would silently shadow this).
if [[ -n "${INFER_STACK_CONFIG_DIR:-}" ]]; then
  _oj_cfg_src="env (inherited — may shadow this runbook's catalog)"
else
  _oj_cfg_src="runbook default"
fi
export INFER_STACK_CONFIG_DIR="${INFER_STACK_CONFIG_DIR:-$ROOT/reproduce/open_judge_gpt_oss/config/infer_stack}"
echo "[open-judge] infer-stack config dir: $INFER_STACK_CONFIG_DIR (source: $_oj_cfg_src)" >&2

# Store roots (aiq-gpu /data). Snapshots/results/cache live under an
# open-judge subtree so nothing collides with the candidate audit store.
STORE_ROOT="${AUDIT_STORE_ROOT:-/data/crfm-helm-audit-store}"
OJ_ROOT="${OJ_ROOT:-$STORE_ROOT/open-judge}"
OJ_CORPUS="${OJ_CORPUS:-/data/crfm-helm-public}"
OJ_SNAPSHOT_ROOT="${OJ_SNAPSHOT_ROOT:-$OJ_ROOT/response-snapshots}"
OJ_RESULTS_ROOT="${OJ_RESULTS_ROOT:-$OJ_ROOT/results}"
OJ_CACHE_ROOT="${OJ_CACHE_ROOT:-$OJ_ROOT/cache}"
OJ_SIDECAR_DIR="${OJ_SIDECAR_DIR:-$OJ_ROOT/judge-sidecars}"
OJ_ANALYSIS_ROOT="${OJ_ANALYSIS_ROOT:-$OJ_ROOT/analysis}"
OJ_AUDIT_JSON="${OJ_AUDIT_JSON:-$OJ_ROOT/source-audit.json}"

# Candidate model + benchmarks in scope (v1: the two implemented families).
OJ_CANDIDATE_MODEL="${OJ_CANDIDATE_MODEL:-openai/gpt-oss-20b}"
OJ_BENCHMARKS="${OJ_BENCHMARKS:-xstest wildbench}"

# Judge arms (v1). Endpoint names must match config/infer_stack/catalog.yaml;
# JudgeSpec JSONs live in configs/open_judge/.
OJ_JUDGE_ENDPOINTS="qwen3.5-27b-judge qwen3.6-35b-a3b-judge"
OJ_JUDGE_JSON_QWEN35="$ROOT/configs/open_judge/qwen3_5_27b.json"
OJ_JUDGE_JSON_QWEN36="$ROOT/configs/open_judge/qwen3_6_35b_a3b.json"
OJ_EXPERIMENT="${OJ_EXPERIMENT:-gpt-oss-20b-open-judge-v1}"

LITELLM_PORT="${LITELLM_PORT:-14042}"
LITELLM_BASE_URL="${LITELLM_BASE_URL:-http://localhost:$LITELLM_PORT}"

# Readiness wait for a judge lease. A 27B/35B judge's FIRST acquire must
# download tens of GiB of weights from HF before it can serve, which blows
# past the default 600s acquire timeout (that mode RELEASES the lease on
# timeout). So we acquire with --no-wait (holds the lease while it loads)
# and block with `infer-stack wait` up to this budget instead.
OJ_LEASE_WAIT_TIMEOUT="${OJ_LEASE_WAIT_TIMEOUT:-3600}"

# Resolve a judge key to "<lease-endpoint> <judge-json>". Accepts the short
# key (qwen35/qwen36) or the JudgeSpec id. Prints the pair or returns nonzero
# so the caller can fail loudly on an unknown judge (never a silent default).
oj_judge_spec() {
  case "$1" in
    qwen35|qwen3_5_27b|qwen3.5-27b) echo "qwen3.5-27b-judge $OJ_JUDGE_JSON_QWEN35" ;;
    qwen36|qwen3_6_35b_a3b|qwen3.6-35b-a3b) echo "qwen3.6-35b-a3b-judge $OJ_JUDGE_JSON_QWEN36" ;;
    *) return 1 ;;
  esac
}

# Resolve the snapshot directory for a benchmark by reading manifests under
# OJ_SNAPSHOT_ROOT (built by 08). Prints the dir path or returns nonzero.
oj_snapshot_for_benchmark() {
  local want="$1" manifest bench
  for manifest in "$OJ_SNAPSHOT_ROOT"/*/response_manifest.json; do
    [[ -f "$manifest" ]] || continue
    bench="$("$PYTHON_BIN" -c "import json,sys;print(json.load(open(sys.argv[1])).get('supported_benchmark',''))" "$manifest" 2>/dev/null)"
    if [[ "$bench" == "$want" ]]; then
      dirname "$manifest"
      return 0
    fi
  done
  return 1
}

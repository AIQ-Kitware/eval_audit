#!/usr/bin/env bash
# Shared definitions for the OPEN-JUDGE experiment runbook (aiq-gpu).
# Source from the numbered scripts: `source "$(dirname "$0")/_lib.sh"`.
#
# Rejudges frozen HELM candidate responses (gpt-oss-20b closed-judge rows)
# with open-weight judges (the Qwen3.5 size ladder + Qwen3.6-35B-A3B) served
# via infer-stack + LiteLLM.
#
# THE SCRIPTS HERE ARE THE SERIAL PATH: one judge endpoint is leased for a
# whole pass (the model must stay up across many judge requests), the sidecar
# bundle is exported against the live gateway, the rejudge CLI runs in-process
# against the gateway, then the lease is released. That is SERIAL over judges,
# and every judge arm is TP1, so ONE worker occupies ONE GPU and the rest of a
# multi-GPU host idles.
#
# For fan-out use the kwdagger path instead: `eval-audit-schedule-rejudge`
# (eval_audit/pipelines/rejudge_pipeline.py) emits one job per
# (judge, benchmark, replicate), each bracketing its own infer-stack lease, so
# kwdagger fans the matrix out and the admission queue decides what co-hosts.
#
# HISTORICAL NOTE, because this comment previously said the opposite and shaped
# the whole runbook: what does not fit here is the candidate runbooks' PER-RUN
# leasing idiom (acquire -> infer -> release for each run), which would reload
# judge weights per job. That is an argument against one idiom, NOT against
# kwdagger — the plan always specced Commit 11 as "reuse one serving session
# across several rejudge jobs". Do not read "serial" as "kwdagger cannot do
# this".
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

# Candidate model + benchmarks in scope. Cheap label-metric benchmarks first,
# WildBench last: it is the cost center (~20 s/inst on the dense 27B vs ~1-7 s
# for the safety family), so a night that runs short still yields the complete
# safety picture. Omni-MATH joins when its annotator lands.
# NOTE: a benchmark only runs once 05 (audit) + 08 (snapshot) + 09 (replay
# gate) have covered it; the overnight driver skips uncovered ones as
# NO_SNAPSHOT rather than failing.
OJ_CANDIDATE_MODEL="${OJ_CANDIDATE_MODEL:-openai/gpt-oss-20b}"
OJ_BENCHMARKS="${OJ_BENCHMARKS:-xstest simple_safety_tests harm_bench anthropic_red_team wildbench}"

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

# Resolve a judge key to "<lease-endpoint> <judge-json>". Accepts the
# JudgeSpec id (qwen3_5_9b), the endpoint-ish form (qwen3.5-9b), or the
# legacy short keys (qwen35/qwen36 -> the two v1 arms). Prints the pair or
# returns nonzero so the caller fails loudly on an unknown judge (never a
# silent default).
oj_judge_spec() {
  local key="$1" id ep
  case "$key" in
    qwen35) id=qwen3_5_27b;       ep=qwen3.5-27b ;;
    qwen36) id=qwen3_6_35b_a3b;   ep=qwen3.6-35b-a3b ;;
    qwen3_5_0_8b|qwen3.5-0.8b) id=qwen3_5_0_8b; ep=qwen3.5-0.8b ;;
    qwen3_5_2b|qwen3.5-2b)     id=qwen3_5_2b;   ep=qwen3.5-2b ;;
    qwen3_5_4b|qwen3.5-4b)     id=qwen3_5_4b;   ep=qwen3.5-4b ;;
    qwen3_5_9b|qwen3.5-9b)     id=qwen3_5_9b;   ep=qwen3.5-9b ;;
    qwen3_5_27b|qwen3.5-27b)   id=qwen3_5_27b;  ep=qwen3.5-27b ;;
    qwen3_6_35b_a3b|qwen3.6-35b-a3b) id=qwen3_6_35b_a3b; ep=qwen3.6-35b-a3b ;;
    *) return 1 ;;
  esac
  echo "${ep}-judge $ROOT/configs/open_judge/${id}.json"
}

# The judge-SIZE sweep, small -> large (the Qwen3.5 post-trained ladder plus
# the Qwen3.6 MoE). Used as the default judge list for the overnight run.
OJ_JUDGE_LADDER="${OJ_JUDGE_LADDER:-qwen3_5_0_8b qwen3_5_2b qwen3_5_4b qwen3_5_9b qwen3_5_27b qwen3_6_35b_a3b}"

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

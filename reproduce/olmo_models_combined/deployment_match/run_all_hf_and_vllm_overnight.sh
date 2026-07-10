#!/usr/bin/env bash
# Overnight batch: for EVERY OLMo instruct model, run BOTH the vLLM sweep and the
# HF fp32 probe — into separate result dirs — sequentially, logging each run and
# continuing past failures, then print a summary table. Built to be launched with
# nohup and left overnight.
#
#   nohup reproduce/olmo_models_combined/deployment_match/run_all_hf_and_vllm_overnight.sh \
#     > /tmp/olmo-overnight.log 2>&1 &
#
# For each model it runs, in order:
#   * vLLM sweep  -> <store>/deployment-match/<slug>--ifeval-vllm   (auto/hf-match; all dtypes)
#   * HF fp32     -> <store>/deployment-match/<slug>--ifeval-hf     (transformers.generate fp32)
# Both are scored against the same oracle (the public run's completions), so their
# best_deployment.yaml / ranking.txt are directly comparable per model.
#
# GPU config (defaults assume 80 GB cards; override for your hardware). The same
# device set is used for the HF `device_map=auto` visible GPUs AND as the vLLM fp32
# tensor-parallel size (its comma-count):
#   GPU_OLMOE (default 0)      OLMoE-1B-7B  ~28 GB fp32 -> 1 GPU
#   GPU_7B    (default 0)      OLMo-2-7B    ~28 GB fp32 -> 1 GPU
#   GPU_13B   (default 0)      OLMo-2-13B   ~52 GB fp32 -> 1x80 GB (use 0,1 on 40 GB)
#   GPU_32B   (default 0,1)    OLMo-2-32B  ~128 GB fp32 -> 2x80 GB (use 0,1,2,3 on 40 GB)
#
# This runner sweeps EVERY parameter both engines expose (SWEEP=full, the default):
#
#   vLLM (already maximal at its defaults): the hf-match profile sweeps the
#     attention backend {default, FLASH_ATTN, XFORMERS, TORCH_SDPA} x
#     add_generation_prompt {True, False}, and the grid default sweeps dtype
#     {auto, float16, bfloat16, float32} x add_special_tokens {True, False}. vLLM's
#     scheduler determinism knobs (enforce-eager / no-chunked-prefill / no-prefix-
#     cache / max_num_seqs=1) stay PINNED — they're confounder-removal for matching
#     an HF official, not axes to vary.
#   HF probe (transformers.generate): sweeps the fp32-FORWARD-PASS knobs that
#     actually move the greedy logits — dtype {float32, bfloat16, float16} x
#     attn_implementation {eager, sdpa} x device_map {auto, single*} — plus the
#     request/decode variants decode {helm, greedy} x add_generation_prompt {T,F} x
#     add_special_tokens {T,F}. (*single is per-model: infeasible for 32B fp32,
#     which can't fit one GPU, so 32B keeps device_map=auto.)
#
# Cost: the HF sweep is a big cartesian (full = up to 3x2x2 reloads x 2x2x2 = 96
# cells/model, each cell = DM_N generations at fp32 — slow). It's an overnight job.
# Use SWEEP=quick, or narrow any single axis, to cut it.
#
# Other knobs:
#   ENGINES  (default "vllm hf")   which engines to run, space-separated
#   SKIP     (default "")          space-separated model slugs to skip
#   DM_N     (default 12)          sampled instances per model
#   SWEEP    (default full)        full = every parameter; quick = narrow (fp32 / sdpa
#                                  / helm / one devmap) for a fast pass
#   DM_HF_DTYPES  HF dtype axis (comma list; default per SWEEP)
#   DM_HF_ATTN    HF attn_implementation axis (default per SWEEP; 'none' = tf default)
#   DM_HF_DEVMAPS HF device placement axis (default: per-model — auto,single ≤13B/OLMoE;
#                                  auto for 32B). Set to override globally.
#   DM_HF_DECODE  HF decode axis (helm,greedy; default per SWEEP)
#   DM_HF_AST / DM_HF_AGP  HF add_special_tokens / add_generation_prompt (default per SWEEP)
#   DM_PROFILE (default hf-match)  vLLM grid profile (empty = plain default grid)
#   DM_DTYPES / DM_ATTN  narrow the vLLM dtype / attention_backend sweep (empty = full)
#   DM_DRY=1                       preview: vLLM emits its CPU plan; HF is skipped (no dry mode)
#   LOG_DIR  (default <store>/deployment-match/_overnight-<stamp>)
#   AUDIT_STORE_ROOT               override the store root (tests: point at /tmp)
#
# NB OLMoE is a MoE model, so its vLLM fp32 cells are auto-pruned (Triton
# shared-memory OOM) — its matched-precision answer comes from the HF run. The
# dense OLMo-2 models can serve fp32 in vLLM (that's why DM_FP32_TP is set from the
# GPU count), but only HF matches the official *engine* exactly.

set -uo pipefail    # deliberately NOT -e: one failed run must not abort the batch

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../_lib.sh"          # ROOT, STORE_ROOT, HF_TOKEN, INFER_STACK_DATA_DIR, ...
cd "$ROOT"

STAMP="$(date +%Y%m%d-%H%M%S)"
LOG_DIR="${LOG_DIR:-$STORE_ROOT/deployment-match/_overnight-$STAMP}"
mkdir -p "$LOG_DIR"
DM_N="${DM_N:-12}"
DM_DRY="${DM_DRY:-0}"
ENGINES="${ENGINES:-vllm hf}"
SKIP="${SKIP:-}"
SWEEP="${SWEEP:-full}"

# ---- Sweep axes (both engines) ---------------------------------------------
# SWEEP=full exercises every parameter; SWEEP=quick is a narrow fast pass. Any
# individual knob below still overrides the preset (`${VAR:-...}`).
if [[ "$SWEEP" == full ]]; then
  DM_HF_DTYPES="${DM_HF_DTYPES:-float32,bfloat16,float16}"   # fp32 is the known match; sweep down to prove it
  DM_HF_ATTN="${DM_HF_ATTN:-eager,sdpa}"                     # fp32-safe kernels (flash is fp16/bf16-only)
  DM_HF_DECODE="${DM_HF_DECODE:-helm,greedy}"                # HELM's sample-at-1e-7 vs plain argmax
  DM_HF_AST="${DM_HF_AST:-both}"
  DM_HF_AGP="${DM_HF_AGP:-both}"
else                                                          # quick
  DM_HF_DTYPES="${DM_HF_DTYPES:-float32}"
  DM_HF_ATTN="${DM_HF_ATTN:-sdpa}"
  DM_HF_DECODE="${DM_HF_DECODE:-helm}"
  DM_HF_AST="${DM_HF_AST:-true}"
  DM_HF_AGP="${DM_HF_AGP:-both}"
fi
# device_map is per-MODEL (single is infeasible for 32B fp32), so leave it to each
# runbook's own default unless the user pins it globally here.
DM_HF_DEVMAPS="${DM_HF_DEVMAPS:-}"
# vLLM is already maximal at its defaults: hf-match sweeps attn x add_generation_prompt,
# the grid default sweeps all dtypes x add_special_tokens. Keep them full unless narrowed.
DM_PROFILE="${DM_PROFILE:-hf-match}"
DM_DTYPES="${DM_DTYPES:-}"      # empty = grid default (auto,float16,bfloat16,float32)
DM_ATTN="${DM_ATTN:-}"          # empty = hf-match's 4 backends

# slug | per-model runbook | GPU set (HF device_map visible set / vLLM fp32 TP count)
MODELS=(
  "olmoe-1b-7b-0125-instruct|run_deployment_match.sh|${GPU_OLMOE:-0}"
  "olmo-2-1124-7b-instruct|run_deployment_match_olmo2_7b.sh|${GPU_7B:-0}"
  "olmo-2-1124-13b-instruct|run_deployment_match_olmo2_13b.sh|${GPU_13B:-0}"
  "olmo-2-0325-32b-instruct|run_deployment_match_olmo2_32b.sh|${GPU_32B:-0,1}"
)

gpu_count() {  # "0,1" -> 2 ; "" -> 1
  local s="$1"; [[ -z "$s" ]] && { echo 1; return; }; awk -F, '{print NF}' <<<"$s"
}

declare -a SUMMARY

run() {  # $1=slug $2=engine $3=script $4=gpus $5=out_dir
  local slug="$1" engine="$2" script="$3" gpus="$4" out="$5"
  local log="$LOG_DIR/${slug}-${engine}.log"
  echo ">>> [$(date +%H:%M:%S)] START $slug / $engine  -> $out"
  echo "        log: $log"
  local t0=$SECONDS rc=0
  (
    export DM_OUT="$out" DM_N="$DM_N" DM_DRY="$DM_DRY" DM_ALLOWED_GPUS="$gpus"
    if [[ "$engine" == "hf" ]]; then
      export DM_HF_FP32=1                       # HF transformers.generate() reproduction
      # Full forward-pass + request sweep (device_map left per-model unless pinned).
      export DM_HF_DTYPES DM_HF_ATTN DM_HF_DECODE DM_HF_AST DM_HF_AGP
      [[ -n "$DM_HF_DEVMAPS" ]] && export DM_HF_DEVMAPS
    else
      unset DM_HF_FP32                          # vLLM sweep
      export DM_FP32_TP="$(gpu_count "$gpus")"  # let fp32 vLLM cells fit on the dense models
      export DM_PROFILE                         # hf-match: sweep attn x add_generation_prompt
      [[ -n "$DM_DTYPES" ]] && export DM_DTYPES
      [[ -n "$DM_ATTN" ]] && export DM_ATTN
    fi
    "$HERE/$script"
  ) >"$log" 2>&1 || rc=$?
  local dur=$(( SECONDS - t0 ))
  local winner="-" comp="-" verdict="-"
  local best="$out/results/best_deployment.yaml"
  if [[ -f "$best" ]]; then
    winner="$(grep -m1 '^winner_cell:' "$best" | sed 's/^winner_cell: *//')"
    comp="$(grep -m1 '^composite:'   "$best" | sed 's/^composite: *//')"
    verdict="$(grep -m1 '^verdict:'  "$best" | sed 's/^verdict: *//')"
  fi
  local status; [[ $rc -eq 0 ]] && status="OK" || status="FAIL($rc)"
  echo "<<< [$(date +%H:%M:%S)] DONE  $slug / $engine  $status  ${dur}s  verdict=$verdict composite=$comp"
  echo
  SUMMARY+=("$slug|$engine|$status|${dur}s|$verdict|$comp|$winner")
}

echo "================================================================"
echo "OLMo instruct overnight — engines=[$ENGINES]  n=$DM_N  dry=$DM_DRY  sweep=$SWEEP"
echo "  store : $STORE_ROOT/deployment-match/"
echo "  logs  : $LOG_DIR"
[[ " $ENGINES " == *" hf "* ]] && echo "  hf    : dtype=$DM_HF_DTYPES attn=$DM_HF_ATTN devmap=${DM_HF_DEVMAPS:-<per-model>} decode=$DM_HF_DECODE ast=$DM_HF_AST agp=$DM_HF_AGP"
[[ " $ENGINES " == *" vllm "* ]] && echo "  vllm  : profile=${DM_PROFILE:-<default-grid>} dtype=${DM_DTYPES:-<all>} attn=${DM_ATTN:-<hf-match 4>}"
[[ -n "$SKIP" ]] && echo "  skip  : $SKIP"
echo "  start : $(date '+%Y-%m-%d %H:%M:%S')"
echo "================================================================"
echo

for row in "${MODELS[@]}"; do
  IFS='|' read -r slug script gpus <<<"$row"
  if [[ " $SKIP " == *" $slug "* ]]; then echo "-- skip $slug"; continue; fi
  for engine in $ENGINES; do
    run "$slug" "$engine" "$script" "$gpus" \
        "$STORE_ROOT/deployment-match/${slug}--ifeval-${engine}"
  done
done

echo "================================================================"
echo "SUMMARY  ($(date '+%Y-%m-%d %H:%M:%S'))"
echo "================================================================"
printf "%-28s %-5s %-8s %-7s %-9s %-9s %s\n" MODEL ENGINE STATUS DUR VERDICT COMPOSITE WINNER
for r in "${SUMMARY[@]}"; do
  IFS='|' read -r slug engine status dur verdict comp winner <<<"$r"
  printf "%-28s %-5s %-8s %-7s %-9s %-9s %s\n" \
    "$slug" "$engine" "$status" "$dur" "$verdict" "$comp" "$winner"
done
echo
echo "Per-run logs: $LOG_DIR/<model>-<engine>.log"
echo "Result dirs : $STORE_ROOT/deployment-match/<model>--ifeval-{vllm,hf}/results/"

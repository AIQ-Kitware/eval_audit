#!/usr/bin/env bash
# The unattended OVERNIGHT rejudge run: benchmarks x judge arms x replicates,
# then the per-benchmark analysis. One judge is leased at a time (the model
# stays up across its whole arm), so each judge's weight download/load happens
# once per night, not once per rejudge.
#
#   ./50_overnight_run.sh
#
# JUDGES. `OJ_JUDGES` (default `$OJ_JUDGE_LADDER`) is the judge-size sweep:
# the Qwen3.5 post-trained ladder 0.8B -> 2B -> 4B -> 9B -> 27B plus the
# Qwen3.6-35B-A3B MoE. Ordered small -> large so the cheap arms land early and
# a night that runs short still yields the low end of the curve.
#
# REPLICATES. Resolved per (benchmark, judge): a per-pair override wins, else
# the per-benchmark default, else 3 replicates. An EMPTY value skips that pair.
#   OJ_REPS_<BENCHMARK>              e.g. OJ_REPS_WILDBENCH="0"
#   OJ_REPS_<BENCHMARK>_<JUDGE>      e.g. OJ_REPS_WILDBENCH_QWEN3_5_27B="0"
#   OJ_PARALLELISM                   (default 8) concurrent judge requests
#
# Idempotent: a completed (snapshot, judge_spec, replicate) attempt is served
# from its DONE gate, so a re-run resumes instead of recomputing. Individual
# rejudge failures are logged and skipped (the night is never aborted by one
# bad pair); the tail summary lists every attempt's status.
set -uo pipefail   # NOT -e: one failed rejudge must not abort the whole night
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

P="${OJ_PARALLELISM:-8}"
OJ_JUDGES="${OJ_JUDGES:-$OJ_JUDGE_LADDER}"

# Replicates for one (benchmark, judge): per-pair override, else per-benchmark
# default, else "0 1 2". Uses `-` (not `:-`) so an explicitly empty setting
# means "skip this pair" rather than falling back to the default.
oj_reps_for() {
  local bench="$1" judge="$2" pair_var bench_var
  pair_var="OJ_REPS_$(printf '%s_%s' "$bench" "$judge" | tr 'a-z.-' 'A-Z__')"
  bench_var="OJ_REPS_$(printf '%s' "$bench" | tr 'a-z.-' 'A-Z__')"
  if [[ -n "${!pair_var+set}" ]]; then printf '%s' "${!pair_var}"
  elif [[ -n "${!bench_var+set}" ]]; then printf '%s' "${!bench_var}"
  else printf '0 1 2'; fi
}

log_dir="$OJ_ANALYSIS_ROOT/overnight-logs"
mkdir -p "$log_dir"
run_log="$log_dir/overnight.log"
say() { echo "[$(date '+%F %T %z')] $*" | tee -a "$run_log" >&2; }

declare -a SUMMARY=()

# Rejudge every (benchmark, replicate) for one already-leased judge.
run_arm() {
  local judge_key="$1" judge_json="$2" benchmark reps snapshot r rc
  for benchmark in $OJ_BENCHMARKS; do
    reps="$(oj_reps_for "$benchmark" "$judge_key")"
    [[ -n "$reps" ]] || { say "skip $benchmark:$judge_key (no replicates configured)"; continue; }
    snapshot="$(oj_snapshot_for_benchmark "$benchmark")" || {
      say "FAIL $benchmark:$judge_key — no snapshot (run 05+08)"
      SUMMARY+=("$benchmark:$judge_key:* NO_SNAPSHOT"); continue; }
    for r in $reps; do
      say "rejudge $benchmark:$judge_key replicate=$r (parallelism=$P) …"
      eval-audit-rejudge-helm \
        --snapshot "$snapshot" \
        --judge-json "$judge_json" \
        --replicate "$r" \
        --experiment-name "$OJ_EXPERIMENT" \
        --out-root "$OJ_RESULTS_ROOT" \
        --cache-root "$OJ_CACHE_ROOT" \
        --sidecar-config "$OJ_SIDECAR_DIR" \
        --parallelism "$P" >>"$run_log" 2>&1
      rc=$?
      if [[ $rc -eq 0 ]]; then SUMMARY+=("$benchmark:$judge_key:r$r OK")
      else say "FAIL $benchmark:$judge_key r$r (rc=$rc) — see $run_log"
           SUMMARY+=("$benchmark:$judge_key:r$r FAIL(rc=$rc)"); fi
    done
  done
}

# Lease one judge for the duration of its whole arm, then release.
run_judge() {
  local judge_key="$1" spec endpoint judge_json lease_env has_work="" b
  spec="$(oj_judge_spec "$judge_key")" || { say "FAIL: unknown judge $judge_key"; return 1; }
  read -r endpoint judge_json <<<"$spec"

  for b in $OJ_BENCHMARKS; do [[ -n "$(oj_reps_for "$b" "$judge_key")" ]] && has_work=1; done
  [[ -n "$has_work" ]] || { say "skip judge $judge_key (no work configured)"; return 0; }
  [[ -f "$judge_json" ]] || { say "FAIL: missing JudgeSpec $judge_json"; SUMMARY+=("judge:$judge_key NO_SPEC"); return 1; }

  say "=== judge arm: $judge_key (endpoint=$endpoint) ==="
  infer-stack gc --yes >>"$run_log" 2>&1 || true
  lease_env="$(mktemp)"
  # shellcheck disable=SC2064
  trap "if [[ -s '$lease_env' ]]; then infer-stack release --env-file '$lease_env' --evict --yes >>'$run_log' 2>&1 || true; fi; rm -f '$lease_env'" RETURN

  say "acquiring $endpoint (--no-wait; holds lease while weights load)…"
  if ! infer-stack acquire "$endpoint" --no-wait --yes --env-file "$lease_env" >>"$run_log" 2>&1; then
    say "FAIL: acquire $endpoint"; SUMMARY+=("judge:$judge_key ACQUIRE_FAIL"); return 1; fi
  local master_key; master_key="$(infer-stack env LITELLM_MASTER_KEY)"
  say "waiting up to ${OJ_LEASE_WAIT_TIMEOUT}s for $endpoint READY…"
  if ! infer-stack wait "$endpoint" --timeout "$OJ_LEASE_WAIT_TIMEOUT" >>"$run_log" 2>&1; then
    say "FAIL: $endpoint not READY in ${OJ_LEASE_WAIT_TIMEOUT}s"; SUMMARY+=("judge:$judge_key NOT_READY"); return 1; fi

  say "exporting sidecar bundle for $judge_key…"
  LITELLM_MASTER_KEY="$master_key" eval-audit-export-judge-bundle \
    --judge-json "$judge_json" \
    --config-dir "$INFER_STACK_CONFIG_DIR" \
    --base-url "${LITELLM_BASE_URL}/v1" \
    --infer-stack-revision "$(git -C "$ROOT/submodules/infer_stack" rev-parse --short HEAD 2>/dev/null || echo unknown)" \
    --out "$OJ_SIDECAR_DIR" >>"$run_log" 2>&1 \
    || { say "FAIL: bundle export $judge_key"; SUMMARY+=("judge:$judge_key EXPORT_FAIL"); return 1; }

  run_arm "$judge_key" "$judge_json"
  say "=== judge arm done: $judge_key (releasing lease) ==="
}

say "OVERNIGHT run start. parallelism=$P benchmarks='$OJ_BENCHMARKS'"
say "judge ladder: $OJ_JUDGES"
for jk in $OJ_JUDGES; do
  for b in $OJ_BENCHMARKS; do say "  scope $b:$jk -> replicates [$(oj_reps_for "$b" "$jk")]"; done
done

for judge_key in $OJ_JUDGES; do
  run_judge "$judge_key"
done

say "generating per-benchmark analysis reports…"
mkdir -p "$OJ_ANALYSIS_ROOT"
for benchmark in $OJ_BENCHMARKS; do
  snapshot="$(oj_snapshot_for_benchmark "$benchmark")" || continue
  hash="$(basename "$snapshot")"
  say "analyze $benchmark …"
  eval-audit-analyze-judges \
    --snapshot "$snapshot" \
    --results-root "$OJ_RESULTS_ROOT" \
    --output "$OJ_ANALYSIS_ROOT/$benchmark-$hash.json" \
    --text "$OJ_ANALYSIS_ROOT/$benchmark-$hash.txt" >>"$run_log" 2>&1 \
    && say "  report: $OJ_ANALYSIS_ROOT/$benchmark-$hash.txt" \
    || say "  WARN: analysis failed for $benchmark (see $run_log)"
done

say "OVERNIGHT run complete. Attempt summary:"
for s in "${SUMMARY[@]}"; do say "  $s"; done

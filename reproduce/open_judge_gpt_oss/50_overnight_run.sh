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
# CONCURRENCY. The driver is SERIAL over judges and every ladder arm is TP1, so
# ONE worker holds one GPU and leaves the rest idle. Prefer the kwdagger path
# (`eval-audit-schedule-rejudge`) for fan-out. If you run several workers by
# hand, each MUST be pinned to a different judge AND their judge sets must be
# DISJOINT:
#   OJ_JUDGES=qwen3_5_2b OJ_SKIP_ANALYSIS=1 ./50_overnight_run.sh
#
# Two workers that reach the SAME judge destroy each other. Learned the hard way
# on 2026-07-19, when a full-ladder worker walked into a judge that a dedicated
# worker was already running:
#   * SQLite cache — the request cache path is keyed per (response set, judge
#     spec, replicate), so two workers on the SAME cell target the SAME file and
#     deadlock with `sqlite3.OperationalError: database is locked`. That key
#     makes DISTINCT cells disjoint; it does NOT make DUPLICATE cells safe.
#   * sidecar bundle — each worker now exports into a PRIVATE
#     $OJ_SIDECAR_DIR/<judge> (see run_judge). Sharing one directory let the
#     last writer's model_deployments.yaml win, silently deleting the other
#     judge's deployment; HELM then fell back to the `litellm/` name prefix and
#     every request died with OptionalDependencyNotInstalled.
# Exit status alone caught NEITHER failure — a rejudge whose every request
# errored still exits 0 — so attempts are health-checked (oj_attempt_health)
# rather than trusted.
#
# Partition by JUDGE, not by benchmark: two workers leasing the same endpoint
# share one refcounted deployment on one GPU (more request concurrency, no extra
# GPU). Distinct judges get distinct deployments, which VRAM-aware placement
# lands on distinct free GPUs. Sharing the RESULTS root is safe (attempts are
# content-addressed, atomically written, DONE-gated), and `infer-stack gc`
# sweeps only TTL-EXPIRED leases, so it never reclaims a peer's live lease.
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

# Per-run log so CONCURRENT workers (one per judge, to use >1 GPU) do not
# interleave into one file. Label defaults to the judge list.
OJ_RUN_LABEL="${OJ_RUN_LABEL:-$(printf '%s' "$OJ_JUDGES" | tr ' ' '+')}"
log_dir="$OJ_ANALYSIS_ROOT/overnight-logs"
mkdir -p "$log_dir"
run_log="$log_dir/overnight-${OJ_RUN_LABEL}.log"
say() { echo "[$(date '+%F %T %z')] $*" | tee -a "$run_log" >&2; }

declare -a SUMMARY=()

# Rejudge every (benchmark, replicate) for one already-leased judge.
run_arm() {
  local judge_key="$1" judge_json="$2" sidecar_dir="$3" benchmark reps snapshot r rc
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
        --sidecar-config "$sidecar_dir" \
        --parallelism "$P" >>"$run_log" 2>&1
      rc=$?
      if [[ $rc -ne 0 ]]; then
        say "FAIL $benchmark:$judge_key r$r (rc=$rc) — see $run_log"
        SUMMARY+=("$benchmark:$judge_key:r$r FAIL(rc=$rc)")
        continue
      fi
      # Exit code 0 is NOT enough. A judgment whose request failed is recorded
      # as structured data (by design — one bad response must not abort a
      # batch), so an attempt where EVERY request failed still exits 0 and used
      # to be logged OK. That is how 14 dead attempts were reported as
      # successes on 2026-07-19. Gate on the artifact's actual health.
      local health
      health="$(oj_attempt_health "$benchmark" "$judge_key" "$r")"
      case "$health" in
        ok*)   SUMMARY+=("$benchmark:$judge_key:r$r OK ($health)") ;;
        *)     say "UNHEALTHY $benchmark:$judge_key r$r — $health"
               SUMMARY+=("$benchmark:$judge_key:r$r UNHEALTHY($health)") ;;
      esac
    done
  done
}

# Classify the newest artifact for (benchmark, judge, replicate) by parse
# status. Prints "ok <n> judged, <pct>% parsed" or a DEAD/DEGRADED reason.
oj_attempt_health() {
  "$PYTHON_BIN" - "$OJ_RESULTS_ROOT" "$1" "$2" "$3" <<'PY' 2>/dev/null || echo "unknown (health check failed)"
import json, os, sys
from collections import Counter
root, benchmark, judge_id, replicate = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
best = None
for name in os.listdir(root):
    m = os.path.join(root, name, "rejudge_manifest.json")
    if not os.path.exists(m):
        continue
    man = json.load(open(m))
    if (man.get("benchmark"), man.get("judge_id"), man.get("replicate")) != (benchmark, judge_id, replicate):
        continue
    if man.get("max_instances"):        # never judge health off a smoke subset
        continue
    mtime = os.path.getmtime(m)
    if best is None or mtime > best[0]:
        best = (mtime, os.path.join(root, name), man)
if best is None:
    print("DEAD (no artifact found)"); raise SystemExit
_, dpath, man = best
counts = Counter(json.loads(l)["annotation"]["parse_status"]
                 for l in open(os.path.join(dpath, "judgments.jsonl")))
n = man["num_judged"] or 1
err = counts.get("request_error", 0)
ok = counts.get("ok", 0)
if err > n * 0.5:
    print(f"DEAD ({err}/{n} request_error — judge unreachable/misregistered?)")
elif ok < n * 0.5:
    print(f"DEGRADED (only {ok}/{n} parsed; {dict(counts)})")
else:
    print(f"ok {n} judged, {100.0*ok/n:.1f}% parsed")
PY
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

  # PER-JUDGE sidecar dir, never the shared root. export_judge_bundle writes a
  # model_deployments.yaml containing ONLY the judges it was given, so two
  # workers exporting into one directory clobber each other's registration:
  # the loser's deployment vanishes, HELM falls back to the 'litellm/' name
  # prefix, tries to build its LiteLLM client, and every judge request dies
  # with OptionalDependencyNotInstalled. That cost 14 of one arm's 15 attempts
  # on 2026-07-19. A private directory per judge removes the shared state.
  local sidecar_dir="$OJ_SIDECAR_DIR/$judge_key"
  mkdir -p "$sidecar_dir"
  say "exporting sidecar bundle for $judge_key -> $sidecar_dir …"
  LITELLM_MASTER_KEY="$master_key" eval-audit-export-judge-bundle \
    --judge-json "$judge_json" \
    --config-dir "$INFER_STACK_CONFIG_DIR" \
    --base-url "${LITELLM_BASE_URL}/v1" \
    --infer-stack-revision "$(git -C "$ROOT/submodules/infer_stack" rev-parse --short HEAD 2>/dev/null || echo unknown)" \
    --out "$sidecar_dir" >>"$run_log" 2>&1 \
    || { say "FAIL: bundle export $judge_key"; SUMMARY+=("judge:$judge_key EXPORT_FAIL"); return 1; }

  run_arm "$judge_key" "$judge_json" "$sidecar_dir"
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

if [[ -n "${OJ_SKIP_ANALYSIS:-}" ]]; then
  # Concurrent workers should skip this: every worker would regenerate the
  # SAME per-benchmark report (the analyzer scans the whole results root), so
  # they would race on one output path and the early finishers would publish a
  # partial picture. Run 30_analyze_judges.sh once after the last worker exits.
  say "OJ_SKIP_ANALYSIS set — skipping report generation (run 30 when all workers finish)."
  say "Attempt summary:"; for s in "${SUMMARY[@]}"; do say "  $s"; done
  exit 0
fi

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

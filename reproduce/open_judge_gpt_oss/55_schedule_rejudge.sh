#!/usr/bin/env bash
# Fan the rejudge matrix out via kwdagger — the scheduled alternative to the
# serial 50_overnight_run.sh. kwdagger owns the tmux panes, worker assignment
# and retries; you do not hand-partition judges across sessions (which is what
# caused the 2026-07-19 sidecar/SQLite collisions).
#
#   ./55_schedule_rejudge.sh [BENCHMARK...]            # PREVIEW (default)
#   ./55_schedule_rejudge.sh omni_math --run           # submit
#   OJ_JUDGES="qwen3_5_2b qwen3_5_9b" ./55_schedule_rejudge.sh omni_math --run
#
# With no BENCHMARK arguments it schedules everything in $OJ_BENCHMARKS that
# actually has a snapshot. Judges default to the full $OJ_JUDGE_LADDER.
#
# Preview prints the job count per judge/benchmark and the exact kwdagger argv
# WITHOUT submitting, so the fan-out size is reviewed rather than discovered.
#
# Smoke first. This path had never been executed as of 2026-07-20; validate it
# on a subset before trusting a full matrix to it:
#   OJ_SMOKE_INSTANCES=20 ./55_schedule_rejudge.sh omni_math --smoke --run
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

RUN=0
SMOKE=""
declare -a WANT=()
for arg in "$@"; do
  case "$arg" in
    --run)   RUN=1 ;;
    --smoke) SMOKE=1 ;;
    -*)      echo "FAIL: unknown flag $arg" >&2; exit 1 ;;
    *)       WANT+=("$arg") ;;
  esac
done
[[ ${#WANT[@]} -gt 0 ]] || read -r -a WANT <<<"$OJ_BENCHMARKS"

JUDGES="${OJ_JUDGES:-$OJ_JUDGE_LADDER}"
KW_ROOT="${OJ_KWDAGGER_ROOT:-$OJ_ROOT/kwdagger}"

declare -a ARGS=()
for benchmark in "${WANT[@]}"; do
  if snapshot="$(oj_snapshot_for_benchmark "$benchmark")"; then
    ARGS+=(--snapshot "$benchmark=$snapshot")
    echo "  snapshot $benchmark -> $snapshot" >&2
  else
    echo "  SKIP $benchmark — no snapshot (run 05 + 08 + 09 first)" >&2
  fi
done
[[ ${#ARGS[@]} -gt 0 ]] || { echo "FAIL: no snapshots for [${WANT[*]}]." >&2; exit 1; }

for judge_key in $JUDGES; do
  spec="$(oj_judge_spec "$judge_key")" || { echo "FAIL: unknown judge $judge_key" >&2; exit 1; }
  read -r _endpoint judge_json <<<"$spec"
  [[ -f "$judge_json" ]] || { echo "FAIL: missing JudgeSpec $judge_json" >&2; exit 1; }
  ARGS+=(--judge-json "$judge_json")
done

if [[ -n "$SMOKE" ]]; then
  ARGS+=(--max-instances "${OJ_SMOKE_INSTANCES:-20}" --replicates 0)
  echo "  SMOKE: first ${OJ_SMOKE_INSTANCES:-20} instances, replicate 0 only" >&2
fi

exec eval-audit-schedule-rejudge \
  "${ARGS[@]}" \
  --out-root "$OJ_RESULTS_ROOT" \
  --cache-root "$OJ_CACHE_ROOT" \
  --sidecar-config "$OJ_SIDECAR_DIR" \
  --experiment-name "$OJ_EXPERIMENT" \
  --parallelism "${OJ_PARALLELISM:-8}" \
  --root-dpath "$KW_ROOT" \
  --queue-name "${OJ_QUEUE_NAME:-open-judge-rejudge}" \
  --tmux-workers "${OJ_TMUX_WORKERS:-4}" \
  --devices "${OJ_DEVICES:-0,1,2,3}" \
  --run "$RUN"

#!/usr/bin/env bash
# 08_check_discovery.sh — from-spec discovery dry-check (migration plan Change 4).
#
# CPU-only preflight: for every OLMo preset, resolve each run-entry against the
# public corpus using the SAME token-subset matcher the from-spec replay uses
# (find_best_precomputed_run / run_dir_matches_requested in aiq-magnet), and
# report RESOLVED / NO_MATCH / AMBIGUOUS per entry. No GPU, no serving, no HELM
# run — pure filesystem discovery.
#
# Run this BEFORE any GPU work to confirm every run-entry points at exactly one
# official run dir to replay from. A NO_MATCH means the entry's tokens are not a
# subset of any official dir name (it would fail discovery at replay time); an
# AMBIGUOUS means it matches several (replay picks the best deterministically).
# Use it to baseline the current run-entries and to validate reduced discovery
# keys after editing the presets (Change 1).
#
# Env:
#   PRECOMPUTED_ROOT  public corpus root (default /data/crfm-helm-public — the
#                     parent that spans mmlu/lite/capabilities/safety, so one root
#                     covers every OLMo benchmark; the per-root scan is a few
#                     seconds and matching is in-memory).
#   MODE              smoke | full   (default full — the real entry set)
#   STRICT            1 to also fail on AMBIGUOUS (default 0)
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

PRECOMPUTED_ROOT="${PRECOMPUTED_ROOT:-/data/crfm-helm-public}"
MODE="${MODE:-full}"
strict_flag=(); [[ "${STRICT:-0}" == 1 ]] && strict_flag=(--strict)

if [[ ! -d "$PRECOMPUTED_ROOT" ]]; then
  echo "FAIL: PRECOMPUTED_ROOT not found: $PRECOMPUTED_ROOT" >&2
  echo "  (set PRECOMPUTED_ROOT to the public HELM corpus root)" >&2
  exit 1
fi

echo "=================================================================="
echo "== from-spec discovery dry-check (Change 4)"
echo "==   root=$PRECOMPUTED_ROOT  mode=$MODE"
echo "=================================================================="

failed_presets=()
for target in "${OLMO_TARGETS[@]}"; do
  preset="$(olmo_preset "$target")"
  echo
  echo "------------------------------------------------------------------"
  echo "-- $preset"
  echo "------------------------------------------------------------------"
  if PYTHONPATH="$ROOT" "$PYTHON_BIN" -m eval_audit.cli.check_precomputed_discovery \
       --preset "$preset" --precomputed-root "$PRECOMPUTED_ROOT" --mode "$MODE" \
       "${strict_flag[@]}"; then
    :
  else
    failed_presets+=("$preset")
  fi
done

echo
echo "=================================================================="
if [[ ${#failed_presets[@]} -eq 0 ]]; then
  echo "OK: every preset's run-entries resolve to an official run dir."
else
  echo "FAIL: ${#failed_presets[@]} preset(s) have unresolved run-entries:"
  printf '  - %s\n' "${failed_presets[@]}"
  echo "Reduce those entries to discovery keys (drop hand-authored recipe tokens"
  echo "absent from the official dir name) and re-run. See"
  echo "docs/planning/olmo-from-run-spec-migration-plan.md Change 1."
  exit 1
fi

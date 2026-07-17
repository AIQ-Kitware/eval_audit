#!/usr/bin/env bash
# Verify HELM run artifacts recorded the expected model + nlstrip deployment.
#
# Accepts either a single HELM run dir (contains run_spec.json) or an
# experiment/suite root (e.g. $AUDIT_RESULTS_ROOT/audit-qwen35-9b-base-vllm-full),
# which is swept for every run_spec.json-bearing dir — the post-overnight shape.
# With no argument, sweeps the full experiment root.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

if [[ $# -gt 1 ]]; then
  echo "usage: $0 [/path/to/helm-run-dir | /path/to/experiment-root]" >&2
  exit 2
fi
target="${1:-$RESULTS_ROOT/$QWEN35_EXPERIMENT_FULL}"

if [[ ! -d "$target" ]]; then
  echo "ERROR: no such directory: $target" >&2
  exit 2
fi

if [[ -f "$target/run_spec.json" ]]; then
  exec "$PYTHON_BIN" configs/local_models/qwen35_9b_vllm/verify_run_artifacts.py "$target"
fi

# Suite root: sweep every run dir; report a pass/fail tally and fail loud if
# any run verifies dirty (exit 1) or the sweep finds nothing (exit 2).
n_pass=0
n_fail=0
failed_dirs=()
while IFS= read -r spec; do
  run_dir="$(dirname "$spec")"
  if "$PYTHON_BIN" configs/local_models/qwen35_9b_vllm/verify_run_artifacts.py "$run_dir" >/dev/null; then
    n_pass=$((n_pass + 1))
  else
    n_fail=$((n_fail + 1))
    failed_dirs+=("$run_dir")
  fi
done < <(find "$target" -name run_spec.json | sort)

echo "verified: $n_pass passed, $n_fail failed (under $target)"
if [[ $n_fail -gt 0 ]]; then
  printf 'FAILED: %s\n' "${failed_dirs[@]}" >&2
  exit 1
fi
if [[ $n_pass -eq 0 ]]; then
  echo "ERROR: no run_spec.json found under $target — nothing to verify." >&2
  exit 2
fi

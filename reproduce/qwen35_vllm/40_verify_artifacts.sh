#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

if [[ $# -ne 1 ]]; then
  echo "usage: $0 /path/to/helm-run-dir" >&2
  exit 2
fi

"$PYTHON_BIN" configs/local_models/qwen35_9b_vllm/verify_run_artifacts.py "$1"

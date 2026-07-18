#!/usr/bin/env bash
# Milestone B: the first LIVE judge smoke — XSTest, Qwen3.5-27B, one replicate.
# Back-compat wrapper: the smoke is now parameterized (benchmark x judge) in
# 20_smoke.sh; this is the xstest+qwen35 invocation it started as. Extra args
# pass through (e.g. OJ_SMOKE_INSTANCES=... ./20_smoke_xstest_qwen35.sh).
set -euo pipefail
exec "$(dirname "${BASH_SOURCE[0]}")/20_smoke.sh" xstest qwen35 "$@"

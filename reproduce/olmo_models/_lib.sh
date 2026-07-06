#!/usr/bin/env bash
# Shared definitions for the OLMo smoke + full grid and grouping runbook.
# Source this from the numbered scripts: `source "$(dirname "$0")/_lib.sh"`.
#
# Thin shim: the real definitions live in reproduce/_lib.sh (the single source of
# truth merged across the three runbooks). `olmo_setup` runs the
# serving / leasing / container / HuggingFace-auth / infer-stack-config setup that
# this file's top-level body used to perform at source time — invoking it here
# preserves that source-time behavior exactly.
source "$(dirname "${BASH_SOURCE[0]}")/../_lib.sh"
olmo_setup

#!/usr/bin/env bash
# HuggingFace-auth preflight — IDENTICAL to ../olmo_models/06_check_hf_auth.sh
# (gpqa is a gated dataset on the instruct models here too, and appears in the
# combined preset's smoke + full manifests). Target-independent, so delegate to
# the single-model runbook's implementation to avoid drift.
set -euo pipefail
_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$_here/../olmo_models/06_check_hf_auth.sh"

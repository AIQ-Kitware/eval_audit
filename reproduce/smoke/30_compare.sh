#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STORE_ROOT="${AUDIT_STORE_ROOT:-/data/crfm-helm-audit-store}"
cd "$ROOT"
# DEPRECATED (2026-07-12): eval-audit-compare-batch is scheduled for deletion
# after one deprecation cycle; migrate to the planner-driven
# eval-audit-analyze-experiment path. Kept here for this pre-existing runbook.
eval-audit-compare-batch --manifest "$STORE_ROOT/configs/manifests/smoke_manifest.generated.yaml"

#!/usr/bin/env bash
# Shared function definitions for the KubeAI small-models overnight runbook.
#
# Thin shim: the real definitions (small_models_root, resolve_kubeai_namespace,
# print_kubeai_diagnostics, patch_model_for_tonight, wait_for_model_objects,
# wait_for_model_pods_ready) live in reproduce/_lib.sh, the single source of truth
# merged across the three runbooks. Sourcing it is side-effect-free — it only
# defines functions — matching this file's original behavior (no source-time
# environment setup for the kubeai path).
source "$(dirname "${BASH_SOURCE[0]}")/../_lib.sh"

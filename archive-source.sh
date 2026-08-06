#!/usr/bin/env bash
# Produce eval_audit-source-<stamp>-<hash>.tar.gz at the repo root
# (the pattern is gitignored). Requires the git-well tool.
set -euo pipefail

if ! command -v git-well >/dev/null 2>&1; then
    echo "error: required command not found: git-well" >&2
    exit 1
fi

git-well archive-source --submodule_depth '
submodules/every_eval_ever: 1
submodules/helm: 1
'

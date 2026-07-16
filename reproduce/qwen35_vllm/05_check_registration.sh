#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# CPU-only preflight: the qwen/qwen3.5-9b-base registration lives in the
# VENDORED helm (submodules/helm). This asserts the helm the current venv
# imports actually sees it — a pip-installed crfm-helm would not, and the run
# would fail late at tokenizer resolution instead of here.
python - <<'EOF'
import sys

import helm
print(f"helm from: {helm.__file__}")

from helm.benchmark.config_registry import register_builtin_configs_from_helm_package
from helm.benchmark.model_metadata_registry import get_model_metadata
from helm.benchmark.tokenizer_config_registry import get_tokenizer_config

register_builtin_configs_from_helm_package()

name = "qwen/qwen3.5-9b-base"
try:
    metadata = get_model_metadata(name)
except ValueError:
    print(
        f"FAIL: {name} not registered in this helm install.\n"
        "Install helm from submodules/helm (pip install -e submodules/helm) "
        "so the -base registration is visible.",
        file=sys.stderr,
    )
    raise SystemExit(1)

tokenizer_config = get_tokenizer_config(name)
if tokenizer_config is None:
    print(f"FAIL: tokenizer config for {name} not registered.", file=sys.stderr)
    raise SystemExit(1)

print(f"OK: {name} metadata (tags={metadata.tags}) + tokenizer registered.")
EOF

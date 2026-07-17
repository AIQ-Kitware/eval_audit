#!/usr/bin/env bash
# Preflight: every serving endpoint the preset's profiles reference exists in
# the active infer-stack catalog (shipped by this runbook via
# INFER_STACK_CONFIG_DIR, set in _lib.sh), and each declares its
# placement.min_vram_gib — the whole point of this runbook is unpinned
# VRAM-aware placement, so an undeclared endpoint is a config regression.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

# The catalog infer-stack will actually read (config_root()/catalog.yaml,
# where config_root honors INFER_STACK_CONFIG_DIR). Print it so a shadowing
# env var is obvious, not silent.
resolved_catalog="$INFER_STACK_CONFIG_DIR/catalog.yaml"
shipped_catalog="$ROOT/reproduce/qwen35_small_vllm/config/infer_stack/catalog.yaml"
echo "inspecting catalog: $resolved_catalog"

available="$(infer-stack catalog endpoint list 2>/dev/null || true)"
if [[ -z "$available" ]]; then
  echo "WARN: 'infer-stack catalog endpoint list' produced no output; cannot validate." >&2
  echo "      Ensure infer-stack is installed and INFER_STACK_CONFIG_DIR points" >&2
  echo "      at your config. Continuing without validation." >&2
  exit 0
fi

fail=0
for endpoint in $QWEN35S_ENDPOINTS; do
  if ! grep -qw -- "$endpoint" <<<"$available"; then
    echo "FAIL: infer-stack endpoint not defined: $endpoint" >&2
    fail=1
  else
    echo "OK: serving endpoint defined: $endpoint"
  fi
done
if [[ $fail -ne 0 ]]; then
  if [[ "$resolved_catalog" != "$shipped_catalog" ]]; then
    echo >&2
    echo "NOTE: infer-stack is reading a DIFFERENT catalog than this runbook ships:" >&2
    echo "        reading:  $resolved_catalog" >&2
    echo "        shipped:  $shipped_catalog" >&2
    echo "      A leftover INFER_STACK_CONFIG_DIR (e.g. from the 9B runbook) shadows" >&2
    echo "      this one. Fix with:  unset INFER_STACK_CONFIG_DIR   then re-run." >&2
  else
    echo "Define the models + endpoints in your infer-stack catalog (see" >&2
    echo "config/infer_stack/catalog.yaml)." >&2
  fi
  exit 1
fi

# min_vram_gib declared for every endpoint (parse the shipped catalog file
# directly — the declaration is what makes unpinned placement safe).
"$PYTHON_BIN" - "$INFER_STACK_CONFIG_DIR/catalog.yaml" $QWEN35S_ENDPOINTS <<'EOF'
import sys

import yaml

catalog_fpath, *endpoints = sys.argv[1:]
doc = yaml.safe_load(open(catalog_fpath)) or {}
missing = []
for name in endpoints:
    ep = (doc.get("endpoints") or {}).get(name) or {}
    value = (ep.get("placement") or {}).get("min_vram_gib")
    if not value:
        missing.append(name)
    else:
        print(f"OK: {name} declares placement.min_vram_gib={value}")
if missing:
    print(
        f"FAIL: endpoints without placement.min_vram_gib: {missing} — this "
        f"runbook relies on VRAM-aware placement instead of GPU pinning; "
        f"declare a best guess (weights + margin) or run "
        f"`infer-stack measure <endpoint> --record`.",
        file=sys.stderr,
    )
    raise SystemExit(1)
EOF

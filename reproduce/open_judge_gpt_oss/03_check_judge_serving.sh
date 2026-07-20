#!/usr/bin/env bash
# Preflight: every judge endpoint exists in the active infer-stack catalog
# and declares placement.min_vram_gib (§2.9 — the no-pinning contract).
# (In v1 there is no dynamic routing to check; this replaces the original
# 03_check_dynamic_routing.sh — see open-judge-plan.md §21.)
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

resolved_catalog="$INFER_STACK_CONFIG_DIR/catalog.yaml"
shipped_catalog="$ROOT/reproduce/open_judge_gpt_oss/config/infer_stack/catalog.yaml"
echo "inspecting catalog: $resolved_catalog"

available="$(infer-stack catalog endpoint list 2>/dev/null || true)"
if [[ -z "$available" ]]; then
  echo "WARN: 'infer-stack catalog endpoint list' produced no output; cannot validate." >&2
  echo "      Ensure infer-stack is installed and INFER_STACK_CONFIG_DIR points here." >&2
  exit 0
fi

fail=0
for endpoint in $OJ_JUDGE_ENDPOINTS; do
  if grep -qw -- "$endpoint" <<<"$available"; then
    echo "OK: judge endpoint defined: $endpoint"
  else
    echo "FAIL: judge endpoint not defined: $endpoint" >&2
    fail=1
  fi
done
if [[ $fail -ne 0 && "$resolved_catalog" != "$shipped_catalog" ]]; then
  echo >&2
  echo "NOTE: infer-stack is reading a DIFFERENT catalog than this runbook ships:" >&2
  echo "        reading: $resolved_catalog" >&2
  echo "        shipped: $shipped_catalog" >&2
  echo "      A leftover INFER_STACK_CONFIG_DIR shadows this one." >&2
  echo "      Fix with:  unset INFER_STACK_CONFIG_DIR   then re-run." >&2
fi
[[ $fail -eq 0 ]] || exit 1

# min_vram_gib declared on each endpoint (parse the shipped catalog directly).
"$PYTHON_BIN" - "$resolved_catalog" $OJ_JUDGE_ENDPOINTS <<'EOF'
import sys, yaml
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
    print(f"FAIL: endpoints without placement.min_vram_gib: {missing}", file=sys.stderr)
    raise SystemExit(1)
EOF

#!/usr/bin/env bash
# The pre-v0.5 validation GATE — the classic-era ladder rungs that gate the grid.
#
# This is the dev-runbook home of what was reproduce/classic_era_replay/
# 05_ladder_gate.sh, trimmed to the rungs that PRECEDE the end-to-end grid:
#
#   Tier 0 (host only)   : pytest era suites + the static era-import checker
#   Tier 1 (docker, CPU) : rung 2 instrument fidelity + rung 5 HF-fetch audit,
#                          per era (each needs the era image + the corpus)
#
# Rung 1 (image build/smoke) moved to 06_check_era_images.sh; the end-to-end
# rungs 3-4 ARE the grid (10 -> 15 -> 20 -> 25 -> 30 -> 40). Run this after 06
# and before 10.
#
# Portable by design: defaults + env overrides only (no ladder.env). The gate
# SKIPs any rung whose prerequisites are missing and names what unlocks it.
# Exit non-zero iff an *attempted* rung FAILED (skips never fail the gate).
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

: "${LADDER_ERAS:=$(_era_keys_from_targets | tr '\n' ' ')}"
mkdir -p "$ERA_OUT"

declare -a REPORT=()   # rows: "STATUS|rung|detail"
add(){ REPORT+=("$1|$2|$3"); }

run_step(){
    # run_step <rung-label> <logfile> <cmd...>  -> records PASS/FAIL
    local label="$1" log="$2"; shift 2
    echo "=== ${label} ==="
    if "$@" >"$log" 2>&1; then
        add PASS "$label" "$(basename "$log")"
    else
        add FAIL "$label" "see $log"
    fi
}

# ------------------------------------------------------------------ tier 0 --
PYTEST=""
for py in "${ROOT}/.venv/bin/python" python3; do
    command -v "$py" >/dev/null 2>&1 && "$py" -c 'import pytest' >/dev/null 2>&1 && PYTEST="$py" && break
done
if [[ -n "$PYTEST" ]]; then
    run_step "tier0: era unit suites" "${ERA_OUT}/tier0-pytest.log" \
        "$PYTEST" -m pytest tests/test_eras.py tests/test_eras_hostside.py \
            tests/test_eras_pipeline.py tests/test_era_shim_imports.py \
            tests/test_era_shim_hostside.py \
            -q -o addopts=""
else
    add SKIP "tier0: era unit suites" "no python with pytest (repo .venv or system)"
fi

# ------------------------------------------------------------------ tier 1 --
HAVE_DOCKER=0
command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1 && HAVE_DOCKER=1

if [[ $HAVE_DOCKER -eq 0 ]]; then
    add SKIP "rung 2+5 (all eras)" "docker unavailable — run on a docker host"
elif [[ -z "${PRECOMPUTED_ROOT:-}" || ! -d "${PRECOMPUTED_ROOT:-/nonexistent}" ]]; then
    add SKIP "rung 2+5 (all eras)" "PRECOMPUTED_ROOT unset or missing — set it in the environment"
else
    for era in ${LADDER_ERAS}; do
        # Rung 2/5 need this era's image; skip (don't fail) when it's absent so
        # the gate stays green on a host that only built one era.
        if ! docker image inspect "$(era_image "$era")" >/dev/null 2>&1; then
            add SKIP "rung 2+5 ${era}" "era image absent — run 06 / ERA=${era} ./docker/build.sh"
            continue
        fi
        run_step "rung 2: fidelity ${era}" "${ERA_OUT}/rung2-${era}.log" \
            env ERA="$era" "${ERA_DIR}/_rung2_instrument_fidelity.sh"
        run_step "rung 5: hf-fetch ${era}" "${ERA_OUT}/rung5-${era}.log" \
            env ERA="$era" "${ERA_DIR}/_rung5_hf_fetch_audit.sh"
    done
fi

# ------------------------------------------------------------------ report --
echo
echo "==================== validation-ladder gate ===================="
fails=0
for row in "${REPORT[@]}"; do
    IFS='|' read -r status rung detail <<<"$row"
    printf '%-5s %-32s %s\n' "$status" "$rung" "$detail"
    [[ "$status" == FAIL ]] && ((fails++))
done
echo "================================================================"
if [[ $fails -gt 0 ]]; then
    echo "GATE: ${fails} rung(s) FAILED — logs under ${ERA_OUT}/"
    exit 1
fi
echo "GATE: all attempted rungs passed (SKIPs need the prerequisite noted above)."
echo "Next: ./10_run_smoke_grid.sh"

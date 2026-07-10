#!/usr/bin/env bash
# The validation-ladder GATE: run every rung the current machine can support,
# skip the rest, and print a per-rung PASS/FAIL/SKIP table with the reason.
#
# Portable by design: copy the repo to any machine, write
# reproduce/classic_era_replay/ladder.env (see ladder.env.example), and run
# this with no arguments. Nothing in the scripts is machine-specific.
#
#   Tier 0 (host only)      : pytest era suites + static era-import checker
#   Tier 1 (docker, CPU)    : rung 1 build+smoke per era; rung 2 instrument
#                             fidelity; rung 5 HF-fetch audit
#   Tier 2 (GPU + vLLM)     : rung 3-4 via 10/20/30 scripts — driven only when
#                             ERA_PRESET/SOURCES_FPATH/IMAGE_REF are set
#
# Exit non-zero if any attempted rung FAILED (skips don't fail the gate).
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HERE="${ROOT}/reproduce/classic_era_replay"
cd "$ROOT"
[[ -f "${HERE}/ladder.env" ]] && . "${HERE}/ladder.env"

: "${LADDER_ERAS:=helm-v0.2.4 helm-v0.3.0}"
: "${LADDER_OUT:=${ROOT}/ladder-out}"
mkdir -p "${LADDER_OUT}"

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
    run_step "tier0: era unit suites" "${LADDER_OUT}/tier0-pytest.log" \
        "$PYTEST" -m pytest tests/test_eras.py tests/test_eras_hostside.py \
            tests/test_eras_pipeline.py tests/test_era_shim_imports.py \
            -q -o addopts=""
else
    add SKIP "tier0: era unit suites" "no python with pytest (repo .venv or system)"
fi

# ------------------------------------------------------------------ tier 1 --
HAVE_DOCKER=0
command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1 && HAVE_DOCKER=1

if [[ $HAVE_DOCKER -eq 0 ]]; then
    add SKIP "rung 1-2-5 (all eras)" "docker unavailable — run on a docker host"
elif [[ -z "${PRECOMPUTED_ROOT:-}" || ! -d "${PRECOMPUTED_ROOT:-/nonexistent}" ]]; then
    # Rung 1 needs only docker; rungs 2/5 also need the corpus.
    for era in ${LADDER_ERAS}; do
        run_step "rung 1: image ${era}" "${LADDER_OUT}/rung1-${era}.log" \
            env ERA="$era" "${HERE}/00_build_era_image.sh"
    done
    add SKIP "rung 2+5 (all eras)" "PRECOMPUTED_ROOT unset or missing — set it in ladder.env"
else
    for era in ${LADDER_ERAS}; do
        run_step "rung 1: image ${era}" "${LADDER_OUT}/rung1-${era}.log" \
            env ERA="$era" "${HERE}/00_build_era_image.sh"
        run_step "rung 2: fidelity ${era}" "${LADDER_OUT}/rung2-${era}.log" \
            env ERA="$era" "${HERE}/15_instrument_fidelity.sh"
        run_step "rung 5: hf-fetch ${era}" "${LADDER_OUT}/rung5-${era}.log" \
            env ERA="$era" "${HERE}/50_hf_fetch_audit.sh"
    done
fi

# ------------------------------------------------------------------ tier 2 --
if [[ -n "${ERA_PRESET:-}" && -n "${SOURCES_FPATH:-}" && -n "${IMAGE_REF:-}" ]]; then
    era_one="${LADDER_ERAS%% *}"
    run_step "rung 3-4: bundle export" "${LADDER_OUT}/rung3-export.log" \
        env ERA="$era_one" "${HERE}/10_export_bundle.sh"
    run_step "rung 3-4: manifest" "${LADDER_OUT}/rung3-manifest.log" \
        env ERA="$era_one" "${HERE}/20_make_manifest.sh"
    run_step "rung 3-4: run" "${LADDER_OUT}/rung3-run.log" \
        env ERA="$era_one" OUT_MANIFEST="${OUT_MANIFEST:-configs/era_${era_one//./_}_manifest.yaml}" \
        "${HERE}/30_run.sh"
else
    add SKIP "rung 3-4 (end-to-end)" "set ERA_PRESET + SOURCES_FPATH + IMAGE_REF in ladder.env (GPU + vLLM host)"
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
    echo "GATE: ${fails} rung(s) FAILED — logs under ${LADDER_OUT}/"
    exit 1
fi
echo "GATE: all attempted rungs passed (SKIPs need the env noted above)."

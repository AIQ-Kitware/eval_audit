#!/usr/bin/env bash
# Ladder rung 2 — instrument fidelity (no model, no GPU).
#
# Dry-runs scenario construction + adaptation inside the era image for the
# pandas-sensitive runs (entity_matching, plus one math and one raft run) and
# diffs INSTANCE IDENTITY against the official artifacts. The tech report
# demonstrated pandas 2.0.x vs 2.2+ flips instance selection; this rung proves
# the era pins reproduce the official selection byte-for-byte.
#
# Needs: docker, the built era image, the public corpus on disk. No GPU/vLLM.
# All machine specifics come from the environment (see ladder.env.example):
#   ERA                      era key (e.g. helm-v0.3.0)            [required]
#   PRECOMPUTED_ROOT         public corpus mirror                   [required]
#   ERA_IMAGE                image ref (default <image_name>:dev)
#   HF_CACHE_DIR             HF cache to mount (default: temp dir; datasets
#                            download on first run — network needed then)
#   LADDER_FIDELITY_RUNS     comma-separated run dirs RELATIVE to
#                            PRECOMPUTED_ROOT (overrides the default picks)
#   CANONICAL_CORPUS_PREFIX  prefix run_details.yaml paths carry
#                            (default /data/crfm-helm-public)
#   LADDER_OUT               output root (default ./ladder-out)
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
[[ -f reproduce/classic_era_replay/ladder.env ]] && . reproduce/classic_era_replay/ladder.env

: "${ERA:?set ERA (e.g. helm-v0.3.0)}"
: "${PRECOMPUTED_ROOT:?set PRECOMPUTED_ROOT (public corpus mirror)}"
: "${CANONICAL_CORPUS_PREFIX:=/data/crfm-helm-public}"
: "${LADDER_OUT:=${ROOT}/ladder-out}"
SUITE_VERSION="${ERA#helm-}"

# Resolve the image: explicit ERA_IMAGE, else <image_name from eras.yaml>:dev.
if [[ -z "${ERA_IMAGE:-}" ]]; then
    for py in python3 "${ROOT}/.venv/bin/python"; do
        command -v "$py" >/dev/null 2>&1 || continue
        NAME="$("$py" "${ROOT}/docker/read_eras.py" "${ROOT}/docker/eras.yaml" "${ERA}" image_name 2>/dev/null)" && break
    done
    [[ -n "${NAME:-}" ]] || { echo "cannot resolve image_name for ${ERA}; set ERA_IMAGE"; exit 1; }
    ERA_IMAGE="${NAME}:dev"
fi

# Default fidelity picks: first corpus run per pandas-sensitive family at this era.
if [[ -z "${LADDER_FIDELITY_RUNS:-}" ]]; then
    picks=()
    for family in entity_matching math raft; do
        hit="$(grep -oE 'run_dir: .*' configs/run_details.yaml \
               | sed 's/^run_dir: //' \
               | grep -F "/runs/${SUITE_VERSION}/${family}" | head -1 || true)"
        [[ -n "$hit" ]] && picks+=("${hit#"${CANONICAL_CORPUS_PREFIX}"/}")
    done
    [[ ${#picks[@]} -gt 0 ]] || { echo "no ${SUITE_VERSION} fidelity runs found in configs/run_details.yaml"; exit 1; }
else
    IFS=',' read -r -a picks <<<"${LADDER_FIDELITY_RUNS}"
fi

HF_MOUNT="${HF_CACHE_DIR:-${LADDER_OUT}/hf-cache}"
mkdir -p "$HF_MOUNT"
DRIVERS="${ROOT}/reproduce/classic_era_replay/drivers"

pass=0; fail=0; skip=0
for rel in "${picks[@]}"; do
    official_dir="${PRECOMPUTED_ROOT}/${rel}"
    official_state="${official_dir}/scenario_state.json"
    spec="${official_dir}/run_spec.json"
    name="$(basename "$rel")"
    out="${LADDER_OUT}/fidelity/${ERA}/${name}"
    rm -rf "$out"; mkdir -p "$out"

    if [[ ! -f "$spec" ]]; then echo "SKIP  ${name}: no run_spec.json at ${spec}"; ((skip++)); continue; fi
    if [[ ! -f "$official_state" ]]; then echo "SKIP  ${name}: official scenario_state.json missing"; ((skip++)); continue; fi

    echo "[fidelity] ${ERA} ${name}"
    if ! docker run --rm \
        -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
        -v "${PRECOMPUTED_ROOT}:${PRECOMPUTED_ROOT}:ro" \
        -v "${HF_MOUNT}:/hf-cache" \
        -v "${DRIVERS}:/ladder:ro" \
        -v "${out}:${out}" \
        -w "${out}" \
        "${ERA_IMAGE}" \
        python /ladder/dryrun_driver.py "$spec" "ladder-fidelity" "$out" \
        > "${out}/dryrun.log" 2>&1; then
        echo "FAIL  ${name}: dry-run crashed (see ${out}/dryrun.log)"; ((fail++)); continue
    fi

    produced_state="$(find "${out}/benchmark_output/runs/ladder-fidelity" -maxdepth 2 -name scenario_state.json 2>/dev/null | head -1)"
    if [[ -z "$produced_state" ]]; then
        echo "FAIL  ${name}: dry-run produced no scenario_state.json"; ((fail++)); continue
    fi
    if python3 "${DRIVERS}/instance_diff.py" "$official_state" "$produced_state" > "${out}/instance_diff.txt" 2>&1; then
        echo "PASS  ${name}: $(head -1 "${out}/instance_diff.txt")"; ((pass++))
    else
        echo "FAIL  ${name}: instance identity diverged (see ${out}/instance_diff.txt)"; ((fail++))
    fi
done

echo
echo "[fidelity] ${ERA}: ${pass} pass, ${fail} fail, ${skip} skipped"
[[ $fail -eq 0 && $pass -gt 0 ]]

#!/usr/bin/env bash
# Ladder rung 2 — instrument fidelity (no model, no GPU).
#
# Dry-runs scenario construction + adaptation inside the era image for the
# pandas-sensitive runs (entity_matching, plus one math and one raft run) and
# diffs INSTANCE IDENTITY against the official artifacts. The tech report
# demonstrated pandas 2.0.x vs 2.2+ flips instance selection; this rung proves
# the era pins reproduce the official selection byte-for-byte.
#
# Identity is compared as (instance_id, train_trial_index, prompt): the public
# corpus does NOT ship scenario_state.json (nor instance input/references —
# scenario.json is metadata-only), so the official side is display_requests.json
# and the produced side is the dry-run scenario_state.json. The request prompt is
# a strict superset of instance input + few-shot examples + formatting after
# model-window truncation, so prompt equality proves selection AND construction
# fidelity. See drivers/instance_diff.py.
#
# Helper invoked by 07_run_gate.sh (per era). Needs: docker, the built era image,
# the public corpus on disk. No GPU/vLLM. Inputs (env; _lib.sh supplies defaults):
#   ERA                      era key (e.g. helm-v0.3.0)             [required]
#   PRECOMPUTED_ROOT         corpus root (default classic TRACK root; the mirror
#                            root is derived — see era_mirror_root / ERA_MIRROR_ROOT)
#   ERA_IMAGE                image ref (default: era_image "$ERA")
#   HF_CACHE_DIR             HF cache to mount (default: ERA_OUT/hf-cache)
#   LADDER_FIDELITY_RUNS     comma-separated run dirs RELATIVE to the MIRROR
#                            root (overrides the default picks)
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

: "${ERA:?set ERA (e.g. helm-v0.3.0)}"
: "${PRECOMPUTED_ROOT:?set PRECOMPUTED_ROOT (public corpus mirror)}"
SUITE_VERSION="$(era_suite_version "$ERA")"
ERA_IMAGE="${ERA_IMAGE:-$(era_image "$ERA")}"
# Officials are addressed as <mirror>/<track>/benchmark_output/... — the rel
# paths below are stripped against CANONICAL_CORPUS_PREFIX (the mirror), so
# join them against the mirror root, NOT the track-level PRECOMPUTED_ROOT
# (that would double the 'classic' component).
MIRROR_ROOT="$(era_mirror_root)"

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

HF_MOUNT="${HF_CACHE_DIR:-${ERA_OUT}/hf-cache}"
mkdir -p "$HF_MOUNT"
DRIVERS="${ERA_DIR}/drivers"

pass=0; fail=0; skip=0
for rel in "${picks[@]}"; do
    official_dir="${MIRROR_ROOT}/${rel}"
    official_reqs="${official_dir}/display_requests.json"
    spec="${official_dir}/run_spec.json"
    name="$(basename "$rel")"
    # docker -v parses ':' as the host:container:opts delimiter, and HELM run
    # names contain them (entity_matching:dataset=...), so the mount/workdir path
    # must be colon-free. Sanitize for the dir; keep $name for the log lines.
    safe_name="${name//:/_}"
    out="${ERA_OUT}/fidelity/${ERA}/${safe_name}"
    rm -rf "$out"; mkdir -p "$out"

    if [[ ! -f "$spec" ]]; then echo "SKIP  ${name}: no run_spec.json at ${spec}"; ((skip++)); continue; fi
    if [[ ! -f "$official_reqs" ]]; then echo "SKIP  ${name}: official display_requests.json missing"; ((skip++)); continue; fi

    echo "[fidelity] ${ERA} ${name}"
    # Forward HF auth (like rung 5): script-based datasets (e.g. math's
    # competition_math) need a token to fetch their loader from the 2026 Hub —
    # without it get_instances() dies with "Unauthorized ... competition_math.py".
    if ! docker run --rm \
        -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
        -e HF_TOKEN -e HUGGING_FACE_HUB_TOKEN \
        -v "${MIRROR_ROOT}:${MIRROR_ROOT}:ro" \
        -v "${HF_MOUNT}:/hf-cache" \
        -v "${DRIVERS}:/ladder:ro" \
        -v "${out}:${out}" \
        -w "${out}" \
        "${ERA_IMAGE}" \
        python /ladder/dryrun_driver.py "$spec" "ladder-fidelity" "$out" \
        > "${out}/dryrun.log" 2>&1; then
        # Distinguish an ENVIRONMENT/RECIPE filter (scenario data no longer
        # fetchable from the 2026 Hub — e.g. MATH's script-based competition_math,
        # which returns 401 without auth and is being deprecated Hub-side) from a
        # genuine instrument crash. A family whose data can't be fetched is a
        # filter reason, NOT an instrument-fidelity failure (same taxonomy rung 5
        # applies), so SKIP it rather than failing the gate.
        if grep -qiE "Unauthorized|Couldn't reach|ConnectionError|use_auth_token|GatedRepo|RepositoryNotFound|401 Client|403 Client|Connection refused|Temporary failure in name resolution|Name or service not known" "${out}/dryrun.log"; then
            echo "SKIP  ${name}: scenario data not fetchable from the 2026 Hub (environment/recipe filter; see ${out}/dryrun.log)"; ((skip++)); continue
        fi
        echo "FAIL  ${name}: dry-run crashed (see ${out}/dryrun.log)"; ((fail++)); continue
    fi

    produced_state="$(find "${out}/benchmark_output/runs/ladder-fidelity" -maxdepth 2 -name scenario_state.json 2>/dev/null | head -1)"
    if [[ -z "$produced_state" ]]; then
        echo "FAIL  ${name}: dry-run produced no scenario_state.json"; ((fail++)); continue
    fi
    if python3 "${DRIVERS}/instance_diff.py" "$official_reqs" "$produced_state" > "${out}/instance_diff.txt" 2>&1; then
        echo "PASS  ${name}: $(head -1 "${out}/instance_diff.txt")"; ((pass++))
    else
        echo "FAIL  ${name}: instance identity diverged (see ${out}/instance_diff.txt)"; ((fail++))
    fi
done

echo
echo "[fidelity] ${ERA}: ${pass} pass, ${fail} fail, ${skip} skipped"
[[ $fail -eq 0 && $pass -gt 0 ]]

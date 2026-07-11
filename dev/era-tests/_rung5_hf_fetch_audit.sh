#!/usr/bin/env bash
# Ladder rung 5 — HF-fetch audit (no model, no GPU).
#
# For each classic scenario FAMILY at the chosen era, dry-runs scenario
# construction inside the era image (one representative run per family): this
# exercises the era-vintage datasets/hub client against the 2026 Hub — the
# plan's main empirical risk. A family that fails here is an ENVIRONMENT/RECIPE
# filter reason (pre-warm or mount-vendor its data; never patch the image at
# run time), not a reproducibility failure.
#
# Helper invoked by 07_run_gate.sh (per era). Needs: docker, the built era image,
# the corpus, network (or a warmed cache). Inputs (env; _lib.sh supplies defaults):
#   ERA, PRECOMPUTED_ROOT      [ERA required; PRECOMPUTED_ROOT default classic]
#   ERA_IMAGE                  (default: era_image "$ERA")
#   HF_CACHE_DIR               HF cache to mount (default ERA_OUT/hf-cache)
#   LADDER_FETCH_CAP           adaptation cap for speed (default 10; fetch +
#                              get_instances still run on the full dataset)
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

: "${ERA:?set ERA (e.g. helm-v0.3.0)}"
: "${PRECOMPUTED_ROOT:?set PRECOMPUTED_ROOT (public corpus mirror)}"
: "${LADDER_FETCH_CAP:=10}"
SUITE_VERSION="$(era_suite_version "$ERA")"
ERA_IMAGE="${ERA_IMAGE:-$(era_image "$ERA")}"
# Join officials against the MIRROR root (rel paths are stripped against
# CANONICAL_CORPUS_PREFIX) — not the track-level PRECOMPUTED_ROOT, which would
# double the 'classic' component. See _lib.sh :: era_mirror_root.
MIRROR_ROOT="$(era_mirror_root)"

# One representative run per scenario family at this era: pair each record's
# run_dir + scenario_class from configs/run_details.yaml (records are 5-line
# blocks), keep the first run_dir seen per scenario_class.
mapfile -t family_runs < <(awk -v ver="/runs/${SUITE_VERSION}/" '
    /run_dir:/        { sub(/^ *run_dir: */, ""); dir=$0 }
    /scenario_class:/ { sub(/^ *scenario_class: */, ""); cls=$0
                        if (dir ~ ver && !(cls in seen)) { seen[cls]=1; print cls "\t" dir }
                        dir="" }
' configs/run_details.yaml)
[[ ${#family_runs[@]} -gt 0 ]] || { echo "no ${SUITE_VERSION} runs in configs/run_details.yaml"; exit 1; }

HF_MOUNT="${HF_CACHE_DIR:-${ERA_OUT}/hf-cache}"
mkdir -p "$HF_MOUNT"
DRIVERS="${ERA_DIR}/drivers"

echo "[hf-fetch] ${ERA}: auditing ${#family_runs[@]} scenario families (cap=${LADDER_FETCH_CAP})"
pass=0; fail=0; failed_families=()
for row in "${family_runs[@]}"; do
    cls="${row%%$'\t'*}"; dir="${row#*$'\t'}"
    rel="${dir#"${CANONICAL_CORPUS_PREFIX}"/}"
    spec="${MIRROR_ROOT}/${rel}/run_spec.json"
    short="${cls##*.}"
    out="${ERA_OUT}/hf-fetch/${ERA}/${short}"
    rm -rf "$out"; mkdir -p "$out"

    if [[ ! -f "$spec" ]]; then echo "FAIL  ${short}: run_spec.json missing at ${spec}"; ((fail++)); failed_families+=("$short"); continue; fi
    if docker run --rm \
        -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
        -e HF_TOKEN -e HUGGING_FACE_HUB_TOKEN \
        -v "${MIRROR_ROOT}:${MIRROR_ROOT}:ro" \
        -v "${HF_MOUNT}:/hf-cache" \
        -v "${DRIVERS}:/ladder:ro" \
        -v "${out}:${out}" \
        -w "${out}" \
        "${ERA_IMAGE}" \
        python /ladder/dryrun_driver.py "$spec" "ladder-fetch" "$out" "${LADDER_FETCH_CAP}" \
        > "${out}/dryrun.log" 2>&1; then
        echo "PASS  ${short}"; ((pass++))
    else
        echo "FAIL  ${short} (see ${out}/dryrun.log)"; ((fail++)); failed_families+=("$short")
    fi
done

echo
echo "[hf-fetch] ${ERA}: ${pass}/${#family_runs[@]} families fetch cleanly"
if [[ $fail -gt 0 ]]; then
    echo "[hf-fetch] failing families (pre-warm or mount-vendor their data): ${failed_families[*]}"
fi
# Rung 5 is an AUDIT: individual family failures are filter reasons, not a
# broken harness — exit non-zero only when NOTHING fetches (harness-level fault).
[[ $pass -gt 0 ]]

#!/usr/bin/env bash
__doc__='
docker/build.sh
===============

Build the eval-audit HELM runner image (docker/helm-runner.dockerfile).

The image bundles a pinned crfm-helm[all] + aiq-magnet environment used by the
eval_audit kwdagger pipeline to run HELM in a reproducible, auditable container.

Source staging
--------------
By default the image is built from *committed* state only ("pristine"): the
script git-archives HEAD of the eval_audit superproject and of each relevant
submodule (helm, aiq-magnet) into a clean staging context. This guarantees the
image contents correspond to recorded shas and contain no uncommitted edits,
untracked files, or build artifacts.

Set BUILD_FROM=worktree to instead copy the live working trees (excluding VCS
and build junk) for fast iteration on uncommitted changes. Such images are
NON-REPRODUCIBLE and are tagged with a "-dirty" suffix.

Era (pre-v0.5) images
---------------------
Set ERA=<key> (a key in docker/eras.yaml, e.g. helm-v0.2.4) to build a CPU-only
era image instead of the modern one. In this mode the script stages era HELM at
the pinned release commit (git archive of helm_git_ref), skips magnet plus
eval_audit staging, stages docker/era_shim/ and the era constraints file, and
builds docker/helm-runner-era.dockerfile tagged image_name:<eval-audit-sha>.
ERA is incompatible with BUILD_FROM=worktree for the helm tree (the era harness
must be the committed release commit, not the live submodule worktree). With no
ERA the behavior is byte-identical to before.

Usage
-----
  # Pristine build (default), tag eval-audit-helm-runner:<eval-audit-short-sha>
  ./docker/build.sh

  # Iterate on uncommitted submodule changes
  BUILD_FROM=worktree ./docker/build.sh

  # Build an era image (pre-v0.5)
  ERA=helm-v0.3.0 ./docker/build.sh

  # Build and push to a registry
  DOCKER_REPO=ghcr.io/aiq-kitware PUSH_IMAGES=1 ./docker/build.sh

Environment variables
---------------------
  IMAGE_NAME    Logical image name              (default: eval-audit-helm-runner;
                                                 era: <image_name> from eras.yaml)
  IMAGE_TAG     Explicit tag                     (default: derived from eval-audit sha)
  BUILD_FROM    committed | worktree             (default: committed)
  ERA           era key from docker/eras.yaml    (default: empty / modern image)
  DOCKER_REPO   Registry/namespace for push      (default: empty / local only)
  PUSH_IMAGES   1 to push, 0 to build only       (default: 0)
  PYTHON_VERSION                                 (default: 3.11; era: from eras.yaml)
  ERA_YAML_PYTHON  python (with PyYAML) to read eras.yaml (default: python3, then .venv)
  STAGING_DIR   Staging context dir              (default: <repo>/.build-staging/helm-runner)
  BUILD_NOFILE  nofile ulimit for build steps    (default: 1048576; 0 omits --ulimit)
'

set -euo pipefail

log(){ printf "\033[1;34m[helm-runner-build]\033[0m %s\n" "$*"; }
die(){ printf "\033[1;31m[error]\033[0m %s\n" "$*" >&2; exit 1; }

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    printf "%s\n" "$__doc__"
    exit 0
fi

# ------------------------------------------------------------------------------
# Config
# ------------------------------------------------------------------------------
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(realpath "${SCRIPT_DIR}/..")"

: "${IMAGE_NAME:=eval-audit-helm-runner}"
: "${BUILD_FROM:=committed}"
: "${PUSH_IMAGES:=0}"
: "${DOCKER_REPO:=}"
: "${PYTHON_VERSION:=3.11}"
: "${ERA:=}"
: "${STAGING_DIR:=${REPO_ROOT}/.build-staging/helm-runner}"

# ------------------------------------------------------------------------------
# Era registry access (only used when ERA is set)
# ------------------------------------------------------------------------------
ERAS_YAML="${REPO_ROOT}/docker/eras.yaml"
READ_ERAS="${SCRIPT_DIR}/read_eras.py"

# Locate a python that can import PyYAML (build.sh cannot parse YAML natively).
# Prefer an explicit override, then a system python3 with yaml, then the repo venv.
pick_era_python(){
    local candidates=("${ERA_YAML_PYTHON:-}" python3 "${REPO_ROOT}/.venv/bin/python")
    local py
    for py in "${candidates[@]}"; do
        [[ -n "$py" ]] || continue
        if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import yaml' >/dev/null 2>&1; then
            printf '%s' "$py"
            return 0
        fi
    done
    die "no python with PyYAML found to read ${ERAS_YAML}; set ERA_YAML_PYTHON"
}

era_field(){
    # era_field <era_key> <field> -> stdout (dies on unknown era/field)
    "${ERA_PYTHON}" "${READ_ERAS}" "${ERAS_YAML}" "$1" "$2" \
        || die "read_eras.py failed for era='$1' field='$2'"
}
# UV_COMPILE_BYTECODE=1 in the dockerfile makes uv pre-compile the whole
# crfm-helm[all] dep tree via a pool of parallel interpreters; under a low
# container nofile limit that pool exhausts FDs and dies with EMFILE
# ("os error 24"). Raise the build-step FD ceiling. Override BUILD_NOFILE=0 to
# omit the flag (e.g. if a docker version rejects --ulimit through BuildKit).
: "${BUILD_NOFILE:=1048576}"

HELM_SUBMODULE="${REPO_ROOT}/submodules/helm"
MAGNET_SUBMODULE="${REPO_ROOT}/submodules/aiq-magnet"

[[ -d "${HELM_SUBMODULE}/.git" || -f "${HELM_SUBMODULE}/.git" ]] || \
    die "HELM submodule not initialized at ${HELM_SUBMODULE}"
# The era image does not stage or install aiq-magnet (its v0.5+ module paths are
# era-hostile); only require the magnet submodule for modern builds.
if [[ -z "${ERA}" ]]; then
    [[ -d "${MAGNET_SUBMODULE}/.git" || -f "${MAGNET_SUBMODULE}/.git" ]] || \
        die "aiq-magnet submodule not initialized at ${MAGNET_SUBMODULE}"
fi

# ------------------------------------------------------------------------------
# Resolve era registry fields (only when ERA is set)
# ------------------------------------------------------------------------------
ERA_HELM_REF=""
ERA_CONSTRAINTS=""
ERA_EXTRAS=""
ERA_PANDAS_PIN=""
ERA_NUMPY_PIN=""
if [[ -n "${ERA}" ]]; then
    [[ -f "${ERAS_YAML}" ]] || die "era registry not found: ${ERAS_YAML}"
    [[ -f "${READ_ERAS}" ]] || die "era reader not found: ${READ_ERAS}"
    ERA_PYTHON="$(pick_era_python)"
    if [[ "${BUILD_FROM}" == "worktree" ]]; then
        die "ERA=${ERA} is incompatible with BUILD_FROM=worktree: the era harness "\
"must be the committed release commit, not the live helm submodule worktree."
    fi
    ERA_HELM_REF="$(era_field "${ERA}" helm_git_ref)"
    ERA_PYTHON_VERSION="$(era_field "${ERA}" python_version)"
    ERA_CONSTRAINTS_REL="$(era_field "${ERA}" constraints)"
    ERA_EXTRAS="$(era_field "${ERA}" helm_extras)"
    ERA_IMAGE_NAME="$(era_field "${ERA}" image_name)"
    ERA_CONSTRAINTS="${REPO_ROOT}/${ERA_CONSTRAINTS_REL}"
    [[ -f "${ERA_CONSTRAINTS}" ]] || die "era constraints file missing: ${ERA_CONSTRAINTS}"
    # Extract pandas/numpy pins from the constraints file for the final-stage
    # spot-check (empty when unpinned => the assertion is skipped in-image).
    ERA_PANDAS_PIN="$(sed -n 's/^pandas==//p' "${ERA_CONSTRAINTS}" | head -1)"
    ERA_NUMPY_PIN="$(sed -n 's/^numpy==//p' "${ERA_CONSTRAINTS}" | head -1)"
    # Era image name + python override the modern defaults (unless the caller set
    # IMAGE_NAME explicitly).
    if [[ "${IMAGE_NAME}" == "eval-audit-helm-runner" ]]; then
        IMAGE_NAME="${ERA_IMAGE_NAME}"
    fi
    PYTHON_VERSION="${ERA_PYTHON_VERSION}"
fi

# ------------------------------------------------------------------------------
# Resolve provenance refs
# ------------------------------------------------------------------------------
git_ref(){ git -C "$1" rev-parse HEAD; }
git_dirty(){ [[ -n "$(git -C "$1" status --porcelain 2>/dev/null)" ]] && echo 1 || echo 0; }

EVAL_AUDIT_REF="$(git_ref "${REPO_ROOT}")"
# Era: HELM provenance is the pinned release commit, not the submodule HEAD.
if [[ -n "${ERA}" ]]; then
    HELM_REF="$(git -C "${HELM_SUBMODULE}" rev-parse "${ERA_HELM_REF}^{commit}")" \
        || die "era helm ref ${ERA_HELM_REF} not found in ${HELM_SUBMODULE}"
    MAGNET_REF=""
else
    HELM_REF="$(git_ref "${HELM_SUBMODULE}")"
    MAGNET_REF="$(git_ref "${MAGNET_SUBMODULE}")"
fi

DIRTY=0
# The era build never uses the helm worktree (it archives a fixed commit), so
# only the superproject's cleanliness governs reproducibility in era mode.
if [[ -n "${ERA}" ]]; then
    if [[ "$(git_dirty "${REPO_ROOT}")" == "1" ]]; then DIRTY=1; fi
elif [[ "$(git_dirty "${REPO_ROOT}")" == "1" || "$(git_dirty "${HELM_SUBMODULE}")" == "1" || "$(git_dirty "${MAGNET_SUBMODULE}")" == "1" ]]; then
    DIRTY=1
fi

SHORT="${EVAL_AUDIT_REF:0:12}"
if [[ "${BUILD_FROM}" == "worktree" && "${DIRTY}" == "1" ]]; then
    : "${IMAGE_TAG:=${SHORT}-dirty}"
    EVAL_AUDIT_REF="${EVAL_AUDIT_REF}-dirty"
else
    : "${IMAGE_TAG:=${SHORT}}"
fi

IMAGE_QUALNAME="${IMAGE_NAME}:${IMAGE_TAG}"

# ------------------------------------------------------------------------------
# Stage source
# ------------------------------------------------------------------------------
# tar-based filtered copy is portable (no rsync dependency).
copy_worktree(){
    local src="$1" dst="$2"
    mkdir -p "$dst"
    tar -C "$src" \
        --exclude='./.git' \
        --exclude='./.venv' \
        --exclude='*.egg-info' \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        --exclude='./build' \
        --exclude='./dist' \
        --exclude='./.pytest_cache' \
        --exclude='./results' \
        --exclude='./node_modules' \
        -cf - . | tar -C "$dst" -xf -
}

stage_committed(){
    local src="$1" dst="$2"
    mkdir -p "$dst"
    git -C "$src" archive --format=tar HEAD | tar -C "$dst" -xf -
}

# Stage a repo at a SPECIFIC committed ref (era builds pin HELM to its release
# commit, not the submodule HEAD). git archive from an explicit tree keeps the
# era harness reproducible regardless of where the submodule currently points.
stage_committed_ref(){
    local src="$1" ref="$2" dst="$3"
    mkdir -p "$dst"
    git -C "$src" archive --format=tar "${ref}" | tar -C "$dst" -xf -
}

# Copy the era shim package (docker/era_shim/) into the staging context. It is a
# checked-in host dir (not a submodule), so a plain filtered tar is correct.
copy_era_shim(){
    local dst="$1"
    mkdir -p "$dst"
    tar -C "${SCRIPT_DIR}/era_shim" \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        --exclude='*.egg-info' \
        -cf - . | tar -C "$dst" -xf -
}

# eval_audit is the superproject, not a submodule, and we only need the package
# itself + its packaging files (uv_build reads pyproject.toml + README.md, and the
# editable install registers the [project.entry-points.helm] plugins). Stage just
# those paths so helm-run *inside* the image loads the same HELM overrides the host
# venv does (e.g. the allenai/olmo-7b tokenizer repoint) — without dragging the
# whole superproject (submodules, docs, tests, results) into the build context.
EVAL_AUDIT_PATHS=(eval_audit pyproject.toml README.md)
stage_eval_audit_committed(){
    local dst="$1"
    mkdir -p "$dst"
    git -C "${REPO_ROOT}" archive --format=tar HEAD "${EVAL_AUDIT_PATHS[@]}" | tar -C "$dst" -xf -
}
copy_eval_audit_worktree(){
    local dst="$1"
    mkdir -p "$dst"
    tar -C "${REPO_ROOT}" \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        --exclude='*.egg-info' \
        -cf - "${EVAL_AUDIT_PATHS[@]}" | tar -C "$dst" -xf -
}

log "Staging source (BUILD_FROM=${BUILD_FROM}${ERA:+, ERA=${ERA}}) into ${STAGING_DIR}"
rm -rf "${STAGING_DIR}"
mkdir -p "${STAGING_DIR}"

if [[ -n "${ERA}" ]]; then
    # Era context: era HELM at its pinned release commit + the shim + constraints.
    # No magnet, no eval_audit (both are era-hostile).
    log "Staging era HELM @ ${ERA_HELM_REF} (${HELM_REF})"
    stage_committed_ref "${HELM_SUBMODULE}" "${ERA_HELM_REF}" "${STAGING_DIR}/helm"
    copy_era_shim "${STAGING_DIR}/era-shim"
    cp "${ERA_CONSTRAINTS}" "${STAGING_DIR}/constraints.txt"
elif [[ "${BUILD_FROM}" == "committed" ]]; then
    stage_committed "${HELM_SUBMODULE}" "${STAGING_DIR}/helm"
    stage_committed "${MAGNET_SUBMODULE}" "${STAGING_DIR}/aiq-magnet"
    stage_eval_audit_committed "${STAGING_DIR}/eval-audit"
elif [[ "${BUILD_FROM}" == "worktree" ]]; then
    log "WARNING: worktree build is NON-REPRODUCIBLE (includes uncommitted state)"
    copy_worktree "${HELM_SUBMODULE}" "${STAGING_DIR}/helm"
    copy_worktree "${MAGNET_SUBMODULE}" "${STAGING_DIR}/aiq-magnet"
    copy_eval_audit_worktree "${STAGING_DIR}/eval-audit"
else
    die "BUILD_FROM must be 'committed' or 'worktree', got '${BUILD_FROM}'"
fi

cp "${SCRIPT_DIR}/entrypoint.sh" "${STAGING_DIR}/entrypoint.sh"
# The build context is the staging dir, so the ignore file must live there.
cp "${SCRIPT_DIR}/helm-runner.dockerignore" "${STAGING_DIR}/.dockerignore"

# ------------------------------------------------------------------------------
# Build
# ------------------------------------------------------------------------------
print_summary(){
    cat <<EOF
------------------------------------------------------------
docker/build.sh summary
------------------------------------------------------------
IMAGE_QUALNAME  = ${IMAGE_QUALNAME}
BUILD_FROM      = ${BUILD_FROM}
ERA             = ${ERA:-<none / modern>}
PYTHON_VERSION  = ${PYTHON_VERSION}
EVAL_AUDIT_REF  = ${EVAL_AUDIT_REF}
HELM_REF        = ${HELM_REF}${ERA:+ (era ref ${ERA_HELM_REF})}
MAGNET_REF      = ${MAGNET_REF:-<n/a for era>}
DIRTY           = ${DIRTY}
DOCKER_REPO     = ${DOCKER_REPO:-<none>}
PUSH_IMAGES     = ${PUSH_IMAGES}
STAGING_DIR     = ${STAGING_DIR}
------------------------------------------------------------
EOF
}
print_summary

log "Building ${IMAGE_QUALNAME}"
# Raise the FD ceiling for RUN steps so uv's parallel bytecode compilation of
# the helm[all] dep tree doesn't hit EMFILE. BUILD_NOFILE=0 opts out.
ULIMIT_ARGS=()
if [[ "${BUILD_NOFILE}" != "0" ]]; then
    ULIMIT_ARGS=(--ulimit "nofile=${BUILD_NOFILE}:${BUILD_NOFILE}")
fi

if [[ -n "${ERA}" ]]; then
    # Era build: CPU-only era dockerfile; pass the era ref/pins/extras so the
    # final stage can assert them, and stamp org.aiq.era for the bridge's
    # era<->image guard.
    DOCKER_BUILDKIT=1 docker build \
        "${ULIMIT_ARGS[@]}" \
        --progress=plain \
        --file "${SCRIPT_DIR}/helm-runner-era.dockerfile" \
        --build-arg PYTHON_VERSION="${PYTHON_VERSION}" \
        --build-arg EVAL_AUDIT_REF="${EVAL_AUDIT_REF}" \
        --build-arg BUILD_FROM="${BUILD_FROM}" \
        --build-arg HELM_EXTRAS="${ERA_EXTRAS}" \
        --build-arg ERA_KEY="${ERA}" \
        --build-arg ERA_HELM_REF="${HELM_REF}" \
        --build-arg PANDAS_PIN="${ERA_PANDAS_PIN}" \
        --build-arg NUMPY_PIN="${ERA_NUMPY_PIN}" \
        --tag "${IMAGE_QUALNAME}" \
        --tag "${IMAGE_NAME}:dev" \
        "${STAGING_DIR}"
else
    DOCKER_BUILDKIT=1 docker build \
        "${ULIMIT_ARGS[@]}" \
        --progress=plain \
        --file "${SCRIPT_DIR}/helm-runner.dockerfile" \
        --build-arg PYTHON_VERSION="${PYTHON_VERSION}" \
        --build-arg EVAL_AUDIT_REF="${EVAL_AUDIT_REF}" \
        --build-arg HELM_REF="${HELM_REF}" \
        --build-arg MAGNET_REF="${MAGNET_REF}" \
        --build-arg BUILD_FROM="${BUILD_FROM}" \
        --tag "${IMAGE_QUALNAME}" \
        --tag "${IMAGE_NAME}:dev" \
        "${STAGING_DIR}"
fi

log "Built ${IMAGE_QUALNAME} (and local alias ${IMAGE_NAME}:dev)"

# ------------------------------------------------------------------------------
# Optional push
# ------------------------------------------------------------------------------
if [[ "${PUSH_IMAGES}" -eq 1 ]]; then
    [[ -n "${DOCKER_REPO}" ]] || die "PUSH_IMAGES=1 requires DOCKER_REPO"
    REMOTE="${DOCKER_REPO}/${IMAGE_QUALNAME}"
    log "Tagging + pushing ${REMOTE}"
    docker tag "${IMAGE_QUALNAME}" "${REMOTE}"
    docker push "${REMOTE}"
    log "Pushed ${REMOTE}"
    log "Resolve its immutable digest for manifests with:"
    log "  docker image inspect --format '{{index .RepoDigests 0}}' ${REMOTE}"
else
    log "Not pushed (PUSH_IMAGES=0). For cross-machine reproducibility, push to a"
    log "registry and reference the image by digest in your manifest."
fi

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

Usage
-----
  # Pristine build (default), tag eval-audit-helm-runner:<eval-audit-short-sha>
  ./docker/build.sh

  # Iterate on uncommitted submodule changes
  BUILD_FROM=worktree ./docker/build.sh

  # Build and push to a registry
  DOCKER_REPO=ghcr.io/aiq-kitware PUSH_IMAGES=1 ./docker/build.sh

Environment variables
---------------------
  IMAGE_NAME    Logical image name              (default: eval-audit-helm-runner)
  IMAGE_TAG     Explicit tag                     (default: derived from eval-audit sha)
  BUILD_FROM    committed | worktree             (default: committed)
  DOCKER_REPO   Registry/namespace for push      (default: empty / local only)
  PUSH_IMAGES   1 to push, 0 to build only       (default: 0)
  PYTHON_VERSION                                 (default: 3.11)
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
: "${STAGING_DIR:=${REPO_ROOT}/.build-staging/helm-runner}"
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
[[ -d "${MAGNET_SUBMODULE}/.git" || -f "${MAGNET_SUBMODULE}/.git" ]] || \
    die "aiq-magnet submodule not initialized at ${MAGNET_SUBMODULE}"

# ------------------------------------------------------------------------------
# Resolve provenance refs
# ------------------------------------------------------------------------------
git_ref(){ git -C "$1" rev-parse HEAD; }
git_dirty(){ [[ -n "$(git -C "$1" status --porcelain 2>/dev/null)" ]] && echo 1 || echo 0; }

EVAL_AUDIT_REF="$(git_ref "${REPO_ROOT}")"
HELM_REF="$(git_ref "${HELM_SUBMODULE}")"
MAGNET_REF="$(git_ref "${MAGNET_SUBMODULE}")"

DIRTY=0
if [[ "$(git_dirty "${REPO_ROOT}")" == "1" || "$(git_dirty "${HELM_SUBMODULE}")" == "1" || "$(git_dirty "${MAGNET_SUBMODULE}")" == "1" ]]; then
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

log "Staging source (BUILD_FROM=${BUILD_FROM}) into ${STAGING_DIR}"
rm -rf "${STAGING_DIR}"
mkdir -p "${STAGING_DIR}"

if [[ "${BUILD_FROM}" == "committed" ]]; then
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
PYTHON_VERSION  = ${PYTHON_VERSION}
EVAL_AUDIT_REF  = ${EVAL_AUDIT_REF}
HELM_REF        = ${HELM_REF}
MAGNET_REF      = ${MAGNET_REF}
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

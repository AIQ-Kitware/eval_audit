# syntax=docker/dockerfile:1.7
#
# eval-audit HELM runner image
# ============================
#
# A self-contained, GPU-ready image that runs a single HELM run-entry via
# aiq-magnet's ``materialize_helm_run`` CLI inside a pinned, auditable
# environment. The eval_audit kwdagger pipeline invokes this image (by sha256
# digest) instead of running ``helm-run`` in an uncontrolled host venv, so the
# software environment stops being a hidden variable in reproducibility audits.
#
# This image is intentionally independent of the legacy uv/magnet/magnet-heim
# Dockerfile chain in submodules/aiq-magnet/dockerfiles. Build it via
# ``docker/build.sh``, which stages a pristine (committed-state) source tree
# under .build-staging/ and supplies the build args below.
#
# Build context layout (produced by build.sh):
#   <context>/helm/          pristine crfm-helm source (git archive)
#   <context>/aiq-magnet/    pristine aiq-magnet source (git archive)
#   <context>/entrypoint.sh  copied from docker/entrypoint.sh
#
# Design notes:
#   * Multi-stage: a CUDA *devel* builder (has compilers for any source wheels)
#     produces a venv at /opt/venv; the final stage is the slimmer CUDA
#     *runtime* image with the venv + source copied in.
#   * uv is vendored from the official, pinned uv image (modern idiomatic way).
#   * Editable installs keep HELM's on-disk data files discoverable (matches the
#     known-good magnet-heim behavior); the source therefore lives in the final
#     image at a fixed path (/opt/src).
#   * Runs as root so the HF cache + /root writes work; output-dir ownership is
#     handled by entrypoint.sh (chown back to the host uid/gid on exit).

ARG CUDA_DEVEL_IMAGE=nvidia/cuda:12.4.1-cudnn-devel-ubuntu22.04
ARG CUDA_RUNTIME_IMAGE=nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.9.27
ARG PYTHON_VERSION=3.11

# ------------------------------------------------------------------------------
# Stage 1: builder — create /opt/venv with HELM[heim] + aiq-magnet installed.
# ------------------------------------------------------------------------------
FROM ${UV_IMAGE} AS uv
FROM ${CUDA_DEVEL_IMAGE} AS builder

ARG PYTHON_VERSION
ENV DEBIAN_FRONTEND=noninteractive

# git is needed by some editable installs; build-essential for any source wheels.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked <<'EOF'
set -eux
apt-get update -q
apt-get install -q -y --no-install-recommends \
    ca-certificates \
    git \
    build-essential
EOF

# Vendored, pinned uv binary (no curl-to-shell install).
COPY --from=uv /uv /uvx /usr/local/bin/

# A real virtualenv at a non-/root, world-readable path so the final stage can
# copy it and any uid can use it.
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH
RUN uv venv /opt/venv --python=${PYTHON_VERSION} --seed

# Pristine source trees (staged by build.sh). Editable installs need them on
# disk at a stable path that is identical in the final stage.
WORKDIR /opt/src
COPY helm/ /opt/src/helm/
COPY aiq-magnet/ /opt/src/aiq-magnet/

# Install HELM (with the heim extra used by the local-HF recipe) and aiq-magnet.
# The uv cache mount persists resolved wheels (torch, transformers, ...) across
# rebuilds, so source-only edits do not re-download the heavy dependency set.
RUN --mount=type=cache,target=/root/.cache/uv <<'EOF'
set -eux
uv pip install -e '/opt/src/helm[heim]'
uv pip install -e /opt/src/aiq-magnet
# Sanity: both must import in the built venv before we ship it.
python -c "import helm, magnet; print('helm', helm.__version__ if hasattr(helm, '__version__') else '?', '| magnet', magnet.__version__)"
EOF

# ------------------------------------------------------------------------------
# Stage 2: final — slim CUDA runtime + the prebuilt venv and source.
# ------------------------------------------------------------------------------
FROM ${CUDA_RUNTIME_IMAGE} AS final

ARG PYTHON_VERSION
ENV DEBIAN_FRONTEND=noninteractive

# Runtime needs git (some HELM scenarios shell out) and gosu-free chown (busybox
# chown from coreutils is already present). Keep this layer tiny.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked <<'EOF'
set -eux
apt-get update -q
apt-get install -q -y --no-install-recommends \
    ca-certificates \
    git
EOF

# Bring over the prebuilt environment and source (editable install points here).
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/src /opt/src

ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1

# HuggingFace cache lives at a mount point, NOT baked into the image. The
# eval_audit node bind-mounts a host directory here at run time. Setting HF_HOME
# makes hub + datasets + transformers caches all land under /hf-cache.
ENV HF_HOME=/hf-cache
RUN mkdir -p /hf-cache

# Clean entrypoint: run the command, then (on exit) write container provenance
# and chown the working dir back to the invoking host user. No login-shell /
# bashrc sourcing — the venv is on PATH directly.
COPY entrypoint.sh /usr/local/bin/eval-audit-entrypoint.sh
RUN chmod +x /usr/local/bin/eval-audit-entrypoint.sh

WORKDIR /opt/src
ENTRYPOINT ["/usr/local/bin/eval-audit-entrypoint.sh"]
CMD ["python", "-m", "magnet.backends.helm.cli.materialize_helm_run", "--help"]

# --- Provenance labels (populated by build.sh) --------------------------------
ARG EVAL_AUDIT_REF=""
ARG HELM_REF=""
ARG MAGNET_REF=""
ARG BUILD_FROM="committed"
ARG CUDA_RUNTIME_IMAGE
ARG UV_IMAGE

LABEL org.opencontainers.image.title="eval-audit HELM runner" \
      org.opencontainers.image.description="Pinned environment to run HELM via aiq-magnet materialize_helm_run for reproducibility audits." \
      org.opencontainers.image.vendor="Kitware Inc." \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.revision="$EVAL_AUDIT_REF" \
      org.aiq.eval-audit-ref="$EVAL_AUDIT_REF" \
      org.aiq.helm-ref="$HELM_REF" \
      org.aiq.magnet-ref="$MAGNET_REF" \
      org.aiq.build-from="$BUILD_FROM" \
      org.aiq.python-version="$PYTHON_VERSION" \
      org.aiq.cuda-base="$CUDA_RUNTIME_IMAGE" \
      org.aiq.uv-image="$UV_IMAGE"

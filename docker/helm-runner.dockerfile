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
# Stage 1: builder — create /opt/venv with HELM[all] + aiq-magnet installed.
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
    UV_PYTHON_INSTALL_DIR=/opt/uv/python \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH
# The base image has no Python 3.11, so uv downloads a standalone (managed)
# CPython. Pin where it lands (UV_PYTHON_INSTALL_DIR=/opt/uv/python) and force a
# managed interpreter, so the venv's bin/python symlinks point at a path we can
# copy verbatim into the final stage. Without this the interpreter lives under
# ~/.local/share/uv and is left behind, leaving /opt/venv/bin/python a dangling
# symlink in the runtime image ("python: command not found").
RUN uv venv /opt/venv --python=${PYTHON_VERSION} --python-preference only-managed --seed

# Pristine source trees (staged by build.sh). Editable installs need them on
# disk at a stable path that is identical in the final stage.
#
# Layer ordering is deliberately heavy-deps-first / fast-changing-last so the
# expensive layer survives the edits that happen most. crfm-helm[all] pulls the
# entire heavy dependency set (torch, transformers, jax, flax, ...) and is a
# pinned upstream submodule that changes rarely; aiq-magnet is a thin wrapper
# (its deps are crfm-helm + light pure-Python packages, all satisfied by helm's
# set) and is where active development — including this audit's CLI — lands. So:
#   * COPY + install helm FIRST  -> the heavy layer, cached across every magnet edit.
#   * COPY + install magnet LAST -> a magnet-only edit re-runs just this small
#     layer; its deps are already satisfied, so uv only re-links the editable pkg.
# Keeping both in one COPY+RUN (as before) made any magnet edit re-run the whole
# helm[all] install (re-link + bytecode-compile of the full dep tree). The uv
# cache mount persists resolved wheels across rebuilds either way.
WORKDIR /opt/src

# --- heavy, rarely-invalidated layer: HELM + its full dependency tree ----------
COPY helm/ /opt/src/helm/
RUN --mount=type=cache,target=/root/.cache/uv <<'EOF'
set -eux
# [all] (not [heim]) so the text scenario + metric deps are present — notably
# langdetect (ifeval / cleva metrics) and the full scenario set. [heim] is the
# image-generation extra and omits [metrics]/[scenarios]/[cleva], so a text
# benchmark run dies with "ModuleNotFoundError: langdetect".
#
# Pin huggingface_hub to the version HELM's dataset loaders are validated against
# (matches the e2e venv). Left to float under datasets~=3.1 it can resolve to a
# hub whose repo-resolution API breaks old-style dataset ids (e.g. wmt14 ->
# "not a valid HF dataset"). Co-install so uv resolves the pin instead of a
# transitive float; an incompatible pin then fails the build loudly here.
uv pip install -e '/opt/src/helm[all]' 'huggingface_hub==0.36.2'
python - <<'PY'
import helm, huggingface_hub
print('helm', getattr(helm, '__version__', '?'), '| huggingface_hub', huggingface_hub.__version__)
assert huggingface_hub.__version__ == '0.36.2', huggingface_hub.__version__
PY
EOF

# --- light, frequently-invalidated layer: aiq-magnet (installed LAST) ----------
# Must come AFTER helm: magnet depends on crfm-helm, so the editable helm above
# satisfies it (otherwise uv would pull crfm-helm from PyPI instead of the pinned
# submodule source). With helm in place, this resolves to "deps already satisfied"
# and only builds + links the magnet editable package.
COPY aiq-magnet/ /opt/src/aiq-magnet/
RUN --mount=type=cache,target=/root/.cache/uv <<'EOF'
set -eux
uv pip install -e /opt/src/aiq-magnet
# Sanity: both must import in the built venv before we ship it.
python -c "import helm, magnet; print('helm', getattr(helm, '__version__', '?'), '| magnet', magnet.__version__)"
EOF

# --- eval_audit HELM plugins (editable, --no-deps) -----------------------------
# helm-run discovers HELM overrides via [project.entry-points.helm], but only for
# packages installed in ITS environment. The host venv has eval_audit; this image
# did not — so in-container helm-run fell back to the built-in allenai/olmo-7b
# tokenizer config (trust_remote_code -> needs hf_olmo) and died, instead of the
# eval_audit repoint to the transformers-native allenai/OLMo-7B-hf tokenizer.
# Installing eval_audit here registers that entry point (and any future HELM
# override) in-container, so the pinned image matches the host recipe.
#
# --no-deps is deliberate: the plugin only imports helm.benchmark.* (already
# present), so its full dep tree (pandas/plotly/sklearn/kaleido/...) is dead
# weight here, and a full resolve could pull crfm-helm from PyPI over the pinned
# editable submodule. The /opt/src tree (incl. this dir) is copied to the final
# stage, so the editable install resolves there too.
COPY eval-audit/ /opt/src/eval-audit/
RUN --mount=type=cache,target=/root/.cache/uv <<'EOF'
set -eux
uv pip install -e /opt/src/eval-audit --no-deps
python -c "import eval_audit.integrations.helm_plugins; print('eval_audit helm plugins import ok')"
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
# The venv interpreter is a uv-managed standalone CPython; /opt/venv/bin/python
# symlinks into UV_PYTHON_INSTALL_DIR. Copy that interpreter to the SAME path or
# the symlink dangles here ("python: command not found" at run time).
COPY --from=builder /opt/uv/python /opt/uv/python

ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    UV_PYTHON_INSTALL_DIR=/opt/uv/python \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1

# Fail the build in THIS stage (the one that ships) if the interpreter is broken
# — e.g. a dangling venv python symlink. The builder's own import check passes
# even when the final image is broken, because the managed interpreter still
# exists there; this check is what actually guards the shipped image.
#
# Also assert the eval_audit HELM entry-point override actually resolves the way
# helm-run will at run time. load_entry_point_plugins() swallows per-plugin import
# errors (it only warns, then HELM silently falls back to the built-in config), so
# a broken plugin would otherwise ship undetected and resurface as the in-container
# "needs hf_olmo" tokenizer error. Exercise the real discovery path and confirm the
# allenai/olmo-7b alias is repointed to the transformers-native -hf tokenizer.
RUN <<'EOF'
set -eux
python --version
python - <<'PY'
import helm, magnet, huggingface_hub  # noqa: F401  -- interpreter + core imports sound
from importlib.metadata import entry_points
from helm.benchmark.run import load_entry_point_plugins
from helm.benchmark.tokenizer_config_registry import get_tokenizer_config

names = {ep.name for ep in entry_points().select(group="helm")}
assert "eval-audit-tokenizer-overrides" in names, f"helm entry point missing: {sorted(names)}"
assert huggingface_hub.__version__ == "0.36.2", f"shipped hf_hub drifted from pin: {huggingface_hub.__version__}"

load_entry_point_plugins("helm")  # the exact codepath helm-run main() runs
tc = get_tokenizer_config("allenai/olmo-7b")
got = (tc.tokenizer_spec.args or {}).get("pretrained_model_name_or_path") if tc else None
assert got == "allenai/OLMo-7B-hf", f"olmo-7b tokenizer override not applied: {got!r}"
print("final-stage import ok; eval_audit olmo-7b tokenizer override active")
PY
EOF

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

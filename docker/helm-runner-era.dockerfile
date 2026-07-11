# syntax=docker/dockerfile:1.7
#
# eval-audit HELM runner image — ERA (pre-v0.5) variant
# =====================================================
#
# A CPU-only image that replays a single fully-resolved, era-vintage HELM
# ``run_spec.json`` verbatim, via the standalone ``helm_era_shim`` package,
# inside an environment whose HELM harness is pinned to the era's release commit.
#
# WHY a separate dockerfile (not a parameterized modern one): every load-bearing
# layer of ``helm-runner.dockerfile`` is era-hostile — the magnet install
# (imports v0.5+ module paths), the eval_audit plugin + entry-point assertion,
# the ``huggingface_hub==0.36.2`` pin, the olmo tokenizer assertion, and the CUDA
# base. The era image shares none of that. It is:
#   * ubuntu:22.04 (NO CUDA — model inference stays out-of-process on modern vLLM
#     via infer_stack; this container is the measurement instrument only),
#   * uv-managed CPython (era Python, 3.10 for both v0.2.4 and v0.3.0),
#   * era ``crfm-helm[all]`` at the pinned commit + a frozen constraints file that
#     governs instance selection (pandas 2.0.x vs 2.2+ flips instance identity),
#   * the tiny ``helm_era_shim`` package (replay CLI + backported OpenAI-compatible
#     completions client) — never magnet, never eval_audit.
#
# Build context layout (produced by build.sh ERA=<key> mode):
#   <context>/helm/          era crfm-helm source (git archive <helm_git_ref>)
#   <context>/era-shim/      copied from docker/era_shim/
#   <context>/constraints.txt   copied from the era's constraints file
#   <context>/entrypoint.sh  copied from docker/entrypoint.sh (reused verbatim)
#
# Routing to the local vLLM endpoint is purely by-name: the host writes an
# era-schema ``model_deployments.yaml`` registering a deployment under the exact
# official model name, bound to the shim's OpenAI-compatible client. There is no
# ``model_deployment`` field in a pre-v0.5 ``adapter_spec`` to rewrite — era
# replay is verbatim.

ARG BASE_IMAGE=ubuntu:22.04
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.9.27
ARG PYTHON_VERSION=3.10
ARG HELM_EXTRAS=all
# CPU-only torch: install wheels from the PyTorch CPU index so no CUDA runtime is
# pulled into this measurement-only image (open question #6: pin style settled
# empirically to the extra-index form).
ARG TORCH_CPU_INDEX=https://download.pytorch.org/whl/cpu

# ------------------------------------------------------------------------------
# Stage 1: builder — create /opt/venv with era crfm-helm[all] + the era shim.
# ------------------------------------------------------------------------------
FROM ${UV_IMAGE} AS uv
FROM ${BASE_IMAGE} AS builder

ARG PYTHON_VERSION
ARG HELM_EXTRAS
ARG TORCH_CPU_INDEX
ENV DEBIAN_FRONTEND=noninteractive

# git for editable installs; build-essential for any era source wheels (pyext~=0.7
# builds from source under Python 3.10 — open question #2).
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked <<'EOF'
set -eux
apt-get update -q
apt-get install -q -y --no-install-recommends \
    ca-certificates \
    git \
    build-essential
EOF

COPY --from=uv /uv /uvx /usr/local/bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_INSTALL_DIR=/opt/uv/python \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH
# ubuntu:22.04 ships no era Python; uv downloads a managed standalone CPython at a
# path we can copy verbatim into the final stage (same rationale as the modern
# dockerfile).
RUN uv venv /opt/venv --python=${PYTHON_VERSION} --python-preference only-managed --seed

WORKDIR /opt/src

# --- era HELM + full dep tree, CPU-only, with the frozen instance-selection pins.
# The constraints file governs pandas/numpy (instance identity); --extra-index-url
# points torch at the CPU wheel index. --index-strategy unsafe-best-match lets uv
# consider the CPU index alongside PyPI for the torch pin.
COPY helm/ /opt/src/helm/
COPY constraints.txt /opt/src/constraints.txt
RUN --mount=type=cache,target=/root/.cache/uv <<EOF
set -eux
uv pip install \
    --constraint /opt/src/constraints.txt \
    --extra-index-url ${TORCH_CPU_INDEX} \
    --index-strategy unsafe-best-match \
    -e "/opt/src/helm[${HELM_EXTRAS}]"
python - <<'PY'
import helm, pandas, numpy, torch
print('helm', getattr(helm, '__version__', '?'),
      '| pandas', pandas.__version__,
      '| numpy', numpy.__version__,
      '| torch', torch.__version__)
# CPU-only image: a CUDA-enabled torch wheel would silently bloat the image and
# defeat the "measurement instrument only" contract. Assert the CPU build.
assert '+cu' not in torch.__version__, f'expected CPU torch, got {torch.__version__}'
PY
EOF

# --- the era shim (replay CLI + backported OpenAI-compatible client). --no-deps:
# it imports only era ``helm.*`` (already present) + ``requests`` (a helm dep), so
# a full resolve is dead weight and could perturb the frozen environment.
COPY era-shim/ /opt/src/era-shim/
RUN --mount=type=cache,target=/root/.cache/uv <<'EOF'
set -eux
uv pip install -e /opt/src/era-shim --no-deps
python -c "import helm_era_shim; print('helm_era_shim import ok')"
EOF

# ------------------------------------------------------------------------------
# Stage 2: final — slim ubuntu runtime + the prebuilt venv and source.
# ------------------------------------------------------------------------------
FROM ${BASE_IMAGE} AS final

ARG PYTHON_VERSION
ENV DEBIAN_FRONTEND=noninteractive

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked <<'EOF'
set -eux
apt-get update -q
apt-get install -q -y --no-install-recommends \
    ca-certificates \
    git
EOF

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/src /opt/src
COPY --from=builder /opt/uv/python /opt/uv/python

ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    UV_PYTHON_INSTALL_DIR=/opt/uv/python \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1

# Final-stage assertions — this is the stage that ships, so guard the era API
# surface the shim replay depends on, the interpreter, and the frozen pins here
# (the builder's own check can pass while the shipped image is broken).
ARG ERA_KEY=""
ARG ERA_HELM_REF=""
# Finding 6: persist the era identity into the RUNTIME environment. An ARG is
# build-only and does NOT survive `docker run`; helm_era_shim.replay reads these
# for the per-run adapter_manifest.json `replay: {era, helm_git_ref}` block and
# process_context. Without the ENV, every era run recorded replay.era=null /
# helm_git_ref=null (era identity survived only via the image label). The docker
# node already forwards EVAL_AUDIT_ERA_API_KEY; these two ride the image.
ENV EVAL_AUDIT_ERA_KEY=$ERA_KEY \
    EVAL_AUDIT_ERA_HELM_REF=$ERA_HELM_REF
ARG PANDAS_PIN=""
ARG NUMPY_PIN=""
RUN <<'EOF'
set -eux
python --version
python - <<'PY'
import os
import pandas, numpy

# Era API surface the shim + host era yaml rely on. These live at v0.2.4/v0.3.0;
# their absence means the image was built against the wrong helm ref. NB the
# module split the shim depends on: RunSpec is in helm.benchmark.runner, but
# run_benchmarking is in helm.benchmark.run (see replay.py :: _replay_run_spec).
from helm.benchmark.model_deployment_registry import register_model_deployments_from_path  # noqa: F401
from helm.benchmark.runner import RunSpec  # noqa: F401
from helm.benchmark.run import run_benchmarking  # noqa: F401
import helm_era_shim.replay  # noqa: F401  -- the docker node's inner executable
from helm_era_shim.openai_compat_client import OpenAICompatCompletionsClient  # noqa: F401

# Interpreter era.
import sys
assert sys.version_info[:2] == (3, 10), f'expected Python 3.10, got {sys.version_info[:3]}'

# Finding 6: the era identity must be in the RUNTIME env (ENV, not just ARG), so
# replay.py can stamp it into adapter_manifest.json. build.sh always passes
# ERA_KEY/ERA_HELM_REF for an era build, so assert they are present.
assert os.environ.get('EVAL_AUDIT_ERA_KEY'), 'EVAL_AUDIT_ERA_KEY not set in image env (ENV missing? ERA_KEY build-arg empty?)'
assert os.environ.get('EVAL_AUDIT_ERA_HELM_REF'), 'EVAL_AUDIT_ERA_HELM_REF not set in image env'

# Frozen instance-selection pins (spot-check when the build passes them in).
pandas_pin = os.environ.get('PANDAS_PIN') or ''
numpy_pin = os.environ.get('NUMPY_PIN') or ''
if pandas_pin:
    assert pandas.__version__ == pandas_pin, f'pandas pin drift: {pandas.__version__} != {pandas_pin}'
if numpy_pin:
    assert numpy.__version__ == numpy_pin, f'numpy pin drift: {numpy.__version__} != {numpy_pin}'
print('era final-stage import ok; helm_era_shim + era RunSpec/registry resolve')
PY
EOF

# HuggingFace cache lives at a mount point (bind-mounted at run time), NOT baked in.
ENV HF_HOME=/hf-cache
RUN mkdir -p /hf-cache

# Reuse the modern entrypoint verbatim (pure shell: provenance sidecar + chown).
COPY entrypoint.sh /usr/local/bin/eval-audit-entrypoint.sh
RUN chmod +x /usr/local/bin/eval-audit-entrypoint.sh

WORKDIR /opt/src
ENTRYPOINT ["/usr/local/bin/eval-audit-entrypoint.sh"]
CMD ["python", "-m", "helm_era_shim.replay", "--help"]

# --- Provenance labels (populated by build.sh) --------------------------------
ARG EVAL_AUDIT_REF=""
ARG BUILD_FROM="committed"
ARG BASE_IMAGE
ARG UV_IMAGE

LABEL org.opencontainers.image.title="eval-audit HELM runner (era)" \
      org.opencontainers.image.description="CPU-only pinned era HELM harness that replays a pre-v0.5 run_spec.json verbatim via helm_era_shim for reproducibility audits." \
      org.opencontainers.image.vendor="Kitware Inc." \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.revision="$EVAL_AUDIT_REF" \
      org.aiq.eval-audit-ref="$EVAL_AUDIT_REF" \
      org.aiq.helm-ref="$ERA_HELM_REF" \
      org.aiq.era="$ERA_KEY" \
      org.aiq.build-from="$BUILD_FROM" \
      org.aiq.python-version="$PYTHON_VERSION" \
      org.aiq.base-image="$BASE_IMAGE" \
      org.aiq.uv-image="$UV_IMAGE"

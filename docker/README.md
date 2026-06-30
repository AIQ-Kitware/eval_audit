# eval-audit HELM runner image

A self-contained, GPU-ready Docker image that runs a single HELM run-entry via
aiq-magnet's `materialize_helm_run` CLI inside a **pinned, auditable**
environment. The eval_audit kwdagger pipeline can invoke this image (by `sha256`
digest) instead of running `helm-run` in an uncontrolled host venv, removing the
software environment as a hidden variable in reproducibility audits.

This image is intentionally **independent** of the legacy
`uv`/`magnet`/`magnet-heim` Dockerfile chain in
`submodules/aiq-magnet/dockerfiles/`.

## Contents

| File | Purpose |
|---|---|
| `helm-runner.dockerfile` | Multi-stage (CUDA devel builder → CUDA runtime final) image; Python 3.11; `crfm-helm[heim]` + `aiq-magnet` editable-installed into `/opt/venv`. |
| `entrypoint.sh` | Runs the wrapped command; on exit writes `container_provenance.json` and chowns the output dir back to the host user. |
| `build.sh` | Stages pristine (committed) source via `git archive`, resolves provenance shas, builds with BuildKit, optionally pushes. |
| `helm-runner.dockerignore` | Build-context safety net (copied into the staging dir by `build.sh`). |

## Build

```bash
# Pristine build from committed state (default). Tags:
#   eval-audit-helm-runner:<eval-audit-short-sha>  and  :dev
./docker/build.sh

# Fast iteration on uncommitted submodule edits (NON-reproducible, *-dirty tag)
BUILD_FROM=worktree ./docker/build.sh

# Build + push to a registry, then read the immutable digest for manifests
DOCKER_REPO=ghcr.io/aiq-kitware PUSH_IMAGES=1 ./docker/build.sh
docker image inspect --format '{{index .RepoDigests 0}}' \
  ghcr.io/aiq-kitware/eval-audit-helm-runner:<tag>
```

`build.sh` records the eval-audit / helm / aiq-magnet shas as OCI labels
(`org.aiq.eval-audit-ref`, `org.aiq.helm-ref`, `org.aiq.magnet-ref`):

```bash
docker image inspect eval-audit-helm-runner:dev \
  --format '{{json .Config.Labels}}' | python -m json.tool
```

> **Reproducibility note:** `committed` builds reflect only committed state, so
> they *drop* uncommitted submodule edits — commit them first to include them.
> For cross-machine runs, push the image and reference it **by digest** in the
> manifest; the pipeline resolves and pins the digest at schedule time.

## Smoke test

```bash
docker run --rm eval-audit-helm-runner:dev python -c "import helm, magnet; print('ok')"
docker run --rm eval-audit-helm-runner:dev helm-run --help
```

## How the pipeline uses it

Set `container_image` in a manifest (see
[`configs/container_smoke_manifest.yaml`](../configs/container_smoke_manifest.yaml))
and run `eval-audit-run`. For each run-entry the node emits roughly:

```bash
docker run --rm \
  --gpus "device=${CUDA_VISIBLE_DEVICES:-all}" --shm-size=32g \
  -e HOST_UID=$(id -u) -e HOST_GID=$(id -g) \
  -e HF_HOME=/hf-cache -e HF_TOKEN -e HUGGING_FACE_HUB_TOKEN \
  -e EVAL_AUDIT_CONTAINER_IMAGE=<ref> -e EVAL_AUDIT_CONTAINER_DIGEST=<sha256> \
  -v <out_dpath>:<out_dpath> -v <hf_cache_dir>:/hf-cache \
  -w <out_dpath> \
  <repo>@sha256:<digest> \
    python -m magnet.backends.helm.cli.materialize_helm_run --run_entry=... --out_dpath=<out_dpath> ...
```

### HuggingFace cache & token

- The host `hf_cache_dir` is bind-mounted at `HF_HOME=/hf-cache`. Use a
  **dedicated audit cache dir**, not your personal `~/.cache/huggingface`: the
  container runs as root, so downloads land root-owned. A dedicated dir keeps
  ownership consistent and avoids leaving root-owned files inside a personal
  cache.
- **Token delivery is on-disk, not env-forwarded.** At schedule time
  `kwdagger_bridge._prepare_container_execution` writes the resolved
  `$HF_TOKEN`/`$HUGGING_FACE_HUB_TOKEN` into `<hf_cache_dir>/token`; the
  container reads it at `$HF_HOME/token`. The `-e HF_TOKEN` /
  `-e HUGGING_FACE_HUB_TOKEN` flags are a best-effort secondary path — bare
  `-e VAR` only forwards a value present in the **job** shell, which kwdagger's
  fresh tmux pane does not inherit. The token is never baked into the image.

### Permissions

The container runs as root so `/hf-cache` and HELM's `prod_env/cache` writes
succeed. `entrypoint.sh` chowns **only the output dir** back to
`HOST_UID:HOST_GID` on exit, so kwdagger on the host owns the results. The HF
cache stays container-managed (root) by design.

### Provenance

Two records are written:
- **Per node:** `container_provenance.json` inside each output dir (requested
  image, resolved digest, container hostname, GPUs, timestamp) — written by
  `entrypoint.sh`.
- **Per experiment:** `container_provenance.json` in the experiment result root
  (requested → resolved digest, docker version, host) — written by the
  scheduler bridge.

The image's own digest can always be recovered with
`docker image inspect --format '{{index .RepoDigests 0}}' <ref>`.

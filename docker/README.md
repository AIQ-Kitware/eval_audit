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
| `helm-runner.dockerfile` | Multi-stage (CUDA devel builder → CUDA runtime final) image; Python 3.12 (matches eval_audit's `>=3.12` floor + the dev venv); `crfm-helm[all]` + `aiq-magnet` editable-installed into `/opt/venv` with `huggingface_hub==0.36.2` pinned. |
| `helm-runner-era.dockerfile` | **Era (pre-v0.5) variant.** CPU-only `ubuntu:22.04` image; uv-managed **Python 3.10**; era `crfm-helm[all]` at the pinned release commit + a frozen constraints file + the `helm_era_shim` package. No CUDA, no magnet, no eval_audit. |
| `eras.yaml` | Declarative era registry keyed on `(public_track, suite_version)`: `helm_git_ref`, `python_version`, `constraints`, `image_name`, `matches`. Read by `build.sh` **and** `eval_audit/eras.py`. |
| `eras/constraints-helm-*.txt` | Per-era pip constraints governing **instance selection** (pandas 2.0.x vs 2.2+ flips instance identity). Seeded, then frozen at build time (below). |
| `era_shim/` | The `helm_era_shim` package: verbatim-replay CLI (`python -m helm_era_shim.replay`) + backported OpenAI-compatible completions client. Installed into the era image `--no-deps`. |
| `read_eras.py` | Tiny PyYAML query tool over `eras.yaml` for `build.sh` (shell cannot parse YAML). |
| `entrypoint.sh` | Runs the wrapped command; on exit writes `container_provenance.json` and chowns the output dir back to the host user. Reused verbatim by **both** dockerfiles. |
| `build.sh` | Stages pristine (committed) source via `git archive`, resolves provenance shas, builds with BuildKit, optionally pushes. `ERA=<key>` switches to the era path. |
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

## Era (pre-v0.5) images

The audit corpus is ~59% pre-v0.5 (classic-track `v0.2.4` / `v0.3.0` runs). Those
runs cannot be replayed by the modern image (it pins HELM 0.5.x + Python 3.11 and
magnet imports v0.5+ module paths). Each era gets its own **CPU-only** image whose
HELM harness is checked out at the era's release commit, with era Python and era
dep pins — the measurement instrument frozen at the era, with model inference kept
out-of-process on modern vLLM (infer_stack).

```bash
# Build the v0.3.0 era image (tag: helm-runner-era-v0-3-0:<eval-audit-short-sha>)
ERA=helm-v0.3.0 ./docker/build.sh

# Confirm the era stamp + shim + era RunSpec/registry resolve
docker run --rm helm-runner-era-v0-3-0:dev python -m helm_era_shim.replay --help
docker image inspect helm-runner-era-v0-3-0:dev \
  --format '{{index .Config.Labels "org.aiq.era"}}'   # -> helm-v0.3.0
```

The era build stages HELM at `helm_git_ref` (from `eras.yaml`) via `git archive`,
skips magnet + eval_audit, stages `docker/era_shim/` + the era constraints file,
and stamps `org.aiq.era=<key>` (the label the kwdagger bridge checks against the
manifest era at schedule time). `ERA` is rejected with `BUILD_FROM=worktree` — the
era harness must be the committed release commit.

### Era constraints freeze workflow

`docker/eras/constraints-helm-*.txt` start as **seeds** (the tech-report-validated
`pandas==2.0.3`, `numpy==1.23.5` that govern instance selection). To satisfy the
frozen-at-build-time policy, freeze the full environment after a green build:

```bash
ERA=helm-v0.3.0 ./docker/build.sh                       # build with the seed pins
docker run --rm helm-runner-era-v0-3-0:dev \
  python -m pip freeze > docker/eras/constraints-helm-v0.3.0.txt   # capture the full freeze
ERA=helm-v0.3.0 ./docker/build.sh                       # rebuild against the frozen file
```

The final stage spot-checks the `pandas==` / `numpy==` pins from the constraints
file (build args `PANDAS_PIN` / `NUMPY_PIN`), so a pin drift fails the build loudly.
Commit the frozen file; that commit is the era's reproducibility unit.

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

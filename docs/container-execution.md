# Containerized HELM execution

Stage 3 (`eval-audit-run`) normally runs
`python -m magnet.backends.helm.cli.materialize_helm_run` directly in the host
venv of whatever GPU machine kwdagger schedules onto. That makes the software
environment (torch / CUDA / transformers / HELM build) an *uncontrolled*
variable — precisely the kind of drift the audit tries to separate from true
reproducibility failures.

This optional path runs each HELM run-entry inside a **pinned Docker image** and
records *which image (by `sha256` digest) produced each run*, so any change to
the environment is auditable. It is **opt-in**: set `container_image` in a
manifest (or pass `--container-image`). With it unset, the historic bare-python
path is used unchanged.

## TL;DR

```bash
# 1. Build the runner image (pristine, from committed submodule state)
./docker/build.sh                        # tags eval-audit-helm-runner:<sha> and :dev

# 2. Preview the containerized schedule (resolves + pins the image digest)
eval-audit-run configs/container_smoke_manifest.yaml --dry-run

# 3. Execute
eval-audit-run configs/container_smoke_manifest.yaml --run=1
```

For cross-machine runs, push the image and reference it by digest (see
[`docker/README.md`](../docker/README.md)); the digest is pinned at schedule
time either way.

## Manifest fields

| Field | Default | Meaning |
|---|---|---|
| `container_image` | `null` | Tag or digest ref. **Setting it enables the container path.** |
| `container_runtime` | `docker` | Container CLI used to resolve/run (e.g. `docker`). |
| `hf_cache_dir` | `null` | Host dir bind-mounted at `HF_HOME=/hf-cache`. |
| `container_gpus` | `null` | `null` → follow the scheduler's `$CUDA_VISIBLE_DEVICES`; `"none"` → no GPU (CPU); else passed to `--gpus`. |
| `container_shm_size` | `32g` | `--shm-size` for torch dataloaders / NCCL. |
| `container_ipc_host` | `false` | Use `--ipc=host` (unlimited shm) instead of `--shm-size`. |
| `container_mounts` | `[]` | Extra `host:dst[:ro]` bind mounts. |

## How it works

`eval-audit-run` → `prepare_schedule_request`
([kwdagger_bridge.py](../eval_audit/integrations/kwdagger_bridge.py)):

1. **Resolve + pin once.** `resolve_image_digest`
   ([docker_provenance.py](../eval_audit/integrations/docker_provenance.py))
   pulls the image and turns the tag into an immutable `<repo>@sha256:<digest>`
   reference (a tag already pinned with `@sha256:` is kept as-is; a local-only
   image with no registry digest runs by tag with a loud reproducibility
   warning).
2. **Switch pipelines.** The kwdagger `pipeline` factory becomes
   `eval_audit.pipelines.helm_docker_pipeline.helm_single_run_docker_pipeline()`,
   whose `MaterializeHelmRunDockerNode` subclasses the magnet node (so the
   `DONE` sentinel / output-path / job-identity contract is unchanged) and
   renders a `docker run …` command instead of a bare `python -m …`.
3. **Record provenance.** An experiment-level `container_provenance.json`
   (requested → resolved digest, runtime version, host, timestamp) is written to
   the result root; a per-node `container_provenance.json` is written inside each
   node's output dir by the image entrypoint.

The rendered per-run command is roughly:

```bash
docker run --rm \
  --gpus "device=${CUDA_VISIBLE_DEVICES:-all}" --shm-size=32g \
  -e HOST_UID=$(id -u) -e HOST_GID=$(id -g) \
  -e HF_HOME=/hf-cache -e HF_TOKEN -e HUGGING_FACE_HUB_TOKEN \
  -e EVAL_AUDIT_CONTAINER_IMAGE=<ref> -e EVAL_AUDIT_CONTAINER_DIGEST=<sha256> \
  -v <out_dpath>:<out_dpath> -v <hf_cache_dir>:/hf-cache \
  -v <precomputed_root>:<precomputed_root>:ro \
  -w <out_dpath> \
  <repo>@sha256:<digest> \
    python -m magnet.backends.helm.cli.materialize_helm_run --run_entry=... --out_dpath=<out_dpath> ...
```

### Why these choices

- **Same absolute paths in/out.** `out_dpath` is bind-mounted at the identical
  path, so kwdagger's DONE check and any reuse symlinks resolve on the host.
  `precomputed_root` / `model_deployments_fpath` / local HF model dirs are
  mounted read-only at their same host paths.
- **GPU.** cmd_queue's tmux/serial backend sets `CUDA_VISIBLE_DEVICES` per
  worker; `--gpus "device=$CUDA_VISIBLE_DEVICES"` exposes exactly the assigned
  GPU(s).
- **Exit codes.** `docker run` returns the container's exit code and the inner
  CLI writes DONE last, so failures propagate and `skip_existing` still works.

## HuggingFace cache & token

- Use a **dedicated audit cache dir** for `hf_cache_dir` (not your personal
  `~/.cache/huggingface`). The container runs as root, so anything it downloads
  is root-owned; a dedicated dir keeps ownership consistent and avoids leaving
  root-owned files inside a personal cache. The dir is created (host-owned) at
  schedule time if missing.
- The token is forwarded via `-e HF_TOKEN` / `-e HUGGING_FACE_HUB_TOKEN` from
  the worker environment (never baked into the image); `${HF_HOME}/token` is the
  on-disk fallback.

## Permissions

The container runs as root so `/hf-cache` and HELM's `prod_env/cache` writes
succeed. The image entrypoint chowns **only the output dir** back to
`HOST_UID:HOST_GID` on exit, so kwdagger on the host owns the results (it must,
to read DONE, create symlinks, and rsync). The HF cache stays container-managed
(root) by design.

## Auditing which image ran

```bash
# Experiment-level record
cat <result_root>/container_provenance.json

# Per-run record (also captures container hostname + GPUs)
cat <result_root>/.../materialize_helm_run/<hash>/container_provenance.json

# The live image's digest
docker image inspect --format '{{index .RepoDigests 0}}' <ref>
```

## Limitations / follow-ups

- A no-GPU host cannot run `--gpus all`; for CPU smoke tests set
  `container_gpus: "none"` (or build/run the image directly).
- Surfacing `container_provenance.json` in the Stage 4 index (to flag digest
  drift across an experiment) is a natural next step, not yet built.
- Rootless `podman` / userns is not implemented; the schema reserves
  `container_runtime` for it.

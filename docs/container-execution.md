# Containerized HELM execution

Every HELM run-entry executes inside a **pinned Docker image**, and *which image
(by `sha256` digest) produced each run* is recorded, so any change to the
software environment is auditable.

This is **mandatory, not opt-in**: set `container_image` in a manifest (or pass
`--container-image`). A manifest without one is refused at schedule time
(`build_schedule_params` in `eval_audit/integrations/kwdagger_bridge.py`
raises), because the bare host-venv path — running
`python -m magnet.backends.helm.cli.materialize_helm_run` directly in the venv
of whatever GPU machine kwdagger scheduled onto — made the software environment
(torch / CUDA / transformers / HELM build) an *uncontrolled* variable, precisely
the kind of drift the audit exists to separate from true reproducibility
failures. That path has been removed.

Runs produced before the removal have no `container_provenance.json`; their
environment is not recoverable from the artifacts and they should not be cited
as environment-controlled.

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
| `container_image` | `null` | Tag or digest ref. **Required** — scheduling fails without it. |
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
- **Token delivery is on-disk, not env-forwarded.** At schedule time the bridge
  (`kwdagger_bridge._prepare_container_execution`) writes the resolved token
  (`$HF_TOKEN`/`$HUGGING_FACE_HUB_TOKEN` from the env that ran `eval-audit-run`)
  into the cache dir as `<hf_cache_dir>/token`; the container reads it at
  `$HF_HOME/token` (HF_HOME=/hf-cache). The docker node *also* emits
  `-e HF_TOKEN` / `-e HUGGING_FACE_HUB_TOKEN`, but that bare `-e VAR` form only
  forwards a value already set in the **job** shell — and kwdagger runs each job
  in a fresh tmux pane that does not inherit the scheduling shell's ad-hoc
  exports, so the env path can't be relied on. The token is never baked into the
  image. If you'd rather not depend on the env, drop the token in the cache dir
  yourself (`HF_HOME=<hf_cache_dir> huggingface-cli login`).

## Bundle secrets: the model_deployments YAML holds a live key

The generated `model_deployments.<hash>.yaml` in an inference bundle embeds the
resolved `LITELLM_MASTER_KEY` **in plaintext** under
`client_spec.args.api_key`. HELM's `OpenAIClient` reads that arg literally
(`OpenAI(api_key=...)`); this HELM vendoring has no `${ENV}` indirection, so the
live gateway key must sit on disk for the runner container to consume it. The
adapter tightens the file to `0o600` (owner-only) immediately after writing, but
treat the whole bundle as secret-bearing: do not commit it, do not copy it to a
world-readable share, and rotate the master key if a bundle leaks.

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

## Era (pre-v0.5) images

~59% of the audit corpus is pre-v0.5 (classic-track `v0.2.4` / `v0.3.0` runs).
The modern image cannot replay them (it pins HELM 0.5.x + Python 3.11, and
magnet's from-spec CLI imports v0.5+ module paths). Each era instead gets its own
**CPU-only** image whose HELM harness is checked out at the era's release commit,
with era Python + era dep pins — the measurement instrument frozen at the era,
with model inference kept out-of-process on modern vLLM (infer-stack lease). See
`docs/planning/era-pinned-helm-containers-plan.md` and `docker/README.md`.

```bash
# 1. Build the era image (declared in docker/eras.yaml)
ERA=helm-v0.3.0 ./docker/build.sh

# 2. Export an ERA bundle (era-schema model_deployments.yaml + exact-path sources)
python -m eval_audit.integrations.infer_stack export-benchmark-bundle \
  --preset <era-preset> --era helm-v0.3.0 --freeze-rel-paths \
  --bundle-root <bundle-root> --precomputed-root /data/crfm-helm-public

# 3. Run — the bundle export above already wrote <bundle-root>/{smoke,full}_manifest.yaml
#    with the era stamped on it, so there is no make-manifest step. The bridge
#    selects the era pipeline (helm_era_shim.replay) and guards the image's
#    org.aiq.era label against the manifest era at schedule time.
eval-audit-run <bundle-root>/full_manifest.yaml --lease --run=1 \
  --container-image helm-runner-era-v0-3-0:dev
```

`eval-audit-make-manifest` is **not** part of this flow: `export-benchmark-bundle`
is the manifest producer for every from-spec and era runbook. See
[`reproduce/classic_together_combined/_lib.sh`](../reproduce/classic_together_combined/_lib.sh)
(`run_one_grid`) for the invocation these snippets are abridged from.

Key invariants:

- **One manifest = one era = one image = one measurement instrument.** A mixed-era
  source set is a hard error, raised in `eval_audit/eras.py` — at bundle/manifest
  build time, and again at schedule time when `kwdagger_bridge` resolves the
  manifest's `era` key against `docker/eras.yaml` and checks the image label.
- **Verbatim replay.** A pre-v0.5 `adapter_spec` has no `model_deployment` field,
  so nothing is rewritten — routing to vLLM is purely by-name (the era shim
  registers a deployment under the exact official model name). The materializer
  **refuses** to insert a `model_deployment` field into an era spec.
- **`same_deployment` resolves `unknown`** for era pairs (both sides lack the
  field) — the correct behavior, not a bug; no analysis-stage changes.
- **Provenance.** The era key + the image's `org.aiq.era` label are recorded in
  `container_provenance.json`; the era value also rides the manifest.

## Limitations / follow-ups

- A no-GPU host cannot run `--gpus all`; for CPU smoke tests set
  `container_gpus: "none"` (or build/run the image directly). The era images are
  CPU-only by design (model inference is out-of-process on vLLM).
- Surfacing `container_provenance.json` in the Stage 4 index (to flag digest
  drift across an experiment) is a natural next step, not yet built.
- Rootless `podman` / userns is not implemented; the schema reserves
  `container_runtime` for it.

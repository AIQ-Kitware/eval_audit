# Hand-off: wire the new leasing features into eval_audit

**Status:** ready to start · **Date:** 2026-06-23

This is the continuation prompt for the **next phase**: integrate the now-landed
infer-stack / kwdagger / cmd_queue features into the **main `eval_audit`
pipeline** so HELM reproduction runs at high parallelism — kwdagger fans out
client jobs, each job acquires its model's GPU lease (queueing if the fleet is
busy) and releases it after, and the LiteLLM gateway never blips.

Read the design of record first:
[`infer-stack-kwdagger-integration.md`](infer-stack-kwdagger-integration.md)
(esp. §2 job lifecycle, §4 queue-and-wait, §5 GPU ownership, §6 no-blip, §7
determinism recording, §9 tmux design, §13 eval_audit responsibilities). The
session narrative is the latest entry in `dev/journals/claude.md`.

---

## The goal of this phase

Turn the current "bash serves one model at a time, then runs its HELM jobs"
flow (`reproduce/olmo_models/10_run_smoke_grid.sh`,
`dev/e2e-tests/10_run_smoke_grid.sh`) into a **kwdagger fan-out** where each HELM
run is a `ProcessNode` that brackets itself with an infer-stack lease. The
submodule features this needs are all built (below); this phase wires
`eval_audit` to emit and use them.

---

## What's already landed (consume these — do NOT rebuild)

All on feature branches in the submodules (tested; nothing pushed). The
`eval_audit` superproject gitlinks still point at the old commits — you'll need
these branches checked out / pinned.

| Feature | Repo / branch | What you use |
|---|---|---|
| Job `setup`/`teardown` lifecycle | cmd_queue `feature/job-setup-teardown` | `BashJob`/`SlurmJob` accept `setup=` (gating precondition) and `teardown=` (always-run, signal-safe). |
| `ProcessNode.setup`/`.teardown` | kwdagger `feature/processnode-setup-teardown` | Set `node.setup` / `node.teardown` (shell strings); they thread through `submit_jobs` → `queue.submit`. **Requires the cmd_queue branch.** |
| Admission queue | infer-stack `feature/leasing-pipeline-lifecycle` | `infer-stack acquire/run --queue` (wait for a GPU instead of fail-fast). |
| `infer-stack gc` | infer-stack `feature/leasing-pipeline-lifecycle` | sweep TTL-expired (leaked) leases + free GPUs — final/periodic backstop. |
| No-blip gateway | infer-stack `feature/litellm-no-blip` | static superset LiteLLM config from the catalog; gateway not recreated on model churn. **Rendering-validated only — see gate-check.** |

Note: the two infer-stack branches both touch `commands_leasing.py`/`CHANGELOG.md`
in different spots — land `feature/leasing-pipeline-lifecycle` first, then rebase
`feature/litellm-no-blip` (trivial conflict). Install the submodules editable so
the CLI on PATH matches the vendored source (`uv pip install -e submodules/...`).

---

## The integration design (target shape)

Per HELM run, the kwdagger `ProcessNode` should look like:

```
node.setup    = infer-stack acquire <endpoint> --ttl <T> --queue \
                  --env-file <node_dpath>/lease.env --catalog <catalog.yaml>
node.command  = <the HELM run>, pointed at the gateway http://127.0.0.1:14042/v1
node.teardown = infer-stack release --env-file <node_dpath>/lease.env
```

Rendered by cmd_queue as `setup-gates-main; trap teardown; main` (the gateway is
hit only after acquire+wait confirms readiness). Key rules from the design:

1. **tmux mode: request NO GPU for the client job.** infer-stack owns all GPUs;
   the HELM client is a thin HTTP client. Don't use cmd_queue's round-robin
   `CUDA_VISIBLE_DEVICES`. In the docker pipeline that means `container_gpus:
   none` (or equivalent) for the client container. (§5)
2. **`--queue` on acquire** so an oversubscribed fan-out waits instead of
   failing. (§4)
3. **Finite `--ttl`** on every lease (must exceed worst-case model-load + run),
   so a hard-killed job's leak is reclaimed by the queue's sweep / `gc`. (§8)
4. **`reclaim: stop`** on the pipeline endpoints in the shipped `catalog.yaml`
   (former "I4") — otherwise a released keep-warm model holds its GPU and the
   admission queue stalls waiting for it. (§8)
5. **HELM runs must be DAG siblings, not chained**, so cmd_queue's `afterok`-only
   dependencies don't cascade-skip surviving runs when one fails. (§13)
6. **Record the concurrency context per run** for the determinism study: snapshot
   the deployment `demand` (`infer-stack leases --json`) + vLLM determinism flags
   into the run metadata the reproducibility analysis consumes. (§7)
7. **A final `infer-stack gc`** step (and/or a cron) to reclaim any leaked leases
   at the end of a fan-out. (§8)

The per-job lease env-file lives under the node's working dir
(`<node_dpath>/lease.env`) so each job has its own; setup writes it, teardown
reads it. Use kwdagger's node-dpath template for the path.

---

## Files to change in eval_audit (start here)

- `eval_audit/integrations/kwdagger_bridge.py` — `build_schedule_params()` /
  `kwdagger_schedule_argv()`. Where the manifest becomes the kwdagger matrix and
  the pipeline factory is chosen. Likely where (or near where) `setup`/`teardown`
  get attached per run, and where the GPU request is dropped for tmux.
- `eval_audit/pipelines/helm_docker_pipeline.py` — the `ProcessNode` for a HELM
  run + the `--gpus` exposure. Set `container_gpus: none` (client needs no GPU)
  and populate `node.setup`/`node.teardown` (parameterized by endpoint +
  `<node_dpath>/lease.env`).
- `eval_audit/integrations/infer_stack/adapter.py` *(open in the IDE)* —
  `PRESET_CONFIGS`, `resolve_serving_facts`, `materialize_benchmark_bundle`. The
  source of truth for endpoint name ↔ HELM model/served name ↔ gateway base_url.
  The setup/teardown endpoint name must match the catalog endpoint the gateway
  routes (the C-3 "name chain" hazard from the migration plan — assert it).
- `eval_audit/workflows/run_from_manifest.py`, `eval_audit/cli/run.py` — Stage-3
  entry; thread any new options (e.g. per-endpoint TTL, `--queue`).
- The shipped catalogs `dev/e2e-tests/config/infer_stack/catalog.yaml` and
  `reproduce/olmo_models/config/infer_stack/catalog.yaml` — set `reclaim: stop`.
- The grid runners `reproduce/olmo_models/10_run_smoke_grid.sh` and
  `dev/e2e-tests/10_run_smoke_grid.sh` — collapse the per-model
  `release/acquire/wait` bash loop into a single fan-out (one acquire of the
  gateway/catalog, kwdagger schedules all runs, each self-acquires its model);
  add the final `gc`.

---

## Open questions to resolve while integrating

1. **Injection point for `setup`/`teardown`.** Does the magnet/eval_audit HELM
   pipeline factory construct the `ProcessNode` somewhere you can set
   `.setup`/`.teardown`, or do they need to flow via the kwdagger matrix /
   `configure`? Find the cleanest seam (likely the docker pipeline node def).
2. **Endpoint name resolution.** How does a manifest row map to the catalog
   endpoint name `acquire` needs? (Via the preset's `model_deployment_name` /
   `profile` field — verify against `adapter.py`.)
3. **One acquire per run vs per model-group.** Per-run `acquire --queue`
   (ref-counting coalesces same-model runs) is simplest and matches the design.
   Confirm ref-counting behaves under many concurrent same-model acquires.
4. **Worker-pool sizing** for tmux (≈ GPU count to 2× GPU count) — a schedule
   knob, not infra.

---

## Environment & test quirks (save yourself the rediscovery)

- venv: `.venv` (has cmd_queue + kwdagger editable). **infer_stack is NOT in it** —
  run its tests from the submodule with `PYTHONPATH=.`:
  `cd submodules/infer_stack && PYTHONPATH=. ../../.venv/bin/python -m pytest tests/ -q -p no:cacheprovider -o addopts=""`
- pytest needs `-p no:cacheprovider -o addopts=""` everywhere (repo `addopts`
  reference `--xdoctest`, which isn't installed in `.venv`).
- cmd_queue has **2 pre-existing failures** (`test_cli.py`: `cmd_queue: command
  not found` — CLI not on PATH); unrelated to this work.
- `python`/`pip` aren't on PATH; use `./.venv/bin/python`.

## Gate-checks that need a docker/GPU box (can't be done in this env)

- **No-blip behavior (I2):** confirm `docker compose up` does NOT recreate the
  `litellm` container on a model switch, and that a route whose upstream comes up
  late recovers from cooldown. Until verified, treat no-blip as rendering-only.
- **End-to-end tmux fan-out:** the e2e phi-2 and olmo ≥2-model grids with the new
  setup/teardown + `--queue`: no oversubscription failures, no leaked GPUs after
  induced job crashes (kill a job mid-run; confirm `gc`/next-acquire reclaims).
- **Serial-vs-concurrent logprob fidelity (§7):** the determinism gate.

## Repo-state gotchas (left for the human, don't fix blindly)

- `eval_audit` has an **unresolved merge conflict** (`DU
  reproduce/olmo_models/config/infer_stack/config.yaml`) from the in-flight
  CLI-migration merge. It blocks normal `git commit`; use path-scoped
  `git commit <file>` to commit around it. **Do not resolve it** — it's the
  user's CLI-migration decision.
- The **submodule pointer bumps** (gitlinks for cmd_queue/kwdagger/infer_stack)
  are intentionally NOT committed — the recorded gitlinks diverge from the
  in-flight submodule state; the user pins them deliberately once the merge
  settles. You'll likely need to bump them (to the feature-branch commits) for an
  integrated run, but coordinate with the user.
- Commit only when asked; branch before committing if on a default branch; use
  `git merge --no-ff` for merges (never fast-forward).

## Reference — landed commits

- cmd_queue `feature/job-setup-teardown` (off main)
- kwdagger `feature/processnode-setup-teardown` (off main; needs cmd_queue)
- infer-stack `feature/leasing-pipeline-lifecycle` (off `dev/leasing-controller`) — queue-and-wait + gc
- infer-stack `feature/litellm-no-blip` (off `dev/leasing-controller`) — no-blip
- eval_audit `infer-stack-cli-api-migration`: the design doc + journal (committed
  path-scoped around the conflict)

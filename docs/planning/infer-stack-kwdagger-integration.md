# infer-stack ↔ kwdagger integration plan (high-throughput HELM reproduction)

**Status:** in progress (C1+K1 landed) · **Date:** 2026-06-23 · **Branch:** `infer-stack-cli-api-migration`

## Implementation status

All submodule infra is **landed on feature branches** (tested to the limit of a
no-GPU/no-docker env; nothing pushed). The next phase is wiring eval_audit to use
them — see [`infer-stack-kwdagger-eval-audit-handoff.md`](infer-stack-kwdagger-eval-audit-handoff.md).

| Item | Branch | State | Notes |
|---|---|---|---|
| **C1** cmd_queue `setup`/`teardown` | cmd_queue `feature/job-setup-teardown` | ✅ landed | `BashJob` (serial/tmux) + `SlurmJob`. Validated: success / main-fail / setup-fail / dep-skip / **SIGTERM** (process-group, real generated script); main exit code authoritative; slurm `--wrap` parity. |
| **K1** kwdagger `ProcessNode.setup`/`.teardown` | kwdagger `feature/processnode-setup-teardown` | ✅ landed | Threaded `submit_jobs` → `queue.submit`. New boundary test; suite (18) green; e2e demo node ran with `ACQUIRE`/`RELEASE` bracket. |
| **I1** acquire admission queue (`--queue`) | infer-stack `feature/leasing-pipeline-lifecycle` | ✅ landed | `Controller.acquire(wait_for_placement=)` + `acquire`/`run --queue`. 3 fake-budget tests (queue-until-freed / timeout+rollback / fail-fast default). |
| **I3** `infer-stack gc` | infer-stack `feature/leasing-pipeline-lifecycle` | ✅ landed | Sweep TTL-expired leases + reconcile; `--evict` for keep-warm. 2 tests. |
| **I2** static superset litellm (no-blip) | infer-stack `feature/litellm-no-blip` | ✅ landed (rendering-validated) | Deterministic service names + catalog superset; config/spec invariant across model set. **Behavioral no-recreate + cooldown-recovery need a docker/GPU box.** 2 render tests. |
| Cache-gates-setup (don't acquire for a done job) | — | deferred | Not needed on the `--skip_existing=1` default path (done nodes aren't submitted). Only the `cache=True, skip_existing=False` mode wastes a serve cycle; a `skip_if_exists` on the cmd_queue job is the clean fix if ever used. |
| ~~I4 reclaim:stop~~ | — | **dropped** | Was a one-line eval_audit catalog config, not infra — folded into the eval_audit wiring (§13). |
| ~~I5 run SIGTERM handler~~ | — | **dropped** | Optional; the kwdagger path uses the cmd_queue `teardown` trap (already SIGTERM-safe) and TTL + `gc` cover direct-`run` leaks. |



How to drive high-parallelism, high-throughput HELM reproduction by letting
**kwdagger** (via `cmd_queue`) fan out HELM client jobs while **infer-stack**
owns GPU allocation through its leasing (`acquire`/`release`) mechanism. This
document fixes the *shape* of the three infrastructure components
(infer-stack, cmd_queue, kwdagger) **before** wiring `eval_audit` to them. The
`eval_audit` interface (Stage 2/3 manifest → schedule) is deliberately left for
a later pass.

This is a companion to
[`infer-stack-cli-api-migration.md`](infer-stack-cli-api-migration.md) (the
profile→catalog/leasing CLI migration). That plan made the leasing CLI usable;
this one makes it *schedulable*.

---

## 0. Scope & target

- **Single node, 4 GPUs.** No multi-node now. Multi-node is sketched in §11 only
  to ensure we don't make decisions that preclude it; it is **not** built.
- **tmux backend is the priority.** "Good enough, never deadlocks" beats
  "optimal." A job needing 2 GPUs may wait a while for GPUs to free; that is
  acceptable as long as it eventually runs (and far faster than serial).
- **slurm backend is a stretch** — design it on paper, keep the door open
  (§10), build later.
- **Infra-first.** Get infer-stack / cmd_queue / kwdagger into the right shape.
  `eval_audit`'s manifest→schedule wiring (and run-independence, §13) comes
  after and is out of scope here.

---

## 1. Decisions locked (2026-06-23)

1. **The per-job lifecycle is a first-class job setup/teardown phase** — added to
   cmd_queue (and surfaced on kwdagger `ProcessNode`s) as a *general* feature:
   `setup` (a gating precondition) + `teardown` (always-run cleanup, trap-based).
   infer-stack `acquire`/`release` are the canonical *users* of it, not a special
   case; cmd_queue/kwdagger stay infer-stack-agnostic. `infer-stack run -- <cmd>`
   remains a convenience wrapper for non-kwdagger callers. **No separate
   acquire/release DAG nodes.** (Rationale + design: §2.)
2. **infer-stack gets a queue-and-wait (blocking) acquire.** When GPUs are
   busy, acquire *waits* instead of failing. This is now a required infra
   feature, not optional. (§4.)
3. **In tmux mode, kwdagger/cmd_queue do NOT manage GPUs.** Drop the GPU request
   entirely; do not use cmd_queue's round-robin `CUDA_VISIBLE_DEVICES`.
   infer-stack is the sole GPU allocator. (§5, §9.)
4. **LiteLLM must not blip when the served-model set changes.** Move to a static
   superset route table so the gateway container is never recreated. (§6.)
5. **Every pipeline lease has a finite TTL.** This makes the queue-and-wait loop
   double as leak recovery (a SIGKILLed job's lease expires and is swept on the
   next acquire's reconcile). Infinite leases (`serve`) are not used for
   pipeline jobs. (§4, §8.)
6. **GPU non-determinism under concurrent batching is recorded, not
   eliminated.** Treat it as a measured variable in the reproducibility study.
   (§7.)
7. **slurm (later) shadows slurm's GPU grant.** infer-stack places only on the
   GPUs slurm assigned (via `INFER_STACK_ALLOWED_GPUS`). The docker-vs-cgroup
   isolation gap is a non-issue *because* infer-stack respects the grant rather
   than relying on cgroup enforcement. (§10.)

---

## 2. The core model: first-class job setup/teardown

Modeling acquire and release as **separate DAG nodes** is both fragile and, for
this resource, **incorrect**:

- **cmd_queue dependencies are `afterok`-only** (hardcoded —
  [`backends/slurm.py:352-356`](../../submodules/cmd_queue/cmd_queue/backends/slurm.py);
  serial/tmux gate on a `pass_fpath` success marker —
  [`backends/serial.py:194-205`](../../submodules/cmd_queue/cmd_queue/backends/serial.py)).
  A *release node* depending on the run is **skipped if the run fails** — the
  exact leak we must avoid.
- **Co-location is not guaranteed.** A separate release node may be scheduled on
  a different tmux worker (or, later, a different slurm node) than the job that
  acquired the lease. But the lease lives in a **node-local ledger** and the
  gateway is **localhost** — release *must* run where acquire ran. A separate
  node cannot promise that; a job *phase* does, by construction.
- **An acquire is a precondition, not a unit of work** — no artifact, must never
  be cached/"done". kwdagger's skip machinery is output-existence-based, so a
  node is the wrong shape.

**Resolution — make setup/teardown a first-class job lifecycle in cmd_queue, and
surface it on kwdagger nodes.** This is the job-level `try/finally` / RAII /
pytest-fixture pattern, and it serves *any* bracketed resource (GPU lease,
license token, scratch dir, mount, file lock) — a genuine general improvement,
not an infer-stack hack. infer-stack `acquire`/`release` are simply the first
*user*; cmd_queue/kwdagger learn nothing about infer-stack (both fields are
arbitrary shell, supplied by the eval_audit layer).

**The hard half already exists.** cmd_queue's `preamble` already *gates* the main
command ([`backends/serial.py:245-255`](../../submodules/cmd_queue/cmd_queue/backends/serial.py)):

```bash
{ <preamble> && PREAMBLE_OK=1; } || PREAMBLE_OK=0
if [[ "$PREAMBLE_OK" == 1 ]]; then  <main command>  else  RETURN_CODE=3  fi
```

So the **precondition** is essentially done; it just isn't named `setup` or
exposed through kwdagger. The only genuinely new primitive is an **always-run
`teardown`**, via `trap 'teardown' EXIT INT TERM`, with the main command's exit
code preserved as authoritative (serial already captures `RETURN_CODE=$?` at
[`serial.py:288`](../../submodules/cmd_queue/cmd_queue/backends/serial.py)).

**Composition with kwdagger's cache guard is exactly right.** kwdagger renders
`test -e <out> || <cmd>` ([`pipeline.py:2184-2205`](../../submodules/kwdagger/kwdagger/pipeline.py)). We render:

```bash
test -e DONE || { <setup gates main> ; trap 'teardown' EXIT INT TERM ; main ; }
```

- **Job already done:** `test -e DONE` short-circuits the whole brace group →
  setup never runs → **no GPU acquired for a skipped job.** Correct.
- **Job runs:** teardown is armed only inside the branch → fires on success,
  failure, and SIGTERM. Correct.

This enforces "acquire is a precondition, never a cached artifact" *in the
framework*, not by convention.

**Per-backend correctness (all verified — no blockers):**

| Backend | Mechanism | Notes |
|---|---|---|
| serial | preamble gates; `RETURN_CODE` captured; no conflicting trap | clean insertion after [serial.py:288](../../submodules/cmd_queue/cmd_queue/backends/serial.py) |
| tmux | reuses serial rendering per worker `SerialQueue`; `kill-session` → **SIGTERM** → trap fires | |
| slurm | `--wrap 'setup && main'`; `--signal=B:TERM@<sec>` (via `_sbatch_kvargs`) gives the trap grace before SIGKILL | normalize gating to match serial's `RETURN_CODE=3` path |

**The one limit (true of any design).** SIGKILL and power-loss cannot be covered
by *any* in-band mechanism — not traps, not `afterany` (the controller can die
too). The resource manager's **TTL + sweep is the only real backstop** (§8), and
that's universal. Layering: setup/teardown handles graceful exit + SIGTERM;
infer-stack TTL handles SIGKILL/crash.

**`afterany` is a separate, orthogonal improvement.** cmd_queue's `afterok`-only
dependency has a clean extension point ([slurm.py:352-356](../../submodules/cmd_queue/cmd_queue/backends/slurm.py); serial/tmux would
extend their marker check from "pass" to "done"). Worth adding — but its purpose
is *cross-job* "run regardless" (e.g. a final report), **not** resource
bracketing (the co-location problem rules it out here). Don't conflate the two.

**Design decisions to nail (correctness):**
- **setup fails →** main skipped, job marked failed (`RETURN_CODE=3`), dependents
  skip. Arm/run teardown **only if setup succeeded** (don't release what you
  never acquired).
- **teardown fails →** logged; **main's exit code stays authoritative** (a
  release hiccup must not cascade-skip consumers of the run's output). Optional
  `teardown_strict` opt-in later.
- **lease handoff:** setup writes the lease to a per-job env-file under the
  node's `info_dpath` (`infer-stack acquire --env-file f`); teardown reads it
  (`infer-stack release --env-file f`).

`infer-stack run --endpoint X --ttl T -- <cmd>` (acquire → run → release in a
`finally`, [`commands_leasing.py:1097-1101`](../../submodules/infer_stack/infer_stack/cli/commands_leasing.py))
remains as a **convenience wrapper for non-kwdagger callers**; inside kwdagger we
use the native setup/teardown so the lifecycle is visible in logs and the DAG.

---

## 3. Component responsibilities (the contract)

| Concern | Owner |
|---|---|
| Which GPU(s) a model lands on; whole-GPU placement; tp-gang reservation | **infer-stack** (placement.py) |
| Admission control / "wait until a GPU is free" | **infer-stack** (queue-and-wait, §4) |
| Reference-counted model sharing across concurrent jobs | **infer-stack** (ledger demand) |
| Lease lifecycle (acquire precondition, release cleanup, TTL) | **infer-stack** (`run` wrapper) |
| Fan-out / DAG topology / job dependencies / skip-existing | **kwdagger** + **cmd_queue** |
| Bounding how many jobs are in flight (worker pool / slurm slots) | **cmd_queue** |
| Per-node GPU *grant* (slurm only) | **slurm**, shadowed by infer-stack (§10) |

kwdagger stays a pure DAG/fan-out engine. It never reasons about GPUs in tmux
mode. All GPU intelligence is in infer-stack, where it already lives.

---

## 4. Required infra feature #1 — queue-and-wait (blocking acquire)

**Today acquire is fail-fast.** [`controller.py:237-250`](../../submodules/infer_stack/infer_stack/leasing/controller.py)
computes the `unplaced` set and immediately `raise PlacementError(...)`. With
many fan-out jobs competing for 4 GPUs, that means failures, not queueing.

**Change:** insert a wait loop at that decision point (in `Controller.acquire`,
guarded by a new `wait_for_placement=True, placement_timeout=...`, so both the
`acquire` and `run` CLIs inherit it). Semantics:

1. Compute `unplaced` as today.
2. **Satisfiability check** against the *full* inventory (ignoring current
   occupancy): if the request can never fit (e.g. tp=2 on a 1-GPU allow-set),
   **fail immediately** — don't wait forever.
3. Otherwise, if temporarily unplaceable: `sleep(interval)` → `reconcile()`
   (which calls `sweep()` — see below) → replan. Loop until placeable or
   `placement_timeout`.
4. On timeout, raise `PlacementError` as today.

**Two design properties to get right (this is the "handle resources correctly"
part):**

- **Leak self-healing comes for free.** `reconcile()` calls `ledger.sweep()` at
  its start ([`controller.py:128`](../../submodules/infer_stack/infer_stack/leasing/controller.py)),
  and `sweep()` expires TTL-past leases and idles their deployments
  ([`ledger.py:183-200`](../../submodules/infer_stack/infer_stack/leasing/ledger.py)).
  So a waiting acquire's retry loop *reclaims* a crashed job's leaked-but-expired
  lease and then proceeds. **This only works if leaked leases have a finite
  TTL** (Decision 5) — an infinite lease never expires and would block the queue
  forever.
- **Avoid large-request starvation.** With a stream of 1-GPU requests, a waiting
  2-GPU request can starve (small jobs keep grabbing the single freed GPU). The
  queue should be **FIFO with reservation** (hold a freed GPU for the
  head-of-line larger request) rather than greedy. At 4 GPUs with rare tp=2
  jobs this is low-risk, but build the reservation in so it's correct, not
  lucky.

This single feature is what lets us drop GPU management from cmd_queue (§5):
admission control moves entirely into infer-stack.

---

## 5. GPU ownership: drop the GPU request in tmux mode

cmd_queue's tmux backend assigns one `CUDA_VISIBLE_DEVICES` per worker,
round-robin ([`backends/tmux.py:271-280`](../../submodules/cmd_queue/cmd_queue/backends/tmux.py)).
In the gateway topology the HELM client process needs **no** GPU — the GPUs
belong to infer-stack's vLLM containers. Two allocators fighting over the same
GPU indices is pointless and confusing.

**Decision:** in tmux mode, submit jobs with **no GPU request** (no `gpus=`,
no device list). The tmux worker pool then bounds only *client concurrency*, not
GPU placement. infer-stack's queue-and-wait does GPU admission.

- Worker-pool size: ≈ `GPU_count` to `2 × GPU_count` (so a small ready-queue
  exists without hundreds of blocked pollers). At 4 GPUs, 4–8 is fine.
- This is a knob for the (later) `eval_audit` schedule layer, not an infra
  change. cmd_queue already supports "no GPU request."

---

## 6. Required infra feature #2 — LiteLLM no-blip

**Today the gateway is recreated on every model-set change** — by design: the
litellm config is regenerated, its SHA256 is stamped onto the
`infer-stack.config-hash` label, and `docker compose up -d --remove-orphans`
recreates the container so it re-reads routes
([`compose.py:267-309, 547-571, 935`](../../submodules/infer_stack/infer_stack/leasing/compose.py)).
Only litellm blips (~1–3s); the vLLM containers stay resident.

**Change — static superset route table** (infer-stack's own
`dev/leasing-followups.md` Option #1): render litellm with routes to **all**
catalog endpoints using **deterministic upstream addresses** (keyed by
served-model/endpoint name, not the random deployment id). The config then never
changes as models come and go → no hash change → no recreate. vLLM containers
appear/disappear behind stable names; litellm keeps running.

This is safe for our orchestrated flow: we only route a request to a model
*after* acquire+wait confirms readiness, so advertising not-yet-up routes is
harmless.

Code touch is contained to `compose.py` (render full catalog; deterministic
vLLM service naming/aliases; drop the litellm config-hash recreate).

**Gate-check before relying on it:** LiteLLM router **cooldown/health
recovery**. A statically-configured route whose upstream was down at gateway
start must *recover* (leave cooldown) when the vLLM container later comes up at
the same address. Verify via `background_health_checks` / cooldown settings. Do
**not** pursue the DB-backed admin-API alternative (`/model/new`+postgres) —
`leasing-followups.md` records it as "tried before, hit issues."

---

## 7. Required infra feature #3 — record concurrent-batching non-determinism

vLLM continuous batching means a request's logits can depend on what else is in
the batch, so the *same* prompt under concurrency can yield different
tokens/logprobs than served serially. At temperature 0 this is usually stable,
but vLLM has documented batch-dependent numerics (chunked prefill, certain
attention kernels).

**We are not going to fight this — we are going to measure it.** This is a novel
reproducibility-study dimension that the parallel infrastructure *creates*, and
the audit is exactly the place to quantify it. Requirement: capture, per run,
the **concurrency context** so agreement can be analyzed against it:

- **Concurrency proxy:** the deployment's `demand` (number of co-held leases)
  at run start/end — already in the ledger, surfaced by `infer-stack leases
  --json`. Snapshot it in the run metadata.
- **vLLM determinism-relevant flags:** `enforce_eager`, `max_num_seqs`,
  chunked-prefill on/off, vLLM version — record from the catalog endpoint
  runtime config.
- **Optional gate-check** (ties to migration-plan G3): diff serial vs
  concurrent-batched `/v1/completions` (`echo=True, logprobs`) for a fixed
  prompt. If they match, concurrency is numerically free for these tasks; if
  not, we have a *finding*, plotted as agreement-vs-concurrency.

Where this lands in the schema is an `eval_audit`-phase question; the **infra
requirement** is only that the demand snapshot + vLLM flags are *obtainable* at
run time (they are).

---

## 8. Lease lifecycle, leak recovery, cold-start robustness

**Leak recovery (two layers, defense in depth):**
1. **`teardown` trap (`EXIT INT TERM`)** on the kwdagger node (§2) — graceful
   exit, failure, SIGINT, and SIGTERM (incl. `tmux kill-session` and slurm
   `scancel` within the `--signal` grace window). For the non-kwdagger
   convenience path, the `infer-stack run` `try/finally` covers exit/failure/
   SIGINT. (A `run` SIGTERM handler for that path was considered and dropped —
   the kwdagger pipeline path is the one that matters, and it uses this trap.)
2. **Finite TTL + sweep** — SIGKILL / OOM-killer / reboot (uncatchable by *any*
   in-band mechanism). The next acquire's `reconcile()→sweep()` expires the leak
   and frees the GPU (§4); the standalone **`infer-stack gc`** verb (landed) does
   the same as a final pipeline step and/or periodically (cron).

**TTL sizing:** TTL must exceed worst-case (model load + run). Big tp models
load slowly; kwdagger has no heartbeat. Either set TTL generously per endpoint,
or background an `infer-stack renew` loop inside the wrapper. Document the chosen
TTL per endpoint in the catalog.

**Cold-start robustness (your point 5):** the design must tolerate cold start —
a first acquire downloads weights and loads the model, and acquire+wait simply
blocks through it (generous `--timeout`). A **warm-up phase** (pre-pull/pre-load
hot models) is a *convenience optimization, never a dependency*. Reference
counting already coalesces concurrent first-acquires of the same model to a
single download.

**reclaim policy:** pipeline endpoints should be **`reclaim: stop`** (free the
GPU on release) so the next model can place. `keep-warm` holds the GPU and would
starve the queue. (See migration-plan C-1.)

---

## 9. tmux mode — concrete design

```
kwdagger schedule (tmux backend, no GPU request, workers≈4–8)
  └── for each HELM run (DAG siblings, §13), a ProcessNode with:
        setup    = infer-stack acquire <ep> --ttl T --env-file f   (queues+waits, §4)
        main     = helm run against gateway http://127.0.0.1:14042/v1
        teardown = infer-stack release --env-file f                (trap EXIT INT TERM)
  rendered: test -e DONE || { <setup gates main> ; trap teardown ; main ; }
  caching:  the DONE marker short-circuits the whole bracket — a done job
            acquires nothing (TTL is the SIGKILL backstop, §8).
```

**Why it doesn't deadlock.** Single-GPU jobs have no hold-and-wait cycle: a job
holds no GPU while blocked in acquire, so some running job always makes progress
and releases. The only multi-GPU subtlety is **head-of-line blocking**: a
waiting tp=2 job occupies a worker slot until 2 GPUs free. That's bounded
waiting, not deadlock — running jobs finish and release. Acceptable per the
"good enough" bar; the FIFO-with-reservation queue (§4) prevents the tp=2 job
from starving. The real long-run hazard is **GPU-pool silting from leaked
leases**, which §8 closes.

---

## 10. slurm mode — design on paper (stretch, single-node)

slurm becomes the top-level admission gate; infer-stack shadows its GPU grant.

```
kwdagger schedule (slurm backend)
  └── each HELM run submitted with --gres=gpu:N   (N = the run's GPU need: 1, or 2 for tp=2)
        slurm won't start the job until N GPUs are free  ← slurm IS the queue
        same setup/teardown ProcessNode as tmux:
          setup    = export INFER_STACK_ALLOWED_GPUS="$SLURM_JOB_GPUS"   ← shadow slurm's grant
                     infer-stack acquire <ep> --ttl T --env-file f
          main     = <helm cmd>
          teardown = infer-stack release --env-file f   (trap; --signal=B:TERM@N gives it grace)
        a separate afterany cleanup job is unnecessary — teardown is co-located in the job
```

- cmd_queue's slurm backend already renders `--gres` and per-job slurm options
  ([`backends/slurm.py:309-338`](../../submodules/cmd_queue/cmd_queue/backends/slurm.py));
  kwdagger threads `slurm_options` through `submit_jobs`.
- infer-stack honors `INFER_STACK_ALLOWED_GPUS`
  (migration-plan C-9, `context.py:88`). **Verify it is a hard inventory filter,
  not a soft preference** — placement's inventory must be restricted to the
  allowed set.
- **The cgroup gap is a non-issue here.** docker escapes slurm's GPU cgroup, so
  slurm can't *enforce* isolation — but we don't need it to, because infer-stack
  places only on the GPUs slurm granted (the allow-list). No collision as long
  as slurm hands concurrent jobs disjoint GPUs and infer-stack respects the
  list.

**One open design question (resolve when building, not now):** the infer-stack
**ledger is per-`data_root`**. With a single shared ledger on the node,
reference-count coalescing could place a job's model on a deployment outside
that job's slurm grant (job B shares model M already resident on job A's GPU,
leaving B's granted GPU idle). Two clean options:
- **(a) slurm-as-gate + node-global infer-stack:** one ledger; infer-stack owns
  all placement+sharing; slurm's gres only *counts* concurrency. Drop the
  per-job allow-list. Keeps sharing, loosens the "shadow slurm exactly" model.
- **(b) per-job ledger (separate `data_root` per slurm job):** each job is its
  own infer-stack universe, places on its own granted GPU(s). Exactly shadows
  slurm; loses cross-job sharing.

**What keeps the door open (so we don't preclude slurm now):** the per-job
`infer-stack run` wrapper (Decision 1) is already the right shape — slurm
allocates per-job and the lease lifecycle is co-located in the job. We are *not*
adopting a long-lived "serve-phase coordinator" that would assume a single
persistent stack; that would fight slurm's per-job model. As long as the unit of
serving is the job, both tmux and either slurm option remain reachable.

---

## 11. Multi-node — explicitly out of scope

Not built, not designed in detail. Recorded only so single-node choices don't
foreclose it: infer-stack is currently a single-host abstraction (localhost
gateway `:14042`, node-local ledger, node-local docker). Multi-node would need a
per-node stack and co-located serve+run per node — which the per-job wrapper
already satisfies. The fixed gateway port and node-local ledger are the things
to revisit then. No action now.

---

## 12. Required infra changes, by component

| # | Component | Change | Size | Required? |
|---|---|---|---|---|
| C1 | **cmd_queue** | **First-class `setup`/`teardown`** (§2): `setup` reuses the existing PREAMBLE_OK gating; `teardown` is always-run via `trap … EXIT INT TERM`, main exit code authoritative; rendered across serial/tmux/slurm. General feature, infer-stack-agnostic. | Medium | **Required** |
| C2 | cmd_queue | **`afterany`/`afternotok` depend-type** (§2) — orthogonal general feature; extension point at [slurm.py:352-356](../../submodules/cmd_queue/cmd_queue/backends/slurm.py). | Small | Optional |
| K1 | **kwdagger** | **`setup`/`teardown` on `ProcessNode`** (§2): thread through `extra_submitkw` → `queue.submit(...)` ([pipeline.py:537](../../submodules/kwdagger/kwdagger/pipeline.py)); compose inside the cache guard. | Small | **Required** |
| I1 | infer-stack | **Queue-and-wait acquire** (§4): wait loop at `controller.py:237-250` with satisfiability check + FIFO/reservation; leak self-heal via existing `reconcile→sweep` | Medium | **Required** |
| I2 | infer-stack | **Static superset litellm route table** (§6): deterministic vLLM addressing, no gateway recreate, in `compose.py` | Medium | **Required** |
| I3 | infer-stack | **Standalone `infer-stack gc`** (sweep+reconcile) for final/periodic leak reclaim (§8) | Small | **Required** |
| I6 | infer-stack | Verify `INFER_STACK_ALLOWED_GPUS` is a **hard** inventory filter (§10) | Verify | slurm-only |

*(Dropped: ~~I4 reclaim:stop~~ — eval_audit catalog config, folded into §13 wiring;
~~I5 run SIGTERM handler~~ — optional, the kwdagger path is covered by the
cmd_queue `teardown` trap and TTL + `gc`.)*
| — | kwdagger | GPU request omitted in tmux mode — config (the eval_audit schedule layer), not a code change. | — | — |

**Confirmed-good (no change needed):**
- cmd_queue `preamble` already gates the main command ([`serial.py:245-255`](../../submodules/cmd_queue/cmd_queue/backends/serial.py)) — the precondition half is done.
- kwdagger's cache guard short-circuits the whole branch, so a skipped job acquires nothing ([`pipeline.py:2184-2205`](../../submodules/kwdagger/kwdagger/pipeline.py)).
- Multi-GPU/tp-gang placement works ([`placement.py:72-77, 174-183`](../../submodules/infer_stack/infer_stack/leasing/placement.py)).
- `infer-stack run` releases on failure via `finally` ([`commands_leasing.py:1097-1101`](../../submodules/infer_stack/infer_stack/cli/commands_leasing.py)).
- `acquire --ttl` / `run --ttl` exist (default `run`=2h); `sweep()` runs inside every `reconcile()`.
- Compose converge is serialized by an `fcntl` lock — and at **4 GPUs** the serialized no-op `docker compose up` cost is negligible (we are not chasing 50 simultaneous jobs).

---

## 13. `eval_audit` integration — out of scope here (my follow-up responsibilities)

Deferred to the next pass, recorded so it isn't lost:
- **Run independence (point 7).** HELM runs must be emitted as **DAG siblings**,
  not chained, so cmd_queue's `afterok`-only semantics don't cascade-skip
  surviving runs when one fails.
- Manifest → schedule wiring: emit the `infer-stack run --endpoint … -- …`
  job command from the planner; map preset → catalog endpoint; thread the
  per-endpoint TTL and (slurm) `--gres=gpu:N`.
- Surface the §7 concurrency snapshot (`demand` + vLLM flags) into the run
  metadata the reproducibility analysis consumes.

---

## 14. Open questions / gate-checks

1. **LiteLLM cooldown recovery** (§6) — does a static route recover when its
   upstream comes up late? Gate before relying on no-blip.
2. **Serial vs concurrent-batched logprob fidelity** (§7) — measure; informs
   how aggressively we fan out per deployment.
3. **`INFER_STACK_ALLOWED_GPUS` hardness** (§10) — confirm hard filter before
   slurm work.
4. **slurm ledger model** (§10) — pick (a) node-global vs (b) per-job ledger
   when slurm is actually built.

---

## 15. Sequencing

0. (already done) infer-stack CLI/API migration — leasing CLI usable.
1. ✅ **C1 + K1 — `setup`/`teardown`** in cmd_queue + kwdagger. **DONE.**
   Foundational: the whole job shape rides on it.
2. ✅ **I2 no-blip** — **DONE** (rendering-validated; cooldown + no-recreate
   gate-check pending a docker/GPU box).
3. ✅ **I1 queue-and-wait + I3 `gc`** — **DONE.** Validate behavior on a
   2-model, >4-job tmux fan-out (no oversubscription failures, no leaked GPUs
   after induced crashes) once on a GPU box.
4. **`eval_audit` wiring (§13)** — the next phase. See
   [`infer-stack-kwdagger-eval-audit-handoff.md`](infer-stack-kwdagger-eval-audit-handoff.md).
   Includes setting `reclaim: stop` on pipeline endpoints (former I4) and the §7
   determinism recording.
5. **slurm (I6 + ledger decision; C2 `afterany` if wanted)** — only after the
   tmux path is solid on a GPU box.

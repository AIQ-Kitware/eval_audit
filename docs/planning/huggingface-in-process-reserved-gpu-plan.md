# Reproducing HuggingFace-deployed officials in-process on shared GPUs

**Status:** proposed · **Author:** design session 2026-07-08 · **Scope:** infer-stack
+ eval_audit orchestration (HELM/magnet layer needs no change)

## The problem

When we reproduce a public HELM run, eval_audit *always* serves the model with
**vLLM** (via infer-stack, behind a LiteLLM gateway). But a large fraction of
public HELM runs — every `huggingface/*` deployment, including all the OLMo-2 /
OLMoE instruct runs — were produced by HELM's own **`HuggingFaceClient`**, i.e.
`transformers.generate()` on a local GPU. Substituting vLLM for those runs is a
*deployment-boundary mismatch*, not a faithful reproduction:

- The single most consequential case is documented in
  [`docs/vllm-vs-huggingface-deployment-match.md`](../vllm-vs-huggingface-deployment-match.md):
  the official OLMoE run executed at **float32** (unpinned `torch_dtype` defaults
  to fp32 on transformers < 5), and **HF fp32 reproduces the official completions
  exactly** (~1.0), while the best vLLM cell reaches ~0.17. vLLM literally
  *cannot* serve fp32 for that MoE (Triton fused-kernel shared-memory OOM).
- The same unpinned-dtype default applies to **all** OLMo-2 HF deployments, so any
  bf16/fp16 vLLM reproduction of them is a precision mismatch.

We therefore want: **when the official deployment resolves to `HuggingFaceClient`,
reproduce it by running HELM's own `HuggingFaceClient` in-process** (same engine,
same client class the official used), at matched precision.

The blocker is GPU allocation. The GPUs live on a **shared machine** where
infer-stack owns placement and serializes co-hosting through its admission queue.
We cannot just hand the container a GPU out-of-band (the "Path A" we rejected) —
concurrent vLLM runs and other in-process runs would collide. We need infer-stack
to **reserve a GPU (hold it out of the placement pool) without launching a server**,
tell us which GPU it reserved, and free it on release — so the in-process HELM
container runs on exactly that GPU under the same admission accounting as every
served run.

## What already exists (so we don't rebuild it)

Three layers sit between "an official run_spec" and "numbers on disk." **Only two
of the three need changes.**

### HELM / magnet layer — already supports in-process HuggingFace ✓

The from-spec replay CLI already treats in-process HuggingFace as a first-class
served engine
([`materialize_helm_run_from_spec.py:50-64`](../../submodules/aiq-magnet/magnet/backends/helm/cli/materialize_helm_run_from_spec.py#L50-L64)):

> "a local engine (in-process HuggingFace / vLLM) actually served the run … The
> rewrite target MUST be a deployment `name` registered in the run's
> `model_deployments.yaml` (**the by-name override for hf**, the bundle for vLLM)."

The CLI already accepts and forwards `model_deployments_fpath`,
`enable_huggingface_models`, and `enable_local_huggingface_models` to `helm-run`
([`materialize_helm_run.py:326-346, 469-471`](../../submodules/aiq-magnet/magnet/backends/helm/cli/materialize_helm_run.py#L326-L346)).
The Docker node already:

- mounts local HF model dirs `:ro`
  ([`helm_docker_pipeline.py:218-220`](../../eval_audit/pipelines/helm_docker_pipeline.py#L218-L220));
- mounts the HF cache at `/hf-cache` and `model_deployments.yaml` `:ro`
  ([`helm_docker_pipeline.py:199-217`](../../eval_audit/pipelines/helm_docker_pipeline.py#L199-L217));
- renders `--gpus "device=${CUDA_VISIBLE_DEVICES:-all}"` when `container_gpus` is
  `None` ([`helm_docker_pipeline.py:174-177`](../../eval_audit/pipelines/helm_docker_pipeline.py#L174-L177))
  — a **runtime shell variable**, which is exactly the hook a dynamically-assigned
  reserved GPU index plugs into.

The lease bracket is designed to be orthogonal to serving
([`lease_bracket.py:12-20`](../../eval_audit/pipelines/lease_bracket.py#L12-L20)):
"The lease acquires the model server's GPU; the container decides where the HELM
client process runs. The two never need to be coupled." Our new mode is precisely
that decoupling: lease a GPU, run the client *on it* in-process.

### infer-stack layer — reserve-only mechanism is scaffolded but unwired ✗

infer-stack has **no HuggingFace serving engine** (engines are `vllm`/`ollama`
only) — but we don't need one; HELM serves HF in-process. What we need is a
*reserve-only lease*. The pieces exist as "Phase 2" scaffolding but nothing wires
them:

- `placement.available_indices(..., reserved=())`
  ([`placement.py:48-69`](../../submodules/infer_stack/infer_stack/leasing/placement.py#L48-L69))
  already subtracts `reserved` from the placeable pool; `ComposeBackend` stores and
  forwards it — but `_make_backend` never populates it.
- The ledger's `claims` table already has a `kind` column defaulting to
  `'endpoint'` (`store.py:73`) — the seam for a `kind='reserved-gpu'` claim.
- The env-file schema already emits `CUDA_VISIBLE_DEVICES` when
  `descriptor['cuda_visible_devices']` is set
  ([`envfile.py:99-100`](../../submodules/infer_stack/infer_stack/leasing/envfile.py#L99-L100))
  — a **dead branch today**, because the acquire path never passes it. This is the
  channel that returns the reserved GPU index to us.
- `Controller.acquire`/`release`/`gc`, the render-lock serialization, and the
  `--queue` admission loop are all engine-agnostic and reusable.

### eval_audit orchestration layer — needs the glue ✗

Today the pipeline resolves the official deployment name only to *rewrite* it, and
always builds a vLLM bundle. The `official_client_class` resolver exists but only
in the dev tool ([`dev/tools/deployment_match/registry.py`](../../dev/tools/deployment_match/registry.py)
`resolve_official_deployment`), where it merely warns. And leasing hard-codes the
assumption that the container needs no GPU
([`kwdagger_bridge.py:436`](../../eval_audit/integrations/kwdagger_bridge.py#L436),
`manifest.setdefault("container_gpus", "none")`).

## Architecture: the two lease modes side by side

```
SERVED (vLLM) — today                 RESERVED (HF in-process) — proposed
──────────────────────────            ──────────────────────────────────────
setup:  infer-stack acquire <ep>      setup:  infer-stack acquire --reserve-gpu N
          --env-file lease.env                  --env-file lease.env
        → vLLM container up on GPU k          → GPU k held OUT of pool, NO container
        → lease.env: OPENAI_BASE_URL          → lease.env: CUDA_VISIBLE_DEVICES=k
                                                            INFER_STACK_LEASE_ID=…
cmd:    docker run --gpus none        cmd:    source lease.env
        HELM = HTTP caller → gateway          docker run --gpus "device=${CUDA_VISIBLE_DEVICES}"
                                                HELM = HuggingFaceClient in-process on GPU k
teardown: infer-stack release         teardown: infer-stack release   (frees GPU k)
```

Both modes go through the **same admission queue and render lock**, so a reserved
GPU is withheld from concurrent vLLM placements and vice-versa — that co-scheduling
is the whole point, and it comes for free once the reservation is a real ledger
`Deployment` (see Layer 1, option A).

---

## Layer 1 — infer-stack: the reserve-only lease

Add a reservation that is a real ledger `Deployment` (so the existing placement +
admission accounting see it) but renders **no compose service**.

**1.1 CLI surface.** Add `--reserve-gpus N` to `_AcquireFlagsMixin`
(`commands_leasing.py:607`) (or a dedicated `ReserveCLI`). **`N` is a *count*, not
a GPU index** — "hold N available GPUs, infer-stack picks which," exactly like a
vLLM deployment declaring `required_gpu_count`. The reservation never names a
specific card; it goes through placement's existing first-fit pass. When set, the
acquire path skips catalog/endpoint resolution and synthesizes one reservation
request for `N` GPUs. `N` defaults to 1; `N>1` supports device_map sharding for
large fp32 models (e.g. OLMo-2-32B).

**1.2 Request → ledger.** Synthesize a request whose `spec` marks it
non-servable (a sentinel engine, e.g. `engine='reserved'`, or a `spec.reserved:
true` flag). `Ledger.acquire`/`insert_claim` records the claim with
`kind='reserved-gpu'` (column already exists) and creates a `Deployment` with
`required_gpu_count = N` and no served payload.

**1.3 Placement (requests an *available* GPU, does not pin one).** No change needed
if the reserve deployment participates in `desired_deployments()` with a first-fit
`required_gpu_count = N`. This reuses the *same* first-fit pass a vLLM endpoint uses
([`placement.py:72`](../../submodules/infer_stack/infer_stack/leasing/placement.py#L72)),
so infer-stack chooses free GPUs — the reservation is count-based, never
index-based. `plan_placement` marks its GPUs `used`, so concurrent vLLM first-fit
skips them automatically. Preferred over threading the passive `reserved=` param
(which is index-based and in-memory only), because modelling the reservation as a
ledger `Deployment` reuses the render-lock serialization that makes the claim
visible cross-process — the shared-machine requirement.

**1.4 Compose render.** `render_compose` / `_vllm_service` must **skip service
emission** for a reserved deployment while still consuming its `plan.assignments`
slot, and must **not** flag it `unrenderable`/`unplaced` (`compose.py:1262`) — else
`acquire` would treat it as a failed placement. `_ensure_applied` no-ops (no
container) and `wait_ready` is skipped (nothing to probe).

**1.5 Return the GPU index.** In `_descriptor_for` (`commands_leasing.py:333`),
pass `cuda_visible_devices` = the reserved deployment's assigned host indices
(from `outcome.reconcile.assignments[deployment_id]`, already surfaced via
`ReconcileResult.assignments`). `build_descriptor`/`descriptor_env` then emit
`CUDA_VISIBLE_DEVICES=k[,k2…]` into the env-file — no envfile change.

**1.6 Release/gc.** Reuse verbatim. The generic path keys on `lease_id → claims →
demand → idle`; a container-less reservation idles and drops from
`desired_deployments()`, freeing its GPU on the next render. `release`/`gc`/`--ttl`
expiry and the leak backstop all apply unchanged.

**Files touched:** `cli/commands_leasing.py` (flag, request synthesis,
`_descriptor_for`), `leasing/catalog.py` or a small resolver shim (synthesize the
non-servable request), `leasing/ledger.py` (`kind='reserved-gpu'`, non-servable
deployment), `leasing/compose.py` (skip render, don't mark unplaced),
`leasing/controller.py` (skip apply/wait for reserved). `placement.py` and
`envfile.py` unchanged.

**Upstream note.** infer-stack is a submodule (`AIQ-Kitware/infer_stack`). This is a
new feature there; land it on a branch, bump the submodule pointer in eval_audit
per the repo's submodule policy (do not auto-commit the gitlink).

---

## Layer 2 — eval_audit: route HF-officials to the reserve+in-process path

**2.1 Detect the official engine.** Lift `resolve_official_deployment`
(official deployment name → `model_deployments.yaml` → `client_class`) out of the
dev tool into a reusable module (e.g. `eval_audit/deployment/official_client.py`).
At manifest-build time, classify each run: `HuggingFaceClient` → **in-process HF
mode**; hosted/`TogetherClient`/vLLM-registry → existing served-vLLM mode. This is
a *routing* decision, mirroring the doc's standing recommendation
([`vllm-vs-huggingface-deployment-match.md`](../vllm-vs-huggingface-deployment-match.md)
"have the grid read `official_client_class`").

**2.2 Build an HF deployment bundle instead of a vLLM bundle.** Add an HF branch to
`_model_deployment_entry` / the bundle builder
([`bundle_export.py:33-100`](../../eval_audit/integrations/infer_stack/bundle_export.py#L33-L100))
that emits a `model_deployments.yaml` entry with:

- `client_spec.class_name: helm.clients.huggingface_client.HuggingFaceClient`
- `args: { pretrained_model_name_or_path: <hf-id-or-local>, device_map: auto,
  torch_dtype: float32 }` — **pin fp32 explicitly** so we are version-proof rather
  than relying on the container's transformers default (see Layer 3).
- `model_name` / `tokenizer_name`: the same HELM aliases, asserted to exist.
- no `base_url`, no LiteLLM wiring.

Set `enable_local_huggingface_models` / `enable_huggingface_models` and the weights
mount as needed (Layer 3). No vLLM catalog, no gateway.

**2.3 Reserve lease instead of serve lease.** In the lease bracket
([`lease_bracket.py:153-201`](../../eval_audit/pipelines/lease_bracket.py#L153-L201)),
add a per-run flag (e.g. `lease_reserve_gpus: N`) that, when set, renders
`infer-stack acquire --reserve-gpu N …` instead of `acquire <endpoint>`. Teardown
(`release --env-file`) is identical. The manifest producer sets this flag for
in-process HF runs and sets no `lease_endpoint`.

**2.4 Give the container the reserved GPU.** Two coupled changes in
`helm_docker_pipeline.command`
([`helm_docker_pipeline.py:140-237`](../../eval_audit/pipelines/helm_docker_pipeline.py#L140-L237)):

- **Source the lease env-file before `docker run`** on the reserve path, so the
  dynamically-assigned `CUDA_VISIBLE_DEVICES` is in the command's shell (setup and
  command are separate cmd_queue steps — exported env does not survive; the file
  does). Prepend `set -a; . <out_dpath>/lease.env; set +a`.
- Keep `container_gpus = None` for this mode (not `"none"`), so the existing
  default branch renders `--gpus "device=${CUDA_VISIBLE_DEVICES:-all}"`. **Harden
  it** for shared machines: on the reserve path render
  `--gpus "device=${CUDA_VISIBLE_DEVICES:?reserved GPU unset}"` (fail closed rather
  than silently grabbing all GPUs if the lease didn't populate it).

**2.5 Decouple the `container_gpus="none"` assumption.** Make
[`kwdagger_bridge.py:436`](../../eval_audit/integrations/kwdagger_bridge.py#L436)
mode-aware: `setdefault("container_gpus", "none")` only for served (vLLM) leases;
for reserve leases leave `container_gpus` `None`.

**2.6 Deployment-name rewrite (comparability).** Recommended: register the HF
entry under a **local** name (e.g. `huggingface-local/olmoe-1b-7b-0125-instruct`)
and pass `--model-deployment <local-name>` so the produced run records that a local
engine served it (`same_deployment=no`) — consistent with the vLLM path and the
deployment-rewrite convention
([`from-spec-deployment-rewrite-plan.md`](../historical/planning/from-spec-deployment-rewrite-plan.md)).
The engine *class* is the same (`HuggingFaceClient`), so the report's honest signal
is machine/dtype/revision, not engine. (Verbatim by-name replay is the alternative
if we decide the local run is genuinely "the same deployment.")

---

## Layer 3 — fidelity knobs (HELM side, no code change)

- **Precision (decisive).** Pin `torch_dtype: float32` in the generated entry. The
  officials ran fp32 because `torch_dtype` was unpinned on transformers < 5; pinning
  it makes us independent of the container's transformers version. Keep the e2e venv
  on `transformers < 5` regardless (see memory: e2e venv pin) as a belt-and-suspenders.
- **Weights.** Prefer weights resolved from the mounted HF cache (`/hf-cache`,
  already wired) by HF id; use `enable_local_huggingface_models` with a local
  snapshot dir when the weights are a bare directory (the dir is auto-mounted `:ro`).
  Pin an HF `revision` when known (7/148 officials pin one) for exact weights.
- **Attention / determinism.** HuggingFaceClient uses `transformers.generate()`, so
  the Tier-A vLLM↔HF confounders (paged attention, chunked prefill, prefix cache,
  batch non-invariance) simply do not arise — that is the point of running the same
  engine. `attn_implementation` still matters across HF versions; leave default and
  verify against the OLMoE exact-match acceptance test below.

---

## Validation

1. **infer-stack unit tests** for the reserve lease: acquire reserves a real GPU
   index, withholds it from a concurrent vLLM placement (same-host, render-lock),
   emits `CUDA_VISIBLE_DEVICES` in the env-file, releases cleanly, and TTL/gc
   reclaim it after a hard kill.
2. **eval_audit unit tests**: HF-official → reserve manifest shape (HF
   `model_deployments.yaml` entry with fp32, `lease_reserve_gpus` set, no
   `lease_endpoint`, `container_gpus=None`); served-official unchanged. Command
   renders the `source lease.env` prefix and the fail-closed `--gpus` on the reserve
   path only.
3. **End-to-end acceptance test — the OLMoE exact-match.** Run the OLMoE-instruct
   HF-official through the new path on the shared machine and confirm it reproduces
   the official completions **exactly** (quasi/exact ≈ 1.0), matching the standalone
   `hf-probe` result that already validated fp32 HF reproduction
   ([`docs/vllm-vs-huggingface-deployment-match.md`](../vllm-vs-huggingface-deployment-match.md)).
   This is the single decisive test that the integrated path is faithful.
4. **Co-scheduling test**: launch an in-process HF run and a vLLM served run
   concurrently on the shared host; confirm they land on disjoint GPUs and neither
   starves incorrectly.

## Sequencing (dependency order)

1. infer-stack reserve lease (Layer 1) on an infer_stack branch + its unit tests.
2. Bump the submodule pointer in eval_audit.
3. eval_audit: official-engine resolver (2.1) + HF bundle builder (2.2) + reserve
   bracket (2.3) + container GPU wiring (2.4/2.5) + rewrite (2.6), with unit tests.
4. e2e OLMoE acceptance run; then extend to the OLMo-2 dense models (which can
   *also* run vLLM fp32, giving a cross-check) and the 32B (multi-GPU reserve, N>1).

## Effort & risk

- **infer-stack reserve lease** — the only genuinely novel work. Moderate: the
  ledger/placement/env-file seams all exist; the work is a non-servable deployment
  kind + render-skip + wiring the assigned index into the descriptor. Main risk is
  making the reserve deployment invisible to compose render while still visible to
  placement — needs careful handling of `unrenderable`/`unplaced`.
- **eval_audit orchestration** — moderate, mostly additive branching alongside the
  existing vLLM path; the container-GPU wiring reuses the existing
  `${CUDA_VISIBLE_DEVICES}` hook.
- **HELM layer** — none (already supported).
- **Open questions:** (a) verbatim vs local-name rewrite for HF (2.6) — recommend
  local-name; (b) whether to reserve by first-fit vs explicit GPU indices when the
  operator wants to pin specific cards; (c) multi-GPU device_map sharding fidelity
  for the 32B fp32 case (reserve N, let HF shard) vs. the vLLM-fp32-TP cross-check.
```

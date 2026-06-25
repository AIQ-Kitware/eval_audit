# Migrating the phi-2 e2e to faithful `run_spec.json` replay — plan

**Status:** PLAN — not yet implemented.
**Scope:** the two *comparable* phi-2 scenarios (`hf`, `vllm`) move to the
from-spec replay pipeline. The `incomparable` negative control **stays on the
run-entry path** (§7). Gated behind an `E2E_FROM_SPEC` flag so the existing
run-entry coverage — and a run-entry-vs-from-spec parity diff — are retained.
**Depends on:** [`run-from-run-spec-json-plan.md`](run-from-run-spec-json-plan.md)
(the replay pipeline, now implemented on `impl/run-from-run-spec`).
**Method:** read the e2e harness under `dev/e2e-tests/`, the infer-stack bundle
exporter, the per-scenario virtual-experiment configs, and the real public phi-2
artifacts under `/data/crfm-helm-public`. 2026-06-25.

---

## 1. Why this migration (the central insight)

The phi-2 e2e already compares each local scenario against **the public
`microsoft/phi-2` `mmlu:philosophy` run** by canonical logical key — see the
`official_public_index` source in
[`configs/virtual-experiments/e2e-phi2-hf.yaml`](../../configs/virtual-experiments/e2e-phi2-hf.yaml).
That public run is on disk with a fully-resolved recipe:

```
/data/crfm-helm-public/mmlu/benchmark_output/runs/v1.0.0/
  mmlu:subject=philosophy,method=multiple_choice_joint,model=microsoft_phi-2,eval_split=test,groups=mmlu_philosophy/run_spec.json
  → model: microsoft/phi-2   model_deployment: together/phi-2
```

Today the **local** side reconstructs the run-entry string
(`mmlu:subject=philosophy,…,model=microsoft/phi-2,eval_split=test`) and re-derives
the recipe under the installed crfm-helm. The from-spec pipeline instead replays
*this exact official `run_spec.json`* — the very recipe the comparison is against
— so the local reproduction differs from the official only by the model
*execution*, never by a re-derived recipe. For the faithful scenarios this is a
strict upgrade; for the negative control it is the wrong tool (§7).

## 2. What the e2e looks like today (constraints that shape the change)

1. **Three scenarios** in `E2E_TARGETS`
   ([`dev/e2e-tests/_lib.sh`](../../dev/e2e-tests/_lib.sh)):
   - `e2e-phi_2-huggingface-philosophy` (`hf`) — phi-2 loaded **in-process** from
     HuggingFace. Uses a **checked-in** manifest
     ([`manifests/e2e-phi_2-huggingface-philosophy-{smoke,full}.yaml`](../../dev/e2e-tests/manifests/)).
   - `e2e-phi_2-vllm-philosophy` (`vllm`) — phi-2 **served** on vLLM behind
     LiteLLM. Manifest is **generated** by `export-benchmark-bundle` from the
     preset `e2e-phi_2-vllm-philosophy`
     ([`eval_audit/integrations/infer_stack/adapter.py:361`](../../eval_audit/integrations/infer_stack/adapter.py)),
     `model_deployment_name: vllm/phi-2-local`.
   - `e2e-phi_2-vllm-philosophy-incomparable` — same as `vllm` but `temperature=1`,
     a **deliberate recipe deviation** the planner must flag.
2. **Runs are manifest-driven.** `10_run_smoke_grid.sh` / `15_run_full_grid.sh`
   call `eval-audit-run <manifest> --container-image "$E2E_CONTAINER_IMAGE"`
   (+ `--lease` for vLLM). The bridge selects the pipeline from
   `manifest['from_run_spec']`, so flipping the manifest is sufficient — **no
   change to the `eval-audit-run` invocation** for the `hf` path.
3. **The HF manifest computes from the run-entry today:** `precomputed_root:` is
   empty and `enable_huggingface_models: ["microsoft/phi-2"]` registers the
   in-process client.
4. **The vLLM bundle bakes a by-name override:** `export-benchmark-bundle`
   generates `model_deployments.yaml` binding `vllm/phi-2-local` → the LiteLLM
   endpoint, plus the lease facts (`lease_endpoint`/ttl/catalog) used by
   `--lease`. The run_entries carry `model_deployment=vllm/phi-2-local`.
5. **Downstream is canonical-key based.** The per-scenario virtual experiments
   ([`configs/virtual-experiments/e2e-phi2-{hf,vllm,incomparable}.yaml`](../../configs/virtual-experiments/))
   pair the local row against `official_public_index` by canonical logical key,
   so the local run dir *name* need not byte-match the official — and HELM does
   not encode `temperature` in the run name, which is why the `incomparable`
   negative control still pairs.

## 3. Decisions

- **Migrate `hf` + `vllm`; keep `incomparable` on the run-entry path** (§7).
- **`precomputed_root: /data/crfm-helm-public/mmlu`** — narrowed to the mmlu
  subtree so discovery is fast and unambiguous (the full corpus is large and may
  carry phi-2 runs in multiple suites/versions).
- **Gate behind `E2E_FROM_SPEC=1`.** Default keeps the run-entry path; the flag
  flips `hf` + `vllm` to from-spec. This preserves run-entry coverage and makes a
  *parity diff* (run-entry vs from-spec produced `run_spec.json`/`stats.json`) a
  first-class deliverable (the methodology result the replay pipeline exists to
  quantify).
- **Substitution is by-name on the OFFICIAL deployment `together/phi-2`** (§5).

## 4. Changes

### Change 0 — image rebuild & re-pin (prerequisite)

The runner image must contain `materialize_helm_run_from_spec`.
`./docker/build.sh` git-archives aiq-magnet (now pinned at `4b10e1b`, which ships
the CLI) → capture the new digest → set `E2E_CONTAINER_IMAGE` (default
`eval-audit-helm-runner:dev`) to it. `06_check_container_image.sh` verifies.
**Until this lands, every from-spec run fails** (the module isn't in the image).

### Change 1 — HF manifests + the `together/phi-2` override

In both `manifests/e2e-phi_2-huggingface-philosophy-{smoke,full}.yaml`, under the
`E2E_FROM_SPEC` variant (Change 3 decides whether this is a new file or a runtime
edit):

```yaml
from_run_spec: true
precomputed_root: /data/crfm-helm-public/mmlu      # was empty
model_deployments_fpath: configs/debug/e2e_phi2_fromspec_overrides.yaml
# enable_huggingface_models is now redundant (the override fully specifies the
# client); keep it only if the override omits weights/tokenizer.
```

New override file `configs/debug/e2e_phi2_fromspec_overrides.yaml` — rebinds the
**official** deployment name to a local in-process HuggingFace client:

```yaml
model_deployments:
  - name: together/phi-2          # the name the OFFICIAL run_spec.json carries
    model_name: microsoft/phi-2
    tokenizer_name: microsoft/phi-2
    client_spec:
      class_name: "helm.clients.huggingface_client.HuggingFaceClient"
      args:
        pretrained_model_name_or_path: microsoft/phi-2
```

### Change 2 — vLLM preset (adapter.py): from-spec fields + rekey the override

In the `e2e-phi_2-vllm-philosophy` preset
([`adapter.py:361`](../../eval_audit/integrations/infer_stack/adapter.py)):

- add `from_run_spec: true` and `precomputed_root: /data/crfm-helm-public/mmlu`
  to the `smoke_manifest` / `full_manifest` blocks (so `export-benchmark-bundle`
  writes them into the generated manifests);
- **rekey the generated `model_deployments.yaml`** so it binds **`together/phi-2`**
  (the official deployment) → the LiteLLM endpoint, instead of (or in addition to)
  `vllm/phi-2-local`. The replayed recipe names `together/phi-2`; the lease still
  acquires the same served endpoint. The `run_entries` themselves are no longer
  consulted for the recipe (discovery uses only the benchmark+tokens to *locate*
  the official dir), but keep them as the discovery key.

This is the only code change (vs. config) and lives entirely in the infer-stack
adapter — no eval_audit-core change.

### Change 3 — grid gating (`E2E_FROM_SPEC`)

In `_lib.sh` add `E2E_FROM_SPEC="${E2E_FROM_SPEC:-0}"`. In `10_run_smoke_grid.sh`
/ `15_run_full_grid.sh`, when set:

- `hf`: select the from-spec manifest variant (a sibling
  `…-fromspec-{smoke,full}.yaml`, the cleanest no-mutation form) instead of the
  run-entry manifest;
- `vllm`: pass a `--from-spec`-style flag (or a preset suffix) to
  `export-benchmark-bundle` so it emits the Change-2 fields;
- `incomparable`: **unchanged** — always run-entry (§7).

Prefer sibling manifest files over in-place edits so a single grid invocation can
run *both* paths and the parity step (Change 6) can diff them.

### Change 4 — leave `incomparable` on the run-entry path

No change. Document in `_lib.sh`/README why (§7) so a future reader does not
"finish the migration" by flipping it and silently destroying the negative
control.

### Change 5 — downstream verification (expected no-op)

`20_index_local` → `30_compose` → `40_build_summary` and the virtual-experiment
configs are untouched. The from-spec local run dir now carries the **full
official `run_spec.name`** (incl. `groups=mmlu_philosophy`), making it
name-identical to the official side; pairing is by canonical key so it still
matches. Verify on the first run that the planner pairs `hf`/`vllm` exactly as
before (and that `same_deployment=no` still holds — the local client differs from
Together even though the *name* is now `together/phi-2`).

### Change 6 — parity + tests

- **Discovery dry-check (CPU, no model):** assert `find_best_precomputed_run`
  resolves the public phi-2 dir from `precomputed_root=/data/crfm-helm-public/mmlu`
  for the phi-2 run-entry, and that its `run_spec.json` deserializes + passes the
  preflight (it is plain MC mmlu — no annotators/judges, so it should).
- **Smoke end-to-end (`hf`, `E2E_FROM_SPEC=1`):** DONE written; `adapter_manifest`
  `status=replayed`, `recipe.source=discovery`, `replay.applied_max_eval_instances=5`;
  produced dir == official `run_spec.name`.
- **Parity:** diff the run-entry vs from-spec produced `run_spec.json` / `stats.json`
  for `hf` — quantifies the recipe drift the replay removes (methodology result).
- **Full grid + compose + summary** on `E2E_FROM_SPEC=1`; confirm the per-scenario
  reports still build and pair.

## 5. The deployment-substitution subtlety (the crux)

The official `run_spec.json` names `model_deployment: together/phi-2`. By-name
substitution only rebinds names **present in the override yaml** — so replayed
verbatim, the run would call the real **Together API**. Today's paths avoid this
because the run-entry explicitly names the *local* deployment
(`microsoft/phi-2` in-process for `hf`; `vllm/phi-2-local` for `vllm`). From-spec
does not: the recipe carries `together/phi-2`, so **every from-spec phi-2 run
must override `together/phi-2`** — to a local HuggingFace client (`hf`, Change 1)
or the LiteLLM endpoint (`vllm`, Change 2). This is the single behavioral
difference an implementer must get right; everything else is plumbing.

## 6. What deliberately does NOT change

- Stages 4–6 and the per-scenario virtual-experiment configs (pairing by
  canonical key).
- The `incomparable` scenario (§7).
- The lease bracket for `vllm` (the served endpoint and `--lease` are orthogonal
  to which recipe is replayed).

## 7. Open items / risks

- **`incomparable` cannot be from-spec.** It exists to inject `temperature=1` and
  verify the planner flags the deviation. From-spec replays the official recipe
  verbatim, erasing the deviation; the pipeline's discovery path has no way to
  inject it (only the standalone `--run-spec-json` with a hand-edited spec could,
  and that is not wired through manifests). It must stay on the run-entry path.
- **Recipe params tuned for the official serving stack.** The official
  `adapter_spec` may carry request-shape params (e.g. token limits) chosen for
  Together; replaying them against in-process HF / vLLM could surface request
  mismatches. That is a *real* reproducibility signal worth seeing — but note it
  so it is not mistaken for a migration bug.
- **Run-name change ripples.** The produced dir gains the `groups=mmlu_philosophy`
  suffix (now identical to the official). Pairing is canonical-key based so it
  should be inert, but confirm on first run (Change 5).
- **Discovery ambiguity.** If multiple public phi-2 `mmlu:philosophy` runs exist
  across suites/versions, `find_best_precomputed_run` picks the best-scoring match
  deterministically; narrowing `precomputed_root` to `…/mmlu` reduces the surface.
- **Judge-dependent benchmarks (not this scenario).** mmlu:philosophy is
  deterministic MC with no annotators, so no judge override is needed. A future
  judge-dependent e2e would also need the judge deployment in the override
  (replay-pipeline plan §5/§10).

## 8. Sequencing

0. Rebuild + re-pin the image (Change 0).
1. Add the `together/phi-2` override + HF from-spec manifest variant (Change 1).
2. Gate the grid (Change 3); run the `hf` smoke under `E2E_FROM_SPEC=1`; check the
   discovery dry-check + parity (Change 6).
3. Teach `export-benchmark-bundle` the from-spec fields + rekey (Change 2); run
   the `vllm` smoke.
4. Full grid + compose + summary; confirm pairing (Change 5).
5. Document the `incomparable` carve-out (Change 4).

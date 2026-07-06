# Migrating the phi-2 e2e to faithful `run_spec.json` replay — plan

> **AMENDED (2026-06-25) by
> [`from-spec-deployment-rewrite-plan.md`](from-spec-deployment-rewrite-plan.md).**
> The by-name-only substitution this plan describes (Change 1/2 rekey both sides to
> the official `together/phi-2`, §5) **masked** the engine substitution: the local
> run recorded `together/phi-2`, so the comparison reported `same_deployment=yes`.
> The amendment reverses that — the local overrides now register **local**
> deployment names (`huggingface/phi-2-local` for `hf`, the bundle's native
> `vllm/phi-2-local` for `vllm`), and the from-spec CLI **rewrites**
> `adapter_spec.model_deployment` to the local name (threaded via a new manifest
> `model_deployment` field), so the produced run records the served endpoint and
> the audit reports `same_deployment=no`. Where this plan says "rekey to
> `together/phi-2`," read "register the local name + emit it as the rewrite
> target." The sections below are kept as the historical record.

**Status:** IMPLEMENTED (code/config + tests) — the GPU runs + parity diff
(Change 0 image re-pin, Change 6 end-to-end) remain.
**Update (post-implementation):** at the user's direction, from-spec is now the
**unconditional default** for the comparable scenarios — the `E2E_FROM_SPEC`
toggle was removed (no run-entry opt-out). Sections that still describe the gated
design below are annotated; the carve-out and substitution mechanics are
unchanged. The run-entry-vs-from-spec parity diff is now a **manual** step
(point at an archived run-entry manifest), not a grid mode.
**Scope:** the two *comparable* phi-2 scenarios (`hf`, `vllm`) replay the official
`run_spec.json`. The `incomparable` negative control **stays on the run-entry
path** (§7) — the sole, structural carve-out (from-spec would erase its
`temperature=1` deviation).
**Depends on:** [`run-from-run-spec-json-plan.md`](../../planning/run-from-run-spec-json-plan.md)
(the replay pipeline, now implemented on `impl/run-from-run-spec`).
**Method:** read the e2e harness under `dev/e2e-tests/`, the infer-stack bundle
exporter, the per-scenario virtual-experiment configs, and the real public phi-2
artifacts under `/data/crfm-helm-public`. 2026-06-25.

---

## 1. Why this migration (the central insight)

The phi-2 e2e already compares each local scenario against **the public
`microsoft/phi-2` `mmlu:philosophy` run** by canonical logical key — see the
`official_public_index` source in
[`configs/virtual-experiments/e2e-phi2-hf.yaml`](../../../configs/virtual-experiments/e2e-phi2-hf.yaml).
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
   ([`dev/e2e-tests/_lib.sh`](../../../dev/e2e-tests/_lib.sh)):
   - `e2e-phi_2-huggingface-philosophy` (`hf`) — phi-2 loaded **in-process** from
     HuggingFace. Uses a **checked-in** manifest
     ([`manifests/e2e-phi_2-huggingface-philosophy-{smoke,full}.yaml`](../../../dev/e2e-tests/manifests/)).
   - `e2e-phi_2-vllm-philosophy` (`vllm`) — phi-2 **served** on vLLM behind
     LiteLLM. Manifest is **generated** by `export-benchmark-bundle` from the
     preset `e2e-phi_2-vllm-philosophy`
     ([`eval_audit/integrations/infer_stack/adapter.py:361`](../../../eval_audit/integrations/infer_stack/adapter.py)),
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
   `--lease`. **Unlike every other preset, the phi-2 run_entries are *bare*
   `…model=microsoft/phi-2,eval_split=test` — they do NOT carry a
   `model_deployment=` token** (`_manifest_doc` writes them verbatim,
   [`adapter.py:1242`](../../../eval_audit/integrations/infer_stack/adapter.py); HELM
   resolves the deployment from the registered `model_deployments.yaml`). This
   *helps* the migration: the bare run-entry is a clean token-subset of the
   official dir name (which only adds `groups=mmlu_philosophy`), so discovery
   (`find_best_precomputed_run`) matches it with no stray `model_deployment=` token
   to reconcile (§5, Change 6).
5. **Downstream is canonical-key based.** The per-scenario virtual experiments
   ([`configs/virtual-experiments/e2e-phi2-{hf,vllm,incomparable}.yaml`](../../../configs/virtual-experiments/))
   pair the local row against `official_public_index` by canonical logical key,
   so the local run dir *name* need not byte-match the official — and HELM does
   not encode `temperature` in the run name, which is why the `incomparable`
   negative control still pairs.

## 3. Decisions

- **Migrate `hf` + `vllm`; keep `incomparable` on the run-entry path** (§7).
- **`precomputed_root: /data/crfm-helm-public/mmlu`** — narrowed to the mmlu
  subtree so discovery is fast and unambiguous (the full corpus is large and may
  carry phi-2 runs in multiple suites/versions).
- **~~Gate behind `E2E_FROM_SPEC=1`.~~ → From-spec is the unconditional default**
  (superseded). The comparable scenarios always replay the official
  `run_spec.json`; there is no run-entry opt-out (`e2e_uses_from_spec` in `_lib.sh`
  is purely the incomparable carve-out). The run-entry-vs-from-spec *parity diff*
  (run-entry vs from-spec produced `run_spec.json`/`stats.json`) is still the
  methodology deliverable, but is produced **manually** — diff a from-spec run
  against one driven by an archived run-entry manifest — rather than as a grid mode.
- **Substitution is by-name on the OFFICIAL deployment `together/phi-2`** (§5).

## 4. Changes

### Change 0 — image rebuild & re-pin (prerequisite)

The runner image must contain `materialize_helm_run_from_spec`.
`./docker/build.sh` git-archives aiq-magnet (now pinned at `4b10e1b`, which ships
the CLI) → capture the new digest → set `E2E_CONTAINER_IMAGE` (default
`eval-audit-helm-runner:dev`) to it. `06_check_container_image.sh` verifies.
**Until this lands, every from-spec run fails** (the module isn't in the image).

### Change 1 — HF manifests + the `together/phi-2` override

**As shipped (default-from-spec):** the canonical hf manifests
`manifests/e2e-phi_2-huggingface-philosophy-{smoke,full}.yaml` *are* the from-spec
manifests — there is no separate run-entry sibling (the original sibling-file
design was collapsed when from-spec became the default). Each carries:

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

### Change 2 — vLLM preset + exporter (adapter.py): from-spec fields + rekey

Two parts, **both code** (not just preset data), both in the infer-stack adapter —
no eval_audit-core change.

**(a) Thread the fields through the exporter (the easy-to-miss part).** Adding
`from_run_spec: true` + `precomputed_root: /data/crfm-helm-public/mmlu` to the
preset's `smoke_manifest` / `full_manifest` blocks is **not sufficient on its
own**. `_manifest_doc`
([`adapter.py:1232`](../../../eval_audit/integrations/infer_stack/adapter.py)) builds
a *fixed* manifest dict: it **hardcodes `precomputed_root: None`** (`:1251`), has
**no `from_run_spec` key**, and only passes through `_CONTAINER_SPEC_KEYS`
(`:1175`, which contains neither field). So an exporter run would silently drop
both and land on the run-entry path **with no error** (the bridge guard only fires
when `from_run_spec` is truthy). `_manifest_doc` must therefore be edited to
(i) read `from_run_spec` from the spec and (ii) stop hardcoding
`precomputed_root: None` and read it from the spec instead. Only then do the
preset-block fields take effect.

**(b) Rekey the deployment to `together/phi-2`.** Change the **profile spec's
`model_deployment_name`** from `vllm/phi-2-local` → `together/phi-2`
([`adapter.py:369`](../../../eval_audit/integrations/infer_stack/adapter.py); it flows
to `_model_deployment_entry`'s `name` field, `:1084`). The generated
`model_deployments.yaml` then binds **`together/phi-2`** (the name the official
`run_spec.json` carries) → the LiteLLM endpoint. Registering it locally **shadows
HELM's built-in `together/phi-2`** (the real Together API), exactly as
`repro_model_overrides.yaml` shadows `together/qwen2.5-...`. The lease is
unaffected: `_lease_facts` keys the single-endpoint `lease_endpoint` off the
*catalog endpoint* (`phi2-single`), not the deployment name (`:1219`). Keep the
bare run_entries as the discovery key (§2.4) — they only *locate* the official
dir; the recipe comes from its `run_spec.json`.

### Change 3 — grid wiring (no toggle; from-spec is the default)

**As shipped:** there is no `E2E_FROM_SPEC` env var. `_lib.sh` exposes
`e2e_uses_from_spec <scenario>` — the structural carve-out that returns false only
for `*-incomparable`. In `10_run_smoke_grid.sh` / `15_run_full_grid.sh`:

- `hf`: `e2e_hf_manifest` returns the canonical (from-spec) manifest — no infix,
  no branch;
- `vllm`: the grid appends `--from-spec` to `export-benchmark-bundle` iff
  `e2e_uses_from_spec` (so the comparable baseline gets the Change-2(a) fields +
  Change-2(b) rekey; the incomparable control does not);
- `incomparable`: run-entry — its preset carries no from-spec wiring (Change 2b)
  and `e2e_uses_from_spec` excludes it (§7).

*(Originally specified as an `E2E_FROM_SPEC=1` gate that kept the run-entry path
as default; that toggle was removed at the user's direction — from-spec is now
unconditional for the comparable scenarios.)*

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
before (and that `same_deployment=no` holds). **Amended:** under the
deployment-rewrite plan `same_deployment=no` now holds *because the recorded name
differs* — the local run records its local deployment (`huggingface/phi-2-local` /
`vllm/phi-2-local`), not `together/phi-2` — which is the honest signal (the
original by-name design recorded `together/phi-2` on both sides and so reported
`same_deployment=yes`, masking the substitution). Pairing stays inert: HELM run
names encode `model=…`, not `model_deployment=…`.

### Change 6 — parity + tests

- **Discovery dry-check (CPU, no model):** assert `find_best_precomputed_run`
  resolves the public phi-2 dir from `precomputed_root=/data/crfm-helm-public/mmlu`
  for the phi-2 run-entry, and that its `run_spec.json` deserializes + passes the
  preflight (it is plain MC mmlu — no annotators/judges, so it should). **Done**
  (`tests/test_e2e_from_spec_bundle.py`, corpus-gated) alongside exporter-rekey +
  preset-wiring + manifest unit tests.
- **Smoke end-to-end (`hf`):** DONE written; `adapter_manifest` `status=replayed`,
  `recipe.source=discovery`, `replay.applied_max_eval_instances=5`; produced dir ==
  official `run_spec.name`. *(Needs the rebuilt image — Change 0.)*
- **Parity (now manual):** diff a from-spec `run_spec.json` / `stats.json` against
  one produced from an archived run-entry manifest for `hf` — quantifies the recipe
  drift the replay removes (methodology result). No longer a grid mode (the toggle
  was removed); run the two manifests by hand into separate result roots and diff.
- **Full grid + compose + summary**; confirm the per-scenario reports still build
  and pair. *(Needs the rebuilt image — Change 0.)*

## 5. The deployment-substitution subtlety (the crux)

> **AMENDED by [`from-spec-deployment-rewrite-plan.md`](from-spec-deployment-rewrite-plan.md).**
> This section's resolution (re-register `together/phi-2` → a local client, keeping
> the official *name*) makes the run call the local engine — but it also makes the
> produced run **record** `together/phi-2`, so the comparison reads
> `same_deployment=yes` and the substitution is invisible. The amendment registers a
> **local** name instead and rewrites `adapter_spec.model_deployment` to it, so the
> run records the local endpoint and the comparison reports `same_deployment=no`.
> Read the paragraph below as the *motivation* (the recipe carries `together/phi-2`
> and the local engine must serve it); the *mechanism* is now register-local +
> rewrite, not re-register-the-official-name.

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
- **"Full" is still a prefix, not the official instance count.** The official
  `adapter_spec.max_eval_instances` is `10000`; the e2e `full` caps at `1000`
  (smoke at `5`). The replay CLI truncates by replacing `max_eval_instances`, so
  even the full grid compares on HELM's deterministic instance *prefix*, not the
  complete official set — identical to today's run-entry path, but stated so
  "full" is not read as official parity.
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

Steps 1–6 (override + canonical from-spec hf manifests, exporter threading +
rekey, grid wiring, incomparable carve-out, tests incl. the discovery dry-check)
are **done**. Remaining, all requiring the rebuilt image:

0. Rebuild + re-pin the image (Change 0) — the hard prerequisite.
1. Run the `hf` smoke; check `status=replayed` + produced dir == official
   `run_spec.name` (Change 6).
2. Run the `vllm` smoke (the grid passes `--from-spec` for the comparable
   baseline).
3. Full grid + compose + summary; confirm pairing holds (Change 5).
4. Produce the manual run-entry-vs-from-spec parity diff (Change 6).

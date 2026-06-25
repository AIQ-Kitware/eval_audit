# Recording the local deployment on from-spec replays — plan

**Status:** PLAN — not yet implemented.
**Problem:** faithful `run_spec.json` replay with **by-name** deployment
substitution makes the local run record `model_deployment: together/phi-2`
(identical to the official), so the comparison reports `same_deployment=yes` and
the engine substitution (local HF / vLLM vs the hosted Together API) becomes
invisible — the single most important difference the audit exists to surface.
**Fix:** after loading the official `run_spec.json`, **rewrite
`adapter_spec.model_deployment` to the local deployment name** (the thing that
actually ran), so the produced run records the local name and the existing
comparability logic flags `same_deployment=no` again — with no downstream
plumbing.
**Depends on / supersedes:** the by-name decision in
[`run-from-run-spec-json-plan.md`](run-from-run-spec-json-plan.md) §5 and the
`together/phi-2` rekey in
[`e2e-from-run-spec-migration-plan.md`](e2e-from-run-spec-migration-plan.md)
Change 2b. The window-metadata fix (override `max_sequence_length: 2047`) already
landed and is unrelated.
**Method:** read the comparison core (`normalized/diff.py`, `recipe_facts.py`),
the planner, the magnet from-spec CLI, and real artifacts under
`/data/crfm-helm-{public,audit}`. 2026-06-25.

---

## 1. The bug (why `same_deployment` is masked)

The comparison's deployment fact is a plain string compare in
[`normalized/diff.py:209`](../../eval_audit/normalized/diff.py):

```python
if _both(facts_a.model_deployment, facts_b.model_deployment) and (
    facts_a.model_deployment != facts_b.model_deployment
):
    deployment_changed = True
```

`facts_*.model_deployment` is resolved **only** from the run spec's
`adapter_spec.model_deployment`
([`recipe_facts.py:138`](../../eval_audit/normalized/recipe_facts.py) →
`extract_run_spec_fields`). The client override that swaps the engine lives in
`model_deployments.yaml`, which is **never written into `run_spec.json`**. So:

| run | recorded `adapter_spec.model_deployment` | source |
|---|---|---|
| official public | `together/phi-2` | `/data/crfm-helm-public/.../run_spec.json` |
| **old run-entry local** | `microsoft/phi-2` | on disk, pre-migration hf run |
| **from-spec local (by-name)** | `together/phi-2` | replayed verbatim |

- Old path: `microsoft/phi-2 != together/phi-2` → `deployment_changed=True` →
  **`same_deployment=no`** (correct).
- From-spec by-name: `together/phi-2 == together/phi-2` → `deployment_changed=False`
  → **`same_deployment=yes`** (the substitution is invisible).

There is **no compensating signal**: the planner declares *judge* substitutions
via a `judge_substitution_planned` flag
([`core_report_planner.py:422`](../../eval_audit/planning/core_report_planner.py)),
but nothing analogous exists for the model deployment. Net effect: a faithful
replay that matches the official metrics reads as "perfect reproduction, identical
deployment," over-claiming; a replay that drifts has no fact explaining why.

## 2. The fix (the central idea)

The `model_deployment` is the **execution endpoint label**, not recipe semantics
— it is precisely the thing we substitute. So record the truth: after
`from_json(run_spec.json, RunSpec)`, rewrite the deployment to the local name,
exactly where the CLI already replaces `max_eval_instances`:

```python
adapter_spec = dataclasses.replace(
    run_spec.adapter_spec,
    model_deployment=local_deployment,   # e.g. vllm/phi-2-local, NOT together/phi-2
    # max_eval_instances replace stays as-is
)
run_spec = dataclasses.replace(run_spec, adapter_spec=adapter_spec)
```

`model` (`microsoft/phi-2`) is **not** touched. The produced `run_spec.json` then
carries the local deployment → `recipe_facts.model_deployment` differs from the
official → `same_deployment=no`, via the *existing* `diff.py` logic. No index /
planner / report / diff changes. This restores exactly the signal the old
run-entry path produced.

## 3. Why this is correct and safe

- **Pairing is untouched.** The produced run dir is named by `run_spec.name`,
  which we do not modify, and HELM run names encode `model=microsoft_phi-2` —
  **never `model_deployment`** (verified against the official dir name). So the
  dir stays name-identical to the official and canonical-key pairing / logical
  run key are inert. (The existing `max_eval_instances` replace already proves the
  name survives a `dataclasses.replace` of `adapter_spec`.)
- **It is honest, not less faithful.** Scenario, prompt construction, metrics,
  annotators, and `adapter_spec.model` all replay verbatim. Only the endpoint
  label changes — to the endpoint that actually served the run. The model
  identity (`same_model=yes`) and recipe facts stay correct; the one fact that
  *should* differ now does.
- **HELM consistency holds.** HELM looks up the client by `model_deployment`; as
  long as the local deployment is registered (it is — via the override / bundle)
  and its `model_name` equals `adapter_spec.model` (`microsoft/phi-2`), the run
  proceeds. The window service is derived from the *local* deployment, so it must
  carry `max_sequence_length` (already fixed: `2047`).

## 4. Changes

### Change 1 — magnet from-spec CLI: optional deployment rewrite

In `submodules/aiq-magnet/.../materialize_helm_run_from_spec.py`:

- Add config field `model_deployment` (optional, default unset). Treat as
  **algo identity** (a different deployment is a different run).
- In the substitution step (where `max_eval_instances` is replaced), when set:
  `adapter_spec = dataclasses.replace(adapter_spec, model_deployment=<value>)`.
  Rewrite **only** `model_deployment`; never `model`.
- Record provenance in `adapter_manifest.json`:
  `replay.deployment_substitution = {"from": <official>, "to": <local>}` (and
  leave it `null` when unset). This is provenance only — the comparability signal
  comes for free from the rewritten spec; the record exists so the substitution is
  auditable from the artifact.
- **This reverses the replay plan §5 "no `--model-deployment` arg, by-name only"
  decision.** Document the reversal there (Change 7 below): by-name is exactly
  what hid the substitution. Default (arg unset) is still pure by-name, so the
  general replay path is unchanged unless a caller opts in.

### Change 2 — image rebuild & re-pin (hard prerequisite)

The CLI lives in the runner image. As with the original Change 0: commit the
magnet change, `./docker/build.sh` → capture digest → re-pin `E2E_CONTAINER_IMAGE`
→ deliberate gitlink bump. **Until this lands every rewrite is a silent no-op**
(the old image ignores the new arg). `06_check_container_image.sh` verifies.

### Change 3 — eval_audit manifest plumbing (mirror `precomputed_root`)

Thread the rewrite target manifest → container, exactly as `precomputed_root` /
`model_deployments_fpath` already flow:

- `manifests/models.py`: add `model_deployment: str | None = None`.
- `manifests/builders.py`: add `--model-deployment` flag + `_build_manifest`
  wiring.
- `integrations/kwdagger_bridge.py`: in the from-spec branch, add
  `matrix["helm.model_deployment"] = manifest.get("model_deployment")`.
- `pipelines/helm_docker_pipeline.py`: the from-spec node renders
  `--model_deployment=<v>` when present, and adds `model_deployment` to its
  `algo_params` (the run-entry node leaves it out). The replay plan said *not* to
  add it under by-name; we add it now.

**Invariant to honor + test:** the manifest's `model_deployment` MUST equal a
deployment `name` registered in the run's `model_deployments.yaml` (the override
for hf, the bundle for vLLM) — otherwise HELM can't resolve the client. See the
decision in §5 for how to avoid the two-places-in-sync coupling.

### Change 4 — hf override + manifest use a LOCAL name

- `configs/debug/e2e_phi2_fromspec_overrides.yaml`: rename the deployment from
  `together/phi-2` → **`huggingface/phi-2-local`** (keep `model_name`,
  `tokenizer_name`, `max_sequence_length: 2047`, the `HuggingFaceClient` spec).
- `manifests/e2e-phi_2-huggingface-philosophy-{smoke,full}.yaml`: add
  `model_deployment: huggingface/phi-2-local`.

### Change 5 — vLLM: drop the rekey, rewrite to the bundle's native name

This *simplifies* the shipped vLLM path (`integrations/infer_stack/adapter.py`):

- **Remove the Change-2b rekey** (`from_spec_model_deployment_name: together/phi-2`
  on the profile + the `materialize_benchmark_bundle` rekey branch). The bundle
  keeps its native `vllm/phi-2-local` name (which already carries
  `max_sequence_length` from the catalog `max_model_len`).
- `_manifest_doc` / the exporter: when `from_run_spec`, set the generated
  manifest's `model_deployment` to the model entry's name (`vllm/phi-2-local`) —
  the same name the bundle registers, so the rewrite target and the registration
  agree by construction (no drift). This replaces emitting the rekey.

### Change 6 — tests

- **magnet (unit):** with `model_deployment` set, the loaded spec's
  `adapter_spec.model_deployment` is rewritten and `run_spec.name` is unchanged;
  `model` is untouched; `adapter_manifest.replay.deployment_substitution` records
  from→to. With it unset, the spec is byte-unchanged (pure by-name).
- **eval_audit comparability (the proof):** build two `RecipeFacts`
  (`together/phi-2` official vs `vllm/phi-2-local` / `huggingface/phi-2-local`
  local) and assert `diff.py` yields `deployment_changed=True` /
  `same_deployment=no`. This directly demonstrates the masking is gone.
- **plumbing:** make-manifest emits `model_deployment`; the bridge from-spec
  branch puts it on the matrix; the from-spec node renders `--model_deployment`.
- **exporter:** the vLLM generated `model_deployments.yaml` binds
  `vllm/phi-2-local` (NOT `together/phi-2`), and the generated manifest's
  `model_deployment` equals that name (the §3 invariant).
- **override (regression guard, update existing):** the hf override registers a
  *local* name (not `together/phi-2`) and still carries `max_sequence_length`.
- **invariant guard:** a test asserting each from-spec manifest's
  `model_deployment` is present in its referenced `model_deployments.yaml`.

### Change 7 — docs

- This plan.
- `e2e-from-run-spec-migration-plan.md`: rewrite §5 (substitution is now
  *deployment-rewrite*, not by-name), Change 1/2 (local override names + drop the
  rekey + new `model_deployment` field), and Change 5 (`same_deployment=no` is now
  correct *and explained* — the local run records the local deployment).
- `run-from-run-spec-json-plan.md` §5/§10: note the CLI now supports an optional
  `model_deployment` rewrite; default stays by-name, but the e2e (and any audit
  that substitutes a local engine) opts in for comparability honesty.

## 5. Decision: explicit arg vs. auto-derive from the override

**Recommended: explicit `--model-deployment` (Changes 1+3).** Predictable, part
of algo identity, and mirrors the existing `precomputed_root` /
`model_deployments_fpath` manifest-field plumbing. Cost: the manifest value and
the registered override `name` must agree (the §3 invariant) — guarded by a test.

**Alternative: auto-derive (magnet-only).** The CLI already loads the override;
it could rewrite `adapter_spec.model_deployment` to the override deployment whose
`model_name` matches `adapter_spec.model`, with **no** manifest/bridge/node
plumbing and a single source of truth (rename the override deployment → the spec
follows). Cost: implicit, and the `model_name` match is a heuristic (fragile if an
override carries two deployments for the same model). Judge-dependent runs are
handled because the judge deployment has a different `model_name`.

Both require the Change-2 image rebuild, so that cost is identical. Pick explicit
for predictability; auto-derive only if the reduced surface is worth the implicit
behavior. (A hybrid — explicit wins, else auto-derive — is possible but adds CLI
branching for little gain.)

## 6. What deliberately does NOT change

- `adapter_spec.model` (`microsoft/phi-2`) and `run_spec.name` — so `same_model`
  stays `yes` and pairing/canonical key are inert.
- Stages 4–6, `recipe_facts`, `diff.py`, the planner, the report — the fix lands
  entirely in *what the run records*, so the comparison layer is untouched and
  the `same_deployment=no` it already knows how to compute returns for free.
- The incomparable control (still run-entry; never replayed).
- The default `from_run_spec` behavior for non-opted-in callers (pure by-name).

## 7. Open items / risks

- **Image rebuild gates everything (Change 2).** With the old image the arg is
  ignored and the run silently stays by-name (`same_deployment=yes`) — so a test
  must assert the *produced* spec carries the local name on a real run, not just
  that the flag was passed. Until the rebuild, the masking persists.
- **Manifest/override name agreement (§3 invariant).** A mismatch is a hard HELM
  failure (deployment not found), not a silent wrong answer — acceptable, and the
  §6 invariant test catches it pre-run.
- **Judge-dependent replays (future).** The same rewrite applies to the primary
  model deployment; judge deployments are substituted via their own override
  entries and would want the analogous treatment if/when a judge-dependent run is
  audited from spec (ties back to the `same_judge` machinery).
- **Provenance vs. signal.** v1 gets the comparability signal purely from the
  rewritten spec; `adapter_manifest.replay.deployment_substitution` is provenance
  only. A future enhancement could let the planner surface "recipe deployment X,
  locally substituted → Y" as a richer fact, but `same_deployment=no` is already
  the honest answer and needs no interpretation.

## 8. Sequencing

1. magnet CLI: add the `model_deployment` rewrite + `adapter_manifest` record
   (Change 1) + unit tests; commit the submodule.
2. Rebuild + re-pin the image; bump the gitlink (Change 2).
3. eval_audit plumbing (Change 3) + comparability/plumbing tests (Change 6).
4. hf override + manifest local names (Change 4); drop the vLLM rekey + emit the
   manifest `model_deployment` (Change 5); update exporter tests.
5. Run the hf + vLLM smoke; confirm the produced `run_spec.json` records the local
   deployment and the per-scenario report shows `same_deployment=no`.
6. Docs (Change 7).

# Migrating the OLMo reproduction to faithful `run_spec.json` replay — plan

**Status:** PLAN (not yet implemented). The from-spec *machinery* is already
shipped (built for the phi-2 e2e); this migration is **wiring + run-entry
reconciliation**, not new infrastructure.
**Scope:** all **six** OLMo vLLM presets in
[`reproduce/olmo_models/`](../../reproduce/olmo_models/). Unlike the phi-2 e2e there
is **no carve-out** — OLMo has no temperature-deviation negative control and no
hf-direct scenario, so every preset migrates uniformly.
**Depends on (all IMPLEMENTED):**
[`run-from-run-spec-json-plan.md`](run-from-run-spec-json-plan.md) (the replay
pipeline), [`from-spec-deployment-rewrite-plan.md`](from-spec-deployment-rewrite-plan.md)
(the `--model-deployment` rewrite), and
[`e2e-from-run-spec-migration-plan.md`](e2e-from-run-spec-migration-plan.md) (the
exporter/grid wiring this mirrors). Also depends on the converter `prod_env`
registration fix (`b5c4cfe`, `eval_audit/normalized/eee_artifacts.py`) — without
it the local run's `vllm/allenai-<model>` deployment won't resolve at HELM→EEE
conversion time (the same bug the e2e hf scenario hit).
**Method:** read the six OLMo presets in
[`adapter.py`](../../eval_audit/integrations/infer_stack/adapter.py), the olmo
grid + `_lib.sh`, the e2e from-spec implementation, and the **real** official OLMo
artifacts under `/data/crfm-helm-public`. 2026-06-29.

---

## 1. Why this migration (the central insight)

The reproducibility question is *"same recipe, same data, same model → same
metrics?"* Today the OLMo local side **reconstructs** each recipe from a
hand-authored run-entry string and re-derives it under the installed crfm-helm.
That lets *recipe drift* confound the comparison: a metric difference no longer
isolates model **execution**. From-spec instead replays the **official
`run_spec.json`** — the very recipe the audit compares against — so the local
reproduction differs from the official only by execution.

This is not hypothetical for OLMo — its own runbook NOTES already document the
drift:

- [`NOTES-bbq-instructions-drift.md`](../../reproduce/olmo_models/NOTES-bbq-instructions-drift.md)
  — the run-entry/run-expander path silently drifts BBQ's
  `output_format_instructions` from the official recipe (raises
  `comparability_drift:same_instructions`). It even points at the truth: *"on disk
  (`/data/crfm-helm-public/.../run_spec.json`, the authoritative recipe)."*
- [`NOTES-dropped-run-expander-keys.md`](../../reproduce/olmo_models/NOTES-dropped-run-expander-keys.md)
  — run-expander keys dropped vs. the official `run_spec.json`.

These are symptoms of not being from-spec. Under faithful replay they vanish by
construction — the BBQ instruction drift becomes the headline parity result
(Change 6).

## 2. What the OLMo runbook looks like today

1. **Six vLLM presets** (`OLMO_TARGETS` in
   [`reproduce/olmo_models/_lib.sh:87`](../../reproduce/olmo_models/_lib.sh)),
   each defined in [`adapter.py`](../../eval_audit/integrations/infer_stack/adapter.py)
   (lines ~474–943) with `access_kind: vllm-direct`, a
   `model_deployment_name: vllm/allenai-<model>`, and `smoke_manifest` /
   `full_manifest` blocks whose `run_entries` are **embedded in the preset**
   (no checked-in per-preset manifest, unlike the e2e `manifests/` dir).
2. **Run-entries are hand-authored** ("from `candidate_runs.json`") and carry
   recipe tokens (`method=…`, `output_format_instructions=…`,
   `max_train_instances=…`, `subject=all`, …). **None** carry from-spec fields.
3. **Grid is run-entry-based.** `10_run_smoke_grid.sh` / `15_run_full_grid.sh`
   call `export-benchmark-bundle --preset … --bundle-root …` (**no `--from-spec`**)
   then `eval-audit-run <manifest> --container-image "$OLMO_CONTAINER_IMAGE"
   --lease` (no `--from-spec`).
4. **Downstream is canonical-key based.**
   [`configs/virtual-experiments/olmo-models.yaml`](../../configs/virtual-experiments/olmo-models.yaml)
   groups the six `audit-<preset>-full` experiments and pairs them against an
   `official_public_index` source by canonical logical key. (Today the
   comparison facts the planner would derive collapse to
   `comparability_unknown:*` — from-spec is what lets them resolve.)

## 3. The from-spec machinery already exists (this is wiring)

Built and shipped for the phi-2 e2e, reusable verbatim:

| Capability | Where | Status |
|---|---|---|
| Replay a discovered official `run_spec.json` | `materialize_helm_run_from_spec` (aiq-magnet) | shipped |
| Token-subset discovery of the official dir | `find_best_precomputed_run(run_entry, precomputed_root)` | shipped |
| Deployment **rewrite** to the local name | magnet `--model-deployment` + manifest `model_deployment` field + bridge/pipeline threading | shipped |
| Exporter emits from-spec bundle | `export-benchmark-bundle --from-spec` (`_manifest_doc` threads `from_run_spec` + `precomputed_root` + native `model_deployment`) | shipped |
| Local deployment resolves at conversion | `prod_env` registration in the converter (`b5c4cfe`) | shipped |

So OLMo needs **no core/CLI changes** — only preset data, grid wiring, run-entry
reconciliation, and a discovery safety-net.

## 4. Feasibility matrix (the per-model inventory)

From the real artifacts under `/data/crfm-helm-public` (182 official OLMo runs):

| Preset / experiment stem | Served model (`model=`) | Official runs | Root(s) / version | Official `model_deployment` | In-scope benchmarks (current set) | `precomputed_root` |
|---|---|---|---|---|---|---|
| `allenai-olmo-7b` | `allenai/olmo-7b` | 85 | **mmlu v1.1.0** (62) **+ lite v1.2.0** (rest) | `together/olmo-7b` | commonsense, gsm, legalbench×5, med_qa, mmlu×48, narrative_qa, wmt_14×5 | `/data/crfm-helm-public` (spans two roots ⚠ §7) |
| `allenai-olmo-1-7-7b` | `allenai/olmo-1.7-7b` | 57 | mmlu v1.4.0 | *(verify; likely `together/…` or `huggingface/…`)* | mmlu×49 | `/data/crfm-helm-public/mmlu` |
| `allenai-olmoe-1b-7b-0125-instruct` | `allenai/olmoe-1b-7b-0125-instruct` | 10 | capabilities v1.8.0 (+ safety v1.10.0) | `huggingface/olmoe-1b-7b-0125-instruct` | bbq, gpqa, ifeval, mmlu_pro | `/data/crfm-helm-public/capabilities` |
| `allenai-olmo-2-1124-7b-instruct` | `allenai/olmo-2-1124-7b-instruct` | 10 | capabilities v1.8.0 (+ safety v1.10.0) | `huggingface/olmo-2-1124-7b-instruct` | bbq, gpqa, ifeval, mmlu_pro | `/data/crfm-helm-public/capabilities` |
| `allenai-olmo-2-1124-13b-instruct` | `allenai/olmo-2-1124-13b-instruct` | 10 | capabilities v1.8.0 (+ safety v1.10.0) | `huggingface/olmo-2-1124-13b-instruct` | bbq, gpqa, ifeval, mmlu_pro | `/data/crfm-helm-public/capabilities` |
| `allenai-olmo-2-0325-32b-instruct` | `allenai/olmo-2-0325-32b-instruct` | 10 | capabilities v1.8.0 (+ safety v1.10.0) | `huggingface/olmo-2-0325-32b-instruct` | bbq, gpqa, ifeval, mmlu_pro | `/data/crfm-helm-public/capabilities` |

**All six are replayable.** The "olmo-1-7-7b has 0 runs" result is a naming
artifact: the **served model is `olmo-1.7-7b`** (period), which has 57 mmlu runs;
the hyphen form `olmo-1-7-7b` is only the *experiment/endpoint* name. The
local **rewrite target** is each bundle's native `vllm/allenai-<model>`, which
differs from every official deployment above → `same_deployment=no` for free.

The 4 instruct models' current benchmarks (bbq, gpqa, ifeval, mmlu_pro) are all
in **capabilities v1.8.0**, so a single narrow `precomputed_root` covers them.
The safety v1.10.0 benchmarks (anthropic_red_team, harm_bench, …) and capabilities
extras (omni_math, wildbench) **exist officially but are out of current scope** —
from-spec makes adding them later trivial (just add bare discovery keys).

## 5. Decisions

- **Migrate all six uniformly.** No carve-out; from-spec is unconditional (as the
  e2e comparable scenarios are).
- **Per-preset `precomputed_root`** (not one global root): `capabilities` for the
  four instruct models, `mmlu` for olmo-1.7-7b, and the **parent**
  `/data/crfm-helm-public` for olmo-7b (its benchmarks span mmlu + lite, §7).
- **Reduce each run-entry to a minimal discovery key** (Change 1) — the single
  biggest task. Discovery requires *requested tokens ⊆ candidate dir name*, and
  today's entries carry hand-authored tokens that are **absent or different** in
  the official dirs (e.g. `subject=all` vs the official `subset=all`,
  `output_format_instructions=mcqa`). Strip them to: benchmark stem + `model=` +
  only the disambiguating tokens that are *present in the official dir name*.
- **Deployment-rewrite via the exporter's native name** (`vllm/allenai-<model>`),
  automatic under `--from-spec`. No override yaml (vLLM generates its own binding).
- **Scope = the current benchmark set.** Do not expand to safety/omni_math now.

## 6. Changes

### Change 0 — image + converter prerequisites (verify, likely no-op)
`OLMO_CONTAINER_IMAGE` defaults to the **same** `eval-audit-helm-runner` image as
the e2e; once that image carries `materialize_helm_run_from_spec` + the
`--model-deployment` rewrite (e2e Change 0 / deployment-rewrite Change 2), OLMo
inherits it. Verify the pinned digest includes both (`07_check_container_image.sh`).
The converter `prod_env` fix (`b5c4cfe`) is already in the tree.

### Change 1 — reconcile run-entries + add from-spec fields (the core change)
Per preset, in `adapter.py`'s `smoke_manifest` / `full_manifest`:
1. **Add** `precomputed_root: <per-table>`, `container_network: host`,
   `container_gpus: none`, `hf_cache_dir: ~/.cache/eval-audit-hf` (mirror the
   phi-2 vLLM preset).
2. **Reduce** every `run_entries` string to a discovery key. The reduction rule:
   keep the benchmark stem, `model=allenai/<served>`, and only tokens present in
   the official dir name that disambiguate among multiple official runs for that
   (benchmark, model). Examples:

| Current (drifted) run-entry | Reduced discovery key | Why |
|---|---|---|
| `mmlu_pro:subject=all,use_chain_of_thought=true,use_few_shot=false,num_output_tokens=2048,model=allenai/olmo-2-0325-32b-instruct` | `mmlu_pro:model=allenai/olmo-2-0325-32b-instruct` | official uses `subset=all` not `subject=all`; one mmlu_pro run/model → bare key matches |
| `bbq:subject=all,method=multiple_choice_joint,output_format_instructions=mcqa,max_train_instances=0,model=…` | `bbq:model=allenai/olmo-2-…` | `output_format_instructions`/`max_train_instances` are hand-authored, absent from the official dir name (the very drift the NOTES document) |
| `mmlu:subject=abstract_algebra,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b` | `mmlu:subject=abstract_algebra,model=allenai/olmo-7b` | keep `subject=` (disambiguates among 48); drop `method`/`eval_split` if absent officially (confirm via Change 4) |

   The recipe (method, instructions, CoT, temperature, max_tokens) now comes from
   the matched `run_spec.json`, not the entry.

### Change 2 — exporter threading (verify generalization)
The e2e fix made `_manifest_doc` read `from_run_spec` + `precomputed_root` from
the spec instead of hardcoding `precomputed_root: None`
([`adapter.py`](../../eval_audit/integrations/infer_stack/adapter.py); e2e plan
Change 2a). Confirm that path is **preset-agnostic** (keys off the manifest
block, not a phi-2 name) so the OLMo blocks' new fields flow through. The native
`model_deployment` emission for single-deployment bundles already applies to the
OLMo presets (each has one `model_deployment_name`).

### Change 3 — grid wiring
In `reproduce/olmo_models/10_run_smoke_grid.sh` / `15_run_full_grid.sh`, append
`--from-spec` to the `export-benchmark-bundle` call **unconditionally** (no
`e2e_uses_from_spec` carve-out — every OLMo preset is comparable). The
`eval-audit-run` line is unchanged (the bridge selects the pipeline from
`manifest['from_run_spec']`).

### Change 4 — discovery dry-check (the safety net, do this FIRST)
A CPU-only validator (no GPU, no serving) that, for every reduced run-entry,
asserts `find_best_precomputed_run` resolves **exactly one** official dir under
that preset's `precomputed_root`, and that the matched `run_spec.json`
deserializes + passes preflight. Ship it as both a corpus-gated test
(mirror `tests/test_e2e_from_spec_bundle.py`) **and** a runbook preflight
(`reproduce/olmo_models/08_check_discovery.sh`). This catches every token
mismatch (subject/subset, dropped/extra tokens) before a single GPU-hour. Run it
iteratively while doing Change 1.

### Change 5 — downstream verification (expected no-op)
`20_index_local` → `30_compose` → `40_build_summary` and
`configs/virtual-experiments/olmo-models.yaml` are untouched. Verify on first run
that the paired rows now resolve `same_deployment=no` and the
`comparability_unknown:*` warnings clear for benchmarks with a public counterpart.

### Change 6 — tests + parity diff
- **Discovery dry-check** (Change 4) as a committed test.
- **Comparability proof:** `RecipeFacts` for an official `huggingface/olmo-2-…`
  (or `together/olmo-7b`) vs local `vllm/allenai-…` → `same_deployment=no`.
- **Parity diff (the methodology deliverable):** diff a from-spec `run_spec.json`
  / `stats.json` against the archived run-entry result for BBQ — quantifies the
  `output_format_instructions` drift the NOTES document, now removed.

### Change 7 — port the `data_dir` hardening (`8d96a47`)
While in `reproduce/olmo_models/_lib.sh`, fold in the e2e's data-dir resolution
(`env > settings.yaml data_dir: pin > /data default`) + the NFS/autofs warning,
replacing the current hard `INFER_STACK_DATA_DIR=$HOME/.local/share/infer_stack`
default (the bind-mount footgun). Pin `data_dir` in
`reproduce/olmo_models/config/infer_stack/settings.yaml`. *(Independent of
from-spec, but olmo `_lib.sh` is open anyway.)*

### Change 8 — docs
Update `reproduce/olmo_models/README.md` (from-spec is now the default; how
discovery + the deployment-rewrite work) and annotate the two `NOTES-*` drift
files as **resolved by from-spec** (kept as the historical "why").

## 7. Open items / risks

- **olmo-7b spans two precomputed roots** (mmlu v1.1.0 + lite v1.2.0). One
  manifest carries one `precomputed_root`, so either point it at the parent
  `/data/crfm-helm-public` (broad scan — discovery caches, but ~62 entries ×
  full-corpus walk is the slowest case) **or** split olmo-7b into two experiments
  (mmlu vs lite) each with a narrow root. Recommend the parent root first; measure,
  and split only if discovery is too slow. Resolve under Change 4.
- **Token reconciliation is per-benchmark and easy to get subtly wrong.** The
  Change-4 dry-check is the guardrail — treat a "0 matches" or ">1 match" as a
  hard stop, never run it past discovery.
- **olmo-1.7-7b official deployment unverified** — open one `run_spec.json` during
  Change 1 to confirm the rewrite-from name (doesn't block; the rewrite handles
  any official name).
- **`max_eval_instances` is a prefix, not official parity.** The full grid caps
  below the official instance count; the replay truncates deterministically.
  State it so "full" isn't read as complete-set parity (same caveat as e2e §7).
- **Official OLMo-2 recipes use CoT + `temperature: 1`** — replayed faithfully
  (correct), *not* a deviation to flag. (Contrast the phi-2 `incomparable`
  control, which *injects* `temperature=1`; OLMo has no such control.)
- **Request-shape params tuned for the official serving stack** (token limits)
  replayed against vLLM may surface request mismatches — a *real* reproducibility
  signal, not a migration bug. The preset's `helm_max_sequence_and_generated_tokens_length`
  reserve (2016/4064) stays (it's a serving guardrail, orthogonal to the recipe).

## 8. Sequencing

1. **Change 4 first** — stand up the discovery dry-check against the *current*
   run-entries to see exactly which fail and why (baseline the token drift).
2. **Change 1** — reduce run-entries + add from-spec/container fields, iterating
   against the dry-check until all six presets resolve 1:1. Start with an instruct
   model (4 entries, single `capabilities` root) before olmo-7b (62 entries, two
   roots).
3. **Change 2** verify exporter threading; **Change 3** grid wiring.
4. Smoke one instruct preset end-to-end: confirm `status=replayed`, produced dir
   == official `run_spec.name`, recorded `model_deployment: vllm/allenai-…`,
   report `same_deployment=no`.
5. Full grid + compose + summary; confirm pairing + comparability facts populate
   (Change 5).
6. **Change 6** parity diff (BBQ) + **Change 7** data-dir hardening + **Change 8**
   docs.

# OLMo from-spec reproduction — status & plan

**Status (2026-07-06): IMPLEMENTED (host/CPU side); GPU verification is the only
open work.** This is the single live doc for the OLMo from-spec effort. It folds in
and supersedes two now-archived siblings:
`olmo-from-spec-handoff.md`
(the continuation handoff — env gotchas + landed-commit table) and
[`olmo-multi-model-from-spec-plan.md`](../historical/planning/olmo-multi-model-from-spec-plan.md)
(the multi-model fan-out design).

**What's done.**
- **Single-model from-spec migration — CPU-complete** on `impl/run-from-run-spec`.
  Changes 1–4, 6, 7, 8 landed (commits `b5c4cfe` converter `prod_env` fix,
  `5c31a05`/`b2ebad7` discovery dry-check + preset reconciliation incl. the
  olmo-7b `-mmlu`/`-lite` split, `99bdc0e` grid `--from-spec`, `037ba68`
  corpus-gated tests, `1ad68a8` data_dir hardening, `90d9581` docs). All 7 presets
  resolve 1:1 (149 entries, 0 NO_MATCH / 0 AMBIGUOUS). See the per-change DONE
  markers in §6.
- **Multi-model fan-out — implemented.** The combined preset
  `allenai-olmo-combined` (`eval_audit/integrations/infer_stack/presets.py`) plus
  the [`reproduce/olmo_models_combined/`](../../reproduce/olmo_models_combined/)
  runbook fan five OLMo models across GPUs under one schedule via
  `export-benchmark-bundle --from-spec --freeze-rel-paths` + `eval-audit-run
  --tmux-workers N`; olmo-7b runs as two extra single-model suites folded into the
  same grouped virtual experiment. The multi-model plan's local-deployment-token
  discovery strip is realized through the exact-path `--freeze-rel-paths` replay.

**What's open (GPU box + the user's own shell).**
- **Change 5 — first GPU smoke + downstream verification** (single-model and
  combined): confirm the produced run dir == official `run_spec.name`, recorded
  `model_deployment: vllm/allenai-<model>`, `same_deployment=no`, and that the
  `comparability_unknown:*` warnings clear. Preflight with
  `reproduce/olmo_models/08_check_discovery.sh` (must be 0 NO_MATCH / 0 AMBIGUOUS).
- **Change 6 parity diff** — diff a from-spec BBQ run against the archived
  run-entry result to quantify the removed `output_format_instructions` drift
  (needs a produced from-spec run dir).

**Scope:** all six OLMo vLLM presets (olmo-7b split into `-mmlu`/`-lite`) in
`reproduce/olmo_models/` — no carve-out (OLMo has no
temperature-deviation control, unlike the phi-2 e2e).
**Depends on (all IMPLEMENTED):**
[`run-from-run-spec-json-plan.md`](run-from-run-spec-json-plan.md) (the replay
pipeline),
[`from-spec-deployment-rewrite-plan.md`](../historical/planning/from-spec-deployment-rewrite-plan.md)
(the `--model-deployment` rewrite), and
[`e2e-from-run-spec-migration-plan.md`](../historical/planning/e2e-from-run-spec-migration-plan.md)
(the exporter/grid wiring this mirrors). Also depends on the converter `prod_env`
registration fix (`b5c4cfe`, `eval_audit/normalized/eee_artifacts.py`) — without it
the local run's `vllm/allenai-<model>` deployment won't resolve at HELM→EEE
conversion time (the same bug the e2e hf scenario hit).
**Method:** read the OLMo presets in
[`adapter.py`](../../eval_audit/integrations/infer_stack/adapter.py)/`presets.py`, the
olmo grids + `_lib.sh`, and the **real** official OLMo artifacts under
`/data/crfm-helm-public`. Baseline measured 2026-06-29.

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

- `NOTES-bbq-instructions-drift.md`
  — the run-entry/run-expander path silently drifts BBQ's
  `output_format_instructions` from the official recipe (raises
  `comparability_drift:same_instructions`). It even points at the truth: *"on disk
  (`/data/crfm-helm-public/.../run_spec.json`, the authoritative recipe)."*
- `NOTES-dropped-run-expander-keys.md`
  — run-expander keys dropped vs. the official `run_spec.json`.

These are symptoms of not being from-spec. Under faithful replay they vanish by
construction — the BBQ instruction drift becomes the headline parity result
(Change 6).

## 2. What the OLMo runbook looks like today

1. **Six vLLM presets** (`OLMO_TARGETS` in
   `reproduce/olmo_models/_lib.sh:87`),
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
   `configs/virtual-experiments/olmo-models.yaml`
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

The safety v1.10.0 benchmarks (anthropic_red_team, harm_bench, …) and capabilities
extras (omni_math, wildbench) **exist officially but are out of current scope** —
from-spec makes adding them later trivial (just add bare discovery keys).

### 4.1 Change-4 discovery baseline (MEASURED 2026-06-29)

Ran the dry-check (`eval_audit/cli/check_precomputed_discovery.py`,
`reproduce/olmo_models/08_check_discovery.sh`) against the live corpus with the
**current** run-entries. Results correct two assumptions above:

- **Use a single uniform `precomputed_root: /data/crfm-helm-public`** for every
  preset, not per-suite roots. The per-root enumeration is **4–6 s** (matching is
  in-memory), so the breadth is free, and it's the only root that spans every
  benchmark — including **bbq, which lives under `safety` (v1.10.0), not
  `capabilities`** (the instruct models span two suites just like olmo-7b spans
  mmlu+lite).
- **Almost everything resolves as-is.** `gpqa`/`ifeval`/`mmlu_pro` (instruct) and
  all 57 olmo-1.7-7b `mmlu` entries → RESOLVED with no edit. Official deployments
  confirmed: `huggingface/olmo-2-*`, `huggingface/olmoe-*`,
  `huggingface/olmo-1.7-7b`, `together/olmo-7b`.
- **Only two reductions are needed (Change 1):**
  1. **bbq** (×4 instruct): drop the hand-authored `output_format_instructions=mcqa`
     token — absent from the official `safety` dir (the exact
     `NOTES-bbq-instructions-drift.md` drift). Verified: the reduced key
     `bbq:subject=all,method=multiple_choice_joint,max_train_instances=0,model=…`
     → RESOLVED.
  2. **olmo-7b**: 5 bare `mmlu:subject=…,method=…,model=allenai/olmo-7b` entries
     (no `eval_split=test`) are AMBIGUOUS (match several official copies). Add
     `eval_split=test` (as the sibling entries already have) or dedupe.
- **Net:** 0 NO_MATCH after those two reductions; olmo-7b 71/76 RESOLVED + 5
  AMBIGUOUS→fixable; the four instruct presets 3/4 RESOLVED + bbq→fixable.

## 5. Decisions

- **Migrate all six uniformly.** No carve-out; from-spec is unconditional (as the
  e2e comparable scenarios are).
- **One uniform `precomputed_root: /data/crfm-helm-public`** for every preset
  (revised by the §4.1 measurement — the broad scan is 4–6 s and is the only root
  that spans every benchmark, incl. bbq under `safety`).
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

### Change 1 — reconcile run-entries + add from-spec fields (the core change) — **DONE**
**Outcome:** all 7 presets resolve 1:1 (149 entries, smoke + full, 0 NO_MATCH /
0 AMBIGUOUS under `--strict`). **olmo-7b was split** (per the user decision) into
`allenai-olmo-7b-mmlu` (57 eval_split=test → `/data/crfm-helm-public/mmlu`) and
`allenai-olmo-7b-lite` (19 → `/data/crfm-helm-public/lite`: the 14 lite
benchmarks + the 5 HELM-Lite MMLU subjects) — both serve the one
`allenai-olmo-7b-single` endpoint; `OLMO_TARGETS` and `olmo-models.yaml` updated.
The bbq `output_format_instructions=mcqa` drop was the only token reduction
needed; the 5 olmo-7b mmlu pairs are NOT dupes (each variant reproduces a
different official suite — see §4.1), so they were kept and disambiguated by the
split, not removed.

Per preset, in `adapter.py`'s `smoke_manifest` / `full_manifest`:
1. **Add** `precomputed_root: <per-table>`, `container_network: host`,
   `container_gpus: none`, `hf_cache_dir: ~/.cache/eval-audit-hf` (mirror the
   phi-2 vLLM preset).
2. **Reduce** the run-entries that the §4.1 dry-check flags. **Per measurement,
   only two changes are needed** (most entries already token-subset-match the
   official dirs):

| Entry to fix | Change | Why (measured) |
|---|---|---|
| `bbq:subject=all,method=multiple_choice_joint,output_format_instructions=mcqa,max_train_instances=0,model=…` (×4 instruct) | drop `output_format_instructions=mcqa` → `bbq:subject=all,method=multiple_choice_joint,max_train_instances=0,model=…` | that token is absent from the official `safety` dir (the `NOTES-bbq-instructions-drift.md` drift); reduced key → RESOLVED |
| 5 bare `mmlu:subject=…,method=multiple_choice_joint,model=allenai/olmo-7b` (no `eval_split`) | add `eval_split=test` | bare entries are AMBIGUOUS (match several copies); the sibling entries already carry `eval_split=test` |

   Everything else (`gpqa`/`ifeval`/`mmlu_pro` instruct, all 57 olmo-1.7-7b
   `mmlu`, the olmo-7b `mmlu`+lite set) resolves unchanged. The recipe (method,
   instructions, CoT, temperature, max_tokens) now comes from the matched
   `run_spec.json`, not the entry. Re-run the §4.1 dry-check after editing until
   it reports 0 NO_MATCH (and 0 AMBIGUOUS under `STRICT=1`).

### Change 2 — exporter threading (verify generalization) — **DONE (verified, no code change)**
The e2e fix made `_manifest_doc` read `from_run_spec` + `precomputed_root` from
the spec instead of hardcoding `precomputed_root: None`
([`adapter.py`](../../eval_audit/integrations/infer_stack/adapter.py); e2e plan
Change 2a). Confirm that path is **preset-agnostic** (keys off the manifest
block, not a phi-2 name) so the OLMo blocks' new fields flow through. The native
`model_deployment` emission for single-deployment bundles already applies to the
OLMo presets (each has one `model_deployment_name`).
**Verified:** drove `materialize_benchmark_bundle(..., from_run_spec=True)` for
`allenai-olmo-7b-mmlu` / `-lite` / `-olmoe-…-instruct` and asserted each emitted
manifest carries `from_run_spec: true`, the preset's own `precomputed_root`
(`/mmlu`, `/lite`, parent), and `model_deployment: vllm/allenai-<model>` (a
registered deployment); the run-entry default stays byte-compatible. The path is
fully preset-agnostic — no code change needed.

### Change 3 — grid wiring — **DONE** (`99bdc0e`)
In `reproduce/olmo_models/10_run_smoke_grid.sh` / `15_run_full_grid.sh`, append
`--from-spec` to the `export-benchmark-bundle` call **unconditionally** (no
`e2e_uses_from_spec` carve-out — every OLMo preset is comparable). The
`eval-audit-run` line is unchanged (the bridge selects the pipeline from
`manifest['from_run_spec']`).

### Change 4 — discovery dry-check (the safety net, do this FIRST) — **DONE**
A CPU-only validator that, for every run-entry, resolves it against
`precomputed_root` with the **same** matcher the replay uses and classifies
RESOLVED / NO_MATCH / AMBIGUOUS (reporting the matched dir + its official
deployment). **Shipped:** `eval_audit/cli/check_precomputed_discovery.py` (with a
`--entry` override to validate reduced keys before editing presets) and the
runbook preflight `reproduce/olmo_models/08_check_discovery.sh`. The baseline run
is captured in §4.1. *Remaining:* fold the dry-check into a corpus-gated pytest
(mirror `tests/test_e2e_from_spec_bundle.py`) so CI guards it.

### Change 5 — downstream verification (expected no-op) — **REMAINING (GPU; user's shell)**
`20_index_local` → `30_compose` → `40_build_summary` and
`configs/virtual-experiments/olmo-models.yaml` are untouched. Verify on first run
that the paired rows now resolve `same_deployment=no` and the
`comparability_unknown:*` warnings clear for benchmarks with a public counterpart.
This is the only step left — it needs a GPU (`aiq-gpu`) and the user's own shell
(the agent's e2e venv interpreter is a dangling symlink; see the handoff §"CRITICAL
environment gotchas"). Smoke one preset first, confirm the produced dir ==
official `run_spec.name`, the recorded `model_deployment` is `vllm/allenai-<model>`,
and the per-scenario report shows `same_deployment=no`; then full grid + compose +
summary.

### Change 6 — tests + parity diff — **DONE (tests; parity diff pending the GPU run)** (`037ba68`)
- **Discovery dry-check** as a committed corpus-gated test —
  [`tests/test_olmo_from_spec.py`](../../tests/test_olmo_from_spec.py); all 14
  (preset, mode) blocks resolve 1:1 (0 NO_MATCH / 0 AMBIGUOUS), root enumerations
  cached module-wide. **Done.**
- **Comparability proof:** `RecipeFacts` for an official `together/olmo-7b` /
  `huggingface/olmo-1.7-7b` / `huggingface/olmo-2-…` / `huggingface/olmoe-…` vs
  local `vllm/allenai-…` → `same_deployment=no` via `normalized/diff` +
  `_same_value_fact`. **Done** (same test file).
- **Parity diff (the methodology deliverable):** diff a from-spec `run_spec.json`
  / `stats.json` against the archived run-entry result for BBQ — quantifies the
  `output_format_instructions` drift the NOTES document, now removed. **Pending the
  first GPU run** (needs a produced from-spec run dir to diff against).

### Change 7 — port the `data_dir` hardening (`8d96a47`) — **DONE** (`1ad68a8`)
While in `reproduce/olmo_models/_lib.sh`, fold in the e2e's data-dir resolution
(`env > settings.yaml data_dir: pin > /data default`) + the NFS/autofs warning,
replacing the current hard `INFER_STACK_DATA_DIR=$HOME/.local/share/infer_stack`
default (the bind-mount footgun). Pin `data_dir` in
`reproduce/olmo_models/config/infer_stack/settings.yaml`. *(Independent of
from-spec, but olmo `_lib.sh` is open anyway.)*

### Change 8 — docs — **DONE** (`90d9581`)
Update `reproduce/olmo_models/README.md` (from-spec is now the default; how
discovery + the deployment-rewrite work) and annotate the two `NOTES-*` drift
files as **resolved by from-spec** (kept as the historical "why").

## 7. Open items / risks

- **olmo-7b spans two precomputed roots** (mmlu v1.1.0 + lite v1.2.0) — and the
  instruct models span capabilities + safety. **Resolved by §4.1:** the uniform
  parent `/data/crfm-helm-public` covers all of them and the scan is 4–6 s, so no
  per-suite roots or experiment splits are needed.
- **olmo-7b AMBIGUOUS entries.** 5 bare `mmlu:…,model=allenai/olmo-7b` entries omit
  `eval_split=test` and match several official copies; discovery picks the
  best-scoring deterministically, but add `eval_split=test` (Change 1) so the
  intent is explicit and the dry-check is clean.
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

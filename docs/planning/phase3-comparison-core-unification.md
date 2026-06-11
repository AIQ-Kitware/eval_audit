# Phase 3 Design — Unify the comparison core (HELM + EEE as adapters)

**Status:** DESIGN — no code yet. Requires owner sign-off (see §9) before implementation.
**Author:** drafted 2026-06-11 as the design pass the refactor plan requires for Phase 3.
**Supersedes the Phase 3 sketch in** [`repo-refactor-plan.md`](repo-refactor-plan.md) **§Phase 3** — see §1.2 for the one material correction.

Companion: [`phase3-behavior-equivalence-matrix.md`](phase3-behavior-equivalence-matrix.md) — the test
matrix that gates every sub-stage.

---

## 1. Context: this is not greenfield

Phase 3 sits on top of **two prior, partially-landed efforts** that occupy exactly this territory.
The design's first job is to reconcile them, not to invent a third vocabulary.

### 1.1 The two prior efforts

| Effort | Doc | Goal | Where it stopped |
|---|---|---|---|
| **Normalized refactor** (Stages 1–7) | [`dev/analysis/eee_refactor_stage1_map.md`](../../dev/analysis/eee_refactor_stage1_map.md) | Make `NormalizedRun` the currency; replace `HelmRunDiff` with a `NormalizedDiff` operating on EEE schemas | **Stage 3 landed**: the loader boundary + the `helm_compat.HelmRunView` shim exist. `helm_compat.py:138` literally says *"Stage 4 replaces the comparison core itself; this helper goes away then."* Stage 4 is unstarted. |
| **EEE-only hard split** | [`docs/eee-only-hard-split-todo.md`](../eee-only-hard-split-todo.md) | Make the EEE-only entry points *physically incapable* of importing `eval_audit.helm.*`, so the paper claim "the analysis used only EEE" is a `grep`, not a code review | **Soft separation landed** (`--skip-diagnosis` / `EVAL_AUDIT_SKIP_HELM_DIAGNOSIS=1`, commit `0403ac3`). Hard split deferred, owned by Jon, to be picked up after the heatmap-paper analysis. |

**Phase 3 ≡ normalized-refactor Stage 4 + the enabling half of the EEE-only hard split.** Treat the
existing stage numbering as authoritative and slot this work into it; do not renumber.

### 1.2 The one correction to the original Phase 3 sketch

The plan's Phase 3 sketch said:

> *"Collapse the dual CLIs: `compare-pair` and `compare-pair-eee` become one `eval-audit-compare-pair`
> that auto-detects source type."*

**This is wrong, and the design reverses it.** Merging the entry points directly defeats the
research-integrity requirement in the hard-split doc: the EEE-only path must stay *separately
importable and HELM-free* so that `import eval_audit.cli.from_eee` loading zero `eval_audit.helm.*`
modules is a checkable guarantee (hard-split §4). A single auto-detecting command would, by
construction, import the HELM adapter into the EEE path.

**Corrected goal:** unify the **core** (one `NormalizedRun`-based comparison engine), keep the
**entry points thin and separate**. The duplication worth removing is *logic* duplication (the forked
comparison cores, the thrice-implemented index-row synthesis), **not** the entry-point separation —
which is a feature, not debt.

---

## 2. Current state, grounded in code

### 2.1 The fork: two comparison cores produce overlapping numbers

```
HELM-driven path                         EEE-driven path
────────────────                         ───────────────
compare-pair / compare-batch /           from-eee / compare-pair-eee /
core_metrics (skip_diagnosis=False)      core_metrics (skip_diagnosis=True)
   │                                        │
   ▼                                        ▼
HelmRunView(run) ── helm_compat shim ──► NormalizedRun ◄── EeeArtifactLoader
   │                                        │
   ▼                                        ▼
HelmRunDiff(view_a, view_b)              normalized.compare (ncompare)
  .summary_dict(level=20)                  run_level_core_rows / instance_level_core_rows
  → diagnosis + agreement                  → agreement only; diagnosis = {}
```

- The HELM path's agreement numbers come from `HelmRunDiff` (a 1,902-line class,
  [`helm/diff.py`](../../eval_audit/helm/diff.py)).
- The EEE path's agreement numbers come from
  [`normalized/compare.py`](../../eval_audit/normalized/compare.py) (`run_level_core_rows`,
  `instance_level_core_rows`, `instance_core_score_records`).
- **Both are invoked from the same function** — `_build_pair` in
  [`reports/core_metric_curves.py`](../../eval_audit/reports/core_metric_curves.py) (post-Phase-2
  location) — gated by `skip_diagnosis`. So the agreement math is *already* EEE-native on both paths
  when `skip_diagnosis=True`; what `HelmRunDiff` uniquely still provides is the **`diagnosis` block**
  (`recipe_clean` / `deployment_drift` / `execution_spec_drift` / `comparability_unknown:*`) via
  `_diagnose_repro` ([`helm/diff.py`](../../eval_audit/helm/diff.py), method `_diagnose_repro`).

### 2.2 Three HELM tendrils that keep the EEE path from being clean

A runtime check (`import <mod>; count eval_audit.helm.* in sys.modules`) shows the EEE entry points
are **not** HELM-free today:

| Tendril | Location | Why it's there | Cut strategy |
|---|---|---|---|
| **`helm.metrics` in the EEE-native compare** | `normalized/compare.py:26` `from eval_audit.helm import metrics as helm_metrics` (used at lines 61, 105, 153, 173) | `classify_metric` / `metric_family` — pure string-prefix metric-name taxonomy, 117 lines, stdlib-only, **not actually HELM-specific** | **Lift** `helm/metrics.py` → a HELM-free home (e.g. `eval_audit/metrics_taxonomy.py`), leave a re-export shim in `helm/metrics.py`. Low risk; mechanical (same method as Phase 2). |
| **Unconditional `HelmRunDiff` import** | `core_metric_curves.py:16` `from eval_audit.helm.diff import HelmRunDiff` (module-level) | Used in `_build_pair` only when `skip_diagnosis=False` | The EEE entry points must call a HELM-free renderer (see §3.3). The import being module-level is what makes `from_eee` load `helm.*` even with `skip_diagnosis=True`. |
| **Silent HELM fallback in the EEE loader** | `normalized/loaders.py` `EeeArtifactLoader.load`, the `ref.origin.helm_run_path is not None` branch | Overwrites EEE-derived instances with HELM-derived ones for stable sample ids | **This is the ⚠️ hot finding** in the hard-split doc. Guarded today by `EVAL_AUDIT_EEE_STRICT=1`. Phase 3 removes the fallback from the EEE-only loader path entirely. |

`from-eee` and `compare-pair-eee` each load **2** `eval_audit.helm.*` modules at import today;
`eee_only_heatmap` already loads **0** (it doesn't touch the comparison core). The target is 0 for all
three.

### 2.3 Duplicated index-row synthesis

The EEE → planner-row synthesis is implemented in **`from_eee.py`** (`_build_official_index_row`,
`_build_local_index_row`, `detect_helm_sidecars`, `_discover_eee_artifacts`,
`_extract_artifact_meta`) and **re-used** by `compare_pair_eee.py` and `virtual/compose.py`. That
reuse is already partial-DRY, but the helpers live in a *CLI module* (`cli/from_eee.py`), so every
consumer imports a CLI to get library functions. Phase 3 moves these into a library home alongside
the EEE adapter (see §3.4).

### 2.4 What the planner + render + aggregate already share (the easy half)

Both paths already funnel through **one** planner
([`planning/core_report_planner.py`](../../eval_audit/planning/core_report_planner.py),
`build_planning_artifact` → packets with `components` + `comparisons` + `comparability_facts`), **one**
renderer (`core_metrics` reading `components_manifest.json` + `comparisons_manifest.json`), and **one**
aggregate (`build_reports_summary`, which reads only Stage-5 JSON). The comparability-facts machinery
(`build_comparability_facts`, `_fact_status` → `yes|no|unknown`, `_comparability_warning_lines` →
`comparability_unknown:*`) is **already source-kind-agnostic and correct**. Phase 3 does **not** touch
it. This is why the work is tractable: only the *loader → diff* segment is forked.

---

## 3. Target architecture

### 3.1 One currency, two adapters, two HELM-free-vs-HELM-capable render paths

```
            ┌─ helm_adapter   (run_spec.json + HELM run dirs) ─┐
inputs ─────┤                                                  ├─► NormalizedRun ─► NormalizedDiff ─► report JSON
            └─ eee_adapter    (every_eval_ever artifacts)      ─┘        │              (HELM-free)
                                                                         │
                                                  HELM-driven path only: └─► HelmRunDiff (run_spec
                                                                              semantic diff, raw-evidence
                                                                              inspection) — never imported
                                                                              by the EEE entry points
```

- **`NormalizedRun` is the only thing past the adapter boundary.** It already exists
  ([`normalized/model.py`](../../eval_audit/normalized/model.py)) and already carries everything the
  comparison needs: `evaluation_log` (run-level scores via `metrics_by_id()`), `instances`
  (per-instance `(sample_id, sample_hash, metric_id, score, is_correct)` with a portable `join_key`),
  `source_kind`, `artifact_format`, and `Origin` provenance.

### 3.2 `NormalizedDiff` — the unified comparison core (normalized Stage 4)

A new `eval_audit/normalized/diff.py` (HELM-free) that consumes two `NormalizedRun`s and produces the
**same report shape** the current path emits. It is mostly *assembly* of pieces that already exist:

| Output field (today, from `HelmRunDiff.summary_dict`) | NormalizedDiff source |
|---|---|
| run-level agreement rows | `normalized.compare.run_level_core_rows` (already used) |
| instance-level agreement rows | `normalized.compare.instance_level_core_rows` (already used) |
| per-metric agreement curves | `core_metric_curves._per_metric_agreement_curves` (already EEE-fed) |
| tolerance sweep | port `tolerance_sweep_summary` to read `NormalizedRun.instances` (it already operates on joined instance rows) |
| **`diagnosis`** block | **new `normalized/diagnose.py`** — re-implement `_diagnose_repro` from `recipe_facts` (see §3.5), returning the identical label shape |
| `dataset_overlap` | port `dataset_overlap_from_request_states` (now in `helm/diff_primitives.py`) to read EEE instance ids |

**What stays in `HelmRunDiff`:** the `run_spec.json` *semantic* diff (`_run_spec_semantic_summary`,
`_scenario_semantic_summary`) — these compare raw HELM recipe JSON and are meaningful **only** when
both sides have HELM run dirs. They become "HELM-driven path only," consumed by `compare-pair` /
`compare-batch` for converter validation and debugging — never by the EEE entry points. This matches
normalized-refactor Stage 4's explicit carve-out ("Keep `HelmRunDiff` for run_spec semantic diff").

### 3.3 Two render entry points into the same core

```
core_metrics.main (HELM-capable)            eee_only render (HELM-free)
  imports NormalizedDiff + HelmRunDiff        imports NormalizedDiff ONLY
  diagnosis from recipe_facts, AND            diagnosis from recipe_facts
  run_spec semantic diff when both HELM       (no semantic diff — no run_spec to read)
```

Per hard-split §3, the HELM-free renderer lives in a new **`eval_audit/eee_only/`** namespace
(`eee_only/core_metrics.py`, `eee_only/pair_samples.py`) that a static grep + a `sys.modules` runtime
test both prove HELM-free. The existing `reports/core_metrics.py` stays as the HELM-capable renderer.
Shared sub-logic (everything that's already HELM-free after §2.2 tendril-cutting) is imported by both;
it does **not** get duplicated — the split is at the thin top layer that decides whether `HelmRunDiff`
is in scope.

> **Note on `core_metrics` subprocessing:** both EEE CLIs currently shell out to
> `python -m eval_audit.reports.core_metrics` via subprocess. Once a HELM-free `eee_only` renderer
> exists, the EEE CLIs subprocess to `python -m eval_audit.eee_only.core_metrics` instead — preserving
> the process boundary (and therefore the import-isolation guarantee even more strongly: the EEE
> render runs in an interpreter that never imports `eval_audit.helm`).

### 3.4 Library home for EEE synthesis

Move the EEE-artifact discovery + index-row synthesis out of `cli/from_eee.py` into the EEE adapter
library (`eval_audit/normalized/eee_sources.py` or under `eee_only/`): `discover_eee_artifacts`,
`extract_artifact_meta`, `build_official_index_row`, `build_local_index_row`, `detect_helm_sidecars`.
`from_eee.py`, `compare_pair_eee.py`, and `virtual/compose.py` import from there. Pure relocation
(Phase-2 method, AST-verified). Removes the "import a CLI to get a library function" smell.

### 3.5 The comparability-facts schema decision (the one genuinely open call)

`HelmRunDiff._diagnose_repro` and `build_comparability_facts` need scalar recipe facts:
`scenario_class`, `model_deployment`, `instructions`, `max_eval_instances`, `benchmark_family`, the
run-spec-name string, and a scenario-spec hash (per hard-split §2 and
[`docs/eee-vs-helm-metadata.md`](../eee-vs-helm-metadata.md)). For EEE-only inputs these are absent →
facts collapse to `status='unknown'` → `comparability_unknown:*` warnings. **That collapse is correct
and must be preserved** (CLAUDE.md; it is the honest signal that the recipe is unverifiable).

The design needs these facts to be carriable *natively* by an EEE artifact so that, when they *are*
known, the diagnosis populates without reading `run_spec.json`. Three options (from hard-split §2):

| Option | What | Pro | Con | Recommendation |
|---|---|---|---|---|
| **(a) Extend EEE schema** with a `recipe_facts` block at conversion time | Converter writes scalar facts into the aggregate JSON's `evaluator_metadata` | EEE artifact becomes single source of truth; `_NormalizedJsonView` never reads HELM JSON | Requires a coordinated change in `submodules/every_eval_ever/`; talk to upstream | **Recommended** (hard-split already recommends (a)) |
| **(b) Sidecar `run_spec.json`** next to the EEE artifact | `detect_helm_sidecars` already does this today | Zero schema change; already implemented | Keeps a HELM-shaped file in the EEE path; weaker "EEE is self-describing" story | Acceptable interim; **already the shipped behavior** |
| **(c) Drop diagnosis** from the EEE-only path | EEE path emits agreement only, never a diagnosis label | Simplest | Loses the recipe-drift signal entirely; reviewers lose information | Reject |

**Recommended sequence:** ship **(b) as the interim** (it already works — `detect_helm_sidecars` +
`extract_run_spec_fields`), and pursue **(a)** as the durable end state behind an upstream EEE
coordination. The `NormalizedDiff` diagnosis reads from a single `recipe_facts` accessor that resolves
in priority order: (1) native EEE `recipe_facts` block → (2) sidecar `run_spec.json` → (3) `unknown`.
This makes (a) vs (b) an *input-availability* question, not a code-path fork.

---

## 4. Work breakdown (sub-stages, each independently shippable + behavior-gated)

Every sub-stage ends green against the [behavior-equivalence matrix](phase3-behavior-equivalence-matrix.md).
Numbering continues the normalized-refactor stages.

| # | Sub-stage | Risk | Deliverable | Gate |
|---|---|---|---|---|
| **4.0** | **Cut the `helm.metrics` tendril** | low (mechanical) | Lift `helm/metrics.py` → `metrics_taxonomy.py` (HELM-free); re-export shim. `normalized/compare.py` imports the new home. | `normalized.compare` imports 0 `helm.*`; full suite unchanged |
| **4.1** | **`recipe_facts` accessor** | low | Single resolver: native EEE block → sidecar `run_spec.json` → `unknown`. No behavior change yet (sidecar path already works). | `test_compare_pair_eee` unchanged (unknown without sidecar, populated with) |
| **4.2** | **`normalized/diagnose.py`** | **medium** | Re-implement `_diagnose_repro` from the `recipe_facts` accessor; identical label shape. Unit-test against `HelmRunDiff._diagnose_repro` on shared fixtures. | Diagnosis labels byte-identical to HelmRunDiff on the HELM fixture set |
| **4.3** | **`NormalizedDiff`** (normalized Stage 4 core) | **high** | New `normalized/diff.py` assembling agreement + curves + tolerance sweep + diagnosis (4.2) + dataset_overlap, all from `NormalizedRun`. HELM-free. | Report JSON equivalent (within documented tolerance, see matrix) to current `core_metrics` output on every fixture pair |
| **4.4** | **`eee_only/` renderer** (hard-split §3) | medium | `eee_only/core_metrics.py` + `eee_only/pair_samples.py` calling `NormalizedDiff` only. EEE synthesis lib moved (§3.4). EEE CLIs subprocess to it. | `import eval_audit.cli.from_eee` loads **0** `eval_audit.helm.*`; static grep clean (hard-split §4 tests) |
| **4.5** | **Remove the silent HELM fallback** from the EEE-only loader | medium | EEE-only loader never reads HELM JSON; `EVAL_AUDIT_EEE_STRICT` becomes the default (then retire the flag). | Numbers identical to the `EVAL_AUDIT_EEE_STRICT=1` baseline (the matrix's strict column) |
| **4.6** | **Point `core_metrics` (HELM path) at `NormalizedDiff`** for agreement; keep `HelmRunDiff` only for run_spec semantic diff | medium | One agreement core for both paths; `HelmRunDiff` quarantined to semantic-diff + raw-evidence inspection (normalized Stage 6). | HELM-path report JSON equivalent to pre-4.6 |
| **4.7** | **(a) EEE schema `recipe_facts`** — upstream EEE coordination | external | Converter writes `recipe_facts`; accessor prefers it. | Diagnosis populates from native EEE with no sidecar present |
| **4.8** | **Docs + retire shims** | low | Update `pipeline.md`, `architecture.md`, mark `helm_compat.py` legacy-only, update the paper methods section (hard-split §5). Retire `--skip-diagnosis` (now the structural default). | — |

**Stop conditions** (from hard-split §"Order of operations"): if at 4.3 or 4.6 the report numbers
differ from the pre-stage baseline beyond the documented tolerance, **halt** — it means the soft
separation was load-bearing somewhere unaudited, and the paper claim needs revisiting before
continuing.

---

## 5. Invariants that must not change

1. **`comparability_unknown:*` collapse for EEE-only-without-recipe-facts** — the honest "recipe
   unverifiable" signal. Verified by `test_compare_pair_eee` (unknown without sidecar, `yes`/`no`
   with). Do not "fix" it into a default.
2. **Agreement numbers** at every `abs_tol` on every fixture pair — equivalent within the documented
   tolerance (matrix §Tolerances). This is the reproducibility result itself.
3. **Report artifact shape** — `core_metric_report.{txt,json,png}`, `components_manifest.json`,
   `comparisons_manifest.json`, `warnings.{json,txt}` — same filenames, same JSON keys. Stage 6
   aggregate reads these and must keep working untouched.
4. **`python -m` surfaces** that runbooks/generated `reproduce.sh` invoke
   (`eval_audit.reports.{core_metrics,filter_analysis,eee_only_heatmap}`,
   `eval_audit.workflows.*`) — preserved or aliased.
5. **EEE-only import isolation becomes *stronger*, never weaker** — 0 `helm.*` after 4.4, enforced by
   a CI test, not prose.

---

## 6. Risks

- **`NormalizedDiff` (4.3) is the high-risk hinge.** Mitigation: it is mostly assembly of
  already-EEE-native pieces (`normalized.compare` already powers the `skip_diagnosis=True` path in
  production), and 4.2 proves the *only* genuinely new logic (diagnosis) byte-matches `HelmRunDiff`
  before 4.3 wires it in. Characterization tests (matrix) snapshot every current output first.
- **Instance join-key drift.** HELM joins by `(instance_id, train_trial_index, perturbation_id)`; the
  normalized path joins by `sample_hash or sample_id` + `metric_id` (`InstanceRecord.join_key`). On
  HELM-origin data these can disagree on edge cases (the hot-finding scenario). The matrix's strict
  column (`EVAL_AUDIT_EEE_STRICT=1`) is the reference; any cell that moves between `present` and
  `join_failed` is itself a recorded paper artifact, not a regression to silently absorb.
- **Upstream EEE coordination (4.7) is out of this repo's control.** Mitigation: 4.1's resolver makes
  (a) and (b) input-availability, not a code fork — 4.7 can land late without blocking 4.0–4.6.

---

## 7. Rollout & rollback

- Each sub-stage is one reviewable change behind the matrix gate; revert = revert that commit.
- 4.0–4.2 are additive (new HELM-free modules + a resolver) and change no live path → trivially safe.
- 4.3–4.6 flip live wiring; each keeps the prior path importable for one cycle (e.g. a
  `EVAL_AUDIT_USE_NORMALIZED_DIFF` env toggle defaulting off, flipped on after the matrix passes on
  real data, removed in 4.8). This mirrors how `--skip-diagnosis` was introduced as a reversible soft
  separation before any hard split.

---

## 8. What this explicitly does NOT do (scope fence, from hard-split §"Out of scope")

- Does **not** break or delete the HELM-driven path — it stays for converter validation, debugging
  conversion drift, and the converter-sweep flow (`dev/poc/eee-audit/sweep.py`).
- Does **not** delete `helm_compat.py` — it becomes documented legacy-bridge-only; its in-process
  callers leave the EEE path.
- Does **not** merge the two CLIs (see §1.2).
- Does **not** change EEE artifact *contents* on the local sweep side until upstream EEE has the
  `recipe_facts` slot (4.7).

---

## 9. Open decisions requiring sign-off before implementation

1. **Owner.** The EEE-only hard split is **Jon's**, to be picked up *after the heatmap-paper analysis
   run*. Phase 3 overlaps it substantially — confirm sequencing with Jon before starting 4.4–4.5, and
   confirm the paper analysis has landed (the hard-split doc gates the split on it).
2. **Schema decision (§3.5).** Approve the (b)-interim-then-(a)-durable sequence, or pick a single
   option. (a) needs an upstream `every_eval_ever` issue filed first.
3. **`eee_only/` namespace name + location** (hard-split §3 proposes `eval_audit/eee_only/`). Confirm,
   or fold the HELM-free renderer under `normalized/` instead.
4. **`EVAL_AUDIT_EEE_STRICT` default flip (4.5)** — confirm the paper baseline was captured *with the
   flag set* (hard-split "Required for the paper analysis run") so flipping the default is provably a
   no-op on published numbers.

---

## 10. Pointers

- Currency model: [`normalized/model.py`](../../eval_audit/normalized/model.py)
- Legacy bridge (the Stage-3 seam): [`normalized/helm_compat.py`](../../eval_audit/normalized/helm_compat.py)
- EEE-native compare (already powering `skip_diagnosis=True`): [`normalized/compare.py`](../../eval_audit/normalized/compare.py)
- Diagnosis to re-implement EEE-natively: `_diagnose_repro` in [`helm/diff.py`](../../eval_audit/helm/diff.py)
- Comparability facts (keep as-is): `build_comparability_facts` / `_fact_status` / `_comparability_warning_lines` in [`planning/core_report_planner.py`](../../eval_audit/planning/core_report_planner.py)
- HELM↔EEE field mapping: [`docs/eee-vs-helm-metadata.md`](../eee-vs-helm-metadata.md)
- Prior plans this reconciles: [`dev/analysis/eee_refactor_stage1_map.md`](../../dev/analysis/eee_refactor_stage1_map.md), [`docs/eee-only-hard-split-todo.md`](../eee-only-hard-split-todo.md)

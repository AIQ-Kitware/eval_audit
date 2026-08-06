# Phase 3 Design — Unify the comparison core (HELM + EEE as adapters)

**Status (2026-08-06): LANDED.** The sub-stages are implemented and cite this
doc from their module docstrings: 4.0 → `eval_audit/metrics_taxonomy.py`,
4.2 → `eval_audit/normalized/diagnose.py`, 4.3 → `eval_audit/normalized/diff.py`,
4.4 → `eval_audit/normalized/eee_sources.py`, 4.5 → `eval_audit/reports/core_metrics.py`
(`--instance-source`), 4.6 → `eval_audit/helm/diff.py` (delegation),
4.9 → `eval_audit/judge_registry.py`. §9.2's open judge-identity decision was
answered by [`judge-identity-inventory.md`](judge-identity-inventory.md).
Line references and "will" phrasing below reflect the tree at design time.
**Author:** drafted 2026-06-11; **revised 2026-06-11** for the new research program (see §0).
**Supersedes the Phase 3 sketch in** [`repo-refactor-plan.md`](../historical/planning/repo-refactor-plan.md) **§Phase 3.**

Companion: [`phase3-behavior-equivalence-matrix.md`](phase3-behavior-equivalence-matrix.md) — the test
matrix that gates every sub-stage.

---

## 0. Research context (revised) — what now drives the requirements

The EEE reproducibility case study that motivated the original guardrails is **concluding**. The new
research program is:

- **R1 — HELM reproducibility with verified open-weight models.** Reproduce published HELM results
  using verified open-weight models. This needs **full HELM-level metadata**: complete comparability
  facts, run_spec semantic diff, and recipe diagnosis. The HELM-capable path is the *primary
  production path* for this program, not a legacy surface.
- **R2 — Open-judge extension.** Benchmarks that HELM ran against **closed judges** (today filtered
  out by `CLOSED_JUDGE_BENCHMARKS` in
  [`indexing/historic_filtering.py`](../../eval_audit/indexing/historic_filtering.py): `anthropic_red_team`,
  `harm_bench`, `omni_math`, `simple_safety_tests`, `wildbench`, `xstest`) get **re-run with open
  judges**, and the analysis compares how results and evaluations differ. This is a *deliberate
  recipe substitution*, not drift — a new comparison kind the current core cannot express (any
  recipe difference today reads as `comparability_drift:*`).
- **R3 — EEE-only mode as strategic portability.** HELM will eventually be deprecated; these analyses
  must remain reproducible against other benchmarking frameworks. The EEE-only path is the
  framework-agnostic layer that future framework adapters plug into. It must **keep working** —
  but it is now future-proofing, not a paper-integrity guarantee.

**What this changes vs. the first draft of this design:**

| First draft | Revised |
|---|---|
| EEE-only **import isolation** (0 `eval_audit.helm.*` in `sys.modules`, static grep, subprocess isolation, `eee_only/` namespace) was a *gating requirement*, inherited from the hard-split doc's paper claim | **Demoted to optional hygiene** (§3.3). The binding requirement is now *operability* (EEE-only mode must run with zero HELM artifacts on disk) plus *metadata-tier explicitness* (§3.6) — not import purity |
| `EVAL_AUDIT_EEE_STRICT=1` becomes the default; HELM fallback removed | **Replaced** by explicit, recorded instance-source selection (§3.7). For R1, HELM-derived instances are *desirable*; the sin was silence, not enrichment |
| Diagnosis = reproduce-or-drift labels only | **Extended**: substitution-aware comparisons for R2 (§3.5) — `same_judge` fact, `intended_substitution:*` labels, judge-dependent metric classification |
| HELM path = legacy, kept for converter validation | HELM path = **primary** for R1; full-metadata completeness is a feature requirement |

Both modes (HELM-driven and EEE-only) are kept, permanently. What gets unified is the **core**.

---

## 1. Context: this is not greenfield

Phase 3 sits on top of **two prior, partially-landed efforts**. The design's first job is to
reconcile them, not to invent a third vocabulary.

### 1.1 The two prior efforts

| Effort | Doc | Goal | Where it stopped |
|---|---|---|---|
| **Normalized refactor** (Stages 1–7) | [`dev/analysis/eee_refactor_stage1_map.md`](../../dev/analysis/eee_refactor_stage1_map.md) | Make `NormalizedRun` the currency; replace `HelmRunDiff` with a `NormalizedDiff` operating on EEE schemas | **Stage 3 landed**: the loader boundary + the `helm_compat.HelmRunView` shim exist. `helm_compat.py:138` literally says *"Stage 4 replaces the comparison core itself; this helper goes away then."* Stage 4 is unstarted. |
| **EEE-only hard split** | [`docs/eee-only-hard-split-todo.md`](../eee-only-hard-split-todo.md) | Make the EEE-only entry points *physically incapable* of importing `eval_audit.helm.*`, supporting the EEE case-study paper claim | **Soft separation landed** (`--skip-diagnosis` / `EVAL_AUDIT_SKIP_HELM_DIAGNOSIS=1`, commit `0403ac3`). Hard split deferred. **Per §0, the hard split's guardrails are now demoted**; its *operability* goal (EEE-only analysis works without HELM) survives as R3. |

**Phase 3 ≡ normalized-refactor Stage 4, executed under the revised priorities of §0.** Treat the
existing stage numbering as authoritative; do not renumber.

### 1.2 Entry points: still separate, for a different reason

The original plan sketch said *"collapse `compare-pair` and `compare-pair-eee` into one auto-detecting
command."* The first draft of this design rejected that on import-isolation grounds. The isolation
argument is now demoted — but **the conclusion stands**, on two surviving grounds:

1. **Metadata-tier explicitness (R1).** The new analysis *requires* full HELM metadata. An
   auto-detecting command silently degrades to `comparability_unknown:*` when HELM metadata is
   missing — exactly the failure mode R1 must surface loudly, not absorb. Separate entry points make
   the operator's intent ("I expect full HELM metadata" vs "I have only EEE artifacts") explicit, and
   the HELM-driven entry point can *fail* on missing run_spec rather than degrade.
2. **Future framework adapters (R3).** When HELM deprecates, new frameworks arrive as new adapters +
   their own thin entry points against the same core. One auto-detect CLI accreting per-framework
   sniffing logic is the wrong shape; N thin entry points over one core is the right one.

**Corrected goal (unchanged from first draft): unify the core, keep the entry points thin and
separate.** The duplication worth removing is *logic* duplication (forked comparison cores,
thrice-implemented index-row synthesis), not the entry-point separation.

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
  [`reports/core_metric_curves.py`](../../eval_audit/reports/core_metric_curves.py) — gated by
  `skip_diagnosis`. The agreement math is *already* EEE-native on both paths when
  `skip_diagnosis=True`; what `HelmRunDiff` uniquely still provides is the **`diagnosis` block**
  (`recipe_clean` / `deployment_drift` / `execution_spec_drift` / `comparability_unknown:*`) via
  `_diagnose_repro`, plus the run_spec/scenario **semantic diff** — which R1 makes more important,
  not less.

### 2.2 Three HELM tendrils in the EEE path

A runtime check (`import <mod>; count eval_audit.helm.* in sys.modules`) shows `from-eee` and
`compare-pair-eee` each load 2 `eval_audit.helm.*` modules today; `eee_only_heatmap` loads 0.

| Tendril | Location | Why it's there | Cut strategy (revised) |
|---|---|---|---|
| **`helm.metrics` in the EEE-native compare** | `normalized/compare.py:26` (used at lines 61, 105, 153, 173) | `classify_metric` / `metric_family` — pure string-prefix metric-name taxonomy, 117 lines, stdlib-only, **not actually HELM-specific** | **Lift** `helm/metrics.py` → `eval_audit/metrics_taxonomy.py`; re-export shim stays. **Broadened by R2**: the taxonomy gains a *judge-dependent vs deterministic* metric classification (e.g. `safety_gpt_score` is judge-derived) — needed to interpret open-judge comparisons regardless of the rest of Phase 3. |
| **Unconditional `HelmRunDiff` import** | `core_metric_curves.py:16` (module-level) | Used in `_build_pair` only when `skip_diagnosis=False` | Make the import lazy/conditional so EEE-only invocations don't pay for it. Full namespace isolation is **optional** (§3.3). |
| **Silent HELM fallback in the EEE loader** | `normalized/loaders.py` `EeeArtifactLoader.load`, the `ref.origin.helm_run_path is not None` branch | Overwrites EEE-derived instances with HELM-derived ones for stable sample ids | **Revised — see §3.7.** The problem is *silence* (same artifact → different numbers depending on disk state), not the enrichment itself, which R1 actively wants. Replace silent fallback with explicit, recorded instance-source selection. |

### 2.3 Duplicated index-row synthesis

The EEE → planner-row synthesis is implemented in **`from_eee.py`** (`_build_official_index_row`,
`_build_local_index_row`, `detect_helm_sidecars`, `_discover_eee_artifacts`,
`_extract_artifact_meta`) and re-used by `compare_pair_eee.py` and `virtual/compose.py` — library
functions living in a CLI module. Phase 3 moves them to a library home (§3.4).

### 2.4 What the planner + render + aggregate already share (the easy half)

Both paths already funnel through **one** planner
([`planning/core_report_planner.py`](../../eval_audit/planning/core_report_planner.py),
`build_planning_artifact` → packets with `components` + `comparisons` + `comparability_facts`), **one**
renderer (`core_metrics` reading `components_manifest.json` + `comparisons_manifest.json`), and **one**
aggregate (`build_reports_summary`, reading only Stage-5 JSON). The comparability-facts machinery
(`build_comparability_facts`, `_fact_status` → `yes|no|unknown`, `_comparability_warning_lines`) is
source-kind-agnostic and correct — Phase 3 *extends* it for R2 (`same_judge`, substitution awareness,
§3.5) but does not restructure it. Only the *loader → diff* segment is forked.

---

## 3. Target architecture

### 3.1 One currency, N adapters, one core

```
            ┌─ helm_adapter   (run_spec.json + HELM run dirs) ──┐
            │                                                   │
inputs ─────┼─ eee_adapter    (every_eval_ever artifacts)  ─────┼─► NormalizedRun ─► NormalizedDiff ─► report JSON
            │                                                   │        │            (framework-free core)
            └─ <future framework adapters — R3>  ───────────────┘        │
                                                                         │
                                                   HELM-driven path only:└─► HelmRunDiff (run_spec
                                                                              semantic diff, raw-evidence
                                                                              inspection — first-class
                                                                              for R1, optional for others)
```

- **`NormalizedRun` is the only thing past the adapter boundary.** It already exists
  ([`normalized/model.py`](../../eval_audit/normalized/model.py)) and carries what the comparison
  needs: `evaluation_log`, `instances` with a portable `join_key`, `source_kind`, `artifact_format`,
  `Origin` provenance. R3 lands later as new `ArtifactFormat` values + loaders — no core change.

### 3.2 `NormalizedDiff` — the unified comparison core (normalized Stage 4)

A new `eval_audit/normalized/diff.py` (framework-free) consuming two `NormalizedRun`s and producing
the **same report shape** the current path emits. Mostly assembly of pieces that already exist:

| Output field (today, from `HelmRunDiff.summary_dict`) | NormalizedDiff source |
|---|---|
| run-level agreement rows | `normalized.compare.run_level_core_rows` (already used) |
| instance-level agreement rows | `normalized.compare.instance_level_core_rows` (already used) |
| per-metric agreement curves | `core_metric_curves._per_metric_agreement_curves` (already EEE-fed) |
| tolerance sweep | port `tolerance_sweep_summary` to read `NormalizedRun.instances` |
| **`diagnosis`** block | **new `normalized/diagnose.py`** — re-implement `_diagnose_repro` from `recipe_facts` (§3.6), same label shape, **extended with substitution-aware labels (§3.5)** |
| `dataset_overlap` | port `dataset_overlap_from_request_states` (now in `helm/diff_primitives.py`) to read EEE instance ids |
| **per-metric-class agreement split** (new, R2) | agreement rows grouped by the §2.2 taxonomy's judge-dependent vs deterministic classes |

**What stays in `HelmRunDiff`:** the `run_spec.json` / scenario *semantic* diff
(`_run_spec_semantic_summary`, `_scenario_semantic_summary`) — meaningful only when both sides have
HELM run dirs. Under R1 this is **first-class output of the HELM-driven path** (it is how "same
recipe" is *proven* rather than asserted), invoked alongside `NormalizedDiff`, never inside it.

### 3.3 Entry points and isolation (revised)

Two render entry points into the same core, **same process, no subprocess isolation requirement**:

- **HELM-driven renderer** (`reports/core_metrics.py`, current home): `NormalizedDiff` + `HelmRunDiff`
  semantic diff. In R1 mode it **fails loudly** when `run_spec.json` is absent on a component that
  claims HELM format (no silent degradation to `unknown`).
- **EEE-only renderer**: `NormalizedDiff` only. Whether this lives in a separate `eee_only/` namespace
  with `sys.modules`/grep CI gates is now **optional hygiene, deferred** — implement it only if/when a
  future paper again needs the grep-able claim. The binding requirement is behavioral:
  **the EEE-only entry points must produce their full report tree on a host with zero HELM artifacts**
  (fixture-verified, matrix gate E1), and the lazy-import change in §2.2 keeps them from paying for
  HELM machinery they don't use.

### 3.4 Library home for EEE synthesis

Move EEE-artifact discovery + index-row synthesis out of `cli/from_eee.py` into the adapter library
(`eval_audit/normalized/eee_sources.py`): `discover_eee_artifacts`, `extract_artifact_meta`,
`build_official_index_row`, `build_local_index_row`, `detect_helm_sidecars`. `from_eee.py`,
`compare_pair_eee.py`, and `virtual/compose.py` import from there. Pure relocation (Phase-2 method,
AST-verified).

### 3.5 Substitution-aware comparisons (the open-judge extension, R2)

The extension compares an official run (closed judge) against a local re-run (open judge) of the same
benchmark+model. Today this comparison either never pairs or reads as drift. Four additions:

1. **Stage 1 relax.** The `requires-closed-judge` exclusion
   (`CLOSED_JUDGE_REQUIRED_REASON`, applied in `build_run_failure_reason_details`) gains an
   `--allow-closed-judge-benchmarks` opt-in so those runs enter the manifest with an explicit
   `judge_substitution_planned` tag instead of being filtered. (This resurrects the intent of the
   deleted `allow_closed_judge_flag_plan.md`.) The filter report must show these as a *distinct
   selection path* — they are neither ordinary selections nor exclusions.
2. **Judge identity as a comparability fact.** `extract_run_spec_fields` / `recipe_facts` gain judge
   fields (annotator/judge model identity from `run_spec.json` — exact source fields to be
   inventoried at implementation time: metric/annotator specs, e.g. the annotator model behind
   `safety_gpt_score`-class metrics; open-judge re-runs record their judge in the same field). The
   planner emits a `same_judge` fact via the existing `_fact_status` machinery.
3. **Declared substitutions on comparison intents.** A planner comparison gains an optional
   `substitutions: ["judge"]` declaration. Facts stay **honest** (`same_judge: no` — we do not invent
   a `substituted` status), but the diagnosis layer consults the declaration: an expected difference
   maps to `intended_substitution:judge` instead of a drift label; an *undeclared* judge difference
   still reads as drift. Any declared substitution whose fact comes back `yes` is itself a warning
   (`substitution_not_observed:judge`).
4. **Metric-class split reporting.** Using the §2.2 taxonomy, the report separates **deterministic
   metrics** (must still reproduce within tolerance — this is the control that validates the re-run)
   from **judge-dependent metrics** (expected to shift — this shift *is* the extension's result).
   Aggregate sankeys/breakdowns gain the same split so "agreement" for a substituted pair is never a
   single conflated number.

This is sub-stage **4.9**. It depends on 4.0 (taxonomy) and 4.2 (diagnosis) but **not** on 4.3–4.6 —
if the extension analysis must start before the core unification lands, items 1–2 and a planner-level
version of 3 can be built against the *current* core (`HelmRunDiff` diagnosis consulted after the
fact). The design recommends landing 4.0–4.2 first so 4.9 is built once, on the new diagnosis.

### 3.6 `recipe_facts` — the framework-neutral metadata contract (elevated)

`NormalizedDiff`'s diagnosis reads scalar recipe facts: `scenario_class`, `model_deployment`,
`instructions`, `max_eval_instances`, `benchmark_family`, run-spec-name, scenario hash — now plus
**judge identity** (§3.5). Resolution order, one accessor:

1. **native `recipe_facts` block in the artifact** (EEE schema extension — upstream coordination), →
2. **sidecar `run_spec.json`** (`detect_helm_sidecars`, already shipped), →
3. **`unknown`** → facts collapse to `status='unknown'`, `comparability_unknown:*` warnings.

The collapse-to-unknown behavior is **preserved verbatim** — it is the honest "recipe unverifiable"
signal (CLAUDE.md). What §0 changes is the *weight* of option 1: under R3, a native, framework-neutral
`recipe_facts` block is the keystone that lets a future non-HELM framework participate in diagnosis at
all — its converter writes the same block and the entire comparison core works unchanged. Sequence:
sidecar-interim (shipped) → native block (upstream EEE coordination, sub-stage 4.7, **raised
priority**).

### 3.7 Instance-source explicitness (replaces the strict-default flip)

The hard-split doc's hot finding stands: `EeeArtifactLoader` silently overwrites EEE-derived
instances with HELM-derived ones when `Origin.helm_run_path` exists — same artifact, different
numbers depending on disk state. The first draft removed the fallback and defaulted
`EVAL_AUDIT_EEE_STRICT=1`. **Revised:** under R1 the HELM-derived instances are the *better* data
when available (stable sample ids, full metadata). The fix is to make the choice explicit and
recorded, never disk-state-dependent:

- The loader takes an explicit `instance_source` policy: `helm-preferred` (HELM-driven entry points)
  or `eee-only` (EEE-only entry points). No path decides implicitly from what happens to be on disk:
  `helm-preferred` with a missing HELM origin is an *error on HELM-format components* and a recorded
  degradation on EEE components; `eee-only` never reads HELM JSON.
- Every report JSON records `instance_source: helm|eee` per component. Stage-6 aggregates carry it as
  provenance.
- `EVAL_AUDIT_EEE_STRICT` is subsumed: the EEE-only entry points hard-set `eee-only`; the env var is
  retired after one deprecation cycle.

---

## 4. Work breakdown (sub-stages, each independently shippable + behavior-gated)

Every sub-stage ends green against the [behavior-equivalence matrix](phase3-behavior-equivalence-matrix.md).
Numbering continues the normalized-refactor stages.

**Status 2026-06-12 (end of day): ALL SUB-STAGES IMPLEMENTED** (commits `94c7fb4`..`HEAD`); every
gate green at each commit.

- 4.0–4.6 done. Notes vs. the plan: 4.4 found and lifted a fourth tendril the §2.2 list missed
  (`helm/hashers.py` — the actual last `helm.*` module in the EEE import chain), after which the EEE
  CLIs import **zero** `eval_audit.helm.*` modules; the planned lazy-`HelmRunDiff` change proved
  unnecessary (Phase 2's split had already removed the renderer from the CLI import chain). 4.5's
  loader probe stages the real demo artifact rather than a synthetic aggregate (pydantic-valid by
  construction).
- **4.9 done** (registry → planner → renderer → Stage-1 relax), shaped by the §9.2 spike
  ([`judge-identity-inventory.md`](judge-identity-inventory.md)): curated annotator→judge-model map
  (`eval_audit/judge_registry.py`); `same_judge` fact **scoped to declared-substitution
  comparisons**; declared differences re-label as `intended_substitution:judge` with the
  `metric_class_split` control/measurement separation; `--allow-closed-judge-benchmarks` admits the
  closed-judge benchmarks through a distinct `judge-substitution` selection path. Non-extension
  outputs verified byte-identical (matrix F9/F10 + committed baseline).
- **4.7**: upstream issue drafted, ready to file —
  [`upstream-eee-recipe-facts-issue.md`](upstream-eee-recipe-facts-issue.md).
- **4.8 deliberate divergence:** `--skip-diagnosis` and `EVAL_AUDIT_EEE_STRICT` are **deprecated,
  not removed** — EEE_STRICT's one-cycle deprecation (subsumed by the declared instance-source
  policies) has not elapsed, and `--skip-diagnosis` remains load-bearing for the EEE render path
  until the facts-grade diagnosis is wired as that path's default (follow-on). Docs updated
  (`pipeline.md` Stage 3, CLAUDE.md module table, `helm_compat.py` marked legacy-bridge).

**Follow-ons** (not Phase 3 blockers): wire `NormalizedDiff.diagnosis()` (facts-grade) as the EEE
render path's default diagnosis, replacing the noisy HelmRunDiff-over-empty-defaults output the
baseline currently pins; Stage-6 aggregate columns for `instance_source` / substitutions /
metric-class split; per-metric judge attribution (inventory finding 2 — compare the open ensemble
member's sub-scores as same-judge controls); retire the deprecated flags after one cycle.

| # | Sub-stage | Risk | Deliverable | Gate |
|---|---|---|---|---|
| **4.0** | **Lift + broaden the metric taxonomy** | low | `helm/metrics.py` → `metrics_taxonomy.py` (framework-free); re-export shim. **Add judge-dependent vs deterministic classification** (R2). | `normalized.compare` imports 0 `helm.*`; full suite unchanged; taxonomy unit tests |
| **4.1** | **`recipe_facts` accessor** (+ judge fields) | low | Single resolver: native block → sidecar → `unknown`. Judge identity fields added to `extract_run_spec_fields` and the accessor. No behavior change to existing facts. | `test_compare_pair_eee` unchanged; new judge-fact unit tests |
| **4.2** | **`normalized/diagnose.py`** | **medium** | Re-implement `_diagnose_repro` from the accessor; identical label shape; **substitution-aware label extension** (`intended_substitution:*`, `substitution_not_observed:*`). | Diagnosis labels byte-identical to HelmRunDiff on non-substituted fixtures; substitution labels unit-tested |
| **4.3** | **`NormalizedDiff`** (normalized Stage 4 core) | **high** | New `normalized/diff.py`: agreement + curves + tolerance sweep + diagnosis (4.2) + dataset_overlap + per-metric-class split, all from `NormalizedRun`. | Report JSON equivalent (matrix tolerances) to current output on every fixture pair |
| **4.4** | **EEE-only operability** (revised) | low–medium | EEE synthesis lib moved (§3.4); lazy `HelmRunDiff` import; EEE entry points verified to run with **zero HELM artifacts on disk** (matrix gate E1). Namespace/`sys.modules` isolation **deferred, optional**. | E1 green; outputs unchanged on F3–F5 |
| **4.5** | **Instance-source explicitness** (revised) | medium | Loader policy `helm-preferred`/`eee-only`; `instance_source` recorded per component in report JSON + Stage-6 provenance; silent disk-state-dependent fallback removed; `EVAL_AUDIT_EEE_STRICT` deprecated. | F6 probe: both policies produce their pinned baselines; no disk-state sensitivity |
| **4.6** | **Point the HELM path at `NormalizedDiff`**; `HelmRunDiff` = semantic diff + raw-evidence | medium | One agreement core; HELM renderer fails loudly on missing run_spec for HELM-format components (R1). | HELM-path report JSON equivalent to pre-4.6; loud-failure unit test |
| **4.7** | **Native `recipe_facts` in EEE schema** — upstream coordination (**raised priority**, R3 keystone) | external | Converter writes `recipe_facts` (incl. judge identity); accessor prefers it. | Diagnosis populates from native EEE with no sidecar |
| **4.8** | **Docs + retire shims** | low | Update `pipeline.md`, `architecture.md`; `helm_compat.py` marked legacy-only; retire `--skip-diagnosis` and `EVAL_AUDIT_EEE_STRICT`. | — |
| **4.9** | **Open-judge extension enablement** (R2, §3.5) | medium | `--allow-closed-judge-benchmarks` Stage-1 opt-in + distinct selection path in filter report; `same_judge` fact; `substitutions` declaration on comparison intents; metric-class split in reports + aggregates. | Matrix F9 green; filter-report selection-path test; non-extension runs byte-identical |

**Sequencing notes.**
- 4.0–4.2 first — they are prerequisites for both the core swap (4.3) and the extension (4.9).
- **4.9 may be pulled ahead of 4.3–4.6** if the extension analysis schedule demands it (see §3.5);
  its planner/Stage-1 halves are core-independent.
- 4.7 is external-coordination-bound; everything else proceeds on the sidecar interim.

**Stop condition:** if at 4.3 or 4.6 the report numbers move beyond the matrix tolerance, **halt** —
the soft separation was load-bearing somewhere unaudited.

---

## 5. Invariants that must not change

1. **`comparability_unknown:*` collapse for inputs without recipe facts** — the honest "recipe
   unverifiable" signal. Do not "fix" it into a default. (HELM-driven R1 runs avoid it by *failing
   loudly* on missing metadata, not by defaulting facts.)
2. **Agreement numbers** at every `abs_tol` on every fixture pair — equivalent within the documented
   tolerance. This is the reproducibility result itself.
3. **Report artifact shape** — `core_metric_report.{txt,json,png}`, `components_manifest.json`,
   `comparisons_manifest.json`, `warnings.{json,txt}` — same filenames, same JSON keys (new keys —
   `instance_source`, `substitutions`, metric-class splits — are *additive*). Stage 6 keeps working
   untouched except where it gains the new provenance columns.
4. **`python -m` surfaces** that runbooks/generated `reproduce.sh` invoke — preserved or aliased.
5. **Both modes stay operable, permanently**: HELM-driven with full metadata, EEE-only with zero HELM
   artifacts on disk. Neither is a deprecation candidate; R3 adds frameworks *beside* them.
6. **Facts stay honest under substitution** — a declared substitution never flips a fact to `yes`;
   it re-labels the *diagnosis*, not the *fact*.

---

## 6. Risks

- **`NormalizedDiff` (4.3) is the high-risk hinge.** Mitigation: it is mostly assembly of
  already-EEE-native pieces (`normalized.compare` already powers the `skip_diagnosis=True` path in
  production), and 4.2 proves the only genuinely new logic (diagnosis) byte-matches `HelmRunDiff`
  before 4.3 wires it in. Characterization baseline captured first (matrix §8).
- **Instance join-key drift.** HELM joins by `(instance_id, train_trial_index, perturbation_id)`; the
  normalized path joins by `sample_hash or sample_id` + `metric_id`. The F6 probe pins both policies'
  behavior; `instance_source` provenance (§3.7) makes any residual divergence visible per-report
  instead of silent.
- **Judge-identity extraction (4.9/4.1) is under-specified** until the implementation-time inventory
  of where HELM run_specs carry annotator/judge identity (metric specs vs annotator specs vs args;
  varies by benchmark). Mitigation: the inventory is the first task of 4.9; `same_judge` collapses to
  `unknown` for benchmarks where identity can't be extracted — which is the correct honest signal.
- **Upstream EEE coordination (4.7) is out of this repo's control.** Mitigation: the resolver makes
  native-vs-sidecar an input-availability question; 4.7 can land late without blocking anything.

---

## 7. Rollout & rollback

- Each sub-stage is one reviewable change behind the matrix gate; revert = revert that commit.
- 4.0–4.2 are additive and change no live path → trivially safe.
- 4.3–4.6 flip live wiring; each keeps the prior path importable for one cycle (e.g. an
  `EVAL_AUDIT_USE_NORMALIZED_DIFF` toggle defaulting off, flipped after the matrix passes on real
  data, removed in 4.8).
- 4.9's Stage-1 relax is opt-in by flag; default behavior (closed-judge benchmarks filtered) is
  unchanged until an extension run passes the flag.

---

## 8. What this explicitly does NOT do (scope fence)

- Does **not** delete or demote the HELM-driven path — under R1 it is the primary analysis surface.
- Does **not** delete `helm_compat.py` — it becomes documented legacy-bridge-only.
- Does **not** merge the CLIs (§1.2).
- Does **not** implement the `eee_only/` import-isolation namespace — deferred until a paper claim
  needs it again; the design leaves it compatible (the core is framework-free, so isolation later is
  a thin-layer change).
- Does **not** build any non-HELM framework adapter (R3) — it only guarantees the seam (`ArtifactFormat`
  + loader registry + `recipe_facts`) such an adapter plugs into.
- Does **not** change EEE artifact contents until upstream EEE has the `recipe_facts` slot (4.7).

---

## 9. Open decisions requiring sign-off before implementation

1. **Confirm the guardrail demotion.** The EEE case-study paper drove the hard-split guardrails;
   confirm with Jon (its owner) that the case study is concluded enough that import-isolation drops
   to optional, and that R3 operability (matrix gate E1) is the surviving requirement.
2. **Judge-identity inventory scope (4.9).** Which `run_spec.json` fields carry judge identity per
   closed-judge benchmark, and what the open-judge re-runs will record. Needs a short spike across
   the six `CLOSED_JUDGE_BENCHMARKS` before `same_judge` is specified.
3. **Substitution semantics.** Approve §3.5's choice: facts stay honest (`same_judge: no`), the
   *diagnosis* re-labels via the declared-substitutions list. (Alternative — a `substituted` fact
   status — rejected as dishonest-by-construction, but flagging for explicit sign-off.)
4. **Schema decision (§3.6).** Approve sidecar-interim → native `recipe_facts`; file the upstream
   `every_eval_ever` issue (now also carrying judge-identity fields).
5. **Instance-source policy defaults (§3.7).** Confirm `helm-preferred` for HELM-driven entry points
   and `eee-only` for EEE entry points, and the retirement path for `EVAL_AUDIT_EEE_STRICT`.

---

## 10. Pointers

- Currency model: [`normalized/model.py`](../../eval_audit/normalized/model.py)
- Legacy bridge (the Stage-3 seam): [`normalized/helm_compat.py`](../../eval_audit/normalized/helm_compat.py)
- EEE-native compare (already powering `skip_diagnosis=True`): [`normalized/compare.py`](../../eval_audit/normalized/compare.py)
- Diagnosis to re-implement: `_diagnose_repro` in [`helm/diff.py`](../../eval_audit/helm/diff.py)
- Comparability facts (extend, don't restructure): `build_comparability_facts` / `_fact_status` /
  `_comparability_warning_lines` in [`planning/core_report_planner.py`](../../eval_audit/planning/core_report_planner.py)
- Closed-judge filter to relax (4.9): `CLOSED_JUDGE_BENCHMARKS` / `CLOSED_JUDGE_REQUIRED_REASON` in
  [`indexing/historic_filtering.py`](../../eval_audit/indexing/historic_filtering.py)
- HELM↔EEE field mapping: [`docs/eee-vs-helm-metadata.md`](../eee-vs-helm-metadata.md)
- Prior plans reconciled here: [`dev/analysis/eee_refactor_stage1_map.md`](../../dev/analysis/eee_refactor_stage1_map.md),
  [`docs/eee-only-hard-split-todo.md`](../eee-only-hard-split-todo.md)

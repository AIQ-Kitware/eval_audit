# Repo Simplification & Refactor Plan — 2026-07-12

**Status:** DESIGN — no code yet. Findings audited read-only; awaiting owner
sign-off on sequencing and on the two optional capstones (A4, D1).

**Lens:** elegance, ease-of-understanding, and lower maintenance for the code
as it stands *after* the era-pinned pre-v0.5 replay path landed — deliberately
not a correctness re-audit (the 2026-07-10 era review already did that).

**Method:** five parallel deep audits (helm↔normalized diff cores; `reports/`;
`workflows/`+`cli/`; the execution/replay + era layer; repo hygiene), every
high-impact claim re-verified in the main session (importer greps, dead-symbol
scans, path-parser count, R-6 consolidation status, deferred-item persistence).

---

## 0. Context: this is the fourth pass, and the easy wins are already banked

Three prior passes are fully implemented; **do not re-plan their work:**

| Pass | Doc | What it did |
|---|---|---|
| Codebase audit | `docs/historical/planning/codebase-audit-2026-07-02.md` | implemented except R-2 |
| Repo refactor (Phases 0–3) | `docs/historical/planning/repo-refactor-plan.md` | CLI wrappers, god-module Phase-2 splits, **NormalizedDiff unification** |
| Simplicity audit | `docs/planning/simplicity-audit-2026-07-06.md` | −10,600 lines; retired the legacy diff half; helper consolidation (R-6); doc/journal rotation |
| Era review | `docs/planning/era-pinned-review-findings-2026-07-10.md` | 10 correctness fixes; **3 refactor cleanups explicitly deferred** |

The repo is genuinely clean on the axes that usually rot: git tree clean, 606
tracked files, **zero tracked build artifacts**, comprehensive annotated
`.gitignore`, small fixtures (444K), 77 test files (~0.56 test-to-module),
all 19 console scripts resolve. The 15G on-disk footprint is entirely
gitignored scratch (venvs + machine-local outputs), not committed cruft.

**So the elegance win now is not another deletion pass. It is three things:**

1. **Finish what is in flight.** The multi-phase diff-core migration
   (`HelmRunDiff` → `NormalizedDiff`) and the Phase-2 god-module split both
   stopped one step short. The *residue* — a hollowed-out `helm/` package, a
   general-purpose utility misfiled under `helm/`, a 1,715-line
   `build_reports_summary.py` that already re-exports its own successors — is
   the single biggest source of "these packages look like they overlap"
   confusion.
2. **Consolidate the new era/replay branching.** The pre-v0.5 replay path is
   *well-isolated* (config-driven `eras.py` + `docker/eras.yaml`), but it added
   a uniform layer of `if era` guards and a 3-deep node/factory/constant
   subclass chain that is now the fastest-growing part of the tree.
3. **Small, cheap clarity moves** — dead-symbol deletion, a handful of literal
   duplicate helpers, and a few genuine god-functions.

Everything below is scoped to those three. The numbers behind them:

- `helm/` = 3,336 lines, of which the *live* surface is `HelmRunDiff`'s
  run_spec semantic diff + `run_entries.py` (a misfiled utility) + `StatMeta`.
  The rest is prod-dead or transitional.
- `build_reports_summary.py` = 1,715 lines, ~140 of which are compat
  re-exports of modules that were already extracted.
- Era branching = ~7 uniform single-boolean guard sites + one subclass chain.

---

## 1. Findings summary (where the concentrated debt is)

**F1 — `helm/run_entries.py` is a general utility misfiled under `helm/`.**
It is run-name / `model_deployment` token parsing + `canonical_logical_key` +
`discover_benchmark_output_dirs` — **no diff role at all** — imported by ~10
production modules across `planning/`, `virtual/`, `indexing/`,
`integrations/`, `pipelines/`, `workflows/`, `cli/`. Its residence under
`helm/` is *why* `helm/` and `normalized/` appear to overlap far more than they
do. Highest clarity-per-effort win in the repo.

**F2 — `helm/` is a hollowed-out legacy stack, not a peer of `normalized/`.**
Agreement/curve/quantile math fully migrated to `normalized/diff.py`; diagnosis
labels migrated to `normalized/diagnose.py`. What survives:
- *Live:* `HelmRunDiff`'s deep `run_spec.json`/`scenario.json` **semantic diff**
  (`helm/diff.py` + `diff_primitives.py`) — the one capability `normalized/`
  lacks — reached via the `helm_compat.py` bridge.
- *Prod-dead* (tests only): `HelmRunAnalysis.stats_inventory`
  (`analysis.py:207`, zero callers), the `JoinedInstanceStatTable` join stack
  (`instance_stats.py:67-424`), the level≥5 branch of `analysis_report.py`
  (644 lines, ~80% dead), the `helm/metrics.py` re-export shim.
- *Retired-but-still-executing:* `HelmRunDiff._value_agreement_summary`
  (`helm/diff.py:759`) computes a full agreement split that is no longer
  returned — it runs on every HELM-path pair solely to feed the diagnosis,
  duplicating `NormalizedDiff.value_summary`.

**F3 — `build_reports_summary.py` is a half-finished god-module.** Lines 40-178
are ~140 lines of compat re-exports from the 10 already-extracted
`reports/summary/*` submodules; the entire scope-render layer (nine
`_render_scope_*`/`_write_scope_*`/`_build_scope_*` functions, incl.
`_render_scope_sankeys` at 285 lines and `_render_scope_summary` at 417 lines)
was left behind. The extraction pattern is proven; it just wasn't finished.

**F4 — No shared "analyze over an index" primitive.** `analyze_experiment.main`
is the de-facto one but is argv-driven, so callers re-enter it via
`main(argv)`. `cli/from_eee.py:365-458` **forks the plan→render→summarize
loop** with a different renderer. Index-resolution and `reproduce.sh`-writing
idioms are copy-pasted across `analyze_experiment`, `rebuild_core_report`, and
`build_reports_summary`. The two EEE CLIs duplicate a `core_metrics` subprocess
block **three times** verbatim.

**F5 — The era/replay path is well-isolated but has a subclass chain and a
duplicated path parser.** Adding a replay capability today = new
`Materialize…Node` subclass + new factory + new `_DOCKER_*_PIPELINE` constant +
new branch. The `(public_track, suite_version)` `benchmark_output` path
convention is parsed in **≥3 places** that can drift (`eras.py:197`,
`workflows/compare_batch.py:38`, `indexing/official_public_index.py:48`) —
already flagged as deferred in the 2026-07-10 review.

**F6 — A thin layer of genuine duplication + dead code in `reports/`.** The
`core_*` and `eee_heatmap_*` and `filter_analysis_*` splits are **clean, not
duplicated** (the names mislead). But `_atomic_savefig` is a literal copy in
two modules, plotly bar helpers are diverged duplicates, the two big matplotlib
heatmap renderers share ~300 lines of parallel scaffold, and ~4-5 symbols
(largest: `_build_end_to_end_funnel_root`, 89 lines) are dead.

**F7 — Two rival failure-classification engines.**
`cli/summarize_experiment_failures.py` (5 regexes) vs
`reports/summary/failure_triage.py` (richer, used by the summary builder).
Overlapping taxonomies, neither references the other.

**F8 — Non-code hygiene.** ~8G reclaimable gitignored scratch
(`dev/e2e-tests/.venv`, `.venv-1`, `tmp/`, `ladder-out/`); 9 vestigial
`norecursedirs` entries; a handful of completed planning docs to archive.

---

## 2. The plan

Six workstreams (A–F). Each item carries **effort** and **risk**. Ordered
within each workstream low-risk → high-risk. The recommended global sequencing
is in §3.

### Workstream A — Clarify the comparison core (finish the diff migration)

> Exit picture: `helm/` becomes a single, honestly-named "HELM semantic-diff
> adapter" (`diff.py` + `diff_primitives.py` + a slim reader), the general
> utility is out of it, and the prod-dead stack is gone.

**A1 — Move `run_entries.py` out of `helm/`.** *(effort: med · risk: low ·
the headline clarity win)*
Relocate `eval_audit/helm/run_entries.py` → a neutral home. Recommended:
`eval_audit/runspec/run_entries.py` (new tiny package for "parse/canonicalize
HELM run names & specs"), or fold into `eval_audit/indexing/` if a new package
is unwanted. Pure move + rewrite ~10 import sites
(`cli/index_historic_helm_runs.py:61`, `indexing/historic_filtering.py:12`,
`integrations/infer_stack/{freeze,presets}.py`,
`integrations/kwdagger_bridge.py:104`, `pipelines/lease_bracket.py:126`,
`planning/core_report_planner.py:20`, `virtual/coverage.py:38`,
`workflows/{compare_batch,index_results}.py`) plus `tests/test_run_entries.py`.
Keep a re-export shim at `helm/run_entries.py` for one deprecation cycle if any
`reproduce.sh` references it (grep first).

**A2 — Delete the prod-dead `helm/` code.** *(effort: med · risk: low —
prod-safe; cost is test churn)*
- `HelmRunAnalysis.stats_inventory` (`analysis.py:207-248`) — zero callers.
- `helm/metrics.py` shim — repoint its ~4 consumers (`analysis.py:45`,
  `analysis_report.py:14`, 2 tests) to `eval_audit.metrics_taxonomy`, delete.
- The dead join stack: `JoinedInstanceStatTable`/`InstanceStatRow`/
  `InstanceStatKey`/`InstanceVariantKey` (`instance_stats.py:67-424`, keep
  `StatMeta`) and the level≥5 instance-inventory branch of `analysis_report.py`
  — reachable only from `tests/test_perturbed_agreement_split.py` and
  `tests/test_helm_run_diff_serializable.py`; retire/rewrite those two.
- Slim `HelmRunAnalysis` to the diagnosis surface
  (`run_spec`/`scenario`/`scenario_state`/`stats`/`stat_index` + level-0
  `summary_dict`). Fold the level-0 slice of `analysis_report.py` back in.
- Delete the now-vacuous `tests/test_phase3_diagnose_equivalence.py`
  (it compares `diagnose_repro` against itself since sub-stage 4.6 — verified
  at `helm/diff.py:745-753`) or repurpose it to pin `diagnose_repro` behavior
  directly.

**A3 — Remove the `_json_compatible` twin.** *(effort: low · risk: low)*
`normalized/diagnose.py:38-65` is a hand-copy of
`helm/diff_primitives.py:245` kept only to avoid importing `helm/`. Promote one
copy to `eval_audit/utils/` (e.g. `utils/jsonify.py`) and import it from both;
removes the "must stay identical" unenforced contract.

**A4 — [OPTIONAL CAPSTONE] Retire `HelmRunDiff` entirely.** *(effort: high ·
risk: high — the one place with real behavioral surface)*
Extract the deep `run_spec.json`/`scenario.json` semantic diff
(`_run_spec_semantic_summary` `helm/diff.py:546`, `_scenario_semantic_summary`
:639, the `diff_primitives.py` path classifiers) into a framework-free
`normalized/semantic_diff.py` that reads the specs directly via
`raw_helm`/`Origin.helm_run_path`. Have `NormalizedDiff` optionally consume it;
then `helm_compat.py`, `HelmRunAnalysis`, and the `helm/diff.py` shell all
disappear (`diff_primitives.py` moves alongside the new module; preserve
`dev/tools/deployment_match/score.py`'s `_walker_diff` import — **it is live**,
used by the olmo runbooks + `test_deployment_match.py`). This also delivers the
intentionally-deferred **EEE-only hard split** (`docs/eee-only-hard-split-todo.md`)
for free, since the EEE path would no longer touch `eval_audit.helm.*`.
**Gate:** the phase3 behavior-equivalence matrix + `tests/fixtures/phase3_baseline/`
must stay green; halt if numbers move. **Recommend doing A1–A3 first** (they
shrink `helm/` ~3.3k→~1.6k at near-zero risk and leave a clean target), then
treat A4 as a dedicated, owner-signed-off milestone — or defer it until the
paper actually needs the hard split.

### Workstream B — Consolidate the new era/replay branching

> Do **not** touch `eras.py` (the resolver is the model to emulate),
> `lease_bracket.py` (correctly era-free), the era↔image guard
> (`kwdagger_bridge.py:628-650`, the strongest safety net), or merge the two
> dockerfiles (the CPU/era vs CUDA/modern split is documented and correct).

**B1 — Unify the `benchmark_output` path parser.** *(effort: low · risk: low ·
already deferred from 2026-07-10)*
One shared helper for the `<track>/benchmark_output/runs/<suite_version>/…`
convention, consumed by `eras.parse_public_signal_from_run_dir` (:197),
`workflows/compare_batch.parse_helm_run_dir` (:38), and
`indexing/official_public_index._scan_benchmark_output_dir` (:48) — they even
disagree on fallbacks today, and era resolution silently picking the wrong
instrument makes drift dangerous. Natural home is the new `runspec/` package
from A1 (pairs the two moves). Prefer having era resolution consume the signal
already recorded on the index row where possible, rather than re-parsing.

**B2 — Collapse the 3-deep replay-node chain into one parameterized node +
dispatch table.** *(effort: med · risk: med — hot scheduling path, but
`test_eras_pipeline.py` + submatrix-contract tests cover it)*
`MaterializeHelmRunFromSpecEraDockerNode` (`helm_docker_pipeline.py:368`)
overrides only `executable`. Replace the subclass+factory+`_DOCKER_*_PIPELINE`
constant chain (`kwdagger_bridge.py:33-53`) with one node taking `executable`
and a `{(from_run_spec, exact_path, capability) → pipeline_target}` dispatch
dict, so a future capability is a table entry, not four edits.

**B3 — Consolidate `bundle_export.py` era branching behind a deployment-schema
strategy.** *(effort: med · risk: low-med — pure builder logic,
`test_exporter_freeze.py` covers it)*
The ~6 `resolved_era is not None` guards + `_model_deployment_entry_era`
(`:109-175`) + the "skip modern alias assertion" / "protocol_mode must be
completions" / "no rewrite target" checks are one decision: *which deployment
schema*. Extract an `EraDeploymentSchema` / `ModernDeploymentSchema` pair
(entry-builder + its assertions) selected once.

**B4 — Kill the two small era config/build duplications.** *(effort: low ·
risk: low · both deferred from 2026-07-10)*
Make `helm_extras`/`capability` explicit-required in `eras.yaml` and drop the
hardcoded defaults from **both** readers (`eras.py:_parse_era_spec` and
`docker/read_eras.py`); fold the two near-identical `docker build` invocations
in `build.sh:333-364` into one call parameterized by dockerfile + a
`BUILD_ARGS` array.

**B5 — [OPTIONAL, forward-looking] Make `capability` drive dispatch.**
*(effort: low · risk: low)* Turn the assert-single-value in
`_era_pipeline_for_manifest` (`kwdagger_bridge.py:77-82`) into a
`{capability → executable}` lookup. Skip unless a second era capability is
actually planned; pairs naturally with B2.

### Workstream C — Finish the god-module decomposition

**C1 — Finish the `build_reports_summary.py` split.** *(effort: med · risk:
low — same characterization-gated relocation the Phase-2 split already used)*
Move the nine scope functions into `reports/summary/` submodules following the
existing pattern: `_render_scope_sankeys` → `scope_sankeys.py`;
`_render_scope_plots`+`_render_aggregate_score_diff` → `scope_plots.py`;
`_write_scope_tables`+`_write_scope_scripts`+`_write_story_index` →
`scope_publish.py`; `_build_enriched_scope_rows`+`_build_scope_sankey_rows` →
`scope_rows.py`; optionally `_render_scope_summary` → `scope.py`. Target: the
module drops from 1,715 to ~200-660 lines. **Gate:** `test_end_to_end_summary.py`
+ `test_eee_only_demo.py` artifacts byte-identical (modulo the plotly-UUID /
timestamp noise floor).

**C2 — Extract the shared matplotlib heatmap scaffold.** *(effort: med · risk:
med — pixel-diff the output PNGs)*
`_render_heatmap` (382 lines, `eee_heatmap_render.py:142`) and
`_render_diff_heatmap` (285 lines, :664) share a near-identical scaffold
(rc_context, subplots, nested cell loop, transpose branch, colorbar branch,
atomic-savefig). Extract `_draw_heatmap_grid(cells, models, benchmarks, *,
value_fn, annotate_fn, cmap, norm, transpose, …)`; the singular renderers are
the correct target (the plural wrappers already delegate to them). Biggest
single line-collapse in `reports/`.

**C3 — Split `breakdown.py`'s three responsibilities.** *(effort: med · risk:
med)* Move `_repair_prioritized_example_reports` (:861) +
`_publish_prioritized_examples_tree` (:944) into `publish.py`; decompose the
481-line `_build_prioritized_breakdown_summary` (:269) into bucket-scoring /
example-selection / assembly.

### Workstream D — De-duplicate orchestration & the EEE-CLI layer

**D2 — Extract shared EEE render helpers.** *(effort: low · risk: low — the two
EEE CLIs are both tested)* One `_run_core_metrics(report_dpath, *, passthrough,
render_heavy)` replacing the verbatim `[sys.executable, "-m",
"eval_audit.reports.core_metrics", …]` + `PYTHONPATH` block copied at
`from_eee.py:167-183`, `from_eee.py:473`, and `compare_pair_eee.py:373-389`.
Collapse `compare_pair_eee._meta_from_artifact` (:87-124) into
`eee_sources._extract_artifact_meta` via a `single_path=` param (its own
docstring admits it mirrors the shared extractor). Home: `normalized/eee_sources.py`
or a new `workflows/eee_render.py`. *(This item is numbered D2 to match the
workflows audit; there is no D-prefixed "D1" ordering — D1 below is the larger
structural fix that should come later.)*

**D3 — Extract `resolve_index_pair()` + `write_reproduce_sh()` helpers.**
*(effort: low · risk: low)* The
`Path(x).resolve() if set else latest_*_csv(dir)` idiom
(`analyze_experiment.py:262-271`, `rebuild_core_report.py:370-395`,
`build_reports_summary.py:1629-1633`) and the
`portable_repo_root_lines()`+`write_reproduce_script()`+`PYTHONPATH=…` boilerplate
(`analyze_experiment.py:588-596`, `rebuild_core_report.py:505-570`) each want
one shared home (`workflows/` or `infra/`).

**D4 — Unify the two failure-classification engines.** *(effort: med · risk:
low — both tested; behavior-diff the taxonomies first)* Make
`cli/summarize_experiment_failures.py` consume
`reports/summary/failure_triage.py` (or delete it if triage subsumes it).

**D5 — Move business logic out of the fat CLIs.** *(effort: med · risk: med —
start with the low-risk slice)* `cli/` is 89% logic by line. First slice
(low-risk, self-contained, has `test_portfolio_status_cli`):
`portfolio_status.py`'s `summarize_rows` (:72-260) + `_format_report` →
`reports/portfolio.py` with a thin CLI shim. Then relocate the `-m` operational
tools (`summarize_experiment_failures`, `analyze_backlog`,
`check_precomputed_discovery`'s `_validate_frozen_manifest`) into domain
packages with thin shims (retain module `main()`s so runbook `-m` invocations
keep working). Highest-risk: `index_historic_helm_runs.main`'s ~205-line
Stage-1 orchestration → `indexing/stage1_runner.py` (defer within this item).

**D1 — [OPTIONAL CAPSTONE] Extract a typed `analyze_index()` primitive.**
*(effort: high · risk: med — hot analysis path)* A typed
`workflows/core_analysis.py::analyze_index(local_index, official_index,
experiment, out_dpath, …)` owning index-resolution + plan + per-packet render +
summary. Refactor `analyze_experiment.main`, `cli/from_eee`, and
`build_virtual_experiment` to call it — killing the forked loop in `from_eee`
(`build_virtual_experiment.py:188` already reuses `analyze_experiment.main`,
proving the primitive can be shared). **Sequence after C1** (needs the clean
summary seams) and gate on `test_rebuild_core_report` + `test_virtual_experiment*`
+ an EEE e2e.

**D6 — Decide `compare_batch`'s fate.** *(investigation: low; removal: med —
operator decision)* It is a legacy manifest-driven pipeline on the retired
`HelmRunDiff.summary_dict`, invoked only by the "UNSURE"-marked
`reproduce/{smoke,apples}/30_compare.sh` runbooks yet still a console script.
Confirm it is vestigial vs the planner path; if so, deprecate it like
`cli/reports.py` (keep resolving for old reproduce scripts, stop advertising).

### Workstream E — Small clarity wins (cheap, do early)

**E1 — Delete the dead symbols.** *(effort: trivial · risk: low)*
`_build_end_to_end_funnel_root` (`summary/sankeys.py:216`, 89 lines),
`pair_report._agree_ratio_at` (:95, dead **and** a 3rd copy of
`find_curve_value`), `core_packet_summary.packet_reference_component` (:88),
`core_packet.comparison_sample_history_name` (:41). ~106+ lines.

**E2 — De-dup the plotting helpers.** *(effort: trivial-low · risk: low)*
`_atomic_savefig` (literal copy at `eee_heatmap_render.py:108` &
`core_metric_plots.py:696`) → `reports/_mpl.py`. Shared plotly bar helpers
(`_bar_count_label`, `_AXIS_COUNT_TAGS`, the tick-angle ladder — diverged twins
in `summary/plots.py` & `filter_analysis_charts.py`) → `reports/_plotly_bars.py`.

**E3 — Data-drive the `emit_filter_analysis_artifacts` write phase.**
*(effort: low · risk: low)* The 393-line god-function's tail is dozens of
near-identical `_write_stamped_table(...)` calls — loop over a
`[(stem, rows), …]` list.

**E4 — [OPTIONAL] Shrink the compat re-export facades.** *(effort: med · risk:
med — pure test-import churn)* `core_metrics.py` (~62), `filter_analysis.py`
(~41), `eee_only_heatmap.py` (~30) re-export private symbols *only* so tests can
reach them. Point the tests at the real modules and keep re-exporting only
genuinely public names. ~130 lines + private-symbol coupling removed. Lower
priority — the facades are harmless and the churn is all in tests.

### Workstream F — Non-code hygiene

**F1 — Reclaim ~8G gitignored scratch (on-disk `rm`, not a git change).**
*(ask the user first for `ladder-out/`)* `dev/e2e-tests/.venv/` (5.6G) and
`.venv-1/` (12M) are recreatable; `tmp/` (108K) + `.build-staging/` (11M)
regenerate; `ladder-out/` (2.3G) is machine-local validation-ladder output —
user's call whether the current run still needs it.

**F2 — Trim the vestigial `norecursedirs`.** *(effort: trivial)* 9 entries
point at directories that no longer exist (`.venv13`, `.venv313`, `examples`,
`in-progress-notes`, `old-backup-reports`, `reports`, `reports-filter-bak`,
`reports-old`, `reports-summary-bak`); `testpaths = ["tests"]` already scopes
collection, so trim to what exists or drop the list.

**F3 — Archive completed planning docs** → `docs/historical/planning/` once
`impl/run-from-run-spec` merges: `run-from-run-spec-json-plan.md`,
`simplicity-audit-2026-07-06.md`, `phase3-comparison-core-unification.md`,
`phase3-behavior-equivalence-matrix.md`. *(This plan supersedes none of them —
it continues where they stopped.)*

**F4 — Minor:** add the ~8 missing `reproduce/*/README.md` (or accept that
smoke/setup dirs skip them); archive the two stale `dev/analysis/eee_refactor_stage*.md`
notes; de-duplicate the developer-journal spec shared by `CLAUDE.md` and
`AGENTS.md`. All optional.

---

## 3. Suggested sequencing & commit granularity

Dependency-ordered so each commit leaves the tree working (per CLAUDE.md).
**Never commit the dirty submodule gitlinks.**

**Batch 1 — cheap, high-clarity, near-zero risk (do first):**
E1 (dead symbols) · E2 (dup helpers) · B4 (era config/build dup) · F2
(norecursedirs) · D2 (EEE render helper) · D3 (index/reproduce helpers).
One commit per item; all guarded by the existing suite.

**Batch 2 — the headline structural clarity moves:**
A1 (move `run_entries` out of `helm/`) → B1 (unify path parser, lands in the
same new `runspec/` package) → A2/A3 (delete prod-dead `helm/` + `_json_compatible`
twin). Then C1 (finish the `build_reports_summary` split). Each is one commit
(A1 and C1 may be several — one per moved module — following the Phase-2
granularity).

**Batch 3 — medium consolidations:**
B2 (replay-node table) · B3 (deployment-schema strategy) · C2 (heatmap
scaffold) · C3 (breakdown split) · D4 (failure-engine unify) · D5-portfolio
slice · E3 (filter write-loop). Independent; parallelizable across sessions.

**Batch 4 — optional capstones (each needs owner sign-off + a captured
behavior baseline):**
D1 (`analyze_index` primitive; after C1) · A4 (retire `HelmRunDiff`; delivers
the EEE hard split) · E4 (facade shrink) · D5-remainder · D6 (compare_batch
decision).

**Non-code:** F1 (disk reclaim) any time; F3/F4 after the branch merges.

Batches 1–2 deliver ~80% of the "easy to work with" win at low risk; the diff
core stops looking like two overlapping packages and the largest god-module is
gone. Batch 3 is steady cleanup. Batch 4 is architectural payoff that should
not start until 1–2 land the clean seams.

---

## 4. Verification

- **Every touched file:** `python -m py_compile`; fast suite with the repo
  `.venv` (baseline 442 passed / 71 skipped per the 2026-07-06 audit).
- **Relocations (A1, A2, B1, C1, D2, D3, D5):** grep sweep for every
  moved/deleted symbol across `eval_audit/` + `tests/` + `reproduce/` +
  `docs/`; keep a re-export shim where a `reproduce.sh` still imports the old
  path (ADR 5).
- **Render relocations (C1, C2, C3):** byte-identical artifact gates —
  `tests/test_end_to_end_summary.py` + `tests/test_eee_only_demo.py`
  (C2 additionally: pixel-diff the heatmap PNGs) — modulo the plotly-UUID /
  timestamp noise floor established by HEAD-vs-HEAD reruns.
- **Era items (B1–B4):** `tests/test_eras*.py` + `test_run_spec_materializer.py`
  + `test_exporter_freeze.py` + `test_from_spec_materialized_schedule.py` +
  `test_kwdagger_submatrix_contract.py`. B2/B3 are host-testable without an era
  image; the shim internals still need the validation ladder before results are
  trusted (per the 2026-07-10 review).
- **Diff-core capstones (A4, D1):** the phase3 behavior-equivalence matrix +
  `tests/fixtures/phase3_baseline/`; **stop condition** — if agreement numbers
  move beyond matrix tolerance at any step, halt: a soft separation was
  load-bearing somewhere unaudited.
- **Final:** full suite + a `reproduce/pythia_mmlu_stress` compose→summary smoke
  if the environment allows.

---

## 5. Explicitly NOT in scope (do-not-touch / already-correct)

- **Merging the `core_*` / `eee_heatmap_*` / `filter_analysis_*` splits** — they
  are clean, documented data/render/CLI layer splits; the agreement math is
  already single-sourced in `normalized/diff.py`. The names suggest overlap; the
  code does not have it.
- **Merging the two dockerfiles** — era (CPU/ubuntu/py3.10) vs modern
  (CUDA/py3.11) share almost nothing; the separation is documented and correct.
- **`eras.py`, `lease_bracket.py`, the era↔image guard** — the resolver is the
  design to emulate; leasing is correctly era-free; the guard is the strongest
  safety net in the replay path.
- **Unifying the matplotlib (per-pair paper figures) vs plotly (interactive
  aggregate) backends** — a large project with little payoff.
- **The EEE-only hard import split** as a standalone effort — it is owner-owned
  (`docs/eee-only-hard-split-todo.md`, owner Jon) and research-paper-driven;
  A4 delivers it as a byproduct if/when that milestone is taken.
- **`cli/reports.py`** — the deprecated dispatcher stays (ADR 5: pre-2026-06-11
  `reproduce.sh` scripts still invoke it).

---

## 6. Pointers

- Diff-core detail: `eval_audit/{helm,normalized}/` (see §1 F1–F2).
- Era layer: `eval_audit/eras.py`, `eval_audit/pipelines/helm_docker_pipeline.py`,
  `eval_audit/integrations/{kwdagger_bridge,infer_stack/bundle_export}.py`,
  `docker/`.
- Prior passes: `docs/historical/planning/{codebase-audit-2026-07-02,repo-refactor-plan}.md`,
  `docs/planning/{simplicity-audit-2026-07-06,era-pinned-review-findings-2026-07-10}.md`.
</content>
</invoke>

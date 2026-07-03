# Codebase Audit — 2026-07-02

**Scope:** all of `eval_audit/` (~38k lines, 131 files), plus tests, packaging, and
cross-cutting consistency. Nine parallel deep reviews (one per subsystem: normalized core,
legacy HELM diff, core-metrics reports, summary/filter reports, CLI, workflows,
planning/indexing/virtual/manifests, integrations/pipelines/infra/utils, tests/packaging),
followed by manual verification of every high-severity claim against source.

**Relation to prior plans:** builds on [`repo-refactor-plan.md`](repo-refactor-plan.md)
(Phases 0–3 largely complete). This audit does not re-propose completed work; several
findings are follow-on debt from those phases (dead imports after splits, shims kept
load-bearing, the unretired legacy half of `helm/diff.py`).

**Verification legend:**
- ✅ **verified** — reproduced or confirmed by direct source inspection in the main session.
- ☑️ reported — verified by the reviewing agent (read + caller greps); not independently re-checked.

Severity reflects *research impact* first: a bug that silently corrupts published
agreement numbers outranks a crash, because a crash announces itself.

---

## 1. Executive summary

The codebase is structurally healthy — the Phase 2/3 refactors delivered: determinism is
mostly enforced by sorted globs and key-sorted joins, the tolerance boundary (`<=`) is
consistent across every curve/table/plot consumer, atomic writes are the norm in Stages
3–5, and all 19 console entry points resolve. The serious problems cluster in four themes:

1. **Cross-stage seams drift silently.** Stage 4 renamed its index file; the two duplicated
   copies of `latest_index_csv` in Stages 5/6 still glob the old stamped name (P0-2). The
   tol-variant sankeys mislabel 0.1-tolerance data as 0.010 because of an incoherent
   agreement-key naming scheme (P0-1). Copy-pasted helpers are the delivery mechanism.
2. **The canonical-key fix (a25aac9) was applied inconsistently.** One planner call site
   and the entire `virtual/coverage.py` join layer still match raw strings — the exact
   114-missing-MMLU failure shape, alive in two places (P0-3, P0-4).
3. **Cache identity misses semantic inputs.** `model_deployments.yaml` (protocol mode,
   tokenizer, truncation) enters kwdagger job identity by *path*, not content — bundle
   regeneration silently reuses stale GPU results (P0-5). Same class: `precomputed_root`
   on the from-spec discovery shape, unpinned local docker tags.
4. **Failures are silently absorbed into wrong categories.** The failure-taxonomy chart
   has rendered nothing since `policy_blocked`/`recipe_error` were added (P0-7); five
   positively-identified failure classes chart as "Unknown/Other"; join-failures render
   as "low agreement"; corrupt report bundles silently become "not yet analyzed". Each of
   these blurs the recipe-failure vs reproducibility-failure distinction the paper depends on.

Counts: **9 critical (P0)** · **24 significant (P1)** · **~35 low (P2)** ·
**~15 improvements** · **~13 refactors**.

---

## 2. P0 — Critical (fix first)

### P0-1 ✅ tol010 sankeys render abs_tol=0.1 data under an "abs_tol=0.010" title
`eval_audit/workflows/build_reports_summary.py:459,484-489` · `eval_audit/reports/summary/loading.py:132`
The `repro_tol010` and `b_scope_to_analyzed_tol010` sankeys bucket on
`official_instance_agree_01`, which `loading.py` fills from the **0.1** point of the
agreement curve, while the emitted titles and `story_index` say `abs_tol=0.010`. The
sibling variants are consistent (`tol001`→0.001, `tol050`→0.05), so this is a 10×
mislabel in published research artifacts. Root cause is the incoherent key family
(`_001`=0.001, `_005`=0.05, `_01`=0.1; `analyze_experiment.py` separately uses `_05` for
0.5). **Fix:** add a dedicated 0.01 key in `loading.py` (the curve grid contains 1e-2),
point the tol010 variants at it, and rename the key family to unambiguous milli-units
(`agree_tol001/tol010/tol050/tol100`).

### P0-2 ✅ Stage 5/6 index resolution globs a filename Stage 4 no longer writes
`eval_audit/workflows/rebuild_core_report.py:41` · `eval_audit/reports/summary/common.py:31` · `eval_audit/workflows/index_results.py:384`
`index_results` writes unstamped `audit_results_index.csv` (stamp removed 2026-04-28b),
but both duplicated copies of `latest_index_csv` glob `audit_results_index_*.csv`, which
cannot match it. Fresh stores → `FileNotFoundError` on the default `--index-dpath` path;
stores carrying pre-2026-04-28 stamped files → Stages 5/6 **silently analyze a stale
index**. **Fix:** check the unstamped canonical name first, fall back to the stamped glob
(mirroring `latest_official_index_csv`), and deduplicate the two copies into one shared
helper (see R-6).

### P0-3 ✅ Planner `--run-entry` filter bypasses canonical-key matching
`eval_audit/planning/core_report_planner.py:987`
The component filter uses exact equality (`component.logical_run_key != run_entry and
component.run_entry != run_entry`) while the prefilter (line 288) and grouping use
`_logical_key_variants` / canonical keys — the a25aac9 fix never reached this site. A
run-entry-scoped rebuild where the official spec name differs by token order or a
`groups=` token silently drops the official and emits a `missing_official_component`
packet. **Fix:** compare canonical key sets on both sides; structurally, unify all four
key-matching sites behind one API (R-1).

### P0-4 ✅ `virtual/coverage.py` joins target↔local↔analyzed rows on raw strings
`eval_audit/virtual/coverage.py:87,281,324`
No use of `canonical_logical_key` anywhere in `virtual/`. Locals whose run_entry drifts
from the official form (the endemic a25aac9 pattern) are counted "missing (no local
repro)" even though the planner analyzed them; canonical-merged packets (whose
`run_entry` is the sorted canonical key) match neither side. The paper's coverage-funnel
numbers under-report reproduction. **Fix:** key all join dicts and the analyzed-key set by
`canonical_logical_key(raw) or raw` (same API as R-1).

### P0-5 ✅ `model_deployments.yaml` enters kwdagger job identity by path, not content
`eval_audit/integrations/infer_stack/adapter.py:1705` · `eval_audit/integrations/kwdagger_bridge.py:116`
The bundle always writes the fixed path `<bundle>/model_deployments.yaml`; job identity
hashes the path string, and `skip_existing=True` is the default. Re-exporting a bundle
with changed semantics — `protocol_mode` (chat vs completions: the exact OLMo-7B
EOS-append failure class), tokenizer alias, max sequence length — reuses results computed
under the old config with no warning. This violates the project's own content-addressing
rule, which `run_spec.<content-hash>.json` follows. **Fix:** content-hash the generated
filename (`model_deployments.<hash16>.yaml`) or add a `model_deployments_sha256` algo
param computed at schedule time.

### P0-6 ✅ Perturbed/unperturbed agreement split in `HelmRunDiff` is degenerate
`eval_audit/helm/diff.py:1132` · `eval_audit/helm/instance_stats.py:83`
`hasattr(k, 'perturbation_id')` is always False (`InstanceStatKey` nests it under
`variant`/`stat_perturbation_id`), and the tuple fallback is dead. Every row buckets
"unperturbed": `agree_ratio_unperturbed` silently includes perturbed rows and
`agree_ratio_perturbed` is always None in pair_report JSON/text. No test covers these
fields. **Fix:** `perturbed = k.variant.perturbation_id is not None or
k.stat_perturbation_id is not None`; add an assertion test on a perturbed fixture.

### P0-7 ✅ Failure-taxonomy chart has never rendered since new categories were added
`eval_audit/reports/summary/plots.py:781` · `eval_audit/reports/summary/failure_triage.py:184`
The trace loop iterates all of `_FAILURE_CATEGORY_ORDER` (includes `policy_blocked`,
`recipe_error`) but `cat_colors` has only 4 keys → guaranteed `KeyError` whenever any
failure data exists → swallowed by the outer `except Exception` → `failure_taxonomy.html/.jpg`
silently never written, while READMEs direct readers to them. **Fix:** add the two colors
(or `.get(cat_key, grey)`), skip all-zero categories, and add a smoke test that the file
is produced for a fixture with failures.

### P0-8 ✅ `python -m eval_audit.cli.analyze_backlog` crashes on import
`eval_audit/cli/analyze_backlog.py:19`
Imports `slugify` from `rebuild_core_report`, which only has `slugify_identifier`
(reproduced: `ImportError`). Once fixed, the module's report-dir layout is also stale
(`experiment-analysis-<slug>` prefix vs canonical `experiment_analysis_dpath`;
per-run-entry dirs vs `packet_id`-keyed dirs). **Fix:** repair the import, then re-base its
path construction on `experiment_analysis_dpath()`/packet ids; add it to the import-smoke
test that already covers the other CLI modules.

### P0-9 ☑️ Test suite aborts collection (4 errors) via `kwdagger → cmd_queue → kwconf`
`eval_audit/pipelines/helm_docker_pipeline.py:43` · tests/test_{container_execution,from_run_spec_pipeline,from_spec_materialized_schedule,lease_bracket,kwdagger_submatrix_contract}.py
Module-level `import kwdagger` now pulls `kwconf` (new dep declared in the dirty
`cmd_queue` submodule), which is missing from the dev venv → `pytest` reports
"Interrupted: 4 errors during collection"; the whole suite is currently unrunnable as a
gate. **Fix:** re-sync the venv (install kwconf / re-run setup), *and* add
`pytest.importorskip("kwdagger")` to the five kwdagger-dependent test modules so a missing
optional submodule dep degrades to skips, never a collection abort.

---

## 3. P1 — Significant bugs

### Pairing / comparability semantics
- **P1-1** ☑️ `_fact_status` returns `"yes"` when only one component has a known value —
  partial knowledge reported as verified agreement; also emits spurious
  `substitution_not_observed:judge`. Return `unknown` unless ≥2 components contributed.
  (`planning/core_report_planner.py:614`)
- **P1-2** ☑️ Official components never carry `max_eval_instances`
  (`indexing/official_public_index.py:117` hardcodes None; `schema.extract_run_spec_fields`
  omits it), so with P1-1, `same_max_eval_instances` can never detect drift — a local cap
  of 10 vs official 1000 raises no warning. Extract `adapter_spec.max_eval_instances` in
  `extract_run_spec_fields` and populate both normalizers. (`planning/core_report_planner.py:533`)
- **P1-3** ☑️ Normalized instance join key `(sample, metric)` erases the
  perturbation/train-trial dimension the legacy path keys by; first-wins on duplicates can
  pair a perturbed score against a base score across runs, corrupting agreement ratios with
  no count. Include perturbation/trial in the join key (or at minimum count+surface
  collapsed duplicates). (`normalized/joins.py:58`, `normalized/loaders.py:558`)

### Stale state presented as current
- **P1-4** ☑️ Stage 5 never prunes stale `core-reports/core-metrics-<slug>/` dirs and
  Stage 6 blindly globs them → packets from previous plans inflate `n_analyzed`,
  double-count sankey rows, race last-wins in `repro_keyed`. Prune/quarantine dirs not in
  the current packet set, or restrict loading to the latest planning artifact.
  (`workflows/analyze_experiment.py:306`, `reports/summary/loading.py:55`)
- **P1-5** ☑️ Stage 6 re-runs leave stale `breakdowns/by_<dim>/<value>/` and
  prioritized-example dirs from previous configs — the advertised filesystem-first
  navigation shows outdated slices as current. Extend the existing legacy-alias sweep.
  (`workflows/build_reports_summary.py:192`, `reports/summary/breakdown.py:944`)
- **P1-6** ☑️ `build_virtual_experiment`: stale `scoped_filter_inventory.json` from a
  previous compose survives when a declared pre_filter fails to load (missing/unparseable/
  wrong kind) — downstream summary silently applies yesterday's scope (the file's own
  comments record this cost a debugging session). Delete unconditionally before the loop.
  (`cli/build_virtual_experiment.py:126`)
- **P1-7** ☑️ HELM→EEE conversion cache accepts partially-failed conversions forever: the
  cached/local resolvers gate on "any aggregate present", never `status.json`, unlike the
  official resolver. Apply the same `status == "ok"` gate. (`normalized/loaders.py:438`,
  `normalized/eee_artifacts.py:503`)

### Failure/agreement misclassification (report honesty)
- **P1-8** ☑️ `_FAILURE_CATEGORIES` lacks mappings for five classifier outputs (GPU/CUDA,
  killed, network, permissions, interrupted) → positively identified infrastructure
  failures chart as "Unknown / Other"; also `truncated_or_incomplete_runtime` presents as
  "Hardware / Compute Timeout" with no hardware evidence. (`reports/summary/failure_triage.py:171`)
- **P1-9** ☑️ Coverage matrix paints `not_analyzed` agreement buckets as "analyzed: low
  agreement (<80%)" — join failures become false reproducibility failures. Give
  unknown/not_analyzed its own status level. (`reports/summary/plots.py:577`)
- **P1-10** ☑️ Corrupt/unreadable `core_metric_report.json` bundles are silently skipped →
  runs report as "completed_not_yet_analyzed" with no warning anywhere. Log + surface an
  `unreadable_reports` count in the summary manifest. (`reports/summary/loading.py:70`)
- **P1-11** ☑️ Failure-classifier substring rules are order-dependent and over-generic —
  bare `"429"` matches scores/ids; generic file-not-found rules shadow CUDA-OOM. Use
  anchored patterns, order most-specific-first. (`reports/summary/failure_triage.py:112`)
- **P1-12** ☑️ Flat filter sankey emits one row per (run, reason) so flows exceed the
  titled run count (root label renders "n=X n=Y", Y>X). Pick a primary reason per run or
  retitle as reason-instances. (`reports/filter_analysis_tables.py:485`)

### Numbers on plots/tables
- **P1-13** ☑️ Cross-machine sidecar curve is computed under joint abs+rel tolerance
  (rel_tol up to 1.0) but plotted on the pure-abs_tol axes — systematically inflated
  agreement line at the same x. Re-derive with rel_tol=0 or separate panel.
  (`reports/core_metric_curves.py:48`, `reports/pair_report.py:28`)
- **P1-14** ☑️ Label-legend sidecar colors can attach to the wrong curves: seaborn 0.13
  assigns hue colors in appearance order (verified), `_palette_color_map` assumes sorted
  order — 50% swap chance with two pairs. Pass explicit `hue_order`/palette dicts and
  reuse for the sidecar. (`reports/core_metric_plots.py:117`)
- **P1-15** ☑️ No coherent NaN policy: a NaN score poisons every `np.quantile` output and
  `json.dumps` emits the invalid-JSON `NaN` literal, while the agreement curve counts the
  same row as permanent disagreement. Filter non-finite scores at row construction and
  report the dropped count. (`normalized/diff.py:128`, `reports/core_metrics.py:702`)
- **P1-16** ☑️ A full run with `--no-plots` **deletes** previously rendered figures (they
  drop out of `latest_map`, then the stale-alias cleanup unlinks them), contradicting the
  documented "skip", and re-triggers the aggregate repair loop every build. Exclude figure
  names from cleanup under `--no-plots`. (`reports/core_metrics.py:748`)

### Determinism (hard project requirement)
- **P1-17** ☑️ Stage 1 output order (run_specs.yaml, run_details.yaml, filter inventory)
  depends on filesystem enumeration: `gather_runs` says "# Stable order" but never sorts
  the discovered benchmark_output dirs (magnet's `discover_benchmark_output_dirs` uses
  unsorted `os.walk`; twin copy in `helm/run_entries.py:324`). `dedupe_rows` is
  first-wins, so *which suite's row is retained* varies by machine. Sort at both layers.
  (`indexing/historic_filtering.py:53`)
- **P1-18** ☑️ `HelmRunDiff` top-N mismatch lists are PYTHONHASHSEED-dependent: grouped
  output in raw dict-insertion order from set iteration (`helm/diff.py:1173`), and four
  methods sort ties (`abs_delta` exactly 1.0 is the common case for 0/1 metrics) in hash
  order before truncation (`helm/diff.py:856,940,1155,1330`). Lands byte-different
  pair_report/quantiles JSON across identical runs. Iterate `sorted(keys)` / add a
  serialized-key tiebreaker.

### Operational correctness
- **P1-19** ☑️ `eval-audit-analyze-many` exits 0 after per-experiment failures and still
  builds the aggregate summary over the incomplete set. Exit non-zero; gate/warn on
  `--build-summary`. (`cli/analyze_many.py:163`)
- **P1-20** ☑️ Emitted `reproduce.sh` drops `--no-filter-inventory`, `--no-canonical-scan`,
  `--analysis-root`, `--summary-root` → for from-eee/virtual builds it regenerates a
  different report at the default root and re-includes the excluded inventory. Thread the
  actual invocation flags through `_build_summary_cmd`. (`reports/summary/publish.py:426`)
- **P1-21** ☑️ From-spec *discovery* shape: `precomputed_root` determines which official
  run_spec.json is replayed but remains an identity-neutral perf param → switching corpus
  roots reuses stale results. Promote to algo_params on the from-spec node (or retire the
  discovery shape in favor of `--freeze-rel-paths`, which is correctly content-addressed).
  (`pipelines/helm_docker_pipeline.py:260`)
- **P1-22** ☑️ `_build_manifest` missing-entries error path references undefined `fpath`
  → NameError masks which preset entries were missing. (`manifests/presets.py:51`)
- **P1-23** ☑️ Duplicate `run_entry` labels in a run-spec-sources file silently collapse
  to the first source (`setdefault`) — the manifest schedules fewer runs than declared.
  Raise on duplicate labels. (`manifests/builders.py:278`)

### Packaging
- **P1-24** ☑️ Dependency-metadata drift: `uv.lock` stale vs `requires-python >= 3.11`;
  `run_developer_setup.sh` defaults to python3.10; the known-breaking `transformers<5` /
  `huggingface_hub==0.36.2` pins are not expressed in pyproject (a re-lock reintroduces the
  Stage-5-zeroing failure); `every_eval_ever` is a hard import but undeclared;
  `networkx`/`matplotlib` imported directly but only transitively supplied.
  (`pyproject.toml:20`, `uv.lock:3`, `run_developer_setup.sh:8`, `normalized/loaders.py:40`,
  `utils/sankey_builder.py:24`)

---

## 4. P2 — Low-severity bugs and fragilities

Grouped; each is a small, independent fix.

**Workflows** — dangling/wrong-target publication symlink never repaired
(`analyze_experiment.py:632`); skipped-run records read `.returncode` off `SystemExit`
(always None; it's `.code`) (`analyze_experiment.py:353`); direct-CLI cached packet reuse
ignores index freshness, no `--replan` (`rebuild_core_report.py:194`);
`summary_by_run_spec` last-wins collapses packets sharing a run_spec_name — cross-machine
rows silently missing (`analyze_experiment.py:366`); `compare_batch` duplicate-job
last-wins by path order + corrupt jobs misreported as `missing_kwdg_match`
(`compare_batch.py:213`); combined component index round-trips through pandas dtype
inference ("1000.0" vs "1000") (`index_results.py:316`); `--experiment-name` unquoted in
reproduce.sh (`reports/summary/publish.py:434`).

**Legacy HELM diff** — missing f-string prefix prints literal `{spec_name_a} //
{spec_name_b}` (`helm/diff.py:406`); `sorted(by_group.items())` TypeErrors on None metric
names (`helm/diff.py:1329,1465`); `evaluation_only=True` when only nonsemantic paths
differ (`helm/diff.py:637`); dataset-overlap base map can compare perturbed vs unperturbed
inputs first-wins (`helm/diff_primitives.py:633`); duplicate stat identities silently
last-wins overwritten with no diagnostic (`helm/instance_stats.py:306`,
`helm/analysis.py:189`); diagnosis label changes with `level` because dataset_overlap is
gated on level≥5 (`helm/diff.py:324`).

**Summary reports** — contradictory "analyzed → Reproduction: not_analyzed" node absent
from stage key (`reports/summary/sankeys.py:363`); hierarchical funnel lacks a metadata
gate so missing-model-metadata rows land "unclassified" and the two funnel families
disagree (`reports/filter_analysis_tables.py:298`); quantile-section rows fabricate
`n_attempted`/`n_completed` = analyzed count (`reports/summary/breakdown.py:585`); two
different "analyzed" denominators in the same report (`reports/summary/publish.py:243` vs
`build_reports_summary.py:373`); two coexisting representative-repro-row policies can
show different buckets for the same run (`reports/summary/classification.py:260` vs
orchestrator `repro_keyed`); mpl PNG fallback swallows exceptions with bare `pass`
(`reports/summary/plots.py:191`); manual `rc_context.__enter__/__exit__` without
try/finally leaks paper rcParams on error (`reports/eee_heatmap_render.py:182`).

**CLI** — `eee_metadata_caveats.txt` indentation broken (dedent after interpolation)
(`cli/compare_pair_eee.py:154`); `--classify-backlog` silently a no-op without
`--experiment-name` (`cli/portfolio_status.py:177`); leftover `if 1:` debug block with
arbitrary `break` truncating operator-facing stdout + dead `if 0:`
(`cli/index_historic_helm_runs.py:405,267`); `--dry-run` silently overrides explicit
`--run 1` (`cli/run.py:96`); AMBIGUOUS-entry tie-break inherits unsorted walk order in a
tool whose docstring promises determinism (`cli/check_precomputed_discovery.py:135`);
`--filter-inventory-json` ignored without `--build-summary` (`cli/analyze_many.py:110`);
`REPO_ROOT = parents[2]` breaks under non-editable install (`cli/portfolio_status.py:15`).

**Core-metrics reports** — cross-machine `agree_ratio: null` → TypeError kills the render
(`core_metric_curves.py:507`); partial HELM run dir → FileNotFoundError aborts packet
(`core_metric_curves.py:118`); int-vs-str `max_eval_instances` → false
`same_max_eval_instances: no` in mixed packets (`core_metric_curves.py:690`); missing
local_repeat double-plots the official pair with two colors/identical labels
(`core_metrics.py:581`).

**Normalized core** — `HelmRawLoader` drops the instance-source provenance the EEE loader
recorded (`extra=ref.extra` instead of `run.ref.extra`) so degraded loads mislabel as
"helm" (`loaders.py:485`); non-numeric `retrieved_timestamp` → uncaught ValueError fails
the whole load (`loaders.py:210`); `_clean_text` collapses `""` instructions to None,
hiding instructions drift from facts-grade diagnosis once NormalizedDiff is wired into
production (`recipe_facts.py:113`); `_NormalizedJsonView._load` catches only
FileNotFoundError — corrupt raw JSON aborts the packet instead of degrading, and the
None-marker guarantees the doomed re-read (`helm_compat.py:84`).

**Planning/indexing/virtual** — structural-junk official rows (groups/, confs/) become
noise packets and coverage denominators on unscoped runs (`core_report_planner.py:494`);
inventory sort ties on incomplete rows preserve unsorted walk order
(`historic_filtering.py:424`); `rglob` unsorted → non-deterministic
`example_analyzed_report_dirs` (`virtual/coverage.py:243`); incomplete-row model id uses
replace-all underscores instead of replace-first (`historic_filtering.py:304`);
`packet_id` slug collisions resolve silently first-match-wins and clobber report dirs —
suffix with a short hash like `comparison_artifact_stem` does (`core_report_planner.py:955`);
`has_completed_local` documented as "run_path exists on disk" but never touches the
filesystem (`virtual/coverage.py:339`).

**Integrations/infra** — unpinned local docker tag → rebuilt image, same algo identity,
stale cached results (`docker_provenance.py:160`); digest fallback borrows another
repository's RepoDigest without warning (`docker_provenance.py:138`); unknown `--preset`
silently treated as no preset (`adapter.py:1874,1641`); vllm-direct defaults `base_url` to
the auth-protected gateway while the client sends `api_key="EMPTY"` (`adapter.py:1264`);
SIGKILL leaks the running HELM container (no `--name`, no teardown `docker rm`)
(`helm_docker_pipeline.py:145`); `stat_name_id`/`nice_hash_id` skip
`canonicalize_for_hashing` so env-specific perturbation file paths leak into signatures —
false cross-machine diffs (`utils/hashers.py:120`); LiteLLM master key written plaintext
into bundle YAML; HF token file has a umask window before chmod 0600 (`adapter.py:1336`,
`kwdagger_bridge.py:513`).

**Tests** — `test_helm_run_diff_heavy.py` materializes a full HELM demo run (network,
minutes) with no `slow` marker (`tests/test_helm_run_diff_heavy.py:30`).

---

## 5. Decision items (need a call before fixing)

- **D-1 EEE experiment-name derivation off-by-one** (`normalized/eee_sources.py:126`,
  found independently by two reviewers): for the documented
  `local/<experiment>/<dataset>/<dev>/<model>/` layout, `len(rel.parts) > 4` never fires —
  every local row collapses to `eee_only_local`. The docs (CLI help, from_eee docstring,
  CLAUDE.md) promise per-experiment grouping; `tests/test_eee_only_demo.py` pins the
  collapsed behavior, and repeat-pairing works via `evaluation_id` regardless.
  **Recommendation:** fix the code (`>= 4`) to match the documented contract and update the
  fixture paths in the test — the alternative (documenting the collapse) leaves
  user-chosen groupings silently discarded.
- **D-2 `finish_qwen25_gptoss` preset comments vs contents** (`adapter.py:308`): comments
  promise "MMLU × 10 subjects" and "legalbench × 10 subjects"; each family has exactly one
  entry. Either the gap-closing batch under-covers by ~18 run entries (add them) or the
  comments are wrong (reword to "representative", as wmt_14 does). Research-scope call.
- **D-3 `--allow-single-repeat` gates nothing** (`rebuild_core_report.py:410`): both
  branches render; the flag only silences a log line, yet four CLIs plumb it through as if
  it authorizes single-run analysis. Per the project's no-flags-to-preserve-bugs rule:
  either restore the guard (fail without the flag) or delete the flag and its plumbing.
- **D-4 `reports/quantiles.py`** — non-atomic, timestamp-named accumulating outputs,
  reaches into `HelmRunDiff._value_agreement_summary`, referenced only by `cli/reports.py`.
  Retire it, or align it with pair_report conventions.
- **D-5 Verbatim-replay cap** (`manifests/builders.py:311`): the from-spec path always
  applies a numeric `max_eval_instances` (default 1000) to every replayed spec; there is no
  way to express "keep the official cap", in tension with the replay-verbatim rule. Add an
  `official` sentinel end-to-end?

---

## 6. Improvements (non-bug)

- **IM-1** Atomic writes for Stage 6 canonical artifacts (`summary_manifest.json`,
  `run_inventory.csv`, READMEs, reproduce.sh) and `analyze_index_snapshot` writers — route
  through `write_text_atomic` like Stages 4/5. (`reports/summary/common.py:57`,
  `reports/summary/publish.py:23`)
- **IM-2** O(n_repro × n_scope) linear parent scan in the repro sankey assembly — the keyed
  dict exists three screens up; quadratic per scope render. (`build_reports_summary.py:441`)
- **IM-3** Count-and-surface silent per-line skips when parsing `samples.jsonl` (both
  modes) — corrupted lines currently shrink agreement denominators invisibly.
  (`normalized/loaders.py:279,293`)
- **IM-4** Lazy raw-HELM JSON loading — `scenario_state.json` (largest file in a run dir)
  is parsed even under `--skip-diagnosis`. (`normalized/loaders.py:487`)
- **IM-5** `_instances_from_raw_helm` fabricates `is_correct = score > 0.5` for every
  metric incl. bookkeeping — set None except for genuinely binary metrics. (`loaders.py:606`)
- **IM-6** Management summary reports only the first official_vs_local pair with no "1 of
  N" disclosure. (`reports/core_metric_tables.py:335`)
- **IM-7** `_single_run_core_stat_index` rebuilds the means dict per key (O(K×E)) and
  drifts from `joined_metric_means` semantics. (`core_metric_curves.py:608`)
- **IM-8** `parse_known_args` forwards typo'd flags into every core_metrics subprocess —
  validate the pass-through remainder. (`cli/from_eee.py:297`, `cli/compare_pair_eee.py:282`)
- **IM-9** `write_coverage_artifacts` computes sankey paths and drops them from the
  returned mapping. (`virtual/coverage.py:682`)
- **IM-10** Eleven declared runtime deps never imported anywhere (dacite, fsspec, h5py,
  kwarray, lazy_loader, msgspec, numexpr, pygtrie, ruamel.yaml, scikit-learn,
  typing-extensions) — prune or comment as submodule pins. (`pyproject.toml:20`)
- **IM-11** EEE demo fixture path + skip guard duplicated across six test files — one
  conftest fixture. (`tests/test_eee_only_demo.py:25` et al.)
- **IM-12** `_json_compatible` serializes sets in hash order — latent; sort in the set
  branch. (`normalized/diagnose.py:52`)
- **IM-13** Legacy vs normalized "run-level agreement" differ in join granularity and
  tolerance semantics (rel_tol vs abs-only; per-stat vs collapsed metric handle) — document
  the mapping in `docs/eee-vs-helm-metadata.md` or complete R-2. (`helm/diff.py:796`)

---

## 7. Refactors

Ordered by leverage; R-1/R-2/R-3 are the structural payoff.

- **R-1 One canonical-key API.** `_row_logical_keys` / `_logical_key_variants` /
  `_component_key_variants` (planner) + raw joins (`virtual/coverage.py`) are four
  re-implementations of "what keys identify this run"; P0-3/P0-4 exist because one site
  each never adopted the fix. Extract `logical_key_set(obj)` (wrapping
  `helm/run_entries.canonical_logical_key`) and use it at every matching/join site.
  *This is the fix vehicle for P0-3/P0-4.*
- **R-2 Retire the legacy half of `helm/diff.py` onto `NormalizedDiff`.** The instance
  summary/distance/agreement/tolerance-sweep surface (~700 lines serving only
  pair_report/pair_samples/quantiles/compare_batch) carries P0-6, the P1-18 determinism
  bugs, and triplicated boilerplate (~100 lines × 3), and duplicates the normalized core
  with different join granularity. Keep `HelmRunDiff` for its load-bearing job —
  run_spec-grade semantic diff / diagnosis inputs (~60% reduction). The normalized path is
  deterministic by construction.
- **R-3 Split `infer_stack/adapter.py` (1,915 lines).** Seams: PRESET_CONFIGS data (→
  presets module or YAML under `configs/`), catalog/ServingFacts resolution, bundle
  materialization, freeze/rel-path logic. Also stop importing private `_classify`/
  `_enumerate_runs` from `cli/check_precomputed_discovery` — promote a public discovery API.
- **R-4 One gate-classification function for sankeys.** The six-gate filter ladder is
  duplicated in three row-builders (+2 root tables); the hierarchical funnel in
  `filter_analysis_tables` disagrees with Stage A on metadata/judge gates (P2). Extract
  `_classify_filter_gates(row)`; delete the legacy builders that survive only for tests
  (`_build_end_to_end_funnel_rows`, `_build_filter_to_attempt_rows`,
  `_build_attempted_to_repro_rows`, unused `classification.py` re-exports).
- **R-5 Dedupe EEE conversion machinery.** `convert_helm_run_to_cached_eee` /
  `convert_local_helm_run_to_eee` are ~100-line near-duplicates (P1-7's status-gate fix
  must land in both); four inconsistent "which *.json are aggregates" predicates across
  the normalized package (a sidecar-only dir passes `_artifact_has_aggregate`). One shared
  conversion core + one name-set predicate. (`normalized/eee_artifacts.py:106,574`)
- **R-6 Consolidate copy-pasted workflow helpers.** `latest_index_csv`+`load_rows` (×2 —
  the P0-2 vehicle), `_clean_optional_text` (×4), `_write_csv` (×2), `_is_truthy_text`/
  `_coerce_float` re-declarations. One shared module. Also fold in the run-inventory
  loaders duplicated in `analyze_backlog`/`portfolio_status`.
- **R-7 Extract Stage-1 model-eligibility policy.** The predicate is inlined in
  `cli/index_historic_helm_runs.py` and computed twice (selection at 299-319 vs filter
  report at 346-376) — a silent-divergence hazard for the research-critical selection.
  Move `classify_model_eligibility(row)` into `indexing/historic_filtering.py`.
- **R-8 One run-entry token parser.** `_parse_model_deployment` (lease_bracket),
  `_locator_run_entry` (kwdagger_bridge), `_strip_local_deployment` + writer
  `_inline_local_deployment` (adapter) each re-implement run-entry tokenization.
- **R-9 Metric-handle helper.** `cfg.metric_id or cfg.metric_name or er.evaluation_name`
  maintained in four places (joins, compare, model, core_metric_curves); plus dead
  `metrics_by_id`/`join_run_level`/unused imports. (`normalized/model.py:235`)
- **R-10 Dead-surface sweep, core-metrics.** Six render/table helpers dead outside compat
  re-exports; ~14 unused imports in the `core_metrics` facade; `_infer_run_spec_name`
  triplicated; hardcoded threshold list duplicating `DEFAULT_ABS_TOL_THRESHOLDS` with
  silent-blank lookups downstream — derive from the constant and assert membership.
  (`core_metric_plots.py:636`, `core_metrics.py:391`)
- **R-11 Dead code, misc.** `sankey_builder` demos reference undefined names (NameError if
  called; doctests would fail under xdoctest) (`utils/sankey_builder.py:461,647`);
  `link_alias` self-alias no-op calls with misleading comments (7 sites);
  `filter_analysis.py` ~10 dead imports + double-computed tables in
  `emit_filter_report_bundle`; dead params/branches in `virtual/compose.py`,
  `coverage._row_dim`, `gather_runs(include_max_eval_instances=)`, `if 1:` msgspec branch;
  `_MsgspecRunView` no-op alias + context-free JSONDecodeError in `compat/helm_outputs.py`;
  last internal import of the `helm/hashers` shim (`reports/core_metrics.py:41`).

---

## 8. Implementation plan

Ordering principle: **verified research-number corruption first**, then honesty of
failure reporting, then robustness, then structure. Every phase lands as small,
independently revertable commits (per the commit-in-logical-units convention). Where a fix
*intentionally changes published artifact content* (P0-1, P1-13, P1-12), regenerate the
affected characterization baselines in the same commit and say so in the message.

### Phase A — Restore the gates (½ day)
The suite must run before anything else merges.
1. **P0-9**: install `kwconf` into the dev venv (or re-run setup); add
   `pytest.importorskip("kwdagger")` to the five affected test modules.
2. **P0-8**: fix `analyze_backlog` import; add module to import-smoke coverage.
3. **P1-24** (partial): re-run `uv lock`; bump `run_developer_setup.sh` default python;
   add `transformers<5`, `huggingface-hub==0.36.2` constraints, declare
   `networkx`/`matplotlib`/`every_eval_ever`.
   *Gate:* `pytest --collect-only` clean; full fast suite green.

### Phase B — Research-number corruption (2–3 days)
Each item: fix + a regression test that fails on the old behavior.
1. **P0-1** tol010 relabel: new 0.01 key in `loading.py`; rename the agreement-key family
   to milli-unit names across loading/BRS/analyze_experiment (grep `_01`/`_005`/`_05`).
2. **P0-2** index resolution: unstamped-first fallback; consolidate the duplicated helper
   (starts R-6).
3. **P0-3 + P0-4** canonical keys: implement R-1's `logical_key_set()`; adopt at
   `core_report_planner.py:987` and all `virtual/coverage.py` joins; test with a
   token-order-drifted fixture pair (the a25aac9 shape).
4. **P0-6** perturbed-flag fix + perturbed-fixture assertion.
5. **P1-1 + P1-2** `_fact_status` unknown-on-partial + extract official
   `max_eval_instances` in `extract_run_spec_fields`.
6. **P1-15** NaN policy: filter non-finite at row construction, count drops, verify JSON
   validity with a NaN fixture.
7. **P1-13** cross-machine curve: re-derive at rel_tol=0 (or split panel + explicit label).
   *Gate:* `test_end_to_end_summary`, `test_eee_only_demo`, phase3 equivalence matrix;
   regenerate baselines only for artifacts the fixes intentionally change.

### Phase C — Cache identity / execution correctness (1–2 days)
1. **P0-5** content-hash `model_deployments.yaml` into the bundle filename (mirrors the
   run_spec materializer convention); verify old jobs recompute.
2. **P1-21** promote `precomputed_root` to algo_params on the from-spec discovery node —
   or decide to retire the discovery shape (aligns with the all-from-spec migration on
   this branch).
3. P2: unpinned-tag image id in algo identity; cross-repo digest fallback → image-id
   branch; `docker run --name` + teardown `rm -f`.
4. **P1-22 + P1-23** manifest error-path NameError; duplicate-label SystemExit.
   *Gate:* `test_kwdagger_submatrix_contract`, `test_container_execution`,
   `test_from_run_spec_pipeline`; one manual bundle re-export showing changed algo_id.

### Phase D — Report honesty & stale state (2–3 days)
1. **P0-7** failure-taxonomy colors + artifact-produced smoke test.
2. **P1-8/P1-9/P1-10/P1-11** category mappings; `not_analyzed` status level; unreadable-
   bundle surfacing; anchored classifier patterns.
3. **P1-12** primary-reason filter sankey (conservation restored); note in the artifact.
4. **P1-4/P1-5** stale-dir pruning in Stage 5 and Stage 6 trees.
5. **P1-6** unconditional stale scoped-inventory delete.
6. **P1-7** status.json gate on cached/local EEE resolution (fold into R-5's shared core
   if sequenced together).
7. **P1-16** `--no-plots` preserves figures; **P1-19** analyze-many exit codes;
   **P1-20** reproduce.sh flag threading.
   *Gate:* summary fixture rebuild → diff shows only intended changes; re-run twice →
   byte-identical (validates stale-state fixes).

### Phase E — Determinism sweep (1 day)
1. **P1-17** sort benchmark_output discovery (eval_audit copy + magnet submodule PR) and
   `gather_runs`.
2. **P1-18** sorted iteration/tiebreakers in the four `helm/diff.py` sites (moot for any
   part retired by R-2 — do the cheap sort now anyway).
3. P2 determinism: coverage `rglob` sort; inventory sort key + run_dir; `_json_compatible`
   set sort; `check_precomputed_discovery` candidate sort.
   *Gate:* run Stage 1 + a packet render twice under different `PYTHONHASHSEED`; byte-compare.

### Phase F — P2 backlog + improvements (opportunistic, ~2 days spread)
Small independent fixes: the whole of §4 not already covered, IM-1 atomic writes, IM-3
skip counting, IM-2 quadratic scan, IM-6/IM-7/IM-8/IM-9, secrets hygiene (P2 integrations).
Batch by subsystem so each commit is reviewable against one test module.

### Phase G — Structural refactors (1–2 weeks, characterization-first)
Same method as the Phase-2 god-module splits: snapshot artifacts, pure relocation commits,
then dedup. Order: **R-6** (finishes what B.2 started) → **R-4** (+P2 funnel-gate
alignment) → **R-5** → **R-7** → **R-9/R-10/R-11** (dead surface) → **R-8** → **R-3**
(adapter split) → **R-2** last (largest; touches research numbers — needs the
pair_report/quantiles consumers routed to NormalizedDiff behind the behavior-equivalence
matrix, and closes IM-13 by construction).

### Decision items to resolve with the operator before the relevant phase
D-1 (before Phase B if the `>= 4` fix lands there), D-2 (research scope), D-3 (Phase D),
D-4 (Phase G / R-2), D-5 (Phase C).

---

*Method note: findings were produced by nine parallel subsystem reviews with
caller-verification requirements, then all nine high-severity claims were independently
re-verified in the main session (marked ✅). Reported (☑️) items were agent-verified with
surrounding-context reads and caller greps but not independently reproduced; treat
line numbers as anchors, not exact offsets, if the tree has moved.*

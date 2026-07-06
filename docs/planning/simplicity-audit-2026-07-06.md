# Simplicity & Bloat Audit — 2026-07-06

**Status: PLANNED.** Implementation on `impl/simplicity-audit` (branched from
`impl/run-from-run-spec`). Stamp phases here as they land.

**Lens:** simplicity, bloat reduction, and ease of understanding — deliberately *not* a
correctness re-audit. The [2026-07-02 codebase audit](../historical/planning/codebase-audit-2026-07-02.md) is
fully implemented except R-2; this plan picks up what those passes left: the deferred R-2,
dead dev/POC trees, doc accumulation, and the onboarding surface.

**Method:** three parallel deep explorations (package core; user-facing surface —
README/docs/configs/reproduce/examples/setup; periphery — dev/tests/papers/git hygiene),
with every high-impact claim re-verified in the main session (dead-method caller grep,
shim consumer grep, 3.11-vs-3.12 mismatch, dead `examples/`, `configs/generated/`
contents, empty `eval_audit/scripts/`).

**Operator decisions (2026-07-06):** include R-2 · delete stale content outright
(implemented planning docs archive to `docs/historical/`, not deleted) · rotate journals
to archive · `reproduce/`: dedupe `_lib.sh` only, numbered runbook scripts untouched.

---

## 1. Findings summary

The repo is clean on the axes that usually rot: no tracked build junk or generated
reports, comprehensive `.gitignore`, centralized test conftest, small fixtures (444K),
healthy `infra/` utility layer, all 19 console scripts resolve, no phantom commands in
docs. The bloat is concentrated and specific:

1. **Legacy diff half (R-2, deferred from prior audit).** `helm/diff.py` is 1,844 lines;
   agreement numbers already flow through `NormalizedDiff`. The legacy
   agreement/tolerance half (~L796–1844) survives for exactly two consumers:
   `reports/pair_report.py` and `reports/pair_samples.py`. `instance_agreement_profile`
   (L1366–1509, ~143 lines) has **zero callers** today.
2. **Dead dev trees (~6k lines).** `dev/poc/old-sankey/` (own README declares it dead),
   `dev/poc/eee-audit/` (superseded by `normalized/eee_artifacts.py`), `dev/oneoff/`
   (13 stale scripts, oldest content in repo). `dev/tools/deployment_match/`,
   `dev/scripts/`, `dev/e2e-tests/` are live and stay.
3. **God functions.** `build_reports_summary._render_scope_summary` (~920 lines),
   `core_metrics.main` (~670 lines). Also `presets.py` is 1,153 lines of which ~658 is
   the `PRESET_CONFIGS` data literal — data, not logic.
4. **First-run breakage.** `run_developer_setup.sh` defaults `python3.11` vs
   `requires-python >= 3.12`; both `examples/` scripts call a nonexistent `scripts/`
   dir; README references a nonexistent `reports/` tree and `docs/paper/` (actual:
   `docs/papers/`); `reproduce/README.md` indexes 9 of 22 scenarios and hardcodes
   another user's home path.
5. **Checked-in generated state.** `configs/generated/` — 19 files from 2026-03/04
   (dead hostnames namek/yardrat, `archive-from-aiq-magnet/`), violating README's own
   "checked-in manifests only" policy.
6. **Doc/journal accumulation.** ~7 planning docs describe implemented/superseded work;
   the olmo-from-spec and infer-stack doc trios carry conflicting statuses; journals
   total ~544K (2nd-largest tracked content after `uv.lock`); two 1,000+-line one-off
   paper analysis scripts live under `docs/papers/`.
7. **Helper duplication (R-6 unfinished).** `reports/summary/common.py` exists but
   adoption stopped at its subtree: `_load_json` ×4, `_clean_optional_text` ×6,
   `_find_pair`/`_find_curve_value` ×3 each, `_coerce_float` ×4, `_abbreviate_label` ×2,
   `_latest_run_inventory_csv` ×2.
8. **Shell drift.** Three divergent copies of `reproduce/**/_lib.sh` (olmo_models,
   olmo_models_combined, small_models_kubeai).
9. **Small dead surface.** `eval_audit/helm/hashers.py` re-export shim (all real imports
   already target `utils.hashers`; the 5 remaining in-package uses are trivially
   repointable), empty `eval_audit/scripts/` package, an always-true `if 1:` block in
   `cli/index_historic_helm_runs.py:343`.

---

## 2. Implementation plan

Ordered low-risk → high-risk; each numbered group is a logical commit. Never commit the
dirty submodule gitlinks.

### Phase 1 — Delete dead weight (pure deletions, zero behavior change)
1. `git rm -r dev/poc/old-sankey/ dev/poc/eee-audit/ dev/oneoff/`; fix the two
   `docs/pipeline.md` references to deleted files (`sweep.py`,
   `migrate_eee_source_org_tag.py`).
2. `git rm -r configs/generated/` (grep docs/reproduce for references first) and
   `git rm -r examples/` (every command dead).
3. Package dead code: remove empty `eval_audit/scripts/`; delete `helm/hashers.py` shim
   and repoint the 5 `helm/*` imports to `eval_audit.utils.hashers` (fix the doctest in
   `utils/hashers.py` too); delete dead `HelmRunDiff.instance_agreement_profile`; unwrap
   the `if 1:` block at `cli/index_historic_helm_runs.py:343`.

### Phase 2 — First-run correctness & doc accuracy
1. `run_developer_setup.sh`: `python3.11` → `python3.12`.
2. `README.md`: fix nonexistent `reports/` references and `docs/paper/` → `docs/papers/`;
   document `eval-audit-index-historic`.
3. Rewrite `reproduce/README.md`: index all 22 scenarios (status from top-level README
   table); drop the hardcoded `/home/joncrall/...` path.
4. `git mv docs/papers/neurips-2026/*.py` → `dev/paper-analysis/neurips-2026/`;
   `git mv` the dated `session_log*.md` → `docs/historical/`.

### Phase 3 — Planning-doc & journal hygiene (docs only)
1. `git mv` implemented/superseded plans → `docs/historical/planning/`:
   codebase-audit-2026-07-02, deployment-match-search-plan,
   e2e-from-run-spec-migration-plan, from-spec-deployment-rewrite-plan,
   run-from-relative-path-plan, olmo-smoke-grouped-runner,
   olmo-models-docker-pipeline-plan (verify each status header; fix inbound links).
2. Consolidate the olmo-from-spec trio and the infer-stack trio into one live doc each
   with a current status header; superseded members → `docs/historical/planning/`.
3. Rotate journals → `dev/journals/archive/2026-H1/`; fresh `claude.md`/`codex.md` start
   with a pointer to the archive.

### Phase 4 — Finish helper consolidation (R-6 completion)
Promote the generic helpers to one shared module under `eval_audit/utils/`
(`reports/summary/common.py` re-exports to avoid churning its callers); delete the
private copies listed in Finding 7 and repoint. Check signatures before merging (one
`_coerce_float` differs; `_write_text` variants legitimately diverge — skip).

### Phase 5 — reproduce/ `_lib.sh` dedupe (scoped)
Merge the three `_lib.sh` copies into `reproduce/_lib.sh` preserving every behavior
(parameterize genuine divergences); per-scenario `_lib.sh` becomes a thin
`source ../../_lib.sh` shim. `bash -n` all affected scripts. No other runbook changes.

### Phase 6 — God-function extraction (pure relocation, characterization-gated)
1. Extract `_render_scope_summary`'s sequential per-scope render blocks into
   module-level functions.
2. Extract `core_metrics.main`'s report sections into functions.
3. Move `PRESET_CONFIGS` to a YAML resource next to `presets.py` (verify job identity
   hashes bundle contents, not the .py source, before moving).
Gate: `tests/test_end_to_end_summary.py` + `tests/test_eee_only_demo.py` artifacts
byte-identical before/after.

### Phase 7 — R-2: retire the legacy half of `helm/diff.py` (behavior-changing, last)
1. Migrate `pair_report.py` + `pair_samples.py` onto `NormalizedDiff` (`value_summary`,
   `run_level_summary`, `instance_level_summary`, `per_metric_curves`, `diagnosis`),
   validated against the phase3 behavior-equivalence matrix and
   `tests/fixtures/phase3_baseline/`. Document intentional semantic deltas (join
   granularity, rel_tol handling — prior-audit IM-13) in the commit and in
   `docs/eee-vs-helm-metadata.md`.
2. Delete `helm/diff.py` ~L796–1844; slim `summary_dict`/`summary_text` (target ~650–750
   lines: semantic diff + diagnosis only).
3. Shrink/retire `normalized/helm_compat.py` to what semantic consumers still need.

### Deferred (recorded, not done)
Fat `cli/` module relocation (`from_eee.py` 482 L etc.); deprecated `cli/reports.py`
dispatcher (keep until pre-2026-06-11 `reproduce.sh` scripts age out); phase3/compare
test-cluster consolidation; `_MsgspecRunView` placeholder.

## 3. Verification

- Every phase: `python -m py_compile` on touched files; fast suite with repo `.venv`
  (baseline 443 passed / 71 skipped); slow planner file when planner paths move.
- Phases 1–3: grep sweep for every deleted/moved path across README/docs/reproduce/dev.
- Phase 5: `bash -n` on all affected scripts.
- Phases 6–7: byte-identical artifact gates; phase3 equivalence matrix for R-2.
- Final: full suite + a `reproduce/pythia_mmlu_stress` compose→summary smoke if the
  environment allows.

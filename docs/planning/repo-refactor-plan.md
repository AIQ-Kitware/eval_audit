# eval_audit Refactor Plan

**Goal:** more elegant, easier to understand/reproduce, lower maintenance — without changing pipeline outputs.
Each phase is independently shippable and ordered so earlier phases de-risk later ones.

**Validation gate** for the whole effort: `tests/test_end_to_end_summary.py`, `tests/test_eee_only_demo.py`,
and the `reproduce/smoke` runbook must produce byte-identical reports before/after.

**Plan-doc location decision:** plan docs live here in `docs/planning/`. The older
`docs/agent_plans/` holds one file (`allow_closed_judge_flag_plan.md`); `git mv` it here when
convenient and update the `docs/agent_plans/` reference in `CLAUDE.md`.

---

## Phase 0 — Hygiene & finish-the-rename
*Effort: ~half day · Risk: none · Reversible*

### 0a. Clean up the root

**Move, not delete, the large YAML data files.** `run_specs.yaml` (24KB) and `run_details.yaml`
(108KB) are the canonical HELM run registry and metadata — primary research inputs, not stale
outputs. Facts established by inspection:

- At runtime they are loaded from `audit_store_root() / "configs"` (default
  `/data/crfm-helm-audit-store/configs/`, override via `AUDIT_STORE_ROOT`) through
  `infra/paths.py:run_specs_fpath()` / `run_details_fpath()`, re-exported by `infra/api.py` as
  `repo_run_specs_fpath()` / `repo_run_details_fpath()`, consumed by `manifests/presets.py` and
  `manifests/builders.py`.
- **No sync script exists in the repo** that copies root → audit store (checked
  `run_developer_setup.sh`, `Makefile`, `dev/scripts/`). The repo-root copies appear to be the
  committed source-of-truth that an operator copies into the store manually.

**Action:** `git mv run_specs.yaml run_details.yaml configs/` and grep-fix any operator docs that
mention the root paths (`grep -rn "run_specs.yaml\|run_details.yaml" docs reproduce dev README.md`).
The code paths need no change (they read the audit store, not the repo). Do NOT delete these files.

**Remove or gitignore actual clutter:**
- Untracked junk: `output.txt` (187KB), `missing_qwen.txt`, `todo` — delete; add `output.txt`,
  `todo` to `.gitignore`.
- Gitignore `.venv*` (four venvs exist: `.venv`, `.venv-1`, `.venv-test`, `.venv_eval_audit`; keep
  `.venv` as canonical — it's what `run_developer_setup.sh` creates — and document that in README).
- `qwen35-helm-run.md` is tracked at root but is a run-specific operational note — `git mv` to
  `docs/historical/`.

### 0b. Finish `vllm_service → infer_stack`

The rename is ~complete in `eval_audit/`, `tests/`, `configs/`, `reproduce/`. Remaining items
(verified by grep):

1. `git rm -r eval_audit/integrations/vllm_service/` (empty dir, only `__pycache__` inside).
2. Rename `tests/test_vllm_service_integration.py` → `test_infer_stack_integration.py`; update
   internal references.
3. **Two dev scripts still invoke the deleted module** and are currently broken:
   `dev/e2e-tests/e2e-phi_2-vllm-philosophy.sh:27` and
   `dev/e2e-tests/e2e-phi_2-vllm-philosophy-incomparable.sh:27` call
   `python -m eval_audit.integrations.vllm_service export-benchmark-bundle` →
   change to `python -m eval_audit.integrations.infer_stack`.

This is one small commit that unblocks the `wip` branch.

### 0c. Consolidate root docs

Currently five markdown files compete at the root: `README.md`, `CLAUDE.md`, `AGENTS.md`,
`LOCAL_AGENTS.md`, `ARCHITECTURE.md`.

- `README.md` = user-facing entry (stays at root).
- `AGENTS.md` = primary agent guidance (stays; already authoritative with journal/lessons rules).
- Merge `LOCAL_AGENTS.md` (9 lines: uv path, no-sudo rule, one stale task note) into `AGENTS.md`;
  drop the stale task note.
- Move `ARCHITECTURE.md` (601 lines, 10 ADRs) → `docs/architecture.md`; add a link from README.
- `CLAUDE.md` stays at root; update its `docs/agent_plans/` reference per the location decision above.

### Phase 0 verification

```bash
git status --short                          # clean (besides intended changes)
grep -rn "vllm_service" --include="*.py" --include="*.sh" --include="*.yaml" \
  eval_audit tests configs reproduce dev | grep -v __pycache__   # → empty
python -m pytest tests/ -x -q               # all green
ls *.md                                     # README.md CLAUDE.md AGENTS.md only
```

---

## Phase 1 — Unify the CLI surface
*Effort: 1–2 days · Risk: low (mechanical) · Reversible*

**Problem:** 18 console scripts point inconsistently at `cli/` (8), `workflows/` (7), and
`reports/` (3). `cli/` itself mixes three patterns: 7-line re-export shims, subcommand dispatchers,
and 1000-line implementations.

**Strategy — minimal churn:** library modules KEEP their `main()` functions (runbooks invoke
`python -m eval_audit.workflows.build_reports_summary` etc. — see Pitfalls). The change is that
every *console script* resolves to a thin `eval_audit.cli.*` wrapper, so there is exactly one
documented command surface. Wrappers import the library `main` and nothing else.

### Entrypoint mapping (complete)

| Console script | Currently points at | Action |
|---|---|---|
| `eval-audit-check-env` | `cli.check_env` | none (already conformant) |
| `eval-audit-make-manifest` | `cli.manifests` | none |
| `eval-audit-run` | `cli.run` | none |
| `eval-audit-analyze-many` | `cli.analyze_many` | none |
| `eval-audit-portfolio-status` | `cli.portfolio_status` | none |
| `eval-audit-build-virtual-experiment` | `cli.build_virtual_experiment` | none |
| `eval-audit-from-eee` | `cli.from_eee` | wrapper OK now; logic split in Phase 3 |
| `eval-audit-compare-pair-eee` | `cli.compare_pair_eee` | wrapper OK now; logic split in Phase 3 |
| `eval-audit-index` | `workflows.index_results` | repoint at existing shim `cli/index.py` (give it a real `main`) |
| `eval-audit-analyze-experiment` | `workflows.analyze_experiment` | repoint at existing shim `cli/analyze_experiment.py` |
| `eval-audit-rebuild-core` | `workflows.rebuild_core_report` | repoint at existing shim `cli/rebuild_core.py` |
| `eval-audit-compare-batch` | `workflows.compare_batch` | new shim `cli/compare_batch.py` |
| `eval-audit-build-summary` | `workflows.build_reports_summary` | new shim `cli/build_summary.py` |
| `eval-audit-analyze-index-snapshot` | `workflows.analyze_index_snapshot` | new shim `cli/analyze_index_snapshot.py` |
| `eval-audit-prepare-eee` | `workflows.prepare_eee_artifacts` | new shim `cli/prepare_eee.py` |
| `eval-audit-compare-pair` | `reports.pair_report` | new shim `cli/compare_pair.py` |
| `eval-audit-report-core` | `reports.core_metrics` | new shim `cli/report_core.py` |
| `eval-audit-report-aggregate` | `reports.aggregate` | new shim `cli/report_aggregate.py` |

Unwired CLI-shaped modules to resolve in the same pass:

| Module | Status (verified) | Action |
|---|---|---|
| `cli/index_historic_helm_runs.py` (1015 ln) | Stage 1 of the pipeline; no console script; invoked via `python -m` per docs | add `eval-audit-index-historic` console script; defer the logic split (filtering → `indexing/`) to Phase 2 — it's a god-module problem, not a wiring problem |
| `cli/compare.py`, `cli/reports.py` | grouped-CLI dispatchers; imported by `tests/test_smoke.py::test_cli_help_smoke`; never wired as scripts | **delete**; replace their entries in `test_smoke.py`'s parametrize list with the underlying flat mains (`pair_report.main`, `core_metrics.main`, etc.) |
| `cli/analyze_backlog.py` (144 ln) | operational tool; referenced only in `docs/historical/` and journals | keep, add one-line module docstring noting it's invoked via `python -m`; or wire as `eval-audit-analyze-backlog` if still in active use (ask operator) |
| `workflows/analyze_official_index.py` | 4-line back-compat alias of `analyze_index_snapshot` (which already exposes an `analyze_official_index = analyze_index_snapshot` alias itself) | delete the module; update the reference in `docs/pipeline.md:260` |

### Pitfalls (verified by grep — do not skip)

- `tests/test_smoke.py:7-11` imports five `cli.*` mains directly, including the two dispatchers
  being deleted. Update the import list and parametrize in the same commit as the deletion.
- Runbooks bypass console scripts: `python -m eval_audit.workflows.{build_reports_summary,
  analyze_experiment,index_results}` appear in `reproduce/` and `dev/`. Keeping library `main()`s
  (per the strategy above) means these keep working — do not move/rename the workflows modules in
  this phase.
- `reproduce/llama2_70b_helm_audit/README.md:76` references `eval-audit-make-bundle`, which does
  not exist in pyproject. The real command is `python -m eval_audit.integrations.infer_stack
  export-benchmark-bundle`. Fix while touching runbooks.

### Naming cleanup

`core_metrics.py` / `core_packet.py` / `core_packet_summary.py` / `core_report_planner.py` are easy
to confuse — add module docstrings stating each one's distinct role. Renames are optional; if done,
do them in Phase 2 when those files are already being restructured.

### Phase 1 verification

```bash
# every console script resolves and prints help
for cmd in $(grep -oE "^eval-audit-[a-z-]+" pyproject.toml); do $cmd --help >/dev/null || echo "BROKEN: $cmd"; done
grep -c "eval_audit.cli" pyproject.toml     # == number of [project.scripts] entries
python -m pytest tests/test_smoke.py -q     # green after dispatcher deletion
python -m eval_audit.workflows.build_reports_summary --help   # python -m paths still work
```

---

## Phase 2 — Decompose the god modules
*Effort: 3–5 days · Risk: medium (mitigated by characterization tests) · Highest maintenance payoff*

### Primary target: `build_reports_summary.py`

**5,369 lines, 108 functions** doing ≥7 unrelated jobs: row loading, filter classification, 4
different sankey builders, failure triage, run-multiplicity summary, prioritized-breakdown, artifact
repair/publish, README gen, and symlink management.

Split into a `reports/summary/` subpackage, along the function-name-prefix seams that already exist:

| New module | Absorbs (by prefix) | Role |
|---|---|---|
| `summary/loading.py` | `load_rows`, `latest_index_csv`, `_load_all_repro_rows`, `_load_filter_inventory_rows` | row/JSON I/O |
| `summary/classification.py` | `_classify_*`, `_storyline_*`, `_bucket_*`, `_primary_filter_reason` | filter/stage taxonomy |
| `summary/sankeys.py` | the 4 `_build_*_root` / `_build_*_rows` families | all sankey assembly |
| `summary/failure_triage.py` | `_classify_failure`, `_read_log_tail`, `_pick_example_cases`, `_triage_*` | failure analysis |
| `summary/multiplicity.py` | `_build_run_multiplicity_summary` + formatter | run-multiplicity |
| `summary/breakdown.py` | `_build_prioritized_breakdown_summary` (the 480-line one) + formatter | prioritized breakdown |
| `summary/publish.py` | `_repair_*`, `_publish_*`, artifact/symlink/FD-limit helpers (`_raise_fd_limit`, `_fd_count`) | artifact publishing |
| `workflows/build_reports_summary.py` | orchestrator + `main()` only | **module path must survive** — runbooks call `python -m eval_audit.workflows.build_reports_summary` |

**Implementation notes:**

- **Hoist the `@profile` shim first.** Lines 51–60 define a no-op `profile` decorator that
  `line_profiler` swaps in when `LINE_PROFILE=1`. Several functions use it. Move the shim to
  `infra/profiling.py` and import it from each new summary module; do this as the first commit of
  the split so subsequent moves are clean cut-paste.
- Keep imports one-directional: `loading`/`classification` are leaves; `sankeys`/`triage`/
  `multiplicity`/`breakdown` may import the leaves; `publish` and the orchestrator sit on top.
  If a function is needed by two non-leaf modules, it belongs in a leaf.

### Method (critical for safety)

1. **First** write characterization tests: run the existing summary build on a fixture, snapshot every
   output artifact (JSON/txt/PNG-hash). `tests/test_end_to_end_summary.py` is the anchor.
2. Move functions in dependency order (leaves first: `loading`, `classification`), running tests after
   each move. No logic edits during the move — pure relocation, one commit per module extracted.
3. Only after the split, do targeted dedup within each new module.

### Secondary targets (same characterization-first method, lower urgency)

- `cli/index_historic_helm_runs.py` (1015) — extract filtering/eligibility logic to `indexing/`,
  leave a thin CLI (finishes the Phase 1 deferral).
- `helm/diff.py` (2658) and `helm/analysis.py` (1304) — split diff computation from diff *reporting*.
- `reports/core_metrics.py` (2672) — separate curve math from rendering.
- `reports/filter_analysis.py` (1790) and `reports/eee_only_heatmap.py` (1460) — separate
  data-shaping from plotting.

### Phase 2 verification

```bash
python -m pytest tests/test_end_to_end_summary.py tests/test_packet_driven_summary_loading.py -q
python -m eval_audit.workflows.build_reports_summary --help     # module path intact
wc -l eval_audit/reports/summary/*.py eval_audit/workflows/build_reports_summary.py  # none >1200
# characterization snapshots byte-identical (see step 1 fixture)
```

---

## Phase 3 — One comparison core, HELM + EEE as adapters
*Effort: 1–2 weeks · Risk: high · Needs its own design pass before any code*

**Problem:** the HELM-shaped and EEE-only paths are parallel surfaces — `compare-pair` vs
`compare-pair-eee`, `from-eee`, plus the `normalized/helm_compat` + `compat/helm_outputs` bridges.
Logic is duplicated rather than shared.

### Target architecture

```
                ┌─ helm_adapter  (run_spec.json + HELM run dirs) ─┐
input sources ──┤                                                 ├─→ NormalizedRun ─→ comparison core ─→ reports
                └─ eee_adapter   (every_eval_ever artifacts)      ─┘
                                                   (single path: planner → core_metrics → summary)
```

- **`NormalizedRun` becomes the only currency** past the adapter boundary. `normalized/model.py` and
  `helm_compat.py` already exist — this phase promotes that boundary from "bridge bolted onto the
  HELM path" to "the contract everything speaks."
- **Collapse the dual CLIs:** `compare-pair` and `compare-pair-eee` become one `eval-audit-compare-pair`
  that auto-detects source type (`detect_helm_sidecars` in `cli/from_eee.py` already does part of
  this). Same for the analyze→summarize chain — it should run unchanged regardless of source, which
  is already the stated goal for `build-virtual-experiment`.
- **Keep the "comparability collapses to `unknown`" behavior** — that's correct per the research
  design (see CLAUDE.md). The point isn't to change semantics, it's to stop maintaining two code
  paths that produce them.
- Keep the old `eval-audit-compare-pair-eee` script as a deprecated alias for one release cycle so
  existing runbooks don't break silently; print a deprecation pointer.

### Why last

It's the only phase that touches research-meaningful behavior, so it needs Phases 1–2's clean seams
and test coverage underneath it first. Start it with a written design doc + a behavior-equivalence
test matrix (HELM-input and EEE-input producing the expected `status`/`comparability_unknown:*`
fields), *then* refactor.

**Exit criteria:** a single comparison core with two input adapters; one `compare-pair` and one
analyze→summarize chain; EEE/HELM behavior matrix tests green; `tests/test_eee_only_demo.py` and
`tests/test_compare_pair_eee.py` outputs unchanged.

---

## Cross-cutting cleanups

Fold into whichever phase touches them:

- **`dev/oneoff/` (16 scripts):** not imported anywhere (verified). Move genuinely-dead ones to
  `dev/archive/` or delete; keep only what's reusable.
- **`configs/generated/`:** generated YAML checked into git drifts. Either gitignore it (regenerate
  on demand) or clearly mark it as fixtures. Decide together with the `run_specs.yaml` move (0a).
- **`reproduce/` (94 `.sh` + 14 `.md`, the largest tracked dir):** audit for dead runbooks after
  Phases 0–1. Known stale reference already found: `eval-audit-make-bundle` in
  `reproduce/llama2_70b_helm_audit/README.md:76`. A cheap audit script: extract every
  `eval-audit-*` token and `python -m eval_audit.*` path from `reproduce/` and check each resolves.

---

## Execution order & dependencies

```
Phase 0  ──►  Phase 1  ──►  Phase 2  ──►  Phase 3
(hygiene)    (CLI home)   (god modules)  (HELM/EEE unify)
   │             │              │
   └─ unblocks   └─ gives       └─ gives Phase 3 the seams + tests it needs
      wip commit    Phase 2 a
                    clean place
                    to land split modules
```

Suggested commit granularity: 0a / 0b / 0c as three commits; Phase 1 as ~four (repoint scripts, new
shims, delete dispatchers + fix test_smoke, wire index-historic); Phase 2 as one commit per extracted
module plus one for the profiling shim; Phase 3 behind a design doc reviewed first.

Phases 0–2 deliver ~80% of the "elegance + lower maintenance" win at low risk. Phase 3 is the
architectural payoff and should not start until 1–2 have established clean boundaries and
characterization tests.

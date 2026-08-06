# Deferred simplifications — 2026-08-06 audit

**Status:** PROPOSED — none of this is implemented. This note records the
structural findings from the 2026-08-06 repo audit that were deliberately
**not** acted on (too much churn risk right before the paper), so a future
maintainer can pick them up with the analysis already done. The items that
*were* acted on (stale-doc corrections, ~2,000 lines of verified-dead code,
root-archive hygiene) landed as the commit series ending at this note.

## 1. Unify the plan→render loop (the one item needing real design)

Three callers plan with `core_report_planner.build_planning_artifact` and
then iterate `artifact["packets"]`, each rendering differently:

| Caller | Render mechanism | Output dir convention |
|---|---|---|
| `workflows/analyze_experiment.py` | in-process `rebuild_core_report_main(argv)` | `core-reports/core-metrics-{slug}` |
| `cli/from_eee.py` (`_render_packet` + thread pool) | subprocess `python -m eval_audit.reports.core_metrics` | `{experiment}/core-reports/{packet_id}` |
| `cli/compare_pair_eee.py` | `run_core_metrics` (single packet) | `--out-dpath` |

Problems this creates:

- The two families disagree on packet-dir naming (`{packet_id}` vs
  `core-metrics-{slugify_identifier(packet_id)}`) even though Stage 4
  (`build_reports_summary`) globs `core-metrics-*`; `analyze_experiment`
  papers over the mismatch with a stale-dir pruning pass.
- `cli/compare_pair_eee.py` imports two **private** helpers across CLI
  modules (`from_eee._packets_with_manifests`,
  `from_eee._validate_core_metrics_passthrough`).
  `_packets_with_manifests` is a pure planner→manifest transform with no
  CLI content.

Proposed shape: one `render_packets(artifact, out_root, *, executor,
layout)` in `workflows/eee_render.py` (which already unified the *command
assembly* for the two EEE CLIs — plan item D2); all three callers use it.
Move `_packets_with_manifests` out of `cli/` (into `eee_render` or
`planning/`). Decide one packet-dir naming convention and migrate the
other family behind it.

## 2. Audit the 121-name re-export surface of `build_reports_summary`

`workflows/build_reports_summary.py` defines one function (`main`) and
re-exports ~121 names from `eval_audit.reports.summary.*`. Some are
load-bearing (tests monkeypatch by this module's name), but a mechanical
check of which of the 121 a test or script actually names would likely
retire a large fraction and make the real Stage-4 surface legible.

## 3. Two readers of the HELM `benchmark_output` on-disk format

`eval_audit/compat/helm_outputs.py` (`HelmOutputs`/`HelmSuite`) is a
partial copy of magnet's `helm_outputs.py`, and the codebase imports
**both**: magnet's in `indexing/historic_filtering.py` and
`integrations/infer_stack/discovery.py`; the local copy in
`workflows/index_results.py` and `indexing/historic_candidates.py`.
Pick one and route all call sites through it. Related: `utils/hashers.py`
is an intentional vendored copy of magnet's `helm_hashers` (its header
now says so and states the hash-compatibility invariant).

## 4. Wire the planner through `resolve_recipe_facts` (or drop the resolver)

`normalized/recipe_facts.py`'s `resolve_recipe_facts` is the designed
single entry point for "what recipe produced this run", but the planner
still calls `extract_run_spec_fields` directly; the resolver is exercised
only by the native-block read path and tests. Either finish the routing
(Phase 3 sub-stage 4.1's original intent) or fold the native-block read
into the planner and drop the standalone resolver.

## 5. Overgrown-file splits (mechanical, low priority)

- `planning/core_report_planner.py` (~1,375 L): the artifact
  serialization/presentation layer (`comparison_rows` …
  `planning_summary_lines`, ~160 L of pure dict→row formatting) is a clean
  lift into a `planning/artifact_views.py`.
- `reports/eee_heatmap_render.py` (~1,315 L): four renderers repeating the
  same figure-setup → `_finish_grid_axes` → `_save_grid_figure` sequence.
- `packaging/pack.py` (~1,045 L): planning / execution / path-rewriting /
  verification are cleanly separable; `repoint` is already emitted as a
  standalone script and wants to be its own module.
- `integrations/infer_stack/bundle_export.py`:
  `materialize_benchmark_bundle` is a single ~384-line function.

## 6. `hf_inprocess.py` routing

Parked by design (see
[`huggingface-in-process-reserved-gpu-plan.md`](huggingface-in-process-reserved-gpu-plan.md)):
the mechanism exists and is tested, but nothing in `eval_audit/` imports
it. Either wire it into `bundle_export`/`kwdagger_bridge` or move it under
an explicitly experimental namespace so the import graph stops implying
it is live.

## 7. `eval-audit-rebuild-core` / `eval-audit-verify-provenance` visibility

`eval-audit-rebuild-core` is a console script whose *script* form nothing
exercises (the module is heavily used in-process by `analyze_experiment`).
Decide whether manual per-packet rebuild is a supported UX; if yes give it
a worked example in `docs/pipeline.md`, if no drop the script and keep the
module. `eval-audit-verify-provenance` is new (2026-08) and needs a
runbook step or test so it doesn't drift into the same category.

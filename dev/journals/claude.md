# Claude developer journal — 2026 H2

Convention: append-only, one entry per session, newest at the bottom. Each entry is
a design narrative (see `CLAUDE.md` § "Developer Journal" for the required fields and
tone).

Predecessor (2026 H1) archived at
[`archive/2026-H1-claude.md`](archive/2026-H1-claude.md).

## 2026-07-06 14:45:00 -0400

**User intent.** "Conduct a review of the eval audit repo with a focus towards
simplicity, reducing bloat, and ease of use and understanding of the code" —
findings as an implementation plan (approved interactively), then implemented on
a new branch (`impl/simplicity-audit`) by guided Opus subagents with the main
session reviewing and committing each phase.

**Model/config.** Main session: claude-fable-5 (Claude Code / VSCode extension);
implementation subagents: claude-opus-4-8 via the Agent tool, one per phase,
each returning a written report reviewed against the actual diff before commit.

**What happened.** The 2026-07-02 correctness audit had just landed (except
R-2), so this review deliberately took the orthogonal lens: bloat and
understandability. Three parallel explorations (package core / user-facing
surface / periphery) produced `docs/planning/simplicity-audit-2026-07-06.md`;
operator picked: include R-2, delete stale content outright, rotate journals,
dedupe only `_lib.sh` in reproduce/. Seven phases landed as 15 commits:
dead-tree deletion (~8.5k lines: dev/poc, dev/oneoff, configs/generated,
examples/, hashers shim, dead `instance_agreement_profile`); first-run fixes
(python3.11→3.12 setup bug, README accuracy, complete reproduce/ scenario
index, paper scripts out of docs/); planning-doc archive (22→11 live docs,
olmo/infer-stack trios consolidated to one status-true doc each) + journal
rotation; R-6 helper consolidation into `utils/coercion.py` (killed a
workflows→reports layering violation; four verified-divergent near-namesakes
deliberately left and documented); reproduce/_lib.sh three-way merge (turned
out largely disjoint — olmo setup wrapped as side-effect-free `olmo_setup()`);
god-function extraction (`_render_scope_summary` ~920→orchestrator+7 helpers,
`core_metrics.main` ~670→orchestrator+5, both gated on byte-identical
eee_only_demo artifacts after establishing the plotly-UUID noise floor via
HEAD-vs-HEAD reruns); PRESET_CONFIGS → `preset_configs.yaml` (all 288 comments
carried, every scalar quoted, deep+order equality proven, wheel packaging
verified); and finally R-2 — pair_report/pair_samples migrated onto
NormalizedDiff, legacy half of `helm/diff.py` deleted (1,844→844 incl. the
Phase-1 dead-method cut), diagnosis byte-identical, IM-13 resolved and
documented in `docs/eee-vs-helm-metadata.md`.

**Design insights.** (1) The "characterize the noise floor first" trick — run
HEAD twice, diff, and treat that file set as the only acceptable before/after
diff — made pure-relocation refactors of plot-emitting code provable despite
plotly's random div UUIDs. (2) When merging "duplicated" shell libs, check
disjointness before assuming drift: the real hazard was source-time side
effects, solved by wrapping setup in a function so the shared lib stays
side-effect-free for the consumer that never had side effects. (3) For
dict-literal→YAML moves, quote every string scalar and assert deep+iteration-
order equality; the comment-only-lines assertion caught the single transcription
error immediately. (4) `_value_agreement_summary` survived R-2 as a private
diagnosis input — deleting it would have changed diagnosis labels; "retire the
surface, keep the input" was the behavior-preserving cut line.

**Open/next.** GPU-side verification of the olmo/infer-stack open items is
unchanged by this work. Deferred (recorded in the audit doc): fat cli/ module
relocation, deprecated `cli/reports.py` dispatcher, phase3 test-cluster
consolidation, `_MsgspecRunView`. The branch is unmerged; merge with
`--no-ff` per repo convention. Pre-R-2 report stores keep the old pair_report
semantics — regenerate before citing (same caveat class as the olmo-models
store note).

## 2026-07-08 15:53:29 -0400

**Model/config:** claude-opus-4-8[1m] (Claude Code, VSCode extension harness).

**User intent:** Add a new plot to the experiment reporting — a variant of
the reproducibility heatmap that, instead of instance-level agreement,
shows the difference between the *reproduced* (local) and *official*
(public) **aggregate** scores, per core metric. Color = the score
difference; each cell annotated with both the public and the local score.

**What I built.** A per-core-metric aggregate-score-difference heatmap,
plumbed through the existing `eee_only_heatmap` surface behind a new
`--aggregate-diff` flag:

- `eee_heatmap_data._collect_aggregate_diff_cells_per_metric` — resolves
  `(model, benchmark)` from each `core_metric_report.json` the same way the
  agreement collectors do, then reads the **sibling
  `core_runlevel_table.csv`** (`left_mean`=official, `right_mean`=local)
  that `core_metrics` already writes next to every report. Filters to
  `official_vs_local` core rows, drops bookkeeping metrics by default, and
  micro-averages public/local across contributing pairs. Returns
  `{official, local, diff=local−official, abs_diff, n, status}`.
- `eee_heatmap_render._render_diff_heatmap` — diverging Wong blue↔white↔
  orange colormap on a **symmetric `Normalize(-vmax, +vmax)`** so 0 always
  reads as the white "no drift" midpoint; cells annotated `P <public>` /
  `L <local>`. Plus `_render_aggregate_diff_heatmaps` (one figure per
  metric) and `_render_aggregate_diff_text_table`.

**Key design decision — data source.** The signed per-metric aggregate
means are *not* serialized into `core_metric_report.json`
(`run_level_summary()` only emits abs_delta quantiles). Two options:
(a) add signed means to the JSON and re-render every packet, or (b) read
the run-level CSV that's already written and aliased next to the report.
Chose (b): it works on **existing** outputs with no re-render, it's the
canonical run-level table (`_write_comparison_runlevel_table`), and it's
reliably co-located (`_write_latest_alias` → `report_dpath`). The only
cost is a second artifact read; the collector skips reports whose CSV is
absent (only happens on `--plots-only` re-renders).

**Reused vs. new.** Deliberately wrote a separate `_render_diff_heatmap`
rather than parametrizing the well-tested `_render_heatmap` — the color
semantics (diverging/signed vs. sequential/ratio) and annotation (two
scores vs. one percentage) differ enough that overloading it would muddy
both. Figure sizing / despine / transpose logic mirror the original so the
two heatmaps stay visually coherent.

**Verified.** Compiles; `tests/test_eee_only_demo.py` (12 tests, incl. a
new `test_aggregate_diff_collector_reads_runlevel_scores`) green under
`--run-slow`. Rendered both default and `--transpose` layouts against the
demo fixture — imdb/m1 (public 1.0 → local 0.0) shows deep blue with
`P 1 / L 0`; arc_easy reproduces exactly (white, diff 0). Committed as
545357e.

**Next steps / open questions.** (1) The aggregate-diff plot is currently
only exposed on the standalone `eee_only_heatmap` CLI, not auto-emitted by
`build_reports_summary`'s scope plots — if the paper wants it in the
aggregate roll-up, wire `--aggregate-diff` into the summary render path.
(2) Cross-metric comparability: each per-metric plot auto-scales `vmax` to
its own max |diff|; set `EVAL_AUDIT_DIFF_HEATMAP_VMAX` to pin a shared
scale across metrics when comparing them side by side.

## 2026-07-08 16:05:00 -0400

**Model/config:** claude-opus-4-8[1m] (Claude Code, VSCode extension harness).

**User intent:** Auto-emit the new aggregate-score-difference heatmap
during `build_reports_summary` (previously only on the standalone
`eee_only_heatmap` CLI).

**What I did.**
- Refactored the collector so the accumulation core takes an explicit
  list of report paths (`_accumulate_aggregate_diff_cells`); the
  rglob-based `_collect_aggregate_diff_cells_per_metric` now delegates to
  it. Also extracted `_order_aggregate_diff_axes` (model/benchmark/metric
  display ordering) and pointed the CLI at it (killed the inlined dup).
- Added `_render_aggregate_score_diff` to `build_reports_summary` and
  called it inside `_render_scope_plots` (include_visuals branch). It
  drives the collector off the **scope's own `repro_rows`** (each carries
  `report_json`/`report_dir`) rather than an rglob over a shared root —
  so breakdown scopes stay scope-restricted. Output lands under
  `level_001/aggregate_score_diff/`. README gained a pointer line.

**Key design decisions.**
- *Scope-correctness*: driving from `repro_rows` (already scope-filtered)
  is what makes a future `by_benchmark/boolq` slice show only boolq. An
  rglob over the analysis root would have leaked sibling scopes.
- *Gating*: reused the existing `include_visuals` switch. Breakdown
  renders pass `include_visuals=False` (they skip ALL heavy visuals by
  design), so the heatmap correctly only appears on top-level scopes
  (`all_results` / `--experiment-name`), matching the plotly plots. This
  is intentional, not a gap.
- *Soft failure*: matplotlib-absent or no-runlevel-CSV is a no-op that
  returns an empty result dict; the rest of the summary still renders.
  The demo run showed pre-existing Kaleido/Chrome errors on the *plotly*
  static exports — unrelated; my matplotlib PNGs rendered fine.

**Verified.** `tests/test_eee_only_demo.py` (13, incl. new
`test_aggregate_summary_emits_score_diff_heatmap`) + smoke +
virtual_experiment_eee green under `--run-slow`; rendered PNG shows the
"all_results" scope title with correct color/annotation. Committed
c8707ea.

**Flagged, NOT committed.** Working tree also carries two changes I did
not author and left unstaged: a `docs/planning/huggingface-in-process-
reserved-gpu-plan.md` edit and a `submodules/infer_stack` gitlink that
went `-dirty` (uncommitted changes inside the submodule). Per repo
convention I did not fold the submodule bump into any commit — flag to
the user.

## 2026-07-08 16:01:31 -0400

**Model/config:** claude-opus-4-8[1m] (Claude Code, VSCode extension harness).
(This is the session that authored the `huggingface-in-process-reserved-gpu`
plan-doc edit + submodule changes the preceding entry flagged as not-its-own.)

**User intent:** Public runs deployed via HELM's `HuggingFaceClient` should be
*reproduced* the same way (in-process `transformers.generate()`), not auto-swapped
to vLLM. infer-stack can reserve a GPU without spinning up a server — use that to
host the HF deployment. Scope it, then implement.

**What I built (mechanism, two layers, three commits).**
- *infer_stack submodule* (`e5fba7b`): `acquire --reserve-gpus N` — a reserve-only
  lease. A non-servable ledger `Deployment` (`engine='reserved'`, `DEDICATED` so
  two reservations never coalesce onto one card, `reclaim!='keep-warm'` so release
  frees it). Rides the EXISTING machinery: first-fit placement
  (`required_gpu_count` now honors `reserved_gpu_count`), `render_compose` already
  `continue`s past non-vllm/ollama engines (no container for free, not flagged
  `unplaced`), `probe_ready` short-circuits to ready, and the env-file already
  supported `CUDA_VISIBLE_DEVICES` (acquire now populates it from the placement
  assignment). Because it's a real ledger deployment under the render lock, its GPU
  is withheld from concurrent vLLM placements cross-process — the shared-machine
  property — and it honors `allowed_gpus`/`$SLURM_JOB_GPUS` unchanged. Plus
  `1109b0f`: an opt-in on-host e2e probe (`test_reservation_gpu_frame_e2e.py`) that
  infer-stack's inventory index and `docker --gpus device=` name the same physical
  GPU — the one frame assumption the design can't verify offline.
- *eval_audit* (`2d79201`): `hf_inprocess.py` (resolve the official client class
  from HELM's `model_deployments.yaml`; build the reproduction entry by mirroring
  HELM's own official entry with one knob pinned — `torch_dtype: torch.float32`,
  the officials' effective precision that HF fp32 reproduces exactly per the
  hf-probe result); `lease_reserve_gpus` in the lease bracket; the docker node
  sources `lease.env` then pins `--gpus "device=${CUDA_VISIBLE_DEVICES:?...}"` (fail
  CLOSED so a missing lease never grabs all GPUs); bridge decouples the old
  `container_gpus="none"` assumption for reserve leases.

**Design insights.** (1) The cheapest correct reproduction isn't "teach infer-stack
to serve HF" — it has no HF engine and needs none; HELM already runs
`HuggingFaceClient` in-process, so the only new primitive is "hold a GPU, serve
nothing." (2) The reserve feature is ~130 lines because the seams were pre-placed
(the `reserved` param, `claims.kind`, the env-file's `cuda_visible_devices`,
render_compose's engine skip) — assembly of existing hooks + a non-servable
deployment kind, not new subsystems. (3) `torch_dtype` MUST be `torch.float32` (HELM
converts `torch.<x>` via `getattr(torch,…)`; a bare `float32` is silently ignored).

**Critically NOT done — the routing switch.** Nothing in the default replay path
calls the resolver: `materialize_benchmark_bundle` still builds only vLLM bundles
and no producer sets `lease_reserve_gpus`. So **replaying a public run still uses
vLLM by default** — I built the mechanism + the decision helper, not the switch that
flips a given run. Served path deliberately untouched (270+42 infer_stack + 57
eval_audit served-path tests unchanged).

**Next steps.** (1) HF-in-process manifest producer (plan §2.1/§2.2): when
`official_is_huggingface_inprocess(model_deployment)`, emit a manifest that sets
`lease_reserve_gpus=N`, omits `lease_endpoint`, and ships the fp32
`model_deployments.yaml` from `hf_inprocess_deployment_entry` — likely a `serving:
huggingface-inprocess` preset branch (bundle_export is vLLM/`ServingFacts`-coupled,
the invasive part). (2) GPU-host acceptance: OLMoE-instruct through the new path must
reproduce the official exactly; run the frame probe there first. (3) Push the
infer_stack branch and bump the eval_audit submodule gitlink (left unstaged per the
no-auto-gitlink rule; submodule commits are unpushed). (4) Once routing is wired,
document in user-facing `docs/pipeline.md` + `docs/vllm-vs-huggingface-deployment-match.md`
(held until then to avoid overclaiming a path that doesn't change default behavior).

## 2026-07-08 16:35:00 -0400

**Model/config:** claude-opus-4-8[1m] (Claude Code, VSCode extension harness).

**User intent:** For any existing plot that displays low / moderate /
exact / near-exact agreement categories, state the numeric threshold on
the legend.

**Where the categories are defined.** `classification._bucket_agreement`
(single source of truth): ratio ≥0.999999 → exact_or_near_exact, ≥0.95 →
high, ≥0.80 → moderate, >0 → low, ==0 → zero. The "ratio" is the *share
of paired instances* whose |official − local| is within
`CANONICAL_AGREEMENT_TOL` (0.05) — a fraction of instances, NOT a score.
That subtlety is why the legend labels say "…% of instances".

**What I changed (the two *visual* legends that lacked thresholds).**
- `classification`: added `AGREEMENT_BUCKET_DISPLAY` + `agreement_bucket_label()`,
  the human labels with thresholds inline, co-located with the classifier
  so they can't drift.
- `plots._write_coverage_matrix_plot`: colorbar `ticktext` + hover
  `STATUS_LABEL` now carry the cutoffs; colorbar title notes the % is
  "share of instances within abs_tol".
- `build_reports_summary` reproducibility-buckets bar: added an
  `agreement_bucket` display column and pointed the bar's x/color at it
  (raw `official_instance_agree_bucket` key preserved for logic + README +
  tests). `_AXIS_COUNT_TAGS` already had an `agreement_bucket → n_buckets`
  entry, so the count tag stayed correct.

**Deliberately left alone.** The sankey "Reproduction/agreement" stage and
the management-summary prose already enumerate the thresholds in their
stage-description legend text — no change needed. The EEE agreement
heatmaps render raw numeric `agree_ratio` per cell (no categorical
bucket legend), so they're out of scope.

**Design note.** Kept the raw bucket key on every data row and only added
a parallel *display* field, so nothing that keys off the snake_case
bucket (reproducibility_rows.csv consumers, triage classes, tests
asserting `exact_or_near_exact` counts) had to change. The label is a
presentation concern layered on top, not a replacement.

**Verified.** compiles; end-to-end + eee-demo + virtual-experiment + smoke
(43) green under `--run-slow`; new `test_agreement_bucket_labels.py` (3)
locks labels↔classifier. Rebuilt the demo aggregate summary and confirmed
both legends now show cutoffs (coverage colorbar: "low agreement (<80%)"
… "exact/near-exact (≥99.9999%)"; bar legend: "exact / near-exact
(≥99.9999% of instances)" etc.). Committed 0c8d0eb.

**Still flagged (not mine).** `submodules/infer_stack` gitlink remains
`-dirty` — left unstaged per repo convention.

## 2026-07-08 16:55:00 -0400

**Model/config:** claude-opus-4-8[1m] (Claude Code, VSCode extension harness).

**User intent:** A top-level version of the aggregate-score-diff heatmap
where each benchmark uses its headline metric, so all model × benchmark
pairs are visible holistically in one plot (instead of one plot per
metric).

**Key finding (via Explore).** eval_audit has NO headline/primary-metric
concept — `core_metrics` is just an alphabetical set. But HELM does:
`run_groups[].environment.main_name` in
`submodules/helm/.../static/schema_classic.yaml` (equivalently
`ScenarioMetadata.main_metric`). I queried the schema and transcribed the
values for our families (mmlu→exact_match, imdb→quasi_exact_match,
narrativeqa/quac→f1_score, gsm→exact_match_indicator, wikifact→
quasi_exact_match, the_pile/twitter_aae→bits_per_byte, …).

**Design — headline selection (`headline_metric_for_benchmark`).** Curated
HELM main_name **iff that metric is actually present** in the data; else
first of a headline-likeness priority list (exact_match, quasi_exact_match,
… present); else alphabetical. The "present" guard matters: EEE-only
inputs and metric-name drift mean the schema's exact main_name isn't
always emitted (gsm's `exact_match_indicator` vs a run that only has
`exact_match`), and the fallback keeps the cell populated. The plot names
the *actually-used* metric on each benchmark's axis tick, so the choice is
always transparent — no silent misattribution.

**Design — coherence.** Headline metric is chosen per benchmark from the
*union* of metrics across all models, so every model's cell in that
benchmark's row uses the same metric (the row stays comparable). Reused
`_render_diff_heatmap` (added a `benchmark_metric` axis-annotation arg)
rather than a new renderer — the only difference from the per-metric plots
is that cells in different benchmarks are different metrics, surfaced via
the tick labels.

**Wiring.** `_render_headline_diff` (in the render module, since it already
does file IO) orchestrates collapse→text→json→png and is called from BOTH
the `--aggregate-diff` CLI and `build_reports_summary._render_aggregate_score_diff`,
so the standalone and auto-emitted paths stay identical. Output:
`aggregate_score_diff_headline.{png,pdf,txt,json}` next to the per-metric
subdir.

**Verified.** compiles; demo (standalone + aggregate summary) picks imdb→
quasi_exact_match, truthful_qa→exact_match, arc_easy→exact_match (fallback),
9 holistic cells in one figure with per-benchmark metric labels; 48 tests
green under `--run-slow` incl. new headline-selection unit test +
summary-emission test. Committed 70c867d.

**Caveat for future work.** The curated map covers the classic families;
benchmarks outside it (or with unusual main metrics like msmarco RR@10 /
NDCG@10, code pass@1) rely on the priority fallback, which is exact-match-
biased. If we start auditing those benchmarks, extend
`HEADLINE_METRIC_BY_BENCHMARK` rather than trusting the fallback.

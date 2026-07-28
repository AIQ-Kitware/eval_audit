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

## 2026-07-08 17:10:00 -0400

**Model/config:** claude-opus-4-8[1m] (Claude Code, VSCode extension harness).

**User intent:** Two fixes to the holistic headline aggregate-diff plot:
(1) models on the left / benchmarks on the bottom (mirror the existing
report plots, e.g. coverage matrix); (2) color by squared error
(local − public)² instead of the signed diff — non-negative (a signed
"deviation" is meaningless for this overview) and emphasizes big drifts.

**Implementation.** Generalized `_render_diff_heatmap` with two orthogonal
knobs rather than overloading `transpose`:
- `value_mode` ∈ {"signed" (default, diverging Blue-White-Orange, current),
  "squared" (sequential White→Wong-orange→deep-red, vmin=0)}.
- `force_title` — keep the in-figure title/subtitle in the transposed
  layout, which otherwise suppresses them for paper-slim figures.
`_render_headline_diff` now fixes transpose=True (models-left) +
value_mode="squared" + force_title=True. The per-metric drill-downs stay
signed/diverging (direction is informative there); only the holistic view
switched. Headline JSON gained a `squared_error` field.

**Why decouple instead of reusing transpose.** `transpose` already
conflated orientation with title-suppression (inherited from the agreement
heatmap's paper mode). The headline needs the *transposed orientation* but
*with* a title, so orientation and title-suppression had to become
independent — `force_title` does that without disturbing the per-metric
transpose behavior.

**Scope decision.** Applied to the headline plot ONLY (the "new plot" the
user referenced). Per-metric plots keep signed diff + their existing
orientation — squared error there would lose the direction signal that
makes a per-metric drill-down useful. If the user later wants the
per-metric plots aligned too, flip them to value_mode="squared" and
transpose in `_render_aggregate_diff_heatmaps`.

**Verified.** Rendered demo: models rows / benchmarks cols; colorbar
"Squared error (Local − Public)²" 0–1; imdb/m1 (sq 1.0) deep red, exact
cells near-white; title retained. 36 tests green under `--run-slow`
(headline test now also asserts squared_error). Committed 29fc76c.

## 2026-07-10 09:03:40 -0400

**Model/harness.** Claude Opus 4.8 (1M context), `claude-opus-4-8[1m]`, Claude Code.

**Intent.** Settle the OLMoE/OLMo-2 fp32 reproduction question end-to-end: build
the tooling to test it (HF-side probe + fp32-in-vLLM), run overnight sweeps for
all OLMo instruct models, and interpret the HF-vs-vLLM results.

### Central finding: fp32 reproduces every OLMo instruct official

The public HELM OLMo instruct runs (OLMoE-1B-7B, OLMo-2 7B/13B/32B) all use
`huggingface/<model>` HuggingFaceClient deployments that pin NO `torch_dtype`, so
they ran in float32 (pre-v5 transformers default). Reproducing at fp32 matches;
bf16/fp16 don't. Confirmed by the overnight sweeps (n=12 ifeval, scored vs the
official completions), `/data/crfm-helm-audit-store/deployment-match/<model>--ifeval-{hf,vllm}`:

| Model | HF best | vLLM best |
|---|---|---|
| OLMoE-1B-7B | **fp32 MATCH 0.971** (quasi 1.0) | fp16 PARTIAL 0.494 |
| OLMo-2-7B  | fp32 PARTIAL 0.175 (quasi 0.0) | **fp32 MATCH 0.915** |
| OLMo-2-13B | fp32 PARTIAL 0.324 (quasi 0.0) | **fp32 MATCH 0.904** |
| OLMo-2-32B | fp16 PARTIAL 0.234 (quasi 0.0) | **fp32-tp2 MATCH 0.961** |

**fp32 wins on whichever engine can serve it faithfully.** OLMoE is MoE → vLLM's
Triton fused-kernel can't do fp32 (shared-mem OOM; TP shards experts not the
per-block tile, so it doesn't help) → only HF fp32 reproduces it. The dense
OLMo-2s → vLLM fp32 reproduces them near-exactly.

### Open puzzle: the HF probe is broken for OLMo-2 (NOT a science problem)

`hf-probe` (transformers.generate at fp32) matches OLMoE EXACTLY but gives
quasi=0 / sim~0.15 for all three OLMo-2 models. The output is coherent, on-task,
semantically identical to the official — just WORDED DIFFERENTLY, and stable
across dtypes (fp32/bf16/fp16 agp0 agree with each other, all diverge from the
official). So it is a SYSTEMATIC divergence (prompt or generation-config), not
precision noise. vLLM fp32 — which also applies the chat template — reproduces the
official's exact wording. Since the official IS a HuggingFaceClient run, the HF
probe *should* match it better than vLLM, not worse ⇒ a probe-fidelity bug
specific to OLMo-2. Undiagnosed hypotheses: (a) the probe's manual
`apply_chat_template` reconstruction differs from HELM's `get_prompt` (OLMo-2
template quirk; note the 13B resolves to the 7B tokenizer); (b) OLMo-2's
`generation_config.json` defaults that HELM's `generate()` picks up but the probe
overrides. NEXT STEP (read-only): diff the probe's prompt+sampling params vs
HELM's `serve_request` for one OLMo-2 instance (`compare_prompt.py` + run_spec +
generation_config.json).

Interpretation stays clean: all four officials reproduce at fp32 (OLMoE via HF,
dense OLMo-2 via vLLM). "vLLM beats HF" is real for OLMo-2 but it is the PROBE
mis-generating, not vLLM being more faithful.

### Tooling built (deployment_match)

- `--fp32-tensor-parallel-size N` / `DM_FP32_TP` — serve fp32 across N GPUs,
  lifting the fp32-MoE prune. Confirmed a "let it try": TP does NOT fix OLMoE's
  MoE shared-mem OOM (per-block tiles, not shard count); right knob for dense
  fp32 VRAM. (2698389)
- `hf-probe` — reproduce at fp32 via transformers.generate, score vs the same
  oracle, same result-doc shape as the vLLM probe; `DM_HF_FP32=1` replaces the
  vLLM sweep. (37d91af)
- `hf-probe` dtype sweep — `--dtype` comma list, load once per dtype, rank
  together; `DM_HF_DTYPES` passthrough (default fp32). (3ff73c5)
- `run_all_hf_and_vllm_overnight.sh` — both engines × all 4 models, separate
  dirs, per-run logs, summary table, failure-tolerant. (c00394e)
- OLMo-2 per-model runbooks caught up with DM_HF_FP32/DM_FP32_TP. (79ab77b)

### Docs

- `docs/helm-unrecorded-deployment-params.md` (new) — the reproduction provenance
  gap (dtype/revision/quantization/transformers-version/attn/TF32/template/
  generation_config), ranked by greedy-output effect; 7 deployment-match
  improvements; HF-revision pinning effort (infer-stack + HELM already accept it;
  only eval_audit's boundary layer drops it, ~3-4 files). (426710f)
- `docs/vllm-vs-huggingface-deployment-match.md` — OLMoE fp32 finding + CONFIRMED
  exact match + OLMo-2 scope table.

### Reusable design insights

1. **Revision/dtype pinning is invisible to comparability** iff it lives in
   `client_spec.args` / catalog.yaml / a provenance block — NOT in
   `run_spec.name` / `adapter_spec.model` / `model_deployment` name (the pairing
   key + comparability facts read those). Bake a SHA into an identity string and
   public runs stop pairing.
2. **vLLM-vs-HF winner-composite comparisons are only fair if both sweep the same
   axes.** They share oracle+scorer, but the vLLM sweep is ~64 cells (dtype × attn
   × ast × agp) vs the HF probe's handful. Compare the matched fp32 cell, not the
   two maxima.
3. **Matched precision ≠ matched engine.** fp32-vs-fp32 can still diverge (vLLM↔HF
   kernels) — which is why hf-probe (same engine as the official) exists — but the
   probe must faithfully replicate HELM's `generate()` or it diverges anyway (the
   OLMo-2 bug).

### Note

Reverted an unwanted change: I'd flipped the HF dtype-sweep default from fp32→sweep
on the wrong assumption the overnight only swept fp32; the user corrected (their
overnight DID sweep via DM_HF_DTYPES) and I restored HEAD (3ff73c5). Tree clean.

## 2026-07-10 09:39:13 -0400

**Model/harness.** Claude Opus 4.8 (1M context), `claude-opus-4-8[1m]`, Claude Code.

**Intent.** Diagnose the previous session's open puzzle — why OLMo-2 instruct
diverges when reproduced via the HF probe even though the public run was itself a
`HuggingFaceClient` (transformers) run — then modify the HF probe so a *sweep* can
reproduce the OLMo-2 officials.

### Diagnosis: it's the fp32 FORWARD PASS, not the prompt (hypothesis (a) refuted)

Ran the forensic comparison the last entry proposed. Findings, in order of force:

1. **The prompt is identical everywhere.** Tokenizing the exact ifeval sample
   prompt with the OLMo-2 tokenizer: `add_special_tokens` True==False (no double-BOS
   — `GPT2TokenizerFast`, `add_bos_token` unset, doesn't auto-prepend), and the
   probe's `agp1` render == `apply_chat_template(tokenize=True)` == 48 tokens
   `[100257, 27, 91, 882, 91, 397, …]`. `<|user|>`/`<|assistant|>` are NOT special
   tokens here — they BPE-split — but consistently across every path. So the probe's
   faithful cell feeds byte-identical tokens to what vLLM feeds.

2. **vLLM fp32 reproduces the official character-for-character; the HF probe does
   not.** For instance id1236: official and vLLM-fp32-agp0 both begin "These new
   armchairs from LuxErgo…" (vLLM ranking: MATCH 0.915, first-token **1.00**, sim
   0.95). HF-probe-fp32 begins "Discover the pinnacle…" (agp0) / "Introducing the
   latest… UltraComfort…" (agp1) — coherent, on-task, totally different wording,
   first-token agreement **0.42**.

3. **Therefore the divergence is a fp32 forward-pass difference, and it starts at
   token 0.** Identical prompt ⇒ no accumulated history at step 0 ⇒ a different first
   token can only mean the probe's fp32 forward pass computed a different logit
   vector than the run that produced the official. Greedy is deterministic but
   chaotic: one sub-ULP logit flip at an early near-tie cascades into different but
   coherent text. vLLM happens to land on the official's argmax; today's
   transformers on the probe host does not — *"official was HF" is true, but the
   probe's HF ≠ the original run's HF numerically.*

The discriminating knobs (none recorded in `run_spec.json`, so unrecoverable and
must be searched): **attn_implementation**, **device placement** (accelerate
`device_map=auto` shards a fits-on-one-GPU model across GPUs, changing fp32
reduction order), and the **decode path** (HELM's `do_sample=True, temperature=1e-7`
vs the probe's `do_sample=False` greedy). Note the vLLM sweep matched at fp32 across
ALL three attention backends (first-token 1.00 each), which hints the dominant HF
lever is device-map sharding, not attention — but both are worth sweeping.

Why OLMoE matched via HF but OLMo-2 didn't is NOT science: OLMoE's greedy trajectory
is numerically robust (stable argmax across engines); OLMo-2 has early near-ties
sensitive to the exact fp32 stack. Exactly insight #3 from the prior entry
("matched precision ≠ matched kernels/engine"), now pinned to the **first token**.

### Change: turn the forward-pass numerics into HF-probe sweep axes

`dev/tools/deployment_match/hf_probe.py` + `cli.py` (commit 4516b70). The probe swept
only `dtype × {agp, ast}` — none of which touch the forward pass — so every cell was
doomed. Added, mirroring the vLLM grid's `dtype × attention_backend` sweep:

- `--attn-impls` (default `eager,sdpa`; fp32-safe — flash-attn is fp16/bf16-only and
  is caught+skipped on fp32). Passed to `from_pretrained(attn_implementation=…)`.
- `--device-maps` (default `auto`; `single` == `{"": 0}` pins the whole model on GPU
  0 to remove accelerate's cross-GPU reduction-order shift). New `_resolve_device_map`.
- `--decode` (default `helm` = `do_sample=True, temperature=1e-7, top_p` from the
  recipe, exactly replicating `HuggingFaceServer.serve_request`; `greedy` retained as
  a diagnostic — the old `do_sample=False` argmax).

Each `(dtype × attn × device_map)` is a full model reload (they change the forward
pass); `decode × request-variant` are cheap inner loops. Endpoint names encode the
reload axes (`hf-<model>-fp32-attnsdpa-devsingle`), cell ids append the cheap axes
(`…::ast1-chat-agp1-helm`) — 24 distinct cells for a 1×3×2×2×2 sweep, verified.
Infeasible recipes (flash-attn+fp32, single-GPU OOM) are caught and skipped so the
sweep continues instead of aborting. Runbooks wire `DM_HF_ATTN/DM_HF_DEVMAPS/
DM_HF_DECODE` (7B/13B default `auto,single`; 32B fp32 ~128 GB can't fit one card so
its device-map stays `auto` — the `single` lever is infeasible there, and vLLM
fp32-TP remains the confirmed 32B path).

### Design tradeoffs / uncertainties

- **`single` is the bet, but unproven.** I couldn't run a GPU repro (hit the known
  `aivm` FD-exhaustion issue mid-session; it recovered but I didn't burn a GPU on
  spec). The strongest hypothesis is that `device_map=single` on 7B/13B collapses the
  first-token divergence; the next GPU run of `DM_HF_FP32=1` with the new defaults
  will confirm or refute. If `single` still diverges, the residue is transformers/
  torch/CUDA version skew vs the original public-HELM run → pin those versions next.
- **Why axes over hardcoding.** The user asked for a *sweep* that reproduces, and the
  exact serving numerics aren't in `run_spec.json`. Sweeping is the honest analogue
  of what already made vLLM reproducible; hardcoding `single`+`helm` would bake in an
  unverified guess and hide the 32B case where `single` is impossible.
- **`helm` decode is faithful but probably not the fix.** `temperature=1e-7` sampling
  is argmax-equivalent, so it likely won't move OLMo-2; I made it the default anyway
  because it removes a real probe-vs-HELM infidelity (the probe should replicate
  `serve_request`, not approximate it), and kept `greedy` swept so the decode path can
  be ruled in/out empirically.

### Reusable design insights

1. **First-token disagreement on a byte-identical prompt is a clean litmus for a
   forward-pass (kernel/precision/device) difference** — it excludes prompt,
   template, sampling-trajectory, and history-accumulation causes in one shot,
   because at step 0 there is no history. Reach for it before blaming the prompt.
2. **"Same engine family" (transformers→transformers) does NOT imply reproducible.**
   device_map sharding, attn_implementation default drift across versions, and the
   sample-vs-greedy code path each perturb fp32 logits enough to diverge greedy
   decoding. A reproduction probe must sweep them, not assume the engine name matches.
3. **A sweep only reproduces if its axes touch the quantity that varies.** The old HF
   probe swept prompt-render knobs against a *forward-pass* problem — orthogonal, so
   0/N cells could ever match. Diagnose which layer the divergence lives in first,
   then put the axes there.

### Next steps

- GPU run `DM_HF_FP32=1 ./run_deployment_match_olmo2_7b.sh` (defaults now sweep
  eager/sdpa × auto/single × helm). Expect a `-devsingle` cell to hit first-token
  ~1.0 / MATCH. Then 13B. Confirm, and record which recipe wins in
  `docs/vllm-vs-huggingface-deployment-match.md`.
- If `single` doesn't close it: pin transformers/torch to the public-HELM release
  versions and re-sweep (version skew is the remaining suspect).
- 32B: `single` is infeasible; if HF can't reproduce it under sharding, document that
  vLLM fp32-TP is the only confirmed 32B path (already the journal's standing result).

### Follow-up (same session): overnight now sweeps every parameter for both engines

Extended the sweep wiring from the 3 OLMo-2 runbooks to the OLMoE base runbook and
the overnight batch (`run_all_hf_and_vllm_overnight.sh`, commit d6cc37f). The
overnight previously ran the HF probe at fp32 with default attn/device_map only —
the new forward-pass axes were dormant there. Now:

- **HF (per model):** dtype{fp32,bf16,fp16} × attn{eager,sdpa} × device_map{auto,
  single*} × decode{helm,greedy} × agp{T,F} × ast{T,F}. `*single` is per-model: the
  32B runbook keeps `auto` (128 GB fp32 can't fit one card); OLMoE/7B/13B get
  `auto,single`. Wired `DM_HF_ATTN/DEVMAPS/DECODE/AST/AGP` through all four runbooks.
- **vLLM:** already maximal — hf-match sweeps backend × agp, the grid default sweeps
  all dtypes × ast; kept full, scheduler determinism knobs stay pinned by design.
- New `SWEEP=full|quick` selector; every axis still individually overridable;
  device_map left per-model unless pinned globally.

Verified the whole chain with a `PYTHON_BIN=echo` stub (no GPU): each model builds
the exact `hf-probe`/`auto` command, 32B correctly drops `single`, quick narrows.
Design note: I did NOT add a determinism-knob axis to vLLM (enforce-eager / chunked-
prefill / prefix-cache) — those are confounder-removal for matching an HF official,
not parameters of the model recipe, so sweeping them would muddy the comparison
rather than widen it. "All parameters" means the recipe surface, not vLLM's scheduler.

## 2026-07-10 10:53:25 -0400

**Model/harness.** Claude Opus 4.8 (1M context), `claude-opus-4-8[1m]`, Claude Code.

**Intent.** Implement the era-pinned HELM reproduction plan
(`docs/planning/era-pinned-helm-containers-plan.md`) end to end: enable verbatim
from-spec replay of the ~59% of the corpus that is pre-v0.5 (classic-track
`v0.2.4`/`v0.3.0`) inside CPU-only era images whose HELM harness is the era's
release commit, with model inference kept out-of-process on modern vLLM.

**What I built (branch `impl/era-pinned-helm-containers`, six dependency-ordered
commits).**

1. **Era registry.** `docker/eras.yaml` (helm-v0.2.4=`626d8609`,
   helm-v0.3.0=`8ea285f7`, python 3.10, seed constraints `pandas==2.0.3`/`numpy==1.23.5`)
   + `eval_audit/eras.py` (frozen `EraSpec`/`EraMatch`, `resolve_era` keyed on the
   path-derived `(public_track, suite_version)` signal, `resolve_era_for_sources`
   raising on mixed eras). 11 unit tests.
2. **Era image build.** `docker/build.sh` `ERA=` mode (reads the registry via a
   tiny `read_eras.py`, `git archive`s HELM at the era commit, stages the shim +
   constraints, skips magnet/eval_audit, rejects `BUILD_FROM=worktree`), a
   CPU-only `ubuntu:22.04` era dockerfile with final-stage assertions
   (`register_model_deployments_from_path`, `helm.benchmark.runner.RunSpec`, shim
   imports, py3.10, pin spot-check) stamping `org.aiq.era`, plus the freeze
   workflow in `docker/README.md`.
3. **The shim** `docker/era_shim/helm_era_shim/`: `replay.py` (flag-compatible
   with magnet's from-spec CLI via stdlib argparse; strict-dacite-decode into the
   era RunSpec = drift detector; preflight; prepares `prod_env` + a
   `credentials.conf` keyed on the official model name for v0.2.4's eager
   AutoClient; drives era `run_benchmarking`; Stage-4-compatible output contract)
   and `openai_compat_client.py` (requests port to `/v1/completions` building era
   `Sequence`/`Token`, constructor tolerant of both eras' injection styles).
4. **Host-side.** `run_spec_materializer.py` guard refuses to insert a
   `model_deployment` field into a spec that lacks it (the era signal);
   `bundle_export._model_deployment_entry_era` emits the era schema (official
   model name, shim client, no api_key, cattrs-no-defaults nulls); `freeze.py`
   `omit_model_deployment` for era sources; `--era` export flag. 5 tests.
5. **Threading.** `ManifestSpec.era`; make-manifest `--era {auto,<key>,modern}`
   (auto resolves from sources, mixed-era = SystemExit, exact-path-only
   validation); `MaterializeHelmRunFromSpecEraDockerNode` (executable =
   `helm_era_shim.replay`) + factory + `-e EVAL_AUDIT_ERA_API_KEY`; the bridge
   selects the era pipeline on the exact-path branch, rejects era on
   run-entry/discovery, and guards the image's `org.aiq.era` label against the
   manifest era at schedule time (new `docker_provenance.image_label`). 7 tests.
6. **Docs + runbook.** `reproduce/classic_era_replay/` (README + 4 scripts mapped
   to the validation ladder), a `container-execution.md` era section, a
   `helm-gotchas.md` G10 cross-ref, and a status banner on the plan.

**Design tradeoffs / what I'm confident vs uncertain about.**

- *Confident (host-verifiable):* the registry resolution, mixed-era rejection,
  materializer guard, bundle era schema, builder era resolution, bridge pipeline
  selection + era↔image guard — all unit-tested with the repo `.venv`. build.sh
  passes `bash -n`; the shim's framework-free logic (argparse, guards, coercion)
  is smoke-tested without importing era HELM.
- *Uncertain (needs a GPU host + docker):* nothing that imports era `helm.*` has
  actually run. The shim and era dockerfile are validated only by `py_compile` /
  `bash -n`. I studied the era source at both release commits (`git show`) to get
  the API right — RunSpec is `helm.benchmark.runner.RunSpec` with exactly 6
  fields at both eras; `run_benchmarking` is signature-identical; the AutoClient
  builds a `ServerService(base_path=local_path)` which auto-registers deployments
  + demands `credentials.conf` (v0.2.4 requires a per-deployment key); the
  completions response shape matches vLLM's `/v1/completions`. But the plan's
  open questions (torch-CPU pin style, `pyext~=0.7` build under uv/3.10, dacite
  strict-decode of the full AdapterSpec, HF-Hub drift for old `datasets`) are all
  empirical and unresolved.
- *Ordering wrinkle:* commit 2 (era dockerfile referencing `docker/era_shim/`)
  landed before commit 3 (the shim), per the plan's narrative order. The tree
  stays working because neither the dockerfile nor the era build path is
  exercised by tests/compile; the modern build is byte-identical to before.

**Next steps (all empirical, on a GPU host).** Walk the validation ladder:
build both era images → freeze + commit the era constraints → instrument-fidelity
diff on a pandas-sensitive `entity_matching` run (byte-for-byte instance identity,
no model) → the `synthetic_reasoning_natural × pythia-6.9b` flagship (expect
~20% recovered vs the official 0% Together artifact) → one full packet per era
through Stages 3–6 → the per-scenario HF-fetch audit. Settle the open questions
during that pass and fold the findings back into the plan. One reusable insight:
reading era submodule source with `git show <release-commit>:<path>` (rather than
checking out) let me pin the exact era API surface without perturbing the working
tree — essential when one superproject must target two incompatible library eras.

## 2026-07-10 12:24:00 -0400

**Model/harness.** Fable 5 (`claude-fable-5`), Claude Code (review + harness session).

**Intent.** (1) Audit the six era-pinned commits; (2) make the validation
ladder runnable by anyone, on any machine, without editing scripts.

**Review.** 8-angle review with every era-API claim checked against the era
source (`git -C submodules/helm show 626d8609:… / 8ea285f7:…`) plus one
empirical pyhocon test. 10 findings (8 CONFIRMED, 2 PLAUSIBLE) written to
`docs/planning/era-pinned-review-findings-2026-07-10.md` with per-finding
fix + verify blocks and a commit-grouped fix plan for the next session.
Headline: the shim's era contract was written against v0.3.0 semantics —
v0.2.4 never registers the deployments yaml (silent Together fallthrough for
eleutherai/lmsys/meta/…), and pyhocon dot-splits credential keys so dotted
model names (pythia-6.9b, the flagship) die at both eras.

**Ladder harness.** New in `reproduce/classic_era_replay/`:
`05_ladder_gate.sh` (tiered orchestrator: PASS/FAIL/SKIP table, skips name
the unlocking env var), `15_instrument_fidelity.sh` (rung 2: era-image
dry-run + host instance-identity diff vs official `scenario_state.json`),
`50_hf_fetch_audit.sh` (rung 5: one dry-run per scenario family from
`configs/run_details.yaml`), `drivers/{dryrun_driver,instance_diff}.py`,
`ladder.env.example` (the single machine-specific file; gitignored as
`ladder.env`). Plus `tests/test_era_shim_imports.py` — a static era-import
checker that AST-parses the shim's `helm.*` imports and verifies each symbol
exists at BOTH era commits via `git show`; try/except-guarded imports are
exempt (the sanctioned fix pattern). It xfails exactly findings 3+8 (3
xfailed, 33 pass) so the suite stays green while new era-incompatible
imports fail loudly — this closes the "no host test imports era helm" gap
that let the review findings through.

**Design insights.** (1) The dry-run driver imports the *installed* shim's
own helpers inside the container, so rungs 2/5 validate the real decode
path, not a parallel one. (2) `run_benchmarking(dry_run=True)` is the
era-stable primitive for both instance-identity and fetch auditing (it is
what `helm-run --dry-run` does at both eras); rung 5 caps adaptation for
speed but rung 2 must never cap (capping changes instance selection — the
thing under test). (3) Portable-runbook pattern: all machine specifics in
one sourced env file + a gate that skips-and-names, never scripts that need
editing.

**Next steps.** Opus applies the findings-doc fixes (commit grouping is in
the doc; the import checker's KNOWN_BAD entries come out with findings 3/8);
then walk `05_ladder_gate.sh` on a docker host and the GPU machine for both
eras. Findings 1, 2, 5 surface at rung 3 if not fixed first.

## 2026-07-10 19:37:00 -0400

**User intent.** "Move the pre-0.5.0 testing gates to dev and make it mirror
the e2e test runbook already in dev — a model run e2e testing all
capabilities, all deployments/specs done, runnable with little user input."
Mid-session redirect: produce a *detailed implementation plan an Opus-level
agent can follow* rather than implementing it myself.

**Model/config.** claude-fable-5 (Fable 5), Claude Code VSCode harness, 1M
context; work done in a worktree at `/home/agent/worktrees/era-tests` on
`impl/era-pinned-helm-containers` (the main checkout stays on
`impl/run-from-run-spec` with the uncommitted phi-2 fp16 pin awaiting the
user's grid rerun).

**Deliverable.** `docs/planning/era-tests-dev-runbook-plan.md` — the
migration plan for rebuilding `reproduce/classic_era_replay/` as
`dev/era-tests/` in the `dev/e2e-tests` shape. Fact-finding that shaped it:

- **Test subject**: `eleutherai/pythia-6.9b` is the only corpus model with a
  full official packet at BOTH classic eras (74 runs each at v0.2.4 and
  v0.3.0). Grid = 2 eras × {synthetic_reasoning_natural:easy (generation,
  the ~20%-recovery flagship), mmlu:us_foreign_policy (multiple_choice_joint,
  max_tokens=1/num_outputs=5 — the shim logprob path)}. Verified both run
  dirs at both eras carry the six-key classic run_spec.
- **The canonical `official_public_index.csv` has ZERO classic rows** (55748
  rows, all modern tracks) — same desync family as the stale
  filter_inventory.json. The plan adds a `25_index_official_classic.sh`
  emitting runbook-scoped index+inventory under `indexes/era-tests/`, with a
  loud warning to override `--out_fpath/--out_detail_fpath` (their defaults
  clobber the curated `run_details.yaml`).
- **The freeze path makes the old 20_make_manifest.sh redundant**:
  `export-benchmark-bundle --freeze-rel-paths` (era from the preset via
  `preset_cfg.get("era")`) bakes frozen `run_spec_sources` + `era:` into
  directly runnable smoke/full manifests — so the era grid mirrors the e2e
  vllm transport almost verbatim (gc → gateway bootstrap → export → run
  --lease --container-image <era image>).
- **Phase 0 is the findings doc**: findings 1/2/3/5 each independently break
  this exact runbook path (2 names pythia-6.9b's dotted key), so the plan
  makes fixing them a blocking prerequisite and warns the implementer that
  post-fix, era export takes --base-url/--api-key-value like e2e.

**Tradeoffs.** One preset per era carrying both scenarios (different logical
keys → no packet pooling; e2e's per-scenario split exists only because its
three variants share one scenario). Grid rows are eras, not scenarios (era =
provenance unit). ladder.env dies in favor of e2e-style defaults+env. No
auto-build in 06 (fail-with-remedy, matching e2e). Five open questions are
flagged empirical-first (freeze disambiguation across classic suites,
composer era-scoping, require_per_instance_stats vs per-instance-stats-less
classic officials, era HF-fetch health, lease/network combo) rather than
guessed at.

**Loose ends.** An exploration subagent digesting the era CLI surface died
mid-run (task id vanished); its scope was re-covered by direct reads. The
e2e-conventions digest that fed the plan came from the other subagent and is
reflected in the plan's "copy verbatim" tables. Next: an implementing agent
executes the plan phases 0→3 on this branch, starting with the findings-doc
fix order.

## 2026-07-11 10:40:00 -0400

**User intent.** "Implement the full plan with all bug fixes" — execute
docs/planning/era-tests-dev-runbook-plan.md end to end: fix ALL ten era-path
review findings, then build the turnkey dev/era-tests runbook.

**Model/config.** claude-opus-4-8[1m], Claude Code VSCode harness; worked in a
git worktree at /home/agent/worktrees/era-tests on
`impl/era-pinned-helm-containers` (the main checkout stays on
`impl/run-from-run-spec`, untouched). Ran python via the main checkout's
`.venv/bin/python` with `PYTHONPATH` pointed at the worktree so worktree code
shadows the editable install; the helm submodule isn't populated in a worktree,
so the tier-0 static import checker was validated by symlinking the main
checkout's populated submodule in (removed afterward).

**What landed (10 commits).**
- Phase 0 (6 commits, `0c58ee9`..`0486bb7` + resolution doc): every finding in
  era-pinned-review-findings-2026-07-10.md fixed, with host-importable tests for
  each one that can be exercised without an era image. Notables: Finding 2 (the
  pyhocon dot-split that breaks `eleutherai/pythia-6.9b`'s credential lookup at
  BOTH eras) — fixed on both sides (shim writes a nested-key credentials.conf via
  `_hocon_nested_deployment_key`, verified empirically against pyhocon; export
  puts `api_key` in client_spec args); Finding 1 (v0.2.4 silent Together routing)
  — explicit `register_model_deployments_from_path`; Finding 3 (v0.2.4 image
  couldn't build) — version-tolerant `wrap_request_time` import. Also taught the
  static import checker to exempt except-ImportError handler bodies (its
  documented try/except pattern only exempted the try body — my fixes were the
  first to use the pattern and exposed the gap).
- Phase 1 (`67c0b53`): era-tests infer-stack catalog/settings, two per-era
  presets (era-pythia_6_9b-v0_2_4 / -v0_3_0), two per-era vexp manifests.
- Phase 2 (`99ff4df`): dev/era-tests/ runbook mirroring dev/e2e-tests, git mv'ing
  the ladder rungs + drivers and deleting the superseded build/export/
  make-manifest/run scripts.

**Design decisions + empirically-resolved open questions.**
- *Freeze ambiguity (plan OQ1) → per-era corpus VIEW.* pythia-6.9b's runs exist
  at v0.2.4 AND v0.3.0 with identical run-dir names, so `--freeze-rel-paths`
  against the broad classic root is AMBIGUOUS (confirmed: `_classify` returns
  AMBIGUOUS). Narrowing `--precomputed-root` to `runs/<suite>` breaks discovery
  (it walks for a dir literally named `benchmark_output`). Solution: `_lib.sh ::
  era_corpus_view` builds `<view>/classic/benchmark_output/runs/<suite> ->` real
  suite (one symlink/era), preserving the `classic/benchmark_output/...` layout
  era resolution needs. Verified end-to-end: both entries RESOLVE, era resolves
  to the right key, and a live export produces correctly suite-scoped frozen
  sources for both eras.
- *Cross-suite pairing (plan OQ2) → per-era scoped official indexes.* The
  canonical official_public_index.csv has ZERO classic rows; step 25 runs
  eval-audit-index-historic once per era with `--suite_pattern <suite>` into
  indexes/era-tests/<suite>/, and redirects `--out_fpath/--out_detail_fpath` to
  scratch so the curated run_details.yaml is never clobbered. Per-suite (not one
  combined index) so a v0.2.4 local run can't pair against a v0.3.0 official
  (identical logical keys across suites).
- One preset PER ERA carrying both scenarios (distinct logical keys → clean
  compose); grid rows are eras (the provenance unit); 06 fails-with-remedy rather
  than auto-building; ladder.env retired for e2e-style defaults+env.

**Validation done (sandbox).** 120 pytest green (era + touched-area suites);
bash -n all 13 scripts; _lib.sh helpers exercised; live `--freeze-rel-paths`
export for BOTH eras (correct era schema, api_key in args, explicit base_url,
era: stamp, per-source lease); both vexp manifests load through the real
`virtual.manifest.load_manifest`; drivers py_compile.

**What remains (needs docker + GPU + built era images — cannot run here).**
Build both era images (`ERA=<key> ./docker/build.sh`); 06 image probes; 07 rungs
2/5; the 10/15 grids; step 25's actual index build; 30/40. **Plan OQ3 is still
open:** whether era `run_benchmarking` emits `per_instance_stats.json` — the
generated manifests set `require_per_instance_stats: true` and `_locate_run_dir`
depends on it, but the official classic runs ship none, so the LOCAL side must
produce it. First thing to check on a GPU host; if it doesn't, add an era carve-out
in `_manifest_doc`, not a hand-edit.

## 2026-07-11 11:05:00 -0400

**User intent.** Review the Opus implementation of the era-tests plan for
correctness and elegance, then apply the fixes.

**Model/config.** claude-fable-5 (Fable 5) reviewing claude-opus-4-8's ten
commits (`0c58ee9..3046c8e`), verifying against the era HELM sources
(`git -C submodules/helm show 626d8609/8ea285f7`) and empirically, per the
method of era-pinned-review-findings-2026-07-10.md.

**Verdict.** The Phase-0 era-machinery fixes all check out (registration
placement, nested-HOCON `in`+getitem, create_object dict-merge — no duplicate
kwarg, guard run_ref/pull semantics, Finding-9 blast radius contained to the
manifest builder, corpus-view symlinks never dereferenced in-container since
the exact-path branch mounts no precomputed_root). Two CONFIRMED integration
bugs in the new runbook, both fixed in this commit:

1. **v0.2.4 master-key clobber (would 401 every request).** v0.2.4's AutoClient
   constructs the client with `additional_args={"api_key": <credentials.conf>}`
   and era `create_object` merges additional_args LAST — so the credentials
   value (`EVAL_AUDIT_ERA_API_KEY`, default EMPTY = no Authorization header)
   overrides the master key the export baked into client_spec.args. v0.3.0 is
   unaffected (inject_object_spec_args fills only MISSING params). Fix: the
   grids export `EVAL_AUDIT_ERA_API_KEY="${LEASE_MASTER_KEY:-…}"` before
   eval-audit-run; the shim chmods 600 the prod_env credentials.conf + the
   deployments-yaml copy (both now carry the live key and persist in out_dpath).
2. **Double-`classic` path join broke gate tier 1.** `_lib.sh` repointed
   `PRECOMPUTED_ROOT` at the TRACK root, but the moved rung 2/5 helpers still
   joined `$PRECOMPUTED_ROOT/$(rel stripped against the MIRROR root)` →
   `.../classic/classic/...` → every pick missing → rung 2 exits 1 → gate FAIL.
   Fix: `era_mirror_root` in _lib.sh (detects track-root vs mirror-root
   conventions by probing for `benchmark_output/`; `ERA_MIRROR_ROOT` overrides)
   and both rungs join + mount against it. Verified the join resolves a real
   official run dir under both conventions.

Minor: the pyhocon test now asserts `model in deps` too (v0.2.4 checks
membership BEFORE getitem — the shipped test only covered getitem; verified
`in` resolves for the nested layout); removed the dead
`EVAL_AUDIT_ERA_HF_CACHE_DIR` export. Noted, not changed: `era_image`'s single
`$PYTHON_BIN` dependency for read_eras.py (consistent with the e2e convention),
07's slightly-off SKIP wording when era_image itself fails, and the deliberate
10/15 grid duplication (mirrors e2e). 88 era-suite tests green post-fix.

**Design insight.** Two of the three bugs came from a *meaning shift* in a
shared variable (`PRECOMPUTED_ROOT`: mirror root → track root) and a *merge
order* the modern path doesn't have (additional_args wins at v0.2.4). Both are
invisible to host tests and would only fire on the GPU host at rung 3 / tier 1
— exactly the class of bug the gate exists to catch early, which is an argument
for running 07 before every grid invocation, not just once.

## 2026-07-11 14:20:00 -0400

**Model**: claude-opus-4-8 (Claude Code, VSCode extension). **Branch**:
`impl/era-pinned-helm-containers`.

**User intent**: "Can the new era-tests runbook be run locally on an 8 GB GPU?"
→ then "check if there are two separate smaller models, one for 0.2.4 and one
for 0.3.0" → then "check out to the correct branch and swap out the model" with
option (a) (make the swap as a new commit).

**What I found.** The runbook's containers are CPU-only (`container_gpus: none`);
the entire GPU load is the out-of-process vLLM backend serving the subject. The
prior subject, `eleutherai/pythia-6.9b` (~14 GB fp16), does not fit 8 GB. I swept
the classic corpus (`/data/crfm-helm-public/classic/benchmark_output/runs/{v0.2.4,
v0.3.0}`) for small open-weight models with full official packets. Result: the
"two separate models" framing is unnecessary — `together/redpajama-incite-base-3b-v1`
has the **same full 74-run packet at BOTH eras** (identical coverage to pythia-6.9b)
AND fits 8 GB (~2.8B params, ~5.6 GB fp16). The distinct-per-era small candidates
are all partial (pythia-2.8b: 7 runs @ v0.2.4, 0 @ v0.3.0; pythia-1b: 4/4;
falcon-7b: 0/74 and too big anyway), so splitting subjects would cost packet
completeness for no benefit. Both probe scenarios
(`synthetic_reasoning_natural:easy`, `mmlu:us_foreign_policy`) exist for redpajama-3b
at both eras. `adapter_spec.model` in the official run_spec is
`together/redpajama-incite-base-3b-v1` (model_deployment None — pre-v0.5, expected).

**The swap (pure subject substitution, structure unchanged).** Renamed
`era-pythia_6_9b-*` → `era-redpajama_3b-*`, `pythia69b-single` →
`redpajama3b-single`, `eleutherai/pythia-6.9b` → `together/redpajama-incite-base-3b-v1`
across the runbook scope only: the two era presets in
`eval_audit/integrations/infer_stack/preset_configs.yaml`, the serving catalog
`dev/era-tests/config/infer_stack/catalog.yaml` (HF source
`togethercomputer/RedPajama-INCITE-Base-3B-v1`, runtime retuned for 8 GB —
`gpu_memory_utilization` 0.8→0.85, `max_num_seqs` 16→8), `dev/era-tests/_lib.sh`
(ERA_TARGETS + era_vexp_manifest map + comments), the numbered scripts' comments,
the README, and the two vexp configs (git-mv'd to `era-redpajama-v{024,030}.yaml`,
scope regex `^together/redpajama-incite-base-3b-v1$`). Added a subject-change
banner to the plan doc rather than rewriting its 28 references — the plan's every
structural decision (per-era presets, per-era corpus view for the cross-suite
name collision, per-era official index, verbatim by-name replay) holds verbatim
because redpajama-3b's runs collide across suites exactly as pythia-6.9b's did.

**Deliberately NOT touched.** The era *test* fixtures (test_eras*, test_era_shim*,
test_exporter_freeze) use pythia-6.9b as a generic sample model to exercise the
era machinery (dotted-name HOCON nesting `"eleutherai/pythia-6" { "9b" = … }`,
freeze lease-map keying, discovery) — they don't reference the runbook presets, so
they stay. All the other pythia-6.9b references tree-wide (run_details.yaml,
run_specs.yaml, virtual-experiment tests, historical docs) are unrelated
pythia-6.9b work outside this runbook.

**Validation.** YAML parses + preset/catalog invariants (new keys present, old
keys absent, run_entries carry the new model token); `bash -n` on all scripts;
sourced `_lib.sh` resolves both targets → renamed manifests that exist on disk;
`bash -n` clean; the full era pytest suite (88 tests) passes. Not run (needs
docker+GPU the sandbox lacks): the actual grid — that's the user's GPU-host pass.

**Reusable insight.** When a runbook's subject is chosen for a corpus property
("full packet at both eras"), re-query the corpus before assuming the subject is
forced — the constraint (full packet ∩ both eras) had a second solution that also
satisfied an orthogonal constraint (fits 8 GB). The 8 GB question dissolved into a
one-model swap because the corpus happened to contain a 3B model with identical
coverage. Also: keep the swap a *rename*, not a *value edit* — leaving
`pythia_6_9b` identifiers on a redpajama model would be a landmine for the next
reader.

**Next steps.** User's GPU-host pass is unchanged in shape: build era images
(`ERA=<key> ./docker/build.sh`), 06 → 07 → 10/15 grids → 20/25/30/40. On the 8 GB
card, watch the first vLLM load — if it OOMs, drop `gpu_memory_utilization` to
0.80 in the catalog (noted inline). The phi-2 wip stash on `run-from-run-spec`
(`git stash list`) is untouched and waiting for that branch.

### Addendum (same session): first era image build surfaced a smoke-test bug

The user ran `ERA=<key> ./docker/build.sh` for the first time and the final-stage
import check failed (exit 1). Root cause: the dockerfile smoke test did
`from helm.benchmark.runner import RunSpec, run_benchmarking`, but at BOTH era
refs (626d8609, 8ea285f7) `run_benchmarking` lives in `helm.benchmark.run`, not
`.runner` (only `RunSpec` is in `.runner`). The shim itself is correct
(`replay.py :: _replay_run_spec` does `from helm.benchmark.run import
run_benchmarking`); only the build-time assertion had the wrong module. Split the
import into two lines to match the real era API. Verified all three imported
symbols resolve at both refs (register_model_deployments_from_path, RunSpec,
run_benchmarking). Unrelated to the redpajama swap — a pre-existing build-path bug
that could only surface at first real image build (no docker in the sandbox).
Insight: import-surface smoke tests must be validated against the *actual* pinned
source, not from memory of the modern API — the module a symbol lives in drifts
across releases just as often as the symbol itself.

### Addendum 2: adopt the era's frozen requirements.txt as constraints (enriched seed)

After the pyarrow drift, the user asked whether we could "just use the era's
frozen requirements.txt" instead of pinning drift one at a time. Answer: yes, but
not verbatim — two blockers. (1) The era freeze pins torch/torchvision to
`+cu113` (CUDA) on linux; the era image is CPU-only and the dockerfile asserts a
CPU build, so those won't install (not on the CPU wheel index). (2) It pins
`pandas==1.5.0`/`numpy==1.23.3`, which would revert the tech-report-validated
instance-selection pins (2.0.3/1.23.5). User chose the enriched-seed path.
Generated both era constraints from each ref's requirements.txt (193/192 pins)
with exactly those two deviations: keep validated pandas/numpy, rewrite
torch/torchvision to CPU (`torch==1.12.1`/`torchvision==0.13.1`; CPU index serves
+cpu). Key realization: pinning torch to the era 1.12.1 is what makes the WHOLE
tree internally consistent — the era's old typing_extensions==4.4.0 / sympy /
networkx pins agree with torch 1.12, whereas the prior seed-only build let torch
float to 2.x whose modern deps would fight those pins. Bonus fidelity: era
tokenizers==0.13.3 / transformers==4.28.1/4.33.1 reproduce official tokenization
(the era WindowService does it) better than a modern resolve. Residual risk to
watch at RUNTIME (not build): pandas 2.0.3 against era datasets==2.5.2 (datasets
2.5 predates pandas 2.0; if a scenario hits a removed pandas API it'll surface
then) — the two probe scenarios (synthetic_reasoning, mmlu) are light on
pandas/datasets so likely fine. Close-out remains the pip-freeze workflow once
green. Insight: a CI-era requirements.txt is a coherent lock EXCEPT where the
target environment differs on axes the freeze encodes (GPU vs CPU) or where a
validated override supersedes it — reconcile exactly those axes, adopt the rest.

### Addendum 3: enriched freeze REVERTED — setup.cfg ~= ranges already era-pin

The enriched-freeze build failed at the uv resolve: `crfm-helm==0.2.4 depends on
spacy>=3.5.3,<3.6 and spacy==3.2.4 -> unsatisfiable`. Root cause: the era
requirements.txt is STALE relative to the era setup.cfg — setup.cfg was bumped to
`spacy~=3.5.3` after the requirements.txt froze `spacy==3.2.4`. Hard-pinning the
stale freeze fights setup.cfg (authoritative) and would cascade through spaCy's
subtree (thinc/blis/pydantic/...). More important: the freeze was UNNECESSARY.
setup.cfg already era-pins every fidelity-critical dep via `~=` (compatible
release): transformers~=4.28.1, tokenizers~=0.13.3, datasets~=2.5.2, numpy~=1.23.3,
scipy, scikit-learn, sympy — each floats only within its era minor series. The
ONLY open-upper-bound dep in the whole setup.cfg is `pyarrow>=11.0.0` — the exact
one that drifted to 15 and broke datasets. So the minimal correct fix is: validated
pandas/numpy (pandas is a transitive via datasets, unpinned by setup.cfg; numpy
~=1.23.3 admits 1.23.5) + pyarrow==11.0.0. Reverted constraints to that (the
2e8338f state). The seed-only resolve had already SUCCEEDED at the builder stage
in the very first build (the #21 layer was CACHED), so seed+pyarrow is known-good.
Kept the enriched-freeze commit in history + this note as the learning trail.
Insight: before hard-pinning a lockfile, check the package's OWN dependency
declaration — if it uses compatible-release (`~=`) ranges, it already pins the
minor series; you only need to constrain the deps it leaves open-ended (or pure
transitives it never names, like pandas here). A CI requirements.txt can also be
stale vs the setup.cfg in the same commit; setup.cfg wins.

### Addendum 4: rung-2 fidelity diffed a file the public corpus never ships

First real 07 gate run: tier0 + rung5 PASS, rung2 FAIL for both eras — but as "0
pass, 0 fail, 3 skipped" (all SKIP "official scenario_state.json missing"). Root
cause: rung-2 (instance_diff.py) compared the produced dry-run scenario_state.json
against an OFFICIAL scenario_state.json — which the public HELM corpus NEVER ships
(0 across ~8000 run dirs/suite). scenario.json is metadata-only (no instances)
too. The only published per-instance record is display_requests.json /
display_predictions.json (keyed by instance_id, with the full request prompt).
Pre-existing design flaw, surfaced at first real run (like the dockerfile import
and pyarrow bugs) — orthogonal to the redpajama swap. Fix: rung-2 now compares
identity as (instance_id, train_trial_index, prompt) — official from
display_requests.json, produced from the dry-run scenario_state.json;
instance_diff.py is shape-detecting (list => display records, dict => scenario
state). The prompt is a strict superset of the old input+references key (it embeds
them + few-shot examples + formatting after model-window truncation), so it's a
STRONGER fidelity signal AND the only one the corpus supports. Verified: era
dry_run writes scenario_state.json unconditionally (runner.py:290; request_states
come from adapter.adapt() at :244 before execute), and the two shape-branches
produce identical keys (synthesized-scenario_state vs real display_requests →
INSTANCES_MATCH 1000). Kept the pythia/vicuna picks: instance selection + prompt
construction are what the rung tests, and the dry-run uses the picked run's own
run_spec (same model), so prompts truncate identically — rung-2 validates the ERA
INSTRUMENT, not the runbook subject (redpajama is tested by the 10/15 grid). All
6 picks have run_spec.json + display_requests.json, so none skip now. Whether they
PASS the diff is the actual research question (byte-for-byte prompt fidelity);
can't tell without docker. Insight: a fidelity check is only as good as the
artifact it diffs — validate the comparison target EXISTS in the corpus before
diffing against it; the richest published signal (the prompt) beat the schema-pure
one (input+references) that wasn't published.

### Addendum 5: rung-2 must filter unfetchable-data probes, not fail on them

After wget/unzip + fsspec fixes, rung-2 v0.2.4 = entity_matching PASS
(INSTANCES_MATCH 1000), raft PASS (115), math FAIL. math's crash is
`ConnectionError: Unauthorized ... competition_math.py ... use_auth_token` — MATH
is a SCRIPT-based dataset the 2026 HF Hub blocks (401 on the .py loader). My prior
inference "rung 5 forwards the token and passed, so forwarding it fixes math" was
WRONG on two counts: (1) HF_TOKEN is NOT set in the shell (nothing to forward),
and (2) rung 5 is an AUDIT that passes at 17/20 and itself lists MATHScenario
(+BabiQA, TruthfulQA) under "failing families — pre-warm or mount-vendor". So MATH
is a genuine ENVIRONMENT/RECIPE filter, exactly the CLAUDE.md distinction:
data-unavailable = a filtering reason, NOT a reproducibility/instrument failure.
Fix: rung-2 now classifies a dry-run crash whose log carries data-fetch/auth
signatures (Unauthorized/Couldn't reach/ConnectionError/use_auth_token/GatedRepo/
401/403/DNS) as SKIP (environment filter), not FAIL — so the gate passes on the
families whose data actually loads (entity_matching, raft) and filters the rest.
Kept the HF_TOKEN/HUGGING_FACE_HUB_TOKEN forward (harmless; helps if a token is
ever set). The runbook's own two scenarios (synthetic_reasoning, mmlu) both PASS
rung-5 fetch with no token, so the main grid path needs none. Insight: a gate over
historical data must separate "the instrument is wrong" from "the data is no
longer reachable" — conflating them turns Hub drift into a false reproducibility
failure. rung 5 already had the taxonomy; rung 2 now matches it.
## 2026-07-11 16:59:16 -0400

**Model/harness:** Claude Opus 4.8 (1M context), `claude-opus-4-8[1m]`, Claude Code.

**User intent.** After establishing which public gpt-oss-20b rows exist (11) and
which need closed judges / gated data, the user asked for a runbook "like the olmo
one" for gpt-oss-20b covering **bbq, ifeval, mmlu_pro, gpqa**, authored **in a git
worktree checking out the `impl/run-from-run-spec` branch**. Explicit steer: the
older gpt-oss presets (`gpt_oss_20b_vllm`, `finish_qwen25_gptoss`) are stale/pre-
refactor — model the new work on `olmo_models_combined/`, the accurate from-spec
reference.

**What I built** (worktree at `/home/agent/eval_audit-gptoss-fromspec`, since the
repo parent is root-owned and a sibling worktree couldn't be created):
- New single-model **from-spec** preset `openai-gpt-oss-20b` in
  `preset_configs.yaml`, cloned from the `allenai-olmo-2-1124-7b-instruct` shape
  (top-level profile facts; `precomputed_root: /data/crfm-helm-public` per manifest;
  run_entries with NO inline `model_deployment=` token — the exporter injects
  `vllm/openai-gpt-oss-20b` as the from-spec rewrite target).
- `reproduce/gpt_oss_20b_from_spec/` — `_lib.sh`, `00`–`40` step scripts, shipped
  `config/infer_stack/{catalog,settings}.yaml` (the `gpt-oss-20b-single` endpoint),
  and a README. Direct single-model port of the olmo combined runbook minus the
  fan-out/extra-preset machinery.
- `configs/virtual-experiments/gpt-oss-20b-from-spec.yaml` (scope = the one model;
  official_public_index comparison ON).

**Central design decision — protocol.** The official rows were served via
`together/gpt-oss-20b`, a chat (harmony) client — discovery confirmed
`deploy(official)=together/gpt-oss-20b` for all four. So the faithful replay serves
**chat**, not the frozen `gpt_oss_20b_vllm` preset's completions workaround. The
trade-off: gpt-oss is a reasoning model and chat can return `message.content=null`
(reasoning-only, finish_reason=length), which un-patched HELM crashes on
(`docs/helm-null-completion-text-patch-proposal.md`, still a *proposal* — submodule
not vendored to confirm a merge). Risk is concentrated on the CoT rows
(mmlu_pro, gpqa); documented prominently in the README with the completions fallback
as a liveness escape hatch, treated the way olmo treats gpqa gating.

**Validation (as far as possible without GPUs/serving).** All four target YAMLs
parse; the preset loads through the real `_load_preset_catalog`; `bash -n` clean on
all scripts. Decisive: ran the actual discovery gate
(`check_precomputed_discovery --preset openai-gpt-oss-20b`) against the real
`/data/crfm-helm-public` (84,966 run dirs) — **smoke 2/2 and full 4/4 RESOLVED,
0 NO_MATCH, 0 AMBIGUOUS**. All four official run dirs carry `run_spec.json`. The
export's later materialize steps error only on worktree-absent submodules
(infer_stack catalog path, helm `model_metadata.yaml`) — invocation artifacts, not
preset bugs; 10/15 supply the base-url + run in the initialized main tree.

**Reusable insights.**
1. A single-model from-spec preset = the OLMo-2 single shape verbatim; the only
   real choices are the serving endpoint + `protocol_mode`, and `protocol_mode`
   should match the *official* deployment's client (discovery prints it).
2. `check_precomputed_discovery --preset` is the right cheap proof that run_entries
   resolve 1:1 — it needs neither the helm submodule nor serving, unlike the full
   `export-benchmark-bundle` materialize path.
3. Discovery tolerated `mmlu_pro:subject=all` resolving to the official `subset=all`
   dir (kept, matching the olmo pattern) — the freeze replays the official
   run_spec.json regardless, so the run_entry is only a locator.

**Status / next steps.** WIRED, not yet GPU-run. Remaining: on a serving host,
`./08` (freeze against corpus), then `./10`/`./15` for the real batch; watch the CoT
rows for the null-content crash and fall back to completions if it fires. Committed
onto `impl/run-from-run-spec` (not pushed).

## 2026-07-11 20:30:00 -0400

**Model / harness.** claude-opus-4-8[1m], Claude Code (VSCode), in the
`impl/run-from-run-spec` worktree at `/home/agent/eval_audit-gptoss-fromspec`.

**User intent.** Implement `docs/planning/qwen-models-combined-fanout-plan.md` — the
eight public HELM Qwen text models as ONE `qwen-combined` multi-deployment from-spec
fan-out (the `allenai-olmo-combined` analogue), on `impl/run-from-run-spec` in a
worktree.

**What landed (3 commits).**
1. `feat(qwen-fanout)` — generalized the OLMo-specific combined helpers into
   `_combined_run_entries(keys, mode)` + `_build_combined_preset(name, keys, ...)`
   and built BOTH `allenai-olmo-combined` (unchanged: 73 entries / 5 profiles) and
   the new `qwen-combined` through it (§4.2B option B). 8 member from-spec presets in
   `preset_configs.yaml`, run_entries GENERATED from `official_public_index.csv`.
   `adapter.py` re-export updated. `tests/test_qwen_from_spec.py` mirrors the OLMo
   combined tests.
2. `feat(reproduce)` — `reproduce/qwen_models_combined/` (runbook port + shipped
   infer-stack catalog/settings) + `configs/virtual-experiments/qwen-models-combined.yaml`.
3. `docs(planning)` — flipped the plan status to implemented.

**Key findings / decisions.**
- **The whitelist (§4.3) is the real work, and it's exactly reconstructible.** Filter
  `official_public_index.csv` to classic-core + capabilities and it lands on the
  plan's per-model counts to the row: 85×5 + 86 + 132×2 = **775**. The two non-obvious
  exclusions that make 86/132 (not 87/133) land are **banking77** and **bigcodebench**.
  gpqa (gated) only on the two turbo models.
- **T1 protocol was answerable, not "to confirm."** HELM's own `model_deployments.yaml`
  (read from the installed wheel, not the un-checked-out submodule) says base Qwen1.5
  7b/14b/32b/72b = `TogetherClient` (completions), and 110b-chat / qwen2-72b-instruct /
  the qwen2.5 turbo pair = `TogetherChatClient` (chat). Tokenizers too (qwen1.5 family
  all share the `qwen/qwen1.5-7b` tokenizer entry; both turbos share `qwen/qwen2.5-7b-instruct`).
- **No member splits.** A pure-Python ambiguity pre-check (775 distinct run-dir
  basenames, 0 shared across suite trees, all present on disk) predicts every member
  freezes 1:1 under the shared parent root ⇒ `QWEN_COMBINED_EXTRA_PRESETS` stays empty.
  The split-out machinery is still wired for a future corpus refresh (the olmo-7b path).

**Environment gotcha.** Every venv in this checkout throws `OSError: [Errno 9] Bad
file descriptor` reading `pyvenv.cfg` (the FD-exhaustion family CLAUDE.md warns
about). I did NOT fight it. Validated with a clean `env -i /usr/bin/python3` + a
`kwutil` stub (only used at call time) + PYTHONPATH: V1 (preset loads, correct
shape), V4 (yaml/bash/py_compile), endpoint↔profile cross-check all pass. The
corpus-gated freeze (V2/V3, `08_check_discovery.sh`) additionally needs the `magnet`
backend, which isn't checked out here — genuinely deferred, as the plan stages it.

**Reusable insights.**
1. When a "confirm this" task item names a HELM fact (protocol/tokenizer/max_seq),
   the installed `helm/config/*.yaml` wheel is the authoritative source — no need to
   check out the submodule or hit HF Hub.
2. The plan's stated counts are a spec, not decoration: reverse-engineering the
   keep-set to hit them exactly (banking77/bigcodebench) removes the guesswork the
   plan flagged as "the real work."
3. Generalizing a one-off combined-preset builder is safe here precisely because the
   OLMo combined tests pin the old output — regenerate, diff the entry count, done.

**Status / next steps.** WIRED + analysis-host-validated, not yet GPU-run. On a
serving host with the full install: `./08` (corpus freeze — the authoritative
0-NO_MATCH/0-AMBIGUOUS gate), then `./10`/`./15`. Verify T2 HF weight ids on the Hub
first. Committed onto `impl/run-from-run-spec` (not pushed).

## 2026-07-12 10:34:10 -0400

**User intent**: Assess, then perform, the merge of `impl/era-pinned-helm-containers` into `impl/run-from-run-spec` without breaking existing run paths.

**Model/config**: claude-fable-5 (Claude Code, VSCode extension harness).

**What happened**: Pre-merge audit via `git merge-tree` showed only two conflicts, both append-vs-append (journal + `preset_configs.yaml`). Audited every shared module the era branch touches: the feature is opt-in end-to-end (`ManifestSpec.era=None` default, key omitted from manifest docs so modern manifests stay byte-identical and kwdagger job-identity hashes stable; `_era_pipeline_for_manifest` returns None without the key; exporter/freeze gate on `resolved_era`/`omit_model_deployment`). Two fail-loud guards touch modern paths (materializer refuses `model_deployment` rewrite on pre-v0.5 specs; bridge inspects `org.aiq.era` label only for unpinned modern images) — neither affects existing runbooks.

**Merge mechanics worth remembering**: git anchored the `preset_configs.yaml` conflict on shared trailing lines (`container_network`/`full_manifest`) and split it into three hunks *mid-preset* — a naive per-hunk union would have interleaved the gpt-oss preset with the era presets and corrupted the YAML. Correct resolution: both sides appended whole blocks at the merge-base EOF (line 915), so I rebuilt the file as HEAD's version + era's 86 appended lines, then validated with `yaml.safe_load` (25 presets). Journal resolved by chronological reorder (era-side entries all predate HEAD-side).

**Validation**: era + exporter + provenance + qwen suites in the merged worktree: 94/95 pass. Two failure groups triaged as NOT merge-induced: (1) `test_era_shim_imports` v0.3.0 failures were the merge worktree's helm submodule missing object `8ea285f7` — fixed by local fetch from the main worktree's submodule; (2) `test_qwen_from_spec::test_combined_preset_resolves_with_local_strip` fails identically on the unmerged base `faef9ea` — /data/crfm-helm-public now has token-subset-ambiguous duplicates for the qwen mmlu entries (corpus drift, possibly from era-era classic pulls).

**Next steps**: (a) the pre-existing qwen ambiguity needs a look — either the corpus gained a duplicate suite or the whitelist needs a suite-scoped root, orthogonal to this merge; (b) merge committed as `eb1b9faf` on `impl/run-from-run-spec`, not pushed.

**Design insight**: when both branches append to the same YAML mapping, resolve at the block level (reconstruct base + block A + block B), never at the hunk level — git's common-line anchoring inside repetitive YAML (repeated `container_*` keys) produces structurally misleading conflict hunks.

## 2026-07-12 11:12:00 -0400

**User intent**: Fourth simplification pass. Audit the repo (post era-pinned
pre-v0.5 replay path) for refactors/simplification and write a detailed,
implementable plan. Then (model switch mid-session) review the first-pass
findings and revise the plan with a simplicity-first eye.

**Model/config**: claude-opus-4-8[1m] for the audit + first draft;
claude-fable-5 for the review revision (Claude Code, VSCode extension harness,
five parallel general-purpose audit subagents).

**What happened**: Five parallel deep audits (helm↔normalized diff cores,
reports/, workflows+cli, era/replay layer, repo hygiene), with every
high-impact claim re-verified in the main session before it entered the plan.
Output: `docs/planning/repo-simplification-plan-2026-07-12.md` (commits
`ea74ac38` draft, review revision on top). The central reframe: three prior
passes (2026-07-02/-06/-10) already banked the deletion wins, so this plan is
"finish what's in flight" (diff-core migration residue, the half-split
`build_reports_summary.py`), plus era-branching consolidation, plus small
dead-code/dup-helper wins.

**Review deltas worth remembering** (the second pass made the plan *smaller*):
- Dropped the "collapse the replay-node subclass chain into a dispatch table"
  recommendation. Two verifications killed it: kwdagger addresses pipelines by
  fully-qualified factory-path *strings* (external contract — the three named
  factories must exist regardless), and the era subclass is a docstring plus
  one attribute override. The proposed dispatch dict would add tuple-key
  indirection while deleting almost nothing.
- Simplified the `run_entries.py` relocation: top-level
  `eval_audit/run_entries.py` following the `metrics_taxonomy.py` precedent
  (itself lifted out of `helm/` the same way), not a new package; and no
  back-compat shim — grep shows zero reproduce/generated-script imports of the
  old path.
- Re-scored the facade-re-export cleanup: the "tests need these ~62 names"
  claim was overstated (one test file, module-attribute access); most
  re-exports are referenced by nothing → measured delete becomes a Batch-1
  trivial item.
- Added one missed dead item: `compat/helm_outputs._MsgspecRunView`
  (NotImplementedError placeholder, constructed but never called).

**Uncertainties / open decisions**: the two capstones (A4 retire HelmRunDiff —
also delivers the deferred EEE-only hard split; D6 typed `analyze_index()`
primitive) need owner sign-off and a captured behavior baseline; `ladder-out/`
(2.3G scratch) reclamation needs a user call; D5 (`compare_batch` fate) is an
operator decision.

**Reusable insights.**
1. On a repo with prior audit passes, read the implemented plans *first* and
   scope the new audit to what changed since — the biggest risk is re-planning
   banked work, not missing debt.
2. Before recommending "collapse subclasses into a dispatch table," check who
   addresses the classes: string-addressed factories (schedulers, plugin
   registries) make the named functions the interface, and the table deletes
   nothing.
3. Audit-agent claims about *why* code exists ("kept for tests") are the ones
   to re-verify by grep — liveness claims were reliable, purpose claims less so.

**Next steps**: plan awaits owner sign-off on sequencing + capstones; Batch 1
(dead symbols, dup helpers, unreferenced re-exports, era config dedup,
norecursedirs, shared helpers) is implementable immediately at near-zero risk.

## 2026-07-12 13:20:00 -0400

**User intent**: `/goal Implement latest refactor plan` — execute
`docs/planning/repo-simplification-plan-2026-07-12.md` (Batches 1–3;
capstones/operator decisions explicitly excluded pending sign-off).

**Model/config**: claude-fable-5 (Claude Code, VSCode extension harness).

**What happened**: 22 commits implementing every Batch 1–3 item, one commit
per item, suite-gated throughout (fast suite per commit; slow suites where
the item touched render/EEE/era paths; FULL `--run-slow` suite at A2 and at
the end: 638 passed / 1 skipped / 1 pre-existing corpus-drift failure).
Headlines: `run_entries` moved to a top-level module (A1) + one shared
`benchmark_output` parser (B1); the prod-dead half of `helm/` deleted (A2 —
`instance_stats.py` 425→33, join stack + `helm/metrics.py` shim gone);
`build_reports_summary.py` finished its Phase-2 split (C1, 1,715→334, five
new `reports/summary/scope*.py` modules, AST-computed imports, verbatim
bodies); the two big heatmap renderers now share a six-helper grid scaffold
(C2) gated by a hand-rolled synthetic-cells probe — 9 artifacts
byte-identical before/after.

**Scope judgments made mid-flight** (each recorded in the plan):
- B2 implemented *minimally* (one named `era_mode` flag + documented
  invariant); the full strategy extraction failed the same yardstick that
  killed the node-chain idea (R-a).
- E5b resolved as **keep**: `--skip-diagnosis` is load-bearing (EEE-only
  paper claim, ~57s/packet, judge-substitution tests) — phase3 4.8 closed
  the other way from `EVAL_AUDIT_EEE_STRICT` (retired, E5a).
- E4(b) skipped: after (a), the facades' remaining imports are their own
  implementation imports + string-name monkeypatch targets.
- C3 done as the move (breakdown 1,007→813; repair/publish half →
  publish.py); decomposing the 481-line selection algorithm deferred.
- D3 done as *layering*: the CLI's capture-group regexes first, generic
  tail delegates to `failure_triage` (intentional delta: previously
  `uncategorized_error` logs now get taxonomy names — CUDA-OOM probe).

**Gotcha worth remembering**: the E4a facade pruning was AST-driven and
missed a **string-name** reference — `monkeypatch.setattr(core_metrics,
"_single_run_core_stat_index", ...)` in a slow-marked test. Fixed + lesson
recorded: before pruning re-exports, sweep for string-literal name refs, and
gate prunes with `--run-slow`, not just the fast suite.

**Reusable insights.**
1. For god-module splits, compute each new module's imports mechanically
   (AST free-names ∩ module-level bindings) and move bodies verbatim — the
   C1 five-way split compiled and passed characterization first try.
2. For renderers with zero test coverage, a synthetic-input hash probe
   (double-render HEAD first to prove determinism) is a cheap, real
   byte-identity gate — C2's 9-artifact probe caught nothing because the
   extraction preserved every constant, and now it exists for next time.
3. Apply the same simplicity yardstick to the plan itself while executing:
   two items (B2-full, E4b) shrank on contact with the code, and writing
   the outcome into the plan keeps the audit trail honest.

**Next steps**: Batch 4 remains gated on the owner — A4 (retire
HelmRunDiff; needs the F1/F2/F8 baseline capture first), D6 (typed
analyze_index), D5 (compare_batch fate), D4-remainder, F1 (ladder-out
reclaim), F3/F4 (doc archival after merge). The branch is 22 commits ahead;
not pushed.

## 2026-07-12 13:20:56 -0400

**User intent.** Execute the operator-approved tail of
`docs/planning/repo-simplification-plan-2026-07-12.md` (Batches 1–3 already
landed in 22 commits): D5 phase-1 deprecation of `compare_batch`, A4
gate-prep (extend the phase3 behavior baseline to the HELM render path),
the operator-decision bookkeeping, and reclaim two scratch venvs.

**Model / config.** claude-opus-4-8[1m] subagent dispatched by
claude-fable-5; Claude Code harness, repo `.venv`. Every commit gated by
the fast suite plus `--run-slow` selections on anything touched (the
plan's own lesson: a regression hid in a slow-marked test).

**What happened.**
- **D5 phase 1 (commit 36bb74e0).** Deprecated `workflows/compare_batch.py`
  in place following the `cli/reports.py` tone — module docstring + a
  one-line `logger.warning` in `main()` naming the planner-driven
  replacement (`eval-audit-analyze-experiment`) and flagging that
  `helm_view_from_path` dies with it. One-line deprecation comment added to
  both `reproduce/{smoke,apples}/30_compare.sh` (behavior unchanged;
  `bash -n` clean). No behavior change.
- **A4 gate-prep (commit e1b8547d) — the meat.** The committed phase3
  baseline covered only the EEE cells (F3/F4 via `compare-pair-eee`). A4
  changes the HELM render path those snapshots never exercise, so I
  extended the capture to HELM cells driven through the *same* path the EEE
  cells use — `core_metrics` via components/comparisons manifests. Fixture
  source: the `every_eval_ever` submodule HELM run
  (`mmlu:…openai_gpt2`), the same run `test_normalized_compare.py` already
  loads through the full HELM→EEE conversion, so I know it converts in this
  venv. **F1** = official-vs-itself (diagnosis `reproduced`, agreement 1.0
  across the whole abs_tol sweep). **F2** = official vs a
  deterministically drifted local (flip 3 per-instance `exact_match` means
  0→1; bump the base `exact_match`/`quasi_exact_match` test aggregate to
  0.5) → diagnosis `core_metric_drift`, run agree@0=0.75 / inst
  agree@0=0.9625, both recovering to 1.0 as abs_tol grows — a genuine drift
  regime, not exact-match. Determinism proved by double-capture into
  separate dirs (fresh conversion caches each time) diffed byte-identical.
  New slow parametrized gate in `tests/test_phase3_baseline.py`
  (`importorskip` helm/every_eval_ever; skips if the submodule fixture is
  absent). Zero production-code changes.
- **Bookkeeping (this commit).** Operator-decisions block added to the plan
  (D5 proceed/phase-1-done; A4 trigger-gated + gate-prep done with F8
  status; D6 declined; D4-remainder declined standalone; F1 split —
  venvs reclaimed, `ladder-out/` retained) and this entry.
- **Disk reclaim.** `git check-ignore` + `git ls-files` confirmed
  `dev/e2e-tests/.venv` and `.venv-1` gitignored with zero tracked files,
  then `rm -rf` (only those two; `ladder-out/`, `tmp/`, `.build-staging`,
  and the canonical `.venv` untouched).

**F8 honesty.** F8 (mixed HELM×EEE packet) is **not** captured. No on-disk
fixture pairs a HELM run and an EEE artifact under a shared logical run key,
so a mixed packet can't be assembled from existing fixtures without
*inventing* a new coordinated one — which the brief explicitly forbids
("honesty over completeness"). Recorded as still-missing in three places:
the harness module docstring, `capture_baseline.py`'s docstring, and the
plan's A4 decision. Building F8 (extend `build_fixture.py` per matrix §7)
should be A4's first step.

**Uncertainties / what might break.** (1) The HELM cells depend on the
`every_eval_ever` submodule being checked out and on `helm` +
`every_eval_ever` importing — all three guarded by skips, so a bare
checkout degrades to skip, not fail. (2) The HELM→EEE conversion writes a
persistent cache under `/data/crfm-helm-audit-store/eee/by-run-path/<hash>`;
because run dirs are unique tempdirs there is no stale-cache collision, but
the cache does accrete entries across capture runs (matches production
behavior; harmless). (3) The manifest `comparability_facts` I author are
inputs, not the A4 surface — A4 changes the diff/diagnosis computed from run
*content*, which these snapshots pin — so any consistent facts gate A4
correctly; I set them faithfully from the fixture run_spec anyway.

**Next steps.** A4 itself remains owner/paper-gated: build F8 first, then
retire `HelmRunDiff` with all of F1–F8 green. D5 deletion +
`helm_view_from_path` removal after one deprecation cycle. Branch is 25
commits ahead of the pre-session base; not pushed.

## 2026-07-13 11:46:27 -0400

**User intent.** Diagnose why `infer-stack` leases come up healthy (vLLM
container serving) yet gateway tests fail `400 Bad Request` on
`/v1/completions` — currently olmo-7b, intermittent. Then: decide whether
it's fixable, weigh fix designs, and write an implementation plan.

**Model/config.** Claude Fable 5 (`claude-fable-5`), Claude Code VSCode
extension harness.

**Diagnosis.** The user's `docker logs` was decisive: only `GET /health`
lines — no POST ever reached vLLM, so the 400 originates at the LiteLLM
gateway (:14042). User confirmed the body: "Invalid model name." Root
cause: four runbooks (olmo/qwen/gpt-oss/classic_together) share one stack
(`/data/service/infer-stack`) with disjoint catalogs, and static-superset
mode renders the gateway route table from *the invoking catalog only*
(`compose.py:907` → `_litellm_model_list_from_catalog`). Any cross-catalog
converge recreates the gateway with the other runbook's routes, stranding
still-live deployments. Key insight on symptom mapping: runs rarely die
(blips are point events, HELM retries absorb them) but **acquires** are
maximally exposed — the minutes-long readiness wait (`require_listed`)
silently never passes once a foreign converge strips the alias, so the bug
has been presenting as the user's known "failed to acquire lease" events.

**Design deliberation.** Compared (a) naive union render (catalog ∪ live
set — still blips on live-set churn), (b) persistent route registry
(append-only file in state_dir; bytes converge to a fixed point → zero
steady-state blips), (c) user-proposed compose-roundtrip (parse deployed
config back to a catalog; elegant — state can't drift — but promotes the
rendered config to a forever parse-compatible interface), (d) dynamic
routing (already built; true zero-blip but Postgres sidecar + fleet-wide
settings flip). Landed on the registry storing *semantic inputs* (served
name/engine/host), rendered through the existing entry builders, with a
one-shot seed parsed from the live `litellm_config.yaml` at migration
(stealing the roundtrip's best property without its compatibility tax).
No opt-out flag, per repo policy.

**Artifacts.** Plan written to
`docs/planning/litellm-route-registry-plan.md` (schema, merge semantics,
`render_compose(route_registry=...)` wiring under the existing converge
flock, `routes list/prune/seed` CLI, 10 tests, GPU-host acceptance,
2-commit rollout in the infer_stack submodule). Left UNCOMMITTED — current
branch `impl/run-from-run-spec` is unrelated; commit it on its own branch
or alongside the implementation. Reminder: infer_stack has unpushed
e5fba7b (reserve-gpus); push ordering matters for the eventual gitlink.

**Design insights.** (1) When a shared artifact is rebuilt by multiple
writers, render it from accumulated shared state, never from the invoking
writer's worldview — idempotent-merge state converges to a fixed point and
byte-stable output is what suppresses restart churn. (2) Long waits
(readiness windows) are where rare races concentrate into visible failure
rates; short blips hide, long windows collect. (3) `raise_for_status()`
without printing the response body destroys the one string that names the
culprit — probe code here already does it right (body[:300]).

**Next steps.** Implement per the plan (2 commits in
submodules/infer_stack + optional TUI error-body companion); then
runbook-side `routes seed` hooks in eval_audit as follow-up. Immediate
operational unblock for the user: re-converge under
`reproduce/olmo_models_combined/config/infer_stack` to restore olmo
routes.

## 2026-07-13 12:20:00 -0400 (addendum)

**User intent.** Opus reviewed the route-registry plan (§13 appended to
`docs/planning/litellm-route-registry-plan.md`); Fable to adjudicate the
concerns and update the plan.

**Outcome.** All eight concerns accepted or verified-closed; plan is now
v2 with decisions folded into the body and §13 rewritten as a
review-record + resolution list. The consequential decisions: (A) registry
render is now *unconditional* for litellm+static — stronger than the
suggested guard; the legacy no-catalog branch is retired from the backend
path, because a catalog-less release/gc converge falling through to legacy
would re-strip routes (Opus's best catch). (C) new
`ComposeBackend.merge_route_registry()` for `routes seed`'s
out-of-converge write. (D) seed imports vLLM rows only, skips Ollama
(dns_slug isn't invertible); the `service` override field is deleted from
the schema. (E) exact row-keying rules (per served-map key; RESERVED
engine filtered). (F) unknown-version registries are preserved-as-read,
not reseeded. (H) verified benign by reading apply(): it re-reads the
on-disk file and append-only ⇒ superset, so last-writer-wins is safe.
Verified fresh this pass: catalog feeds only the entries computation in
render_compose; `ConfigModalCLI` proves nested subcommand groups exist;
ledger is shared at data_root()/leasing/ledger.db. Tests 11–14 added.

**Insight.** The reviewer's highest-value finding wasn't a flaw in the
mechanism but in its *activation condition* — the guard `catalog is not
None` quietly preserved a path to the original bug. When a fix is gated,
audit every code path that reaches the old branch, not just the one the
incident came through.

**Next steps.** Unchanged: implement commits 1–2 in submodules/infer_stack
per the plan; plan file still uncommitted (branch is unrelated
impl/run-from-run-spec).

## 2026-07-13 12:51:45 -0400

**Model / harness.** claude-opus-4-8 (Opus 4.8), Claude Code CLI in the VSCode
extension harness. Session goal (via `/goal`): implement
`docs/planning/litellm-route-registry-plan.md` on the submodule branch.

**User intent.** Implement the LiteLLM route-registry plan in
`submodules/infer_stack` — the fix for the shared-gateway 400 "Invalid model
name" incident where a cross-catalog converge stripped another runbook's still-
live routes.

**What landed.** Branched `submodules/infer_stack` off
`infer-stack-cli-api-migration` as `feat/litellm-route-registry`. Three commits:

1. *Registry core* (`compose.py`). Append-only `litellm_registry.json` in the
   shared state dir stores *semantic* route rows (`served`/`engine`/`host`), not
   rendered LiteLLM entries. Every converge merges the invoking catalog +
   every placed deployment in the full `desired` set (spans all runbooks via the
   shared ledger) and renders `model_list` from the whole registry. New pure
   fns (`_registry_incoming_from_catalog/_deployments`, `_merge_route_registry`,
   `_litellm_model_list_from_registry`, `_seed_registry_from_litellm_config`,
   `_dump_route_registry`) + `render_compose(route_registry=...)` branch (order
   dynamic→registry→catalog→legacy) + `ComposeBackend._load/_update_route_
   registry`, `merge_route_registry`. Entry building factored into shared
   `_vllm/_ollama_route_entry` so no render path drifts. 15 new tests
   (test_leasing_route_registry.py) covering the plan's items 1–8, 10–14.
2. *`routes` CLI + docs.* `RoutesModalCLI` (list/seed/prune) per the
   ConfigModalCLI nested precedent; prune shows the drop list + confirms.
   docs/litellm-gateway-routing.md rewritten; module docstring updated. CLI
   happy-path tests.
3. *TUI companion.* `_raise_for_body` folds the HTTP response body into API-tab
   errors (the missing "Invalid model name" body is what made the incident hard
   to diagnose). Tolerant of minimal fake responses.

Full submodule suite: 377 passed / 2 skipped (deterministic order).

**Design decisions worth remembering.**
- The registry path is *unconditional* for `litellm and not dynamic_routing`
  (catalog may be None → deployments-only incoming), which retired the legacy
  `_litellm_model_list` branch from the backend entirely. This is stronger than
  the review's suggested "registry-exists OR catalog" guard and eliminates the
  no-catalog blip too. Verified end-to-end with a scripted A/B/A alternation:
  olmo stays routable, config byte-stable.
- Merge preserves the *existing* version so an unknown-version registry (binary
  rollback) isn't silently rewritten to the current schema.
- loguru is `logger.disable('infer_stack')`d and doesn't reach pytest caplog —
  tests capture warnings via a temporary loguru sink (`capture_warnings`).
- `ComposeBackend.__init__` defaults `litellm=True`, so existing backend
  converge tests already exercised the litellm path; the registry became active
  for them with no assertion breakage (model_list content is equivalent; no
  backend test asserted the legacy per-model `depends_on`).

**Uncertainty / what could break.** The GPU-host acceptance run (§10) is manual
and not done — the unit layer proves render/registry logic and the incident
scenario in-process, but real docker recreate-vs-not behavior and an olmo
completion through the live gateway during a qwen model-load window remain to be
validated on a GPU host. A `test_tui.py` scroll-offset test flaked once under
pytest-randomly reordering (row_count 57≠40) — pre-existing suite isolation bug,
unrelated to the 3-line TUI change; deterministic order and 3 random-order runs
all pass.

**Left for the user (not auto-done, per policy).** The `submodules/infer_stack`
gitlink bump is unstaged — push the submodule branch first, then bump. Unrelated
`reproduce/classic_together_combined/*.sh` edits appeared during the session
(not mine) and were left untouched.

**Next steps.** Push submodule `feat/litellm-route-registry`; run the §10 GPU-
host acceptance; optionally add `reproduce/*/_lib.sh` `routes seed` preflight
hooks (§12 follow-up) once the CLI verb is on a released infer-stack.

---

## 2026-07-13 13:32:34 -0400

**Model / harness.** claude-opus-4-8 (Opus 4.8), Claude Code CLI, VSCode
extension harness. Branch `impl/run-from-run-spec`.

**User intent (session arc).** Diagnose a cascade of `classic_together_combined`
runbook failures on the ssh/GPU box, then (a) fix, (b) parallelize, (c) a deep
provenance investigation ending in "document this finding."

**What shipped (committed).**
1. `fix(freeze)` — `discovery._classify` flagged AMBIGUOUS whenever >1 corpus dir
   matched a run-entry via token-subset, even with a unique strict winner. A bare
   `bbq:...` entry is a token-subset of its own `groups=ablation_multiple_choice`
   sibling, so it matched both and freezing died. Now: unique best score (drop the
   name tie-breaker at index 2) → RESOLVED; only a genuine tie stays AMBIGUOUS
   (preserves the cross-suite-dup guard the per-era corpus view relies on). Test:
   tests/test_discovery_classify.py.
2. `feat(classic-together)` — the smoke/full drivers serialized all 6 (model×era)
   targets in bash. Serialization was unjustified: the era image is a
   container_gpus:none HTTP client, and infer-stack COALESCES two leaseholders on
   the same model onto one vLLM container (demand-refcount; verified via
   infer_stack 50_coalescing.sh), while different models QUEUE (atomic per-model
   acquire, no partial-hold deadlock). Factored run_grid_parallel + run_one_grid
   into _lib.sh; all targets launch concurrently, per-target logs under out/logs/,
   failures aggregated. Added era_corpus_view_path() (side-effect-free) + serial
   pre-creation of views so concurrent exports don't race `ln -sfn`. Control flow
   validated with stubs.

**The provenance finding (documented, NOT yet coded).** Era replay of the classic
three (gptj/neox/opt) fails the shim class preflight: run_spec names
`helm.benchmark.basic_metrics.BasicMetric` but v0.2.4 has it at
`helm.benchmark.metrics.basic_metrics`. Investigation (blob-less clone of
stanford-crfm/helm, 6298 commits) established:
- The stored path `helm.benchmark.basic_metrics` (helm prefix + FLAT) exists in NO
  commit. It's a naive `benchmark.`→`helm.benchmark.` bulk migration (at the
  src/helm/ rename c2ee966d, 2022-11-16) of the original flat `benchmark.basic_metrics`,
  preserving production-time nesting.
- Reversing it triangulates the producing code to a ~4-week window
  **2022-07-31 → 2022-08-26**: scenario nested (after "move scenarios to scenarios"
  0c8738c8, 07-31) AND metric still flat (before "Refactor metrics" 37d8707a, 08-26).
  Unreleased pre-v0.1.0 (tagged 2022-11-17).
- redpajama-3b is immune: ~v0.2.3 origin (post-refactor) → run_specs carry the
  resolvable subpackage path. That's why dev/era-tests passed and this runbook didn't.
- A v0.1.0 era image would NOT help (v0.1.0 already nested) and is infeasible anyway
  (untagged commit; v0.1.0 predates the model_deployments architecture the shim needs;
  run_benchmarking is by-descriptor not by-object).

**Where documented.** docs/helm-gotchas.md §G13 (full lineage + tables + workaround);
reproduce/classic_together_combined/README.md invariant rewritten; memory
classic-officials-carried-forward-v024-v030 + MEMORY.md updated.

**Recommended fix (option 1, deferred per user).** Self-verifying declared class-path
canonicalization in the era shim: when a flat `helm.benchmark.*` class fails
get_class_by_name AND its `helm.benchmark.metrics.*` relocation resolves (same leaf),
remap on the in-memory run_spec before preflight + scoring, recorded as a declared
substitution. Scope: docker/era_shim/helm_era_shim/replay.py (_preflight_resolve_classes
+ the decode step above it). 9 drifted classes across 735 run_specs; BasicMetric in all.

**Design insights.** (1) A stored class_name is provenance, not necessarily a runnable
import path — bulk migrations can synthesize paths that never existed. (2) Class-path
*shape* (flat vs subpackage) is a sharper origin fingerprint than release dates. (3)
blob-less clone (`--filter=blob:none --no-checkout`) is the right tool for path-history
archaeology over a slow link — trees included, blobs lazy, rename-aware `git log`.

**Loose end (not mine).** `submodules/infer_stack` gitlink shows modified; I did NOT
touch it and left it unstaged (per the no-auto-commit-gitlink rule) — flag to user.

## 2026-07-14 09:55:00 -0700

**Model/harness:** claude-opus-4-8[1m] (Opus 4.8, 1M context), Claude Code CLI.

**User intent:** The new "Headline Aggregate Score Drift" plot omits ifeval, gpqa,
and mmlu_pro for the olmo instruct models. Explain why, then fall back to the
instance-level mean and make the fallback visible.

**Root cause (verified on /data/.../virtual-experiments/olmo-models).** Not a filter,
not a failed run. Those three are capabilities-track (v1.8.0) CoT scenarios whose core
metrics are *instance-only*: ifeval → `ifeval_strict_accuracy`, gpqa/mmlu_pro →
`chain_of_thought_correctness`. HELM emits no run-level aggregate stat for them, so the
run-level comparison intersection is empty (`run_level_n: 0`, `instance_level_n: 541/446/1000`),
`core_runlevel_table.csv` is a single blank line, and `_accumulate_aggregate_diff_cells`
(which reads only that CSV) produced no cell → benchmark silently absent. bbq
(multiple_choice_joint, run-level exact_match family) populates normally, which is why it
shows. Local runs succeeded fully (541/446/1000 completions, empty_rate 0.0) — this is a
metric-shape gap, categorically distinct from recipe/env or reproducibility failures.

**Fix (3 seams).** (1) `normalized/diff.py::metric_quantiles` now attaches per-side
aggregate scores `a_mean`(official)/`b_mean`(local) to every `by_metric` entry — for
instance rows that's the mean of per-instance scores = the aggregate accuracy. Lands in
`instance_level.by_metric` of the report JSON. (2) `_accumulate_aggregate_diff_cells`
accumulates run-level and instance-level in separate buckets and merges with run-level
priority (`_finalize` tags `source`); instance-level fills only cells run-level never
produced. a↔official confirmed via `core_metric_curves._build_pair` (nrun_a=OFFICIAL).
(3) Renderers flag `source=="instance_level"` cells: "‡" in the PNG cell corner + a
subtitle footnote (only when a fallback cell is drawn), and a legend line + "‡" suffix in
both text tables. JSON sidecars carry `source` via the existing spread.

**Verification.** Unit test (new `tests/test_aggregate_diff_instance_fallback.py`): fallback
fires on empty runlevel, run-level wins when both present, missing means → no cell.
`metric_quantiles` a_mean/b_mean confirmed by direct call. Full render exercised end-to-end
(PNG visually shows ‡ on gpqa/ifeval, none on bbq; footnote present). phase3 baselines
regenerated — diff is *purely additive* (only a_mean/b_mean lines, 0 removed), which the
capture_baseline docstring explicitly permits. All affected tests green.

**Design insights.** (a) The drift plot's data contract was "run-level means only"; the
right fix widens the contract (add instance-mean fallback) rather than hacking the plot —
the aggregate of a 0/1 instance metric *is* the accuracy, so it's the same quantity, just
sourced differently. Tagging `source` keeps the provenance honest. (b) Persist derived
values where the raw rows live (metric_quantiles), not at plot time — the collector already
loads the JSON, so no new artifact needed.

**Loose end (not mine).** Existing on-disk olmo reports predate the a_mean/b_mean keys, so
the fallback won't populate until they're regenerated. `rebuild_core_report` currently fails
for them (`HELM run path is not a directory` — the local HELM run dirs under
/data/crfm-helm-audit/audit-*-full/ were pruned; the planner component is tagged
artifact_format=helm, not routed to --local-eee-root). Regenerating the olmo store (matches
the pre-existing "olmo store stale" note) is needed to see ifeval/gpqa/mmlu_pro in the real
report — out of scope for this code fix. Flagged to user.

## 2026-07-14 11:20:00 -0700

**Model/harness:** claude-opus-4-8[1m] (Opus 4.8, 1M context), Claude Code CLI.
Continuation of the same session as the entry above.

**User intent:** Audit every benchmark's headline (resolved) metric in the
aggregate score-drift plot vs what it *should* be; then fix the discrepancies.
Chose option **B** (prefer schema `main_split`, not hard-coded `test`).

**Audit method.** Parsed HELM `run_groups[].environment.{main_name,main_split}`
across all `submodules/helm/.../schema_*.yaml` (authoritative), ran the real
resolver over every `core_metric_report.json` in the audit store, cross-referenced.

**Root-cause bug found (bigger than the ask).** `headline_metric_for_benchmark`
matched the curated map / priority list (bare names) against cell metric keys —
but run-level cells are keyed by **full** HELM stat descriptions
("f1_score test on narrativeqa") while my instance-fallback cells are **bare**
("ifeval_strict_accuracy"). Bare never matched full ⇒ every run-level benchmark
silently fell to alphabetical-first; instance-fallback benchmarks hit the map.
That granularity split is exactly why the user saw narrative_qa/wmt_14 "change"
from bleu_1 after regen (their cells routed through the bare fallback). Curated
map *values* were all correct vs schema — only the resolver bypassed them.

**Fix (commit c041abb0).** Resolve on each key's bare *family* (`_metric_family`
strips ` <split> on <scenario>`), and return the representative key on the
benchmark's HELM main split (`HEADLINE_SPLIT_BY_BENCHMARK`, default `test`;
boolq/hellaswag/imdb/msmarco_*/quac/truthful_qa are `valid` — hard-coded `test`
would pick the wrong split's *number*, since test vs valid differ, e.g.
narrative_qa f1 0.597 test vs 0.661 valid). Added `wmt_14→bleu_4` (was
mis-picking exact_match ≈0 for MT via the priority list). Verified end-to-end on
era-redpajama (synthetic_reasoning_natural exact_set_match→f1_set_match). 8 new
tests (full-key resolution, split preference, preserved bare contract).

**Design insight.** Two code paths producing the same logical value at different
key granularities (full stat key vs bare id) is a latent trap for any
allowlist/curated-map match. Normalize to the family at the matching boundary,
not at each call site. Recorded as [[metric-key-granularity-runlevel-vs-instance]].

**Loose end.** Headline artifacts (PNG/JSON/txt) are cached — need regenerating
to reflect the fix. era-redpajama can be refreshed here (runs intact) but writes
to the shared /data store; olmo can't (pruned local run dirs). Flagged to user;
did NOT auto-regenerate the shared store.

## 2026-07-14 12:05:00 -0700

**Model/harness:** claude-opus-4-8[1m] (Opus 4.8, 1M context), Claude Code CLI.
Same session continuation.

**User intent:** Make the coverage matrix and the aggregate score-drift plot
comparable at a glance: (a) same model/benchmark order, (b) show which
instance-level stat the coverage grid uses to decide a "match", (c) fix the
model order being flipped between the two grids.

**Work (commits ca885180, d4bf29d7).**
1. *Shared axis order* — factored canonical ordering out of
   `_order_aggregate_diff_axes` into `order_models`/`order_benchmarks`; routed the
   coverage matrix (was pure `sorted()`) through them. Same input set → identical
   order by construction.
2. *Model-order flip* — root cause: matplotlib drift heatmaps call
   `ax.invert_yaxis()` (models[0] top); plotly coverage matrix defaulted to
   models[0] bottom. Fixed with `yaxis autorange="reversed"` (layout + static
   update_yaxes). Verified via rendered HTML figure config.
3. *Match-stat display* — the coverage cell's agreement % pools ALL of a
   benchmark's core instance-level metrics (`instance_level.agreement_vs_abs_tol`
   at abs_tol=0.05); `repro.core_metrics` carries that set. Annotated each
   benchmark column label (`_format_match_metrics`, wrapped 3/line), hover, and
   JSON (`benchmark_match_metrics`). NOTE this is a *set*, not one stat — the
   honest display lists all (narrative_qa → 6).

**Design insight.** "Same order" between two plots is two separate concerns: the
*list* order (data — shared helper) AND the *render direction* (matplotlib
invert_yaxis vs plotly default). Both must agree. Recorded the axis-order helper
as the single source of truth.

**Env note.** Hit the documented VM FD-exhaustion mid-task (escalated to Python
failing at `init_import_site`); user recycled; verification completed after.
Static JPG export needs chrome/kaleido (absent here) — HTML render + figure-config
inspection used instead; JPG will render in the real pipeline env.

**Loose ends (unchanged from prior entries).** Headline/coverage artifacts are
cached — regenerate to reflect all of today's fixes (metric resolver, axis order,
coverage annotations). era-redpajama refreshable here (writes shared /data); olmo
needs its machine (pruned local run dirs). Canonical `_MODEL_ORDER`/`_BENCHMARK_ORDER`
lists are stale for the olmo corpus (no olmo models; `narrativeqa`/`sythetic_*`
key mismatches) so the shared order is mostly alphabetical — offered to refresh.

## 2026-07-14 12:14:12 -0400

**Model/harness:** claude-opus-4-8[1m] (Opus 4.8, 1M context), Claude Code.

**User intent:** Explain why olmo-7b/mmlu shows a ~0.15 aggregate score
gap (public exact_match 0.295 vs local 0.144) despite near-exact
instance agreement; then implement a dedupe fix.

**What happened.** The 0.15 gap was not real drift. Diagnosis walked
through three artifacts in sequence: (1) the original `olmo-models`
store (Jun 23) predated planner fix a25aac9 and its local run-level
aggregate read a flat 0.0, making every subject's aggregate delta ≈ the
official accuracy; (2) the user had already regenerated into a NEW
experiment dir `olmo-models-combined` (Jul 14) where the per-subject
core reports are healthy (philosophy local 0.338 vs 0.325); (3) but the
`aggregate_score_diff` *plot* still showed 0.144 because
`_accumulate_aggregate_diff_cells` micro-averages raw
`core_runlevel_table.csv` rows and every olmo-7b subject dir carries TWO
`official_vs_local` pairs — the fresh run plus an un-demoted stale
`dr924lfhhilg` 0.0 run. 62 fresh + 62 stale-0.0 → local halved to 0.1435
(reproduced to 15 dp), while official (same on both rows) stayed 0.2955.

**Scope.** Only `allenai/olmo-7b` is polluted — across mmlu (57
subjects), legalbench (5), med_qa, openbookqa, gsm, narrative_qa. wmt_14
(olmo-7b) and every other model (olmo-1.7-7b, olmo-2-*-instruct, olmoe)
are clean single-attempt. narrative_qa's stale run is a degenerate
near-zero run (bleu_4 ≈ 1e-308), not a clean 0.0, but drops the same way.

**Fix (commit 93849b09).** Restrict both the run-level CSV loop and the
instance-level fallback loop to the canonical (first) `official_vs_local`
comparison_id per report — the same pair `_find_pair` returns, so the
drift plot now agrees with the per-pair reports and
reproducibility_rows.csv (which already dedupe). Chose "keep first" over
a "drop zeros" heuristic because (a) it matches the pipeline's existing
canonical selection rather than inventing a new policy, and (b) it's
robust to a legitimate nonzero stale like narrative_qa. Empirically 0
reports have two genuinely-distinct nonzero locals, so nothing real is
dropped; a `logger.warning` fires per dropped attempt so a future
multi-local case surfaces instead of vanishing silently. Guard is a
no-op when comparison_id is absent (older report shape) — the 3 existing
fallback tests pass unchanged; added 2 tests (run-level + instance
paths). Verified against the live store: olmo-7b/mmlu local 0.144→0.287,
diff −0.152→−0.009, n 124→62; controls byte-identical.

**Next steps / caveats.**
- The on-disk plot is stale until Stage 6 (`build_reports_summary`) is
  re-run for `olmo-models-combined` with this code — the fix changes the
  accumulator, not the already-materialized PNG/JSON.
- Residual: n=62 not 57. Five mmlu subjects (abstract_algebra,
  college_chemistry, computer_security, econometrics, us_foreign_policy)
  have TWO report dirs each — a cross-report duplication the within-report
  dedupe can't touch. Value still lands ~0.287 but the physical store
  prune (drop the stale `dr924lfhhilg`/degenerate runs + duplicate dirs)
  is the real cleanup; deferred, flagged to user.
- Better upstream fix would be the planner demoting stale attempts to
  local_repeat (or dropping them) so no consumer re-hits this.

## 2026-07-14 15:24:23 -0400

**Model/harness:** claude-opus-4-8[1m] via Claude Code.

**User intent:** Newest qwen smoke test (`ifeval:model=qwen/qwen2.5-72b-instruct-turbo`,
from-spec replay) died with `openai.BadRequestError 400 / litellm.ContextWindowExceededError`:
served model max context = 4096, request asked for 4096 output tokens → "0 characters upper
bound for 0 input tokens." Diagnose + fix.

**Root cause.** The two qwen2.5 `*-instruct-turbo` endpoints were served at
`max_model_len: 4096` (copied from the classic/OLMo per-model tuning). But those two
turbo endpoints are the *only* qwen members that replay the capabilities whitelist
(`ifeval`, `mmlu_pro`), whose **official** `run_spec.json` sets `max_tokens=4096`
(`capabilities_run_specs.py:200`, `:259`). HELM's window service (`local_window_service.py`)
truncates the *prompt* against `max_sequence_and_generated_tokens_length` but never
clamps `max_tokens` — so the adapter forwards `max_tokens=4096` verbatim. At a 4096
serving ceiling that fills the entire window; vLLM/litellm 400s because there's no room
for even one prompt token. The official Together serve ran `max_sequence_length: 128000`
(model_deployments.yaml:4193), so the overflow never surfaced upstream.

Why OLMo didn't hit it: OLMo's official capabilities rows carry `num_output_tokens=2048`
(faithful to OLMo's own spec), which fits under the 4064 reserve. gpt-oss already serves
16384. The turbo pair is the lone from-spec-4096 case on a 4096 window — a genuine
serving-config bug, not a reproducibility failure.

**Fix (scoped to the two turbo endpoints only).**
- `reproduce/qwen_models_combined/config/infer_stack/catalog.yaml`: `qwen-2-5-7b/72b-
  instruct-turbo-single` → `max_model_len: 8192`, `max_num_batched_tokens: 8192`
  (kept == max_model_len per the gpt-oss catalog convention; single-prefill ceiling).
  8192 = 4096 output + 4096 prompt headroom. Header comment documents the exception +
  the fallback (drop toward 5120 or lower `max_num_seqs` if the 72B fails vLLM startup
  on KV-cache-can't-hold-max_model_len).
- `eval_audit/integrations/infer_stack/preset_configs.yaml`: both turbo presets'
  `helm_max_sequence_and_generated_tokens_length` 4064 → 8160 (32-token reserve below 8192).

Base Qwen1.5 / qwen2-72b-instruct endpoints run only classic-core + `wmt_14` (small
`max_tokens`); left at 4096. Verified via a scan mapping every ifeval/mmlu_pro/gpqa
run_entry to its preset — only the two turbo presets carry them.

**Validation.** Both YAMLs parse; `tests/test_qwen_from_spec.py` +
`tests/test_infer_stack_integration.py` → 21 passed. No test pinned the old 4064.

**Uncertainty / next step.** 8192 on the 72B (tp=2) assumes ≥80GB cards leave a KV pool
that can hold one 8192-token sequence at startup — could not verify without the GPU. If
vLLM refuses to start, the catalog header names the fallback. Also: raising the HELM
window makes any *previously prompt-truncated* turbo row more faithful (official window
was 128k), so if full turbo data already exists it should be regenerated — but at these
short prompts nothing was being truncated, so no silent metric shift expected.

## 2026-07-15 10:05:26 -0400

**Model/harness:** claude-opus-4-8[1m] via Claude Code.

**User intent:** After the qwen window fix let the smoke grid run, the FULL
qwen-combined grid died at schedule time with
`OSError: [Errno 7] Argument list too long: 'kwdagger'` (tmux_workers=4). Fix.

**Root cause.** `kwdagger_bridge.kwdagger_schedule_argv` passed the ENTIRE params
grid inline as one argv token: `--params={request.params_text}`. The smoke grid (40
rows) fit under ARG_MAX; the full grid (~775 run rows across the 8 members) does not,
so `subprocess.run(argv)` raises E2BIG at spawn. This is `Errno 7` (E2BIG), NOT the VM's
`Errno 24` too-many-open-files issue — a genuine command-length bug that only trips at
full-grid scale, which is why it slipped past smoke. The existing FIXME already noted
`--params` accepts inline YAML *or a file path*.

**Fix.** Spill the grid to a file and pass its path at execution:
- `kwdagger_schedule_argv(request, *, params_ref=None)` — new keyword. Default keeps the
  inline text (preview/argv stay readable; every existing argv/params_text test unchanged,
  incl. the preview-vs-execute argv-equality test). `params_ref` selects the file form.
- `run_kwdagger_schedule` writes `params_text` to `<root_dpath>/kwdagger_params.yaml`
  (alongside the existing container-provenance write) and calls argv with
  `params_ref=str(that_path)`.

The `.yaml` extension is load-bearing: kwdagger reads --params via
`kwutil.Yaml.coerce(path_policy='existing_file_with_extension')`, which only treats the
value as a file when it exists AND has an extension (verified by reading the vendored
kwutil source + kwdagger/schedule.py:191). An extensionless temp file would be parsed as
inline YAML and silently mangle the grid.

**Why file-only at execution, inline at preview:** preview's `command`/`argv` are
informational and every test compares them as inline; execution is the only path that
spawns the subprocess, so it's the only one that must dodge ARG_MAX. Keeping the split
is minimal-blast-radius. (A huge grid's preview command is unusably long regardless —
acceptable; not a crash.)

**Validation.** New regression `test_run_kwdagger_schedule_spills_params_to_file`
stubs `subprocess.run`, asserts the executed argv carries a single `--params=<...yaml>`
pointing at an on-disk file whose contents == `params_text`, and that the inline grid
text never appears in argv. Full schedule group green: test_run_surface +
test_from_spec_materialized_schedule + test_lease_bracket + test_container_execution =
52 passed.

**Next step.** GPU-side: re-launch the qwen-combined full fan-out; the schedule step
should now clear. Watch that kwdagger actually loads the spilled params file (a stale
same-named file in an earlier root would be overwritten — each experiment has its own
root_dpath, so no cross-run collision expected).

## 2026-07-15 14:39:22 -0400

**Model/harness:** Claude Opus 4.8 (1M context), Claude Code.

**User intent:** Iterate the collaborative HELM-internship master reference with an
external reviewer (ChatGPT). This session: review the reviewer's consensus package
(`Master_Collaborative_Reference_Consensus_2026-07-15c.zip`), accept/reject each change
and concern, run my own review, and draft a reply — reporting consensus if reached.
Update local docs to match the consensus package.

**Outcome: consensus reached.** Accepted all of the reviewer's changes without
reservation — the evidentiary relabel ("collaborator-verified; artifact not packaged"),
report-freshness-≠-result-acceptance, the three-freshness store ledger, the three
orthogonal reproduction *targets* (artifact reconstruction / procedural / claim-level)
as orthogonal to the six-category *cause* taxonomy, non-identifiability scoped to the
surviving evidence examined, and the B4/B5/B6 structural cleanups. No rejections; the
reviewer's downgrades of my "Established"/"preservation-only" framings were epistemically
correct given the packaged-artifact standard.

**My own review contributed a real, disk-verified finding — the run-artifact acceptance
audit.** Report freshness does not imply run-artifact freshness, and the two flagship
modern stores prove it the hard way: `gpt-oss-20b-from-spec` and `olmo-models-combined`
retain **no raw run artifacts** (pruned; `scenario_state`/`display_requests`/EEE inputs
all gone) — only derived `analysis/`+`reports/` survive. So "regenerate" = **re-run**,
not a report refresh; the OLMo `olmo-7b` halving (0.295/0.144…) is baked into the
surviving aggregate whose inputs no longer exist. `era-redpajama` (both eras) is the
**only** flagship store with surviving raw runs (Jul 12) → genuinely copy+hash-able. The
non-obvious inversion: freshest reports (GPT-OSS) sit on the least-preserved inputs;
best-preserved raw runs (RedPajama) have older reports. Recorded to memory
(`flagship-store-run-artifacts-pruned`).

**Furnished evidence to promote "collaborator-verified" toward packaged:** full commit
SHA for `86ec84af` (`86ec84af09…`, tree `a3734de5…`) as the content-addressed immutable
reference, and sha256 of the GPT-OSS/OLMo/RedPajama store provenance/manifest/headline
artifacts. Governance call: did **not** export a full git bundle to the external
reviewer (private Kitware repo) — the bundle/tag belongs in the eventual artifact release
under Kitware control; the SHA is the verifiable reference.

**Design insights.** (1) When two collaborators have asymmetric evidence access, tag
live-only facts as *collaborator-verified* and close the gap by furnishing hashes/SHAs,
not by promoting testimony to "packaged." (2) A store's report timestamp is a decoy;
the acceptance question is run-artifact + comparison provenance, and pruning can make a
fresh-looking store unciteable without a re-run. (3) One soft, non-blocking rec left for
the *paper* (not the chronicle): foreground one conceptual spine (F-model → six causes →
three targets → identifiability map) and push the operational vocabularies (freshness
dimensions, workflow-status levels) to an appendix, so reviewers meet one framework.

**Repo state:** no code changes this session. Local chronology removal (prior turn) is
`7a3e728e`. Consensus tree extracted to `docs/Master_Collaborative_Reference_2026-07-15c/`
(32/32 SOURCE_HASHES verify OK); reply at
`docs/Master_Collaborative_Reference_Consensus_ACCEPTED_2026-07-15d.zip` and mirrored under
the tree's `validation/claude_consensus_acceptance/`. External-artifact zips left
untracked, matching prior rounds.

**Next step.** Operational only (agreed with reviewer): re-run OLMo and GPT-OSS then
preserve+hash; copy+hash RedPajama and the deployment-match sweeps; complete or document
the ordinary-path OLMo confirmation on held-out instances; regenerate the corpus-wide
denominator from a checked-in manifest.

## 2026-07-15 17:40:00 -0400

**Model/harness:** claude-opus-4-8[1m] (Opus 4.8, 1M context), Claude Code.

**User intent:** Jon is stepping in to help Edward. Session goal was
*orientation* plus "sync our branches to Edward's and branch off him."
The substantive work Jon wants to drive: benchmark **base Qwen 3.5**
models (an *extension*, not a reproduction — Qwen 3.5 has no public HELM
run) on the **same scenario roster** Edward uses for the Qwen 2.x/2.5
reproductions, so Qwen 3.5-vs-2.5 becomes a fair comparison under the
corrected local recipe. Later: LoRAs fine-tuned on Fable outputs
(e.g. `Achilles1089/fable-coder-35B-A3B`). Framing = "eval_audit as a
reproducibility-fixing extension of HELM; new-model results are the
value-add on top of the reproducibility study."

**Orientation findings.**
- **Branch topology.** Local `main` (55fb5ec) is **380 commits behind**
  Edward's live branch `origin/impl/run-from-run-spec` (156ffa0). All of
  his current work (from-spec replay, qwen-combined fan-out, era shims,
  TMLR plan) lives there, not on main. Created working branch
  `jons/qwen35-extension` off his tip; ran `git submodule update
  --init --recursive` to align the 4 drifted submodules (aiq-magnet,
  cmd_queue, infer_stack@v0.6.0-167 route-registry, kwdagger). Tree clean.
- **Environment.** This VM (`aivm-2404-yardrat`) is the **analysis host**:
  no `nvidia-smi`, no `/data`, no `helm`/`vllm` installed; only
  `eval_audit` importable. GPU runs execute on the **parent GPU box**
  (2 cards: Quadro RTX 8000 48GB + RTX 5000 16GB) via the containerized
  runner (`docker/build.sh` → `eval-audit-helm-runner:dev`). CPU-only
  gates (V1/V3, `08_check_discovery`, preset-load) run here; serving +
  runs (V4–V6) run there.
- **The roadmap already exists.** `docs/planning/qwen36-core-new-results-plan.md`
  (proposed 2026-07-11, **not started**) is precisely this extension:
  the `qwen-combined` fan-out shape with **one axis flipped — compute
  instead of reproduce** (no `--from-spec`, no freeze, `precomputed_root:
  null`, no `official_public_index` pairing → standalone local-only
  report). Two serving modes (thinking / non-thinking) as two
  deployments, leased fan-out (`--lease --tmux-workers 2`). Comparability
  is guaranteed by **authoring the same 9-group classic/Lite core
  scenarios** (mmlu 57-subj, commonsense, gsm, math, legalbench, med_qa,
  narrative_qa, natural_qa, wmt_14; ~85 run_entries/mode) with the model
  token swapped. Scaffolding stubs already on disk:
  `configs/local_models/qwen35_9b_vllm/` + `configs/qwen35_vllm_smoke_manifest.yaml`
  (mmlu:anatomy + boolq, max_eval_instances=5, `VLLMChatClient` →
  `Qwen/Qwen3.5-9B`) + `reproduce/qwen35_vllm/` (00–40 scripts).
- **Comparison target roster (Edward's reproduction side).**
  `qwen-models-combined` = 8 public Qwen text models (Qwen1.5
  7b/14b/32b/72b/110b-chat, Qwen2-72B-Instruct, Qwen2.5 7B/72B
  instruct-turbo), 775 run_entries, from-spec replay vs public HELM.
  README explicitly lists `qwen/qwen3.5-9b` as **out of scope (no public
  run to replay)** — i.e. reserved for exactly our extension.

**Open decisions before any GPU work (surfaced to Jon, not yet answered).**
1. **T1 — model identity.** Confirm the real HF repo(s)/id(s)/size(s) for
   "Qwen 3.5". `Qwen/Qwen3.5-9B` is a *placeholder* in the stub config and
   unverified on the Hub. Pick the size roster (9B fits the RTX 8000 at
   bf16; 35B-A3B MoE ~70GB bf16 does **not** fit 48GB → needs FP8/quant or
   offload, a later concern for the Fable-LoRA arc).
2. **Benchmark scope** — adopt the plan's 9-group core verbatim (max
   comparability with the Qwen reproduction heatmap) vs a smaller first cut.
3. **Thinking vs non-thinking** — run both modes (the plan's headline axis)
   or non-thinking only to start.
4. **Where reproduction pairing is impossible** — Qwen 3.5 has no official
   side, so the report is local-only; the scientific comparison is
   Qwen 3.5 placed beside Edward's *reproduced* Qwen 2.5 numbers in the
   same recipe frame (not HELM-pairing).

**Decisions + T1 resolution (same session).** Jon chose: single ~9B
**base** model first, smoke scope first. Web-verified: Qwen3.5 is real
(Small 0.8/2/4/9B released 2026-03-02); the base repo is
**`Qwen/Qwen3.5-9B-Base`** (`Qwen/Qwen3.5-9B` is the post-trained
variant — the old stub pointed at the wrong one). HF card: hybrid
Gated-DeltaNet + sparse-MoE (+ vision encoder), bf16 ~10B params, 262k
ctx, no thinking mode on base. Consequences: (1) **vLLM arch support is
the #1 execution risk** — verify at serve time before any wider grid;
(2) **base ⇒ completions protocol** (`VLLMClient`), matching how base
Qwen1.5 officials were served → base-3.5 vs base-1.5 is the clean
comparison (the "3.5 vs 2.5" group-chat framing is base-vs-instruct,
flag when reporting); (3) the plan's thinking/non-thinking axis
collapses to one deployment for base.

**Implemented (this session).** Jon clarified the repo is mirrored to
yardrat via virtiofs — deliverable = turn-key scripts he runs there.
Retargeted the whole `qwen35_vllm` smoke path to the base model:
- **Vendored HELM** (`submodules/helm` @ b583244f on its local `main`,
  AIQ-Kitware fork): registered `qwen/qwen3.5-9b-base` in
  `model_metadata.yaml` (base tags mirror qwen1.5-7b: no
  INSTRUCTION_FOLLOWING) + `tokenizer_configs.yaml` (explicit
  `pretrained_model_name_or_path=Qwen/Qwen3.5-9B-Base`,
  EOT `<|endoftext|>`). **Intentional submodule change** per the
  qwen36-plan §4 pattern; gitlink staged deliberately. NOTE: the
  submodule commit is local-only — **push the submodule before pushing
  the superproject branch** or the pin dangles for others.
- `configs/local_models/qwen35_9b_vllm/`: deployment renamed
  `vllm/qwen3.5-9b-base-local`, `VLLMClient` (completions, not chat);
  `start_vllm.sh` defaults `Qwen/Qwen3.5-9B-Base`, pins
  `--dtype float16` (RTX 8000 is Turing sm_75, no native bf16 — the
  downcast must be an explicit recorded choice, not vLLM's silent one)
  and `CUDA_VISIBLE_DEVICES=0` (the 48GB card); `validate_vllm.py` now
  exercises the completions API (chat would hit a nonexistent template);
  `verify_run_artifacts.py` expects the base ids.
- `configs/qwen35_vllm_smoke_manifest.yaml`: run_entries →
  `model=qwen/qwen3.5-9b-base`, experiment
  `audit-qwen35-9b-base-vllm-smoke`.
- `reproduce/qwen35_vllm/05_check_registration.sh` (NEW): CPU preflight
  asserting the venv's helm resolves the -base metadata + tokenizer —
  catches a pip-crfm-helm venv early instead of failing late at
  tokenizer resolution.
- `reproduce/qwen35_vllm/README.md` (NEW): yardrat step-by-step,
  success criteria, the vLLM-arch and Turing-fp16 caveats, and the
  bridge to the full core grid (qwen36 plan, single non-thinking mode).

Validation: `bash -n` all scripts, `py_compile` both .py, yaml loads +
targeted assertions on the new registrations. GPU-side validation
(V4/V5 analogues: serve, one completion, smoke run) is Jon's on
yardrat — this VM has no GPU/vllm/helm.

**Next steps.**
- Jon runs `reproduce/qwen35_vllm/{00,05,10,15,20,30,40}` on yardrat.
  First real gate: does yardrat's vLLM load the Qwen3.5 architecture?
  Second: does the venv helm see the -base registration (05)?
- If smoke passes: author the ~85-entry classic/Lite core compute grid
  per `docs/planning/qwen36-core-new-results-plan.md` (single
  non-thinking member since base), local-only virtual experiment,
  report beside the reproduced Qwen1.5/2/2.5 numbers.
- Later arcs: post-trained Qwen3.5-9B (chat), fable-coder-35B-A3B LoRA
  (needs quantization for 48GB).
- Then execute the plan's checklist (§14): add `precomputed_root` +
  `max_eval_instances` params to `_build_combined_preset`; author the
  compute member presets + core run_entries (model-swapped from any
  `qwen/*` row in `official_public_index.csv`); infer-stack catalog with
  the 2 endpoints; port `reproduce/qwen3_5_core/`; add the local-only
  virtual-experiment yaml; register the new ids in the vendored HELM
  `model_metadata.yaml`/`tokenizer_configs.yaml` if not already resolvable;
  V1/V3 CPU gates here, then V4–V6 on the GPU box.
- Nothing was executed on GPUs this session; no runs launched.

**Addendum (same session, ~18:20): vendored-HELM registration replaced by
prod_env registry sidecars.** Jon pushed back on editing HELM: "Can we avoid
changes to HELM via registering custom deployments / custom models? Doesn't
HELM let you do that?" Investigation: (a) HELM natively reads
model_metadata.yaml + tokenizer_configs.yaml + model_deployments.yaml from
--local-path (config_registry.register_configs_from_directory) — the sidecar
mechanism Jon remembered; (b) BUT no support existed in our stack: magnet's
prepare_local_helm_config shipped ONLY model_deployments.yaml (its docstring
said so), and no magnet branch has the passthrough (grepped every remote tip);
(c) what Jon half-remembered as "we did this before" is bundle_export's
_assert_helm_aliases_exist — which is the OPPOSITE (it requires registration
in the VENDORED helm yamls; the qwen36 plan §4 blessed that pattern). All
prior local models were upstream-registered, so only deployments plumbing was
ever needed; qwen3.5-9b-base is the first net-new id.

Built the sidecar passthrough end-to-end:
- **magnet @ 312c894** (branch `jons/prod-env-sidecar-configs`, based on the
  pinned 50489f0 — NOTE magnet main and Edward's pin have DIVERGED; do not
  base on main): prepare_local_helm_config copies optional
  model_metadata.yaml / tokenizer_configs.yaml under canonical names; both
  materialize CLIs expose + manifest-record the fpaths; both are
  identity-bearing algo_params on MaterializeHelmRunNode. Unit tests added.
- **eval_audit**: ManifestSpec fields, kwdagger_bridge matrix keys
  (helm.model_metadata_fpath / helm.tokenizer_configs_fpath) +
  _resolve_manifest_override_path, docker pipeline mounts both :ro (docker
  nodes inherit the algo_params via spread — one magnet edit propagates).
- **configs**: model_metadata.yaml + tokenizer_configs.yaml sidecars next to
  the qwen35 deployment yaml; smoke manifest wires all three fpaths AND the
  now-required container knobs (container_image eval-audit-helm-runner:dev,
  network host for host-side vLLM, container_gpus none — the bare host-venv
  pipeline was REMOVED on this branch, the old runbook predated that).
- **05_check_registration.sh** rewritten: pure-yaml consistency check of
  sidecars vs deployment vs manifest (no helm import needed — with sidecars,
  neither the venv helm nor the baked container helm needs the ids).
- **Reverted the vendored-helm registration**: submodule main reset to
  origin/main e9bd720d; b583244f abandoned deliberately (recoverable via
  reflog; content preserved as the sidecar yamls).

Validation: magnet sidecar tests 3/3; eval_audit schedule group
(test_run_surface + from_spec_materialized_schedule + lease_bracket +
container_execution) 52/52 incl. the qwen35 test extended to assert both new
matrix keys resolve to absolute paths. Ran in a throwaway uv env in the
scratchpad (this VM has no pytest/kwdagger; Jon's real env is on yardrat —
per his preference, install into HIS top-level venv, never make a repo
.venv).

Also noted: the magnet submodule working tree had drifted to magnet main
(47614e7) — not the pin; restored before branching. Push order when
publishing: magnet branch first, then the superproject (helm needs no push —
its gitlink is back at upstream e9bd720d).

**Addendum 2 (same session, ~19:00): qwen35 runbook ported to the
infer-stack lease shape.** Jon ran the runbook on yardrat and hit
`vllm: not found` from the hand-rolled `start_vllm.sh`, then correctly
called out: "we should be using infer stack, not our own hosted vllm."
The old reproduce/qwen35_vllm was a pre-fan-out stub. Rebuilt it as a
single-model port of reproduce/qwen_models_combined with ONE axis
flipped — compute instead of reproduce (the qwen36-plan shape):

- **Preset** `qwen35_9b_base_vllm` (preset_configs.yaml): profile
  `qwen3-5-9b-base-single`, deployment `vllm/qwen3.5-9b-base-local`,
  protocol_mode completions, reserve 4064, COMPUTE manifests
  (precomputed_root null, authored run_entries, no from-spec), container
  keys (network host / hf_cache / gpus none). Full grid = PLACEHOLDER
  (smoke pair @1000) until the ~85-entry core list lands.
- **Sidecar support in bundle_export.py**: preset-level
  `model_metadata_fpath`/`tokenizer_configs_fpath` (a) widen
  `_assert_helm_aliases_exist` (builtin ∪ sidecars — the same union
  helm-run sees after prod_env copy; without this the export would fail
  since we reverted the vendored registration), (b) flow through
  `_manifest_doc` into both generated manifests (emitted only when set —
  existing manifests stay byte-identical).
- **Runbook**: runbook-local config/infer_stack/{catalog,settings}.yaml
  (dtype float16 pinned — Turing sm_75 has no bf16; protocol completions
  or the readiness probe polls /chat/completions forever), _lib.sh
  (QWEN35_*, PYTHON_BIN falls back python→python3), preflights 00/05
  (preset↔sidecar consistency)/06 (endpoint in catalog)/07 (image +
  stale-digest probe), 10_run_smoke.sh + 15_run_full.sh (gc → bootstrap
  gateway for the master key → export compute → eval-audit-run --lease),
  40 via $PYTHON_BIN. Deleted 10_start_vllm/15_validate/20_preview/
  30_run + configs/qwen35_vllm_smoke_manifest.yaml + the hand-written
  model_deployments.yaml/start_vllm.sh/validate_vllm.py (deployments are
  GENERATED by the exporter now; registry sidecars stay).
- **Tests**: test_run_surface qwen35 test → tmp-manifest sidecar
  propagation test (old fixture file deleted); 2 new bundle-export tests
  (sidecars forwarded + compute shape; widened assert still rejects
  unregistered ids). 72 passed + 20 infer-stack integration passed.

Key insight for the paper/tooling story: with sidecar support in the
exporter, "add a net-new model" = one preset block + 2 sidecar yamls +
1 catalog endpoint — no HELM edit, no image rebuild. That is the
extension mechanism the LoRA arc (fable-coder-35B-A3B) will ride.

Next on yardrat: 00→05→06→07→10. First real gates: vLLM loading the
Gated-DeltaNet hybrid arch; the LiteLLM data_dir being docker-mountable.

**Addendum 3 (same session, ~20:00): TUI startup forensics + async-startup
rule enforced in infer_stack.** Jon reported `infer-stack tui` taking >7s
to first frame. Forensics: wrote
`dev/oneoff/profile_infer_stack_tui_startup.py` (phase-bisects the exact
TuiCLI.main pre-frame sequence). On yardrat it measured 0.666s total — so
the 7s was NOT steady-state code cost. Explanation: cold start (first run
after run_developer_setup byte-compiles the whole import chain incl.
textual through a cold page cache) — and the profiler run itself warmed
exactly that chain, which is why "slow before, fast after". History
confirms Jon's compose hypothesis was right for OLD versions: the async
first-paint fix ("kick the docker observe to the worker") landed
2026-06-30 (infer_stack 680926e "TUI improvements"), absent from the
v0.6.0-5 tree this repo pinned at session start, present on
infer-stack-cli-api-migration (which Jon switched the submodule onto; tip
== Edward's pin 22d9431, origin/main is BEHIND it).

Jon then set the design rule: subprocess waits must never gate startup,
and the dependent component must SAY it's loading. Tiering agreed:
subprocess (docker/nvidia-smi) = async + loading note; bounded local-file
reads (catalog yaml ~27ms, settings) = OK sync. Implemented in
**infer_stack @ 0e04b01** (branch `jons/tui-async-startup` off 22d9431):
- ComposeBackend.inventory → lazy property; _make_backend passes None
  (the one remaining pre-frame subprocess — nvidia-smi — now deferred to
  first placement, worker-side under the TUI).
- hardware._run: 20s timeout (wedged driver used to hang forever).
- TUI: '(loading…)' placeholder rows in #ps/#gpus until their first poll
  lands (deterministic via diff-cache sentinel); docker/deployments
  titles say 'observing…' pre-first-observe instead of lying '0 running'.
- 4 new tests; full infer_stack suite 381 passed / 2 skipped.

Gitlink NOT yet bumped in the superproject — the submodule branch should
be pushed + ideally PR'd upstream (this is generic infer-stack
improvement, not eval_audit-specific). Flag to Jon/Edward before folding
into the superproject pin.

**Addendum 4 (same session, ~21:00): first live smoke failed on a
two-worlds master-key mismatch — fixed by pinning the infer-stack world
through the lease bracket.** Jon attached the cmd_queue cache to the VM.
The failed job log showed the FULL pipeline working (lease acquired, vLLM
v0.19.1 served Qwen3.5-9B-Base, containerized HELM sent real mmlu
prompts) until every request got LiteLLM **400 "No connected db."** —
LiteLLM's response when the presented key isn't its master key (it then
attempts a virtual-key DB lookup with no DB attached).

Root cause: TWO infer-stack worlds on yardrat. The runbook shell (with
_lib.sh's INFER_STACK_CONFIG_DIR/DATA_DIR exports) resolved
/data/service/infer-stack; the scheduled cmd_queue tmux job — a FRESH
LOGIN SHELL that inherits none of those exports — resolved Jon's
pre-existing global world /data/service/docker/infer-stack (evidence: the
job's acquire logged "seeded 22 vLLM route(s)" = his global gateway
config, and compose rendered under /data/service/docker/...). Both worlds
converge the SAME docker compose project (same container names, port
14042), so the job-side converge re-created the gateway with world-B's
managed master key while the bundle carried world-A's → every request
401-shaped into "No connected db." infer_stack's data_root() resolution
(env > settings.yaml-in-config_root > XDG) makes this environment-
dependent by design; the bracket already threaded --catalog explicitly
for exactly this class of reason, but not the world dirs.

Fix (superproject): thread the WORLD like the catalog —
- serving_facts: `_infer_stack_data_root()` helper (resolves the
  export-time process's view).
- bundle_export: `_lease_facts` emits `lease_config_dir` +
  `lease_data_dir` (resolved at export time next to lease_catalog);
  flows into both generated manifests via the existing lease_facts
  doc.update.
- kwdagger_bridge.build_broadcast_lease_knobs: forwards both as
  helm.lease_* matrix keys.
- lease_bracket: LEASE_KEYS += both; acquire, release, AND the
  leases-snapshot (ledger lives in data dir) render
  --config-dir/--data-dir (verified all three verbs inherit
  _PathOverridesMixin and _open_controller applies the overrides).
  Manifests without the keys render byte-identical brackets.

Tests: bracket world-pin rendering (acquire/release/snapshot + absent
case), bridge broadcast, export bakes the world into manifests.
Group: 98 passed, 1 skipped.

Note for Jon on next run: the whole flow now coheres to the RUNBOOK
world (/data/service/infer-stack per the shipped settings.yaml). The
converge will re-render the shared gateway containers from that world —
his global 22-route gateway config gets replaced while the run holds it
(same containers). If he prefers the runs to ride his existing world
instead, `export INFER_STACK_DATA_DIR=/data/service/docker/infer-stack`
before 10 — _lib.sh's env-wins resolution + the export-time capture then
pin THAT world everywhere, coherently.

**Addendum 5 (same session, ~22:00): SMOKE PASSED — first HELM-shaped
Qwen3.5-9B-Base numbers — plus a real finding on boolq.** After the
v0.25.1 tag fix + stale-group evict, 10_run_smoke.sh landed both runs.
Verified from the VM (results attached at /data/crfm-helm-audit): both
run_specs record model=qwen/qwen3.5-9b-base +
model_deployment=vllm/qwen3.5-9b-base-local; all three registry sidecars
present in prod_env (the sidecar mechanism worked end-to-end in
production); world pins held (everything under /data/service/infer-stack).

Results: mmlu:anatomy exact_match=1.0 (4/4). boolq exact_match=0.0 (0/5)
— but forensics show the model returned EMPTY strings: raw vLLM cache
(prod_env/cache/vllm.sqlite) shows first generated token = "\n\n"
(logprob -0.28, ~75%), stop='\n' matches inside it, completion truncates
to ''. Prompt is well-formed (5 inline "Answer: Yes/No" shots).
Tokenizer exonerated (HF probe: add_special_tokens adds nothing; no
BOS; eos=<|endoftext|>). Two hypotheses: H1 the model style-prefers
"Answer:\n\nYes" (content fine; HELM's canonical stop zeroes it — a
deployment-boundary artifact in the paper's grade vocabulary); H2
fp16-on-Turing numerics distort the GDN state kernels (fp32 ablation
fits the 48GB card: 9B*4B=36GB). Wrote
dev/oneoff/qwen35_boolq_probe.py — acquires nothing itself; Jon leases
the endpoint, it replays the canonical recipe AND an unstopped variant
on 2 unambiguous cases and prints an H1/H2 verdict.

This is a textbook instance of the project's central taxonomy operating
on a NET-NEW model: a 0.0 cell that is not model weakness but recipe/
substrate interaction, caught by instance-level forensics.

**Addendum 6 (same session, ~23:30): probe VERDICT = H1 (style), plus the
cold-start economics fully measured.** The discriminating probe ran against
the live endpoint: content is CORRECT behind the leading "\n\n" on all
cases — fp16-on-Turing numerics exonerated; Qwen3.5-9B-Base simply answers
paragraph-style ("Answer:\n\nYes") even with 5 inline few-shot examples,
and HELM's canonical stop=['\n'] truncates every completion to '' → the
0/5 boolq cell is a RECIPE-FORMAT ARTIFACT, not model failure. (In the
paper's grade vocabulary: deployment-boundary/recipe artifact.)

Cold-start accounting (2026-07-16, RTX 8000, vllm v0.25.1, fp16):
- total cold start 22:52:41 → 23:26:49 ready ≈ 34 min
- init engine 1937s, of which: VL encoder-cache profiling ~26 min (the
  dominant tax — max-size VIDEO forward on TORCH_SDPA, sm_75 has no FA),
  torch.compile 87s, warmup run 289s, cudagraph capture 4s, mm warmup 31s
- cache mounts verified working: vllm-cache 268M (AOT-compiled model
  saved), triton-cache 380K; torch-cache ~empty (inductor artifacts live
  under vllm's torch_compile_cache)
- mitigations landed for next start: --limit-mm-per-prompt
  '{"image":0,"video":0}' (kills the 26-min profiling outright) + compile
  cache reuse (kills the 87s) → expected <5 min, TO BE VERIFIED on the
  next acquire.

OPEN DECISION (for Jon): how to score generation tasks for
paragraph-style models. boolq isn't in the planned ~85-entry core, but
narrative_qa / natural_qa (short-answer generation with '\n' stops) carry
the same hazard. Options sketched: (1) run canonical + newline-tolerant
variants and report the delta as quantified format-sensitivity (keeps
canonical comparability, most on-thesis); (2) canonical only, document
zeros; (3) tolerant only (breaks strict comparability). Tolerant variant
mechanically = a client-shim that strips leading newlines before applying
stops (the NullSafe-client pattern), declared via deployment name.

**Addendum 7 (same session, ~00:15): Option A implemented — the
newline-tolerant completions client (declared substitution).** Jon chose
the client-shim mechanism. Landed:
- `helm_clients.py`: `_NewlineTolerantCompletionsMixin` +
  `NewlineTolerantOpenAICompletionsClient` (gateway transport) +
  `NewlineTolerantVLLMClient` (vllm-direct). Fires ONLY when the request
  carries a "\n" stop (MC max_tokens=1 shapes pass through
  byte-identically); relaxes the stop server-side (+4-token budget),
  then client-side lstrips newlines, re-applies the ORIGINAL stops, and
  truncates tokens/logprob consistently (straddling tokens kept whole —
  metrics score text; tokens are supplementary).
- Wiring: preset/profile knob `newline_tolerant: true` →
  `_benchmark_client_class(..., newline_tolerant=)` selects the shim;
  `_model_deployment_entry` REFUSES the knob unless the deployment name
  carries an 'nlstrip' marker (the run_spec's model_deployment is where
  the substitution must be visible) and rejects chat protocol
  (completions-only hazard).
- Tests: 8 shim-semantics units (boolq-shape recovery, request
  relaxation, inline no-op, pass-through, multi-stop earliest-wins,
  straddling token) + export wiring (client class, marked-name
  enforcement, chat rejection). 32+75 green.

NOT yet enabled anywhere: the qwen35 preset stays canonical. Enabling =
add `newline_tolerant: true` + rename model_deployment_name to
'vllm/qwen3.5-9b-base-nlstrip-local' in the preset (+ docker/build.sh
rebuild so the container's baked eval_audit has the new class). Decision
on WHICH tasks get it awaits the narrative_qa/natural_qa probes.

**Addendum 8 (2026-07-17, ~00:45): vLLM compile-cache poisoning — a
first-class unrecorded-substrate wart, now fixed structurally.** The
Option-A validation smoke crashed at engine init: AttributeError
"'NoneType' object has no attribute 'size'" INSIDE the AOT-compiled
graph loaded from cache. Root cause: **vLLM's compile-cache key omits at
least `limit_mm_per_prompt`** — the cache hash (71a3a486…) was IDENTICAL
before and after we disabled the vision modalities, so the engine
reloaded a graph traced under multimodal-enabled inputs and fed it None
where a tensor was baked in. We got the LOUD failure mode; the quiet one
is the scary one: **a serving-config change silently reusing compiled
artifacts from a different config could produce wrong numerics with no
error at all.** For a reproducibility project this is a textbook
substrate wart — the compiled-graph provenance is invisible to the
run_spec, invisible to vLLM's own cache key, and (before today)
invisible to our serving records.

Wart taxonomy entry: "compiled-artifact cache keyed narrower than the
config space that shapes the artifacts." Mitigation landed in
infer_stack @ 6616c51 (branch jons/tui-async-startup): the vLLM
compile-cache mount is now keyed
`<vllm_cache>/cfg-<sha256(image+command+generation-env)[:12]>` — any
serve-arg change (including deliberately non-structural extra_args like
--limit-mm-per-prompt) starts a fresh cache dir; identical configs keep
full reuse. Cache-hit economics measured in the same log: graph load
2.5s + torch.compile 11.6s total (vs 87s cold) — the mount works.
Disk cost: one ~270MB subdir per distinct serve config; no GC yet
(acceptable; note if it grows). Upstream-worthy: vLLM should widen its
cache key; our per-config mount is defense-in-depth we keep regardless.

Also worth recording the epistemics: this was caught ONLY because the
config change happened between two runs sharing a cache — a fresh
machine would never see it, and a long-running grid would. Cross-machine
reproduction runs are implicitly also cache-freshness controls.

Full suite 384 passed / 2 skipped. Superproject gitlink bump to follow
with the smoke-validation commit. Jon's next action: infer-stack gc,
re-run 10_run_smoke.sh — the keyed mount gives the mm-limited config a
fresh cache automatically (no manual sudo rm of root-owned cache files).

**Addendum 9 (2026-07-17, ~01:15): Option A PRODUCTION-VALIDATED.** The
re-run smoke (fresh keyed compile cache, mm-limits active, shim baked in
the runner image) landed clean:
- boolq exact_match 0.0 -> **0.8 (4/5)** under the newline-tolerant
  client; raw completions are clean 'Yes' strings (strip+re-stop worked
  exactly as designed).
- mmlu:anatomy stays 1.0 — the MC shape passed through the shim
  untouched, as designed (per-request self-scoping confirmed in prod).
- Both run_specs record model_deployment=
  vllm/qwen3.5-9b-base-nlstrip-local — the substitution is DECLARED in
  the artifact of record.
- The single boolq miss is a REAL model behavior: the base model emitted
  '<think>' on one instance — Qwen3.5 pretraining contamination with
  reasoning-format data surfacing on a plain completions prompt. With
  the recipe artifact removed, the residual failure is honest signal;
  this belongs in the extension study's findings (base-model
  thinking-tag leakage rate is measurable per task).

The full mechanism stack is now proven end-to-end on GPUs: registry
sidecars -> world-pinned leasing -> keyed compile caches -> mm-limited
serving -> newline-tolerant declared-substitution client -> verified
artifacts. Next unit of work: author the ~85-entry classic/Lite core
grid (qwen36 plan §6.1) in the preset's full_manifest and run the first
real breadth batch.

**Addendum 10 (2026-07-16, overnight prep): 86-entry full grid authored
+ batch hardening (commit e78676d).** Jon asked to prepare the full run
for overnight. Model: claude-opus-4-8[1m] (Claude Code). Three design
decisions worth recording:

1. **The grid is lifted, not authored fresh.** The 85 core run_entries
   are copied verbatim from the qwen-1-5-7b preset's full grid (sed on
   the model token only). Rationale: the qwen36 plan §6.1 observed the
   core specs are identical across every reproduced Qwen grid — that
   identity IS the comparability claim. Any hand-authoring risk
   (different mmlu variants, dropped eval_split/groups suffixes) would
   silently break run-key pairing with Edward's grids. The 5 "duplicate"
   mmlu subjects (plain + eval_split=test,groups=… variants) are kept
   deliberately: they mirror the classic-vs-Lite duality in the official
   corpus and cost pennies. boolq rides along at n=1000 as the
   '<think>'-leakage probe (1/5 in the smoke; n=1000 makes it a rate).

2. **workers=2 is a leasing-correctness knob, not a throughput knob.**
   Reading controller.desired_deployments() showed `reclaim: stop` +
   refcount 0 => the converge stops vLLM. With 1 worker, EVERY gap
   between consecutive runs bounces the server: ~86 cold cycles, hours
   of churn. Two workers' overlapping acquire/release brackets keep
   refcount >= 1 across the whole batch (design §4 coalescing) — the
   deployment stays up end to end without touching the reclaim policy,
   so a crashed batch still frees the GPU via gc/TTL. Alternative
   considered and rejected: keep-warm in the catalog (leaves the GPU
   held after a crash, needs an explicit evict step).

3. **lease_ttl 8h: the TTL must exceed the RUN, not the cold-load.**
   The soft TTL is a crash backstop, but a healthy run that outlives it
   gets reclaimed mid-run. 4h is probably fine for every entry; 8h is
   free insurance on a single-model overnight batch (a leaked lease
   blocks nothing — same-endpoint acquires just ref-count on top).

Also: 40_verify_artifacts.sh now sweeps an experiment root with a
pass/fail tally (validated 2/2 against the real smoke artifacts from
this VM — verify_run_artifacts.py is stdlib-only, so it runs even where
the venv is absent); local-only virtual experiment yaml
(qwen35-9b-base-core.yaml, no official_public_index source, per plan
§9). NOTE this analysis VM currently has NO python env with eval_audit
installed (recycled) — YAML/structural validation done here; the
import-level preflights (05/06) re-run on yardrat as part of the
runbook anyway. Uncertainty worth watching on the first overnight: (a)
whether any single run approaches the TTL (narrative_qa n=1000 is the
candidate), (b) whether two concurrent HELM containers contend on the
shared hf_cache dir during first-time dataset downloads (the combined
runbook ran this shape without issue), (c) legalbench/med_qa/wmt_14
first-run dataset downloads need network from the container
(--network host, same as smoke). Resume semantics: force-rerun is OFF
for full — re-running 15_run_full.sh after any interruption skips
completed runs.

Next: Jon runs reproduce/qwen35_vllm/15_run_full.sh on yardrat
overnight; morning-after checklist = 40_verify_artifacts.sh (no arg =
full-suite sweep), then the qwen35-9b-base-core virtual experiment for
the report beside the reproduced Qwen 1.5/2/2.5 numbers.

## 2026-07-17 09:40:46 -0400

**Model/harness:** claude-opus-4-8[1m], Claude Code (VSCode extension).

**User intent:** The overnight full run finished 20 pass / 66 fail. "Are
the failures logged to disk?" → then: "Drop math and natural_qa. Fix our
code, a working rule is that we should never modify HELM."

**Failures are fully logged.** Each job writes a bundle under
`$RESULTS_ROOT/audit-qwen35-9b-base-vllm-full/helm/helm_id_*/`:
`cmd_stderr.txt` (parent-shell traceback for errors before HELM's logger
is up), `helm-run.log` / `helm-run.debug.log` (in-HELM errors),
`cmd_stdout.txt`, `job_config.json` (the run_entry). A failed job = no
`benchmark_output/runs/**/run_spec.json`. Triage CLI:
`python -m eval_audit.cli.summarize_experiment_failures <experiment-root>`
(needs the eval_audit env; the recycled analysis VM lacks `scriptconfig`/
`kwutil`, so I read the logs directly).

**Root-cause split of the 66 (this is the reproducibility-vs-environment
distinction the paper hinges on):**

- **57 × mmlu — OUR recipe bug, now fixed.**
  `TypeError: get_mmlu_spec() got an unexpected keyword argument 'groups'`.
  The overnight grid was lifted verbatim from the `qwen-1-5-7b` grid, which
  is a **from-spec reproduction**. In from-spec mode the
  `…,eval_split=test,groups=mmlu_<subject>` tokens are official-run-NAME
  matcher metadata — HELM replays a frozen `run_spec.json` and never calls
  `get_mmlu_spec(**args)`. This preset is **compute** (no `--from-spec`), so
  every run_entry is parsed and handed straight to `get_mmlu_spec(**args)`,
  whose signature is `(subject, method=…)` — it rejects both `eval_split`
  and `groups` (confirmed against `submodules/helm/.../lite_run_specs.py`).
  The 5 mmlu entries that PASSED were the short-form duplicates already in
  the grid. Fix: collapse mmlu to the **57 canonical compute-form subjects**
  (`mmlu:subject=X,method=multiple_choice_joint,model=…`), dropping the
  5 short+long duplicate pairs to one each. **HELM untouched** — the rule
  held; the bug was entirely in the authored grid.

- **7 × math + 2 × natural_qa — data-access barriers, DROPPED per Jon.**
  math: `DatasetNotFoundError: 'hendrycks/competition_math'` (pulled from
  the HF Hub). natural_qa: `HTTP Error 403: Forbidden` on the source
  download. These are filter reasons, not reproducibility failures; removed
  from the grid with a note to re-add the 7+2 entries if the datasets
  return.

**Grid now 72 entries** (was 86 lines / 85 nominal): boolq 1, commonsense
1, gsm 1, legalbench 5, med_qa 1, **mmlu 57**, narrative_qa 1, wmt_14 5.
Verified via raw-YAML family count (loader needs kwutil, absent here).

**Design insight worth keeping:** a from-spec grid and a compute grid are
NOT interchangeable even for the "same" benchmark. The from-spec run_entry
is a *lookup key into the official corpus* (carries name-metadata like
`groups`/`eval_split`); the compute run_entry is a *constructor call*
(`run_spec_function(**args)`). Lifting one into the other silently smuggles
lookup-only kwargs into a constructor that rejects them. Any future
"port a reproduced grid to a compute extension" must strip run_entry args
down to what the `@run_spec_function` actually accepts.

**Files touched:** `preset_configs.yaml` (qwen35 full_manifest run_entries
+ description + block comment), `reproduce/qwen35_vllm/README.md`,
`15_run_full.sh`, `_lib.sh` (72-job comment),
`configs/virtual-experiments/qwen35-9b-base-core.yaml` (count/description).

**Next:** Jon re-runs `15_run_full.sh` on yardrat (force-rerun OFF → the
20 good runs are skipped, the 57 corrected mmlu + everything else runs
fresh; the old failed helm_id_* dirs are inert — a failed job left no
run_spec.json so the skip check re-runs it). Morning-after:
`40_verify_artifacts.sh` (expect 72 passes), then the qwen35-9b-base-core
virtual experiment.

### Addendum (same day, 12:10) — VRAM-aware placement plan written into infer-stack

Model switch mid-session: this addendum and the infer_stack work are
claude-fable-5[1m] (Fable), not Opus.

Planning for the small Qwen3.5 models (0.8B/2B/4B on yardrat's 16GiB RTX
5000) surfaced that infer-stack placement is count-based first-fit with no
VRAM awareness — Opus's draft plan compensated with per-runbook
INFER_STACK_ALLOWED_GPUS pinning, which Jon rejected: he wants infer-stack to
know that "a request for a particular endpoint can only be satisfied by
certain GPUs." The chosen design (catalog-declared placement.min_vram_gib +
eligibility filter + most-constrained-first + best-fit in plan_placement,
capacity-subtraction internals to leave co-hosting open) is written up as
infer_stack docs/planning/vram-aware-placement.md — objective stated first,
rejected alternatives recorded (pinning, undocumented gpu_indices, SLURM),
submodule commit 1679f8e on jons/tui-async-startup; this superproject commit
is the intentional gitlink bump. HF research that fed it: all three small
sizes exist as both -Base and post-trained; all are DENSE hybrid-GDN
(9B too — its sidecar's "sparse MoE" description is wrong, fix pending);
same Qwen3_5ForConditionalGeneration arch + vision_config as the 9B, so same
vLLM image + --limit-mm-per-prompt {"image":0,"video":0}. fp16 weights:
1.7/4.5/9.3 GB (all fit 16GiB); 9B is 19.3 GB (GPU-0 only — which is exactly
why placement must be eligibility-aware).

Deferred, per Jon: base-vs-instruct variant choice, runbook packaging, and
implementation green-light (Phase 0 = tests first).

### Addendum 2 (same day, 14:45) — VRAM-aware placement Phases 0–4 + the small-family runbook

Fable (claude-fable-5[1m]) continuing. Jon green-lit implementation in two
steps ("do phase 0, 1, and 2", then "do phase 3 and 4 now"), with the
directive that the smalls get their own runbook (9B results mostly done) and
that the small batch exercises the everything-eligible path: "when all your
GPUs are big enough to fit the models you use all your GPUs."

**infer_stack side (commits 16f6bb9 Phases 0–2, 06f2ec2 Phase 3):**
eligibility-constrained placement per docs/planning/vram-aware-placement.md.
Catalog `placement: {min_vram_gib}` (strict-keyed, vllm-only); planner
eligibility + most-constrained-first + best-fit for DECLARED deployments,
byte-identical legacy behavior for undeclared; heterogeneous
simulate_inventory ('48,16' = yardrat); vram.py (vLLM profile parser, weight-
bytes floor from HF cache, measurements overlay, OOM classifier); plan-time
enrichment (declared > measured > floor); guided OOM error naming the exact
`infer-stack measure <ep> --record` fix; kubeai warn-and-ignore. Two
backward-compat subtleties caught and test-pinned: (1) undeclared deployments
keep index-order selection so pre-declaration catalogs never move; (2) the
auto-enriched floor gates ELIGIBILITY only — downloading weights must never
flip an undeclared deployment into best-fit. 376/376 suite green.

**eval_audit side (this commit):** the qwen35_small_vllm arc.
- Preset `qwen35_small_vllm`: combined 3-model COMPUTE preset (profiles ×3,
  per-profile nlstrip/completions facts), 216 full entries = 3 × the 9B's
  corrected 72-entry core token-swapped with inline model_deployment= lease
  keys, GROUPED BY MODEL (reclaim:stop + refcount coalescing ⇒ 3 cold starts
  total, not per-entry thrash); 6-entry smoke. One sidecar pair registers all
  three ids (configs/local_models/qwen35_small_vllm/) — and the descriptions
  are architecture-CORRECT (dense hybrid-GDN; the 9B sidecar's "sparse MoE"
  error is not repeated; fixing the 9B's own file is still pending).
- Runbook reproduce/qwen35_small_vllm/: port of the 9B runbook, QWEN35S_*
  names, THREE endpoints, and the point of the exercise — NO
  INFER_STACK_ALLOWED_GPUS anywhere; the catalog declares min_vram_gib
  best guesses (4 / 7 / 13 GiB; measurement-is-optional per Jon: wrong-low
  fails guided, wrong-high just wastes a card). 06_check_profiles enforces
  the declarations exist (an undeclared endpoint is a config regression in
  this runbook). 40_verify checks the per-run model↔deployment PAIRING
  (a 2B run claiming the 4B deployment is dirty).
- Virtual experiment configs/virtual-experiments/qwen35-small-core.yaml
  (local-only, no official side, same scoping family as the 9B's).

**Validated end-to-end against the REAL new planner** (scratch env with
Phase 0–3 infer_stack installed): the shipped catalog loads through
Catalog.load (placement validation active); resolve → plan on
simulate_inventory('48,16') gives {0.8B→gpu1, 2B→gpu0} for two concurrent
smalls (both GPUs used, zero pinning) and {9B→gpu0, 4B→gpu1} for the
cross-runbook concurrency case. That is precisely the behavior Jon asked
for, demonstrated pre-GPU.

**Not yet validated on yardrat:** the measure command's acquire-once path,
the real vLLM log-format parse against v0.25.1 output, and the 4B-on-16GiB
best guess (13 GiB — the tightest fit; the guided error is the designed
recovery if it's wrong). First real run: ./10_run_smoke.sh in the new
runbook, ideally with the 9B re-run going concurrently to watch eligibility
keep them apart.

### Addendum 3 (same day, 15:30) — open-judge plan review (Fable)

Jon had GPT 5.6 draft docs/planning/open-judge-plan.md (rejudge frozen
official candidate responses with open-weight judges — Qwen3.5-27B /
Qwen3.6-35B-A3B on aiq-gpu; measure judge-substitution effect). Reviewed and
revised. Verification-first: every load-bearing repo claim in the draft
CHECKED OUT against the tree (plugin seams, Phase-0 tests, codec, the six
hard-coded annotators + model_as_judge TODO, extract_judge_models, the
qwen3.6-35b-a3b-dual-tp2-4x96 recipe, both judges on HF). The core design —
immutable response snapshot → attributable judgment attempts, identity-replay
stop gate, prompt-parity tests, judge-attributed metric names — endorsed
unchanged.

Revisions: (1) multi-replica + dynamic routing DEMOTED from v1 requirement
to post-pilot scale-out — it's a throughput optimization presented as a
correctness requirement, and it put the least-proven infra (Postgres LiteLLM
dynamic routing) on the critical path; v1 = one replica per judge arm,
static routing, and Milestone D *measures* whether scale-out is ever needed.
(2) Stitched in same-day VRAM-aware placement (§2.9: judge endpoints declare
min_vram_gib, measure --record refines, lease_ttl lesson). (3) Fixed Phase-0
env instructions (wrong repo name, per-project .venv vs Jon's top-level-venv
convention). (4) New §19.1: at T=0, replicates measure SERVING
nondeterminism, not sampling variance — reportable as such, with a
Milestone-D drop-to-1 decision point; prompt-parity must assert official
temperatures rather than assuming 0.0. (5) Concrete v1 topologies with
declared placements. Review record appended to the doc itself (§25).

Design takeaway: when reviewing a generated plan, the highest-value pass is
fact-verification against the tree (all held here — rare) and then
*risk-ordering*: a plan can be entirely correct and still wrong about what
belongs on the critical path.

## 2026-07-17 17:11:42 -0400

**User intent.** "Please implement the new plan" — begin executing the
reviewed/revised `docs/planning/open-judge-plan.md` (open-weight judge
rejudging). Model: Fable (`claude-fable-5`, 1M context), Claude Code harness,
autonomous session on aivm-2404-yardrat.

**What landed: Milestone A complete (Commits 1–8 of the plan's v1
sequence).** Eight commits, each independently green, 80 new tests, full fast
suite 671 passed (4 pre-existing store-path failures unrelated):

- `cc2240c` Commit 1 — source-artifact audit (`eval_audit/judging/`
  package, canonical display-key module, JSON-level per-run shape
  validation, `eval-audit-audit-judge-sources`).
- `68bc1cf` Commit 2 — immutable content-addressed response snapshots
  (judge-neutral reconstructed ScenarioState via HELM codec, detached
  official annotations, DONE-last atomicity, hash covers only
  judging-relevant facts).
- `8f7f871` Commit 3 — official-annotation identity replay gate (reattach
  originals, real `Metric.evaluate`, 1e-12 vs published stats).
- `b1cb780` Commit 4 — JudgeSpec/JudgmentAttemptSpec (canonical hashing over
  inference-affecting fields only; flat annotator args recoverable by the
  existing `extract_judge_models`).
- `d26e372` Commit 5 — ConfigurableXSTestAnnotator + shared judge
  request/provenance helper; prompt-parity tests vs the official annotator.
- `6742f23` Commit 6 — annotation-only rejudge runner (`helm_rejudge_v1`
  artifacts, per-(snapshot, judge, replicate) SQLite caches,
  `Request.random` replicate identity, candidates proven unchanged;
  deterministic offline fake-judge deployment drives the REAL
  AnnotatorFactory→AutoClient path in tests).
- `9e1c709` Commit 7 — SingleJudgeSafetyMetric (judge-attributed names,
  explicit-field read, stop gate pinned) + stats wired into the runner;
  taxonomy: `*_annotator_success*` → bookkeeping.
- `def0847` Commit 8 — ConfigurableWildBenchAnnotator + metric (official
  template/regex/empty-output semantics, 1..10 range check) + the §10.6
  cross-annotator parse-failure matrix.

**Environment note.** No persistent Python env existed for user `agent` on
this VM; created the CLAUDE.md-documented env at
`~/.local/uv/envs/uvpy3.13.2` (uv, Python 3.13.14) and installed
eval_audit + aiq-magnet + infer_stack editable. Phase 0 baseline: 25
passed / 5 skipped before any new code.

**Two findings worth keeping (both caught by the gates the plan insisted
on):**

1. *The identity-replay gate caught my own fixture twice.* Official
   `safety_score` aggregation is judge-count-weighted (per-instance Stat
   gets one `.add()` per parsed judge, then trial-merge + `take_mean`), not
   mean-of-instance-means (0.85 vs 0.875 on the fixture); and real published
   `stats.json` carries derived `computed_on=worst` robustness/fairness rows
   even for unperturbed runs (`compute_worst_case_metrics` emits them
   unconditionally). Exactly the class of misreconstruction the gate exists
   to stop before a judge request is ever sent.
2. *The official safety judge ensemble is HELM-version-dependent.* The
   installed (newer) crfm-helm has commented out the Llama-405B judge
   (deprecated 2026-03-06) — GPT-only now — while the pinned submodule and
   the published gpt-oss-20b artifacts have both. Prompt-parity tests
   therefore assert prompt bytes and budgets but not ensemble size, and the
   source audit accepts any-of the official judge fields. The judge_registry
   maintenance note ("suite-version-qualified entries") anticipated this.

**Design choices beyond the plan text (documented in-module):** snapshots
also carry verbatim `source_stats.json`/`source_per_instance_stats.json` so
the replay gate survives corpus moves (excluded from hash identity);
out-of-range judge scores are recorded as `out_of_range` with a null score
rather than silently accepted (official parsers accept any float) — never
affects identity replay, which uses original annotations; the fake judge
client answers in whichever official format the prompt calls for, keeping
one deployment for both benchmarks' fixtures.

**Not done / next steps (serving-facing half, needs Jon's input):**

- The Phase 1 stop gate ("at least one real XSTest + WildBench source passes
  the audit") needs a host that mounts `/data/crfm-helm-public` — not this
  VM. Run `eval-audit-audit-judge-sources` there first.
- Commit 9 (judge sidecar export + optional Qwen thinking client — plan
  says only if live smoke proves the switch is needed), Commit 11 (kwdagger
  rejudge pipeline), Commit 12 (indexing + judge-variance analysis),
  Commit 13 (aiq-gpu runbook incl. §14.3 context-length preflight),
  Commit 14 (remaining safety benchmarks + Omni-MATH). Commit 10
  (multi-replica) stays DEFERRED per the review.
- Milestone B (XSTest 20-instance live smoke, Qwen3.5-27B on aiq-gpu) is the
  first GPU step; the §14.1 catalog declaration (TP1, min_vram_gib 60 guess)
  is written in the plan.

**Reusable insights.** (1) A replay-identity gate pays for itself during
*development* — it debugs your reconstruction long before it validates the
experiment. (2) When mirroring an upstream pipeline, generate fixtures from
the upstream's own aggregation semantics or the gate will correctly refuse
your fixtures — hand-derived "obvious" aggregation (mean-of-means) was wrong
twice. (3) Registering a deterministic fake deployment through the real
config/factory/cache stack tests an order of magnitude more plumbing than
injecting a mock client — the cache-restart and replicate-isolation tests
run against HELM's actual SQLite layer.

## 2026-07-18 (addendum) — Phases 1–3 validated on real public data

Jon ran the three-command validation on aiq-gpu (corpus-mounted host).
Both gpt-oss-20b closed-judge sources now audit OK and reproduce their
published judge metrics exactly through the identity-replay gate:

- xstest (safety/v1.14.0): max_err 0, 2250 instance + 15 aggregate rows.
- wildbench (capabilities/v1.12.0): max_err 1.95e-14 (< 1e-12 gate),
  2000 instance + 6 aggregate rows.

One real-data fix in between (`9f3cdc5`): official WildBench public runs
use `adapter_method=chat`, not generation — the audit had whitelisted
generation only and (correctly, loudly) skipped WildBench. Made
supported adapter methods a per-benchmark profile field and allowed
{generation, chat} for WildBench (its annotator reads
instance.input.messages + the single completion, consumes no
reconstruction-default fields → chat is shape-equivalent). Kept it a
curated allow-list, NOT a blanket chat accept (safety stays
generation-only), per the plan's inspect-the-actual-shape rule. Also
fixed the fixture builder, which had wrongly labeled WildBench
generation — had it matched reality, this surfaces in CI not on the GPU
box. The replay gate on the real chat run then *proved* the
reconstruction faithful (max_err ~2e-14) rather than me asserting it.

Also added `eval-audit-verify-judge-replay` (`f91ddd7`) — the runbook
interface for the Phase 3 gate (09_verify_official_identity_replay.sh).

This closes the offline correctness core against real data. Next
buildable-offline: Commit 9 (judge sidecar bundle export) and Commit 12
(indexing + judge-variance analysis, which can run against the rejudge
fixture artifacts). Commit 12 carries one design decision worth Jon's
input (dedicated judge-analysis table vs virtual-experiment
integration — plan §17 recommends the dedicated path first). Serving
commits (9 partially, 11, 13) and Milestone B need the GPU box.

## 2026-07-18 (addendum 2) — Commit 12: indexing + judge analysis (offline)

After real-data validation, continued the plan with the next
cleanly-offline piece. Commit 9 (judge sidecar export) turned out to
couple to the infer-stack catalog + §14.3 context preflight (Commit 13
territory), so building it in isolation would mean guessing the serving
interface — deferred it to land with 13. Did Commit 12 instead
(`73cb1d7`): eval_audit/judging/indexing.py + analysis.py +
eval-audit-analyze-judges. Joins rejudge artifacts + snapshot official
annotations strictly by (response_set_hash, display key); reports
per-arm aggregate/failure/replicate-variance and pairwise
open-vs-official / open-vs-open / official gpt-vs-llama baseline with
diffs, Pearson/Spearman, agreement, kappa (label kind), bootstrap CI.
numpy-only stats (no scipy dep). Tested by running the real fake-judge
runner for 2 arms x 2 replicates and analyzing — 81 tests in the
open-judge suite now.

Remaining is serving/GPU-facing and needs Jon's direction: Commit 9
(sidecars, with 13), Commit 11 (kwdagger rejudge pipeline — orchestration
best validated near real infra), Commit 13 (aiq-gpu runbook + §14.3
prompt-length preflight to size max_model_len + judge catalog with
declared min_vram_gib), Commit 14 (remaining safety benchmarks +
Omni-MATH), then Milestone B (XSTest live smoke, Qwen3.5-27B). The
offline correctness + analysis stack is complete and real-data-proven;
what's left produces judge requests and costs GPU time.

## 2026-07-18 (addendum 3) — serving-facing build: Commits 9 + 13 (runbook ready)

Jon green-lit aiq-gpu ("free, want to utilize it") and confirmed the v1
judges, so I built the full serving-facing scaffolding toward a live
Milestone B. Landed:

- Commit 9 (`c11fef1`): judge HELM sidecars (configs/open_judge/
  model_metadata + tokenizer for qwen/qwen3.5-27b + qwen/qwen3.6-35b-a3b,
  chat/instruct) + judge_bundle_export.py (reuses the pure-static
  resolve_serving_facts + _model_deployment_entry; chat ->
  NullSafeOpenAIChatClient; §23 anti-goal guarded up front) +
  eval-audit-export-judge-bundle + the aiq-gpu judge catalog/settings +
  two JudgeSpec JSONs.
- Commit 13a (`8c8dbf2`): §14.3 prompt-length preflight
  (eval-audit-judge-prompt-lengths) — renders the real judge prompts over
  a snapshot (excludes the empty-candidate shortcut) and sizes
  max_model_len; HF tokenizer optional, char estimate fallback.
- Commit 13b (`6c25c85`): reproduce/open_judge_gpt_oss/ runbook (00/03/05/
  08/09/10/20/30 + README) and run_rejudge(max_instances=N) for a smoke
  subset (folded into attempt/request identity via a ':limitN'
  request_random suffix so it can't collide with a full run).

Design decision made mid-build (correct, prevents a latent bug): JudgeSpec
temperature/max_tokens are now OPTIONAL (default None = the benchmark's
official budget). A judge arm is reused across benchmarks whose official
max_tokens differ (safety 256, WildBench 2000); baking one in would have
truncated WildBench judges. The runner resolves the official per-benchmark
budget when None and records the effective value + source in the manifest.

Commit 9's sidecar export was originally deferred as "needs the catalog
(Commit 13)" — but resolve_serving_facts is pure-static (reads catalog.yaml,
no live gateway), so once I hand-authored the aiq-gpu catalog the export
became fully offline-testable. Good reminder to check whether a "needs
serving" dependency is actually a runtime dependency or just a config file.

Full open-judge suite now 119 tests. Everything that can be validated
without a GPU is validated. The runbook's live scripts (20/30) and the
infer-stack acquire/gateway idioms are UNTESTED against real serving —
mirrored from the qwen35 runbooks; the first Milestone-B run on aiq-gpu is
the validation, exactly as the candidate runbooks were built. Handed to
Jon to run 00->20->30 on aiq-gpu.

Still deferred: Commit 11 (kwdagger rejudge pipeline — grouped-by-arm
scheduling; the smoke uses manual lease instead), Commit 14 (remaining
safety benchmarks + Omni-MATH). Commit 10 (multi-replica) stays out.

## 2026-07-18 18:29:24 -0400

**Model/harness:** claude-opus-4-8[1m] (Opus 4.8, 1M context) via Claude Code.

**User intent:** Continue "implement the new plan" (open-judge). Debug the
Milestone-B live judge smoke on aiq-gpu to a clean parse rate, then record the
milestone and line up Milestone C.

**Milestone B is DONE — first live judge run validated.** XSTest / 20
instances / Qwen3.5-27B / 1 replicate, served via infer-stack+LiteLLM. Final:
18/20 parse ok, **18/18 verdict agreement with the official GPT-4o+Llama
ensemble** (every parsed Qwen score exactly matched both official judges),
`safety_score:judge=qwen3_5_27b`=0.833 (15/18). The 2 failures are legitimate
token-budget truncations (`finish_reason=length`, no `</think>`), not parser
bugs.

**Two debugging insights worth reusing:**

1. *A parser code-change with no spec field is invisible to the attempt cache.*
   The strip_thinking fix (2d78e4b) first returned a stale `cache-hit`: the
   whole attempt short-circuited on its DONE file because `attempt_hash` (←
   `spec_hash` ← parser_version + budget + …) hadn't moved, so it served the
   pre-fix malformed results and the fix never ran. Fixed by bumping
   `parser_version` "official-v1"→"official-v1+strip-think" (d251e2a). This is
   the identity guardrail working as designed ("silent defaults become silent
   experiment configuration") — the honest record of a parser behavior change
   is also exactly what invalidates the cache. **Rule: any change to parser or
   prompt behavior MUST bump its `*_version` field.** Because parser_version
   also keys the SQLite request-cache path, the bump forces a genuine GPU
   regeneration (fine here — no committed full run depends on the old hash).

2. *Qwen thinking judges draft the answer tags as placeholders inside their
   thinking.* The real bug behind the earlier 6 malformed-stop cases was the
   non-greedy `<reasoning>/<score>` regex matching PLACEHOLDER tags the model
   writes while reasoning, before the real `</think>`-terminated answer.
   `strip_thinking` (split on last `</think>`, parse only the suffix) fixes it
   and is a strict no-op on official non-thinking responses, so parse parity
   with the official annotators is preserved. The 2 remaining truncations are a
   budget matter, not a parser matter (judge still mid-thinking at 1280 tokens).

**Design tradeoff still open:** `reasoning_headroom_tokens`=1024 (→1280 for
safety, 256 official + headroom) leaves ~10% truncated at temp=0. Raising it
(e.g. 2048) cuts truncations but slows every judgment (~6.6s/instance already,
since thinking can't be disabled on the deployed vLLM v0.25.1). Deferred the
decision to see the strip-think result cleanly first (done); recommend raising
before the full run and flagging it to Jon.

**Next:** generalize the hardcoded `20_smoke_xstest_qwen35.sh` into a
parameterized smoke (benchmark × judge) so Milestone C (WildBench, Qwen3.5-27B)
and the Qwen3.6-35B-A3B arm (TP2) reuse one script; commit; hand Jon the next
runs. Still deferred: Commit 11 (kwdagger rejudge pipeline), Commit 14
(remaining safety benchmarks + Omni-MATH). `submodules/every_eval_ever` gitlink
remains modified/unstaged (flagged, not committed).

## 2026-07-19 16:35:00 -0400

**Model/harness:** claude-opus-4-8[1m] (Opus 4.8, 1M context) via Claude Code.

**User intent:** Get a full overnight open-judge run launched and analyzed.

**The v1 experiment is COMPLETE.** 12/12 attempts OK in ~20.3 h (est. 21 h) on
aiq-gpu: full XSTest (450) and WildBench (1000), both judge arms, 3 replicates,
temp 0, headroom 4096. Driver `50_overnight_run.sh` worked unattended end to
end — per-judge leasing, idempotent DONE-gated attempts, auto-analysis.

**Three findings, in ascending order of interest.**

1. *XSTest: open judges are a drop-in replacement.* Agreement with official
   GPT-4o is kappa 0.936 (qwen35) / 0.928 (qwen36); the two OFFICIAL judges
   agree with each other at only kappa 0.829. The open-vs-closed perturbation
   is **smaller than the closed ensemble's own internal disagreement**. Parse
   99.6/99.8% (the 4096 headroom fixed the smoke's ~10% truncation).

2. *WildBench: aggregate reproduces, instances do not.* Open judges run ~0.8
   lower than officials, but open-vs-open agreement (abs 0.66, pearson 0.935)
   ≈ official-vs-official (abs 0.63, pearson 0.936). Run-level mean is stable
   across replicates (stddev ~0.22 on ~6.7, ~3%) while **43–46% of individual
   instances change score** (max range 5–6 points on a 1–10 scale).

3. *The mechanism.* Judge TEXT is non-deterministic at temp 0 on both
   benchmarks — 87–96% of instances generate different raw output across
   replicates (vLLM continuous batching → batch-composition-dependent FP
   reduction order → different argmax at near-ties → divergent token →
   cascading CoT). Whether that reaches the metric depends on **metric
   granularity**: XSTest's near-binary label absorbs it (0.2–0.7% score
   change); WildBench's 1–10 rubric has no attractor (43–46%). So **judge
   non-determinism is universal; metric fragility is a property of the metric,
   not the judge.** That lands squarely in the project's per-metric-fragility
   framing.

**The check that made finding 3 trustworthy.** We vary `Request.random` per
replicate for cache keys, which would have been a self-inflicted explanation
for the divergence. Verified it is cache-key-only for our client: our judges
derive from `OpenAIClient`, `helm/clients/openai_client.py` has NO seed
handling, and `client.py:make_cache_key` only appends random to the key. Worth
remembering that bedrock/cohere/mistral/reka/google_genai DO map random→seed —
the same reasoning would be wrong there. General lesson: before attributing
variance to the environment, prove your own experimental knobs aren't the cause.

**Honest caveat.** WildBench+qwen35 still truncates 14.2% (142/1000) at the
6096 ceiling; its replicate stats rest on the 594/1000 parsed in all three
replicates — plausibly a biased subset (long/hard instances drop out). qwen36
(2% parse fail, 883/1000) shows the same effect, which corroborates but does
not fully substitute. Fix truncation before publishing qwen35 WildBench numbers.

**Next:** raise headroom for the WildBench+qwen35 arm specifically (a
per-benchmark headroom would be better than the current single JudgeSpec field)
and re-run that arm; then Commit 11 (kwdagger pipeline) and Commit 14
(remaining safety benchmarks + Omni-MATH). `submodules/every_eval_ever` gitlink
still modified/unstaged (flagged, uncommitted).

## 2026-07-20 09:15:00 -0400

**Model/harness:** claude-opus-4-8[1m] (Opus 4.8, 1M context) via Claude Code.

**User intent:** After the v1 results landed — keep the GPUs busy and "finish it
out": judge-size sweep, remaining benchmarks, and the scheduler.

**Landed this session (after the v1 analysis):** the Qwen3.5 judge-size ladder
(`2b0b867`..`e152496` era configs), the safety trio (`b5816b9`), concurrency
fixes to the serial driver (`27d8cad`), the kwdagger rejudge pipeline
(Commit 11), and Omni-MATH (Commit 14b). The benchmark surface is now complete:
4 safety + Omni-MATH + WildBench, six judge arms, all wired end to end.

**The thing worth writing down is a process failure, not a technical one.**
Jon asked whether `50_overnight_run.sh` would use all 4 GPUs. It would not — it
is serial over judges and every arm is TP1, so one worker holds one card. That
was knowable from the moment I wrote the driver. Worse, I had carried Commit 11
(the kwdagger fan-out) as "deferred" all session, including when he explicitly
asked *what is valuable to run next* — and I answered with benchmarks and
sweeps, never mentioning that the execution substrate was wasting 75% of the
box. He only found out by asking directly, and then apologized for not checking.

Root cause is a sentence I wrote in `_lib.sh` during Commit 13b: "Unlike the
candidate runbooks this does NOT use kwdagger per-run leasing". The *reason* is
sound — the candidate runbooks' per-run acquire→infer→release idiom would
reload judge weights every job. But the sentence hardened "this idiom does not
fit" into "this tool is not used here", and every later decision inherited it.
The plan itself contradicted it: Commit 11's spec is literally "reuse one
serving session across several rejudge jobs."

**Two reusable lessons.** (1) When you write down *why not*, scope the negation
precisely — "not this idiom" and "not this tool" are different claims, and the
looser one propagates. I left the correction in the header explicitly rather
than quietly deleting it, because a reader who only sees clean code will
re-derive the original conclusion. (2) A deferral is a decision with a shelf
life. "Serial is fine" was true for 2 judges x 2 benchmarks; at 6 judges x 6
benchmarks x 3 replicates it is a 4x waste. Deferrals should be re-costed when
the workload changes, not carried as settled. The tell was that my recommended
workaround — open four tmux panes and pin one judge each — was me hand-executing
a scheduler.

**Design notes from the new code.** The rejudge matrix planner is deliberately
free of kwdagger AND helm imports so fan-out logic is unit-testable with no
scheduler and no GPU (it is; 10/10 pass locally via a pytest shim, since this
workstation has neither pytest nor helm). Rows group by judge because contiguity
keeps infer-stack's demand refcount above zero and the weights resident —
interleaving would reload multi-GiB weights under `reclaim: stop`. And
`judge_spec_hash` rides as job identity because a JSON *path* does not change
when the file is edited — the identical trap that produced a stale `cache-hit`
earlier in the session when a parser fix did not move `parser_version`. That bug
class has now appeared three times (safety tags, WildBench JSON, Omni-MATH
headings drafted inside a thinking block); `strip_thinking` before official
parsing is the standing fix.

**Unvalidated — the honest list.** Nothing from this session has executed. The
safety trio, Omni-MATH, and the kwdagger pipeline are compile-checked and
unit-tested where possible, but HELM/kwdagger/pytest are all absent locally, so
the parity tests and the fan-out have never run. Next on aiq-gpu: `05`/`08`/`09`
for the four new benchmarks — the replay gate is the real check on the new
prompt-construction code and costs zero GPU — then a small `--max-instances`
kwdagger fan-out before trusting it with a night. `submodules/every_eval_ever`
gitlink remains modified/unstaged (flagged, uncommitted) throughout.

## 2026-07-21 11:44:05 -0400

**Model/harness:** claude-opus-4-8[1m] (Opus 4.8, 1M context) via Claude Code.

**User intent:** After fetching the repo + submodules, work out what needed
merging, do it, prove it, then chase a research critique to a plan. Four asks
in sequence: (1) determine branch/submodule sync state; (2) merge
`origin/jons/qwen35-extension` — superproject *and* submodule — onto
`impl/run-from-run-spec`; (3) adopt the pre-existing `cmd_queue` gitlink drift;
(4) confirm whether the qwen runbooks run from-spec or via the key expander,
then critique the asymmetry and turn the resolution into a plan.

**The merge work (context for the real content below).** Current branch was 48
commits behind `origin/jons/qwen35-extension` — an entire open-judge/rejudge
workstream landed on the remote *after* the previous merge (`bff4f7a6`). The
only non-trivial conflict was the `infer_stack` submodule gitlink: both sides
had advanced it from base `6616c519` in different directions — ours to
`38b3f39` (tui-async-startup), theirs to `06f2ec2` (VRAM-aware leasing). Merged
the two *inside* the submodule first (`b9a8c89`, a clean --no-ff of both
workstreams, no code conflict), then resolved the superproject gitlink to that
merge. `every_eval_ever` fast-forwarded (`b1c5a0f`→`f6ae03c`); `cmd_queue` was
deliberately kept out of the merge per the never-auto-bump-gitlinks rule, then
adopted as its own commit (`8906ac96`) once the user confirmed. Full suite green
afterward: **765 passed, 75 skipped** (25:43 wall; one heavy diff test is 1485s
of it). Process note worth keeping: I fumbled the background run by
double-backgrounding (`nohup … &` inside a `run_in_background` bash), so the
first "exit 0" was the *launcher* returning, not pytest. Caught it, waited on
the real PID, and only then trusted the result — the lesson is that a wrapper's
exit code is not the wrapped process's exit code, and "completed" on the
launcher means nothing about the job.

**The content worth writing down is the critique, and it is a genuine one.** The
qwen runbooks (`qwen35_vllm`, `qwen35_small_vllm`) are *compute*, not
reproduction, and they author `run_entries` as literal HELM run-key DSL strings
— e.g. `mmlu:subject=abstract_algebra,method=multiple_choice_joint,model=…,
data_augmentation=canonical,model_deployment=…` — in `preset_configs.yaml`, then
hand them to HELM's expander at execution time (no `--from-spec`). That is
*exactly* the fragile artifact this project criticizes HELM for. The user asked
the honest question: do we critique HELM for fragile run-key names + expanders,
then fall back to that pattern for our own new runs?

**Answer: yes, but the strong form ("hypocrisy") overclaims, and the useful
form is narrower and actionable.** Three things the critique gets right: (1) a
*provenance asymmetry* — byte-exact frozen-spec replay for others' runs, a
mutable name for our own; (2) we *manufacture new fragility* — the
`run_spec.json` a compute run emits is a derivative of our pinned expander, so a
future reader inherits the version-coupling we criticize; (3) it has already
bitten — G13 is precisely a stored run-key/class-path that no released expander
resolves. What blunts the strong form: for de-novo models there is *no prior
spec to replay*, so authoring is necessary, not backsliding; and the fragility
is specifically **cross-version** — under a single pinned HELM build,
key→spec is deterministic, so the risk is *deferred onto the future reader*, not
incurred now. The runbooks also label themselves "compute instead of reproduce",
so there is no epistemic sleight of hand.

**The resolution reinforces the thesis rather than undermining it.** A run key
is a lossy *name*; the `RunSpec` is ground truth. HELM's original sin is
treating the mutable name as the durable handle and regenerating on demand. Our
from-spec discipline already fixes that for historical runs; the gap is that we
don't yet extend it to our own freshly-computed runs. The one-line rule:
**expand once at authoring, then freeze** — the expander touches each compute
run exactly once, at birth, and its `run_spec.json` becomes the canonical,
content-addressed, archived handle. Framed that way, the perceived inconsistency
becomes *evidence for* the exact principle the paper argues.

**Deliverables this session.** The plan is
[`docs/planning/compute-run-spec-freeze-plan.md`](../../docs/planning/compute-run-spec-freeze-plan.md):
an offline expand-and-freeze step (content-addressed specs, HELM-version
stamped), routing compute execution through the *existing* `--from-spec` replay
(no second executor), a re-expand-and-diff drift guard that would catch
G13-class breakage, and docs/paper framing — with the honest caveat that until
it lands the presets still keep the key string as the stored source of truth, so
the discipline is aspirational. The blocking unknown is **F1**: whether HELM
exposes run-key → `RunSpec` expansion *without* running inference (`helm-run`
writes `run_spec.json` before inference, so a dry-run or a direct
`run_spec_factory` call may already suffice). Resolve F1 before shaping Change 1.

**Reusable insight.** When you build a reproducibility argument around "the
frozen spec, not its name, is the identity", audit *your own* artifact-producing
paths for the same discipline before a reviewer does — the critique you can make
of an upstream tool is usually latent in your own pipeline wherever you had to
author something that tool couldn't hand you. The tell here was that the *output*
`run_spec.json` was treated as a verification target (something to check exists)
rather than as the *source of truth* (something to freeze and re-run from).

**State at close.** Branch `impl/run-from-run-spec` carries three unpushed
commits (`20ae5fdc` merge, `8906ac96` cmd_queue bump) plus these docs, tree
clean apart from the user's untracked doc zips. Unpushed: pushing requires the
`infer_stack` merge commit `b9a8c89` to reach its remote first, or the
superproject gitlink dangles for others. No code was written toward the plan yet
— it is PROPOSED only.

## 2026-07-20 17:05:00 -0400

**Model/harness:** claude-opus-4-8[1m] (Opus 4.8, 1M context) via Claude Code.

**User intent:** Check the size-sweep results, orient after a context switch,
and — raised by Jon — assess training-data contamination risk, verifying the
Qwen3.5 release date against the publication date of the HELM judgments we
compare to.

**Results state.** 63 healthy artifacts, zero request_error anywhere. 4B came
back clean after the purge, so the size sweep is complete except Omni-MATH
(never snapshotted) and the safety trio for 0.8B/27B/35B-A3B. Two clean
findings: parse rate tracks METRIC COMPLEXITY (XSTest 100% even at 0.8B;
WildBench 6.8% → 51.6% → 61.2% → 84.6% → 90.7%), while agreement given a parse
SATURATES EARLY on label metrics (XSTest ~98–99% from 4B up; the 27B buys
nothing over the 9B, and the official GPT-vs-Llama baseline is only 96.0%).

**Third finding, which nearly fooled me:** format compliance does not imply
calibration. Qwen3.5-2B scores 99.9% parse and 25.7% agreement on
anthropic_red_team — it flags 740/1000 responses unsafe where the official
ensemble says 989/1000 safe. I nearly wrote that up as an anomaly before
checking the score distribution. Parse rate and agreement must always be
reported as separate axes; a "does the judge work?" smoke test based on output
format would have passed this arm. (Also note these safety sets are ~99%
one-class, so "agreement" there is essentially a false-positive rate — worth
stating explicitly in any writeup.)

**Contamination — Jon's point, and he is right.** Verified: the Qwen3.5 family
launched 2026-02-16 and the 0.8B–9B smalls ~2026-03-02. Every benchmark dataset
predates that by 2–4 years, and the candidate (gpt-oss-20b) is Aug 2025. What I
could NOT establish is the publication date of HELM Safety v1.14.0 /
Capabilities v1.12.0 — the corpus run dirs carry no execution timestamp and
public sources did not yield it in a reasonable search. That is exactly the
date that matters, because HELM publishes the official gpt_score/llama_score
values AND the judges' reasoning text; if those were scraped before Qwen3.5's
cutoff, "open judge agrees with GPT-4o" is partly a memorization result. Full
analysis (three channels, counter-evidence, proposed tests) is now in
docs/helm-reproduction-research-journal.md.

Worth recording that our own data argues against WHOLESALE memorization: the 2B
arm inverts the official labels, agreement is size-graded like a capability
curve, and WildBench sits at a systematic offset rather than converging. None
of that is what memorized reproduction looks like. But it is suggestive, not
decisive, and the honest framing is that every agreement number is an UPPER
BOUND on independent agreement until tested.

**Cheapest decisive test: swap the candidate model** to one released after the
judge cutoff. The (prompt, response, judgment) triple then cannot have been
memorized even though the prompts were public — and our pipeline is already
parameterized by candidate, so it is a config change, not new code. I would run
that before any writeup.

**Process note.** I had authored the judge release dates in model_metadata.yaml
as guesses with "confirm against the HF repo" comments, and they sat unverified
for days until Jon asked a question that depended on them. Flagging uncertainty
in a comment is not the same as resolving it; a date that a FINDING depends on
should be verified when the finding is made, not deferred to a reader. The
metadata now records the verified launch date and says explicitly what was and
was not confirmed.

## 2026-07-21 12:29:39 -0400 — CHECKPOINT (pinned before a context compression / pivot)

**Model/harness:** claude-opus-4-8[1m] (Opus 4.8, 1M context) via Claude Code.
**Branch:** `jons/qwen35-extension`, HEAD `aa153a4`. Working tree clean except
the `submodules/every_eval_ever` gitlink, which is Jon's pre-existing local
change and has been deliberately left unstaged throughout. No submodule pin was
changed by any of this work.

Read this entry first if you are resuming. It states what is TRUE, what is
merely WRITTEN, and the exact next commands.

### Validated (real execution, real data)

- **Identity replay: 6/6 benchmarks reproduce published stats exactly.** xstest,
  simple_safety_tests, harm_bench, anthropic_red_team, omni_math all
  `max_err=0`; wildbench `1.95e-14`. This proves snapshot reconstruction is
  faithful and the metric denominators match the official runs.
- **63 healthy rejudge artifacts, zero request_error anywhere.** Judge-size
  sweep across Qwen3.5 0.8B/2B/4B/9B/27B + Qwen3.6-35B-A3B; xstest + wildbench
  on the full ladder, the safety trio on 2B/4B/9B.

### Findings so far

1. *Open judges match closed judges on label metrics.* XSTest agreement 93.7%
   (0.8B) → 96.8% (2B) → ~98–99% from 4B up. Official GPT-vs-Llama baseline is
   96.0%, so a 4B judge sits INSIDE the closed pair's own disagreement. The 27B
   buys nothing over the 9B.
2. *Judge non-determinism is universal; metric fragility is not.* At
   temperature 0, 87–96% of judgments produce different raw text across
   replicates (vLLM batching non-determinism — verified NOT caused by our
   per-replicate `Request.random`, which is cache-key-only for OpenAI-derived
   clients). Score impact: XSTest 0.2–0.7%, WildBench 43–46%. Run-level means
   stay reproducible; instance-level judgments do not.
3. *Parse rate and calibration are INDEPENDENT axes.* WildBench parse climbs
   6.8% → 51.6% → 61.2% → 84.6% → 90.7% with size while XSTest is ~100%
   throughout. And Qwen3.5-2B scores 99.9% parse with 25.7% agreement on
   anthropic_red_team — it flags 740/1000 responses unsafe where the official
   ensemble says 989/1000 safe. A format-based health check would pass it.
   Always report the two axes separately; note these safety sets are ~99%
   one-class, so "agreement" there is essentially a false-positive rate.
4. *Contamination caveat* (full analysis in
   `docs/helm-reproduction-research-journal.md`). Qwen3.5 launched 2026-02-16;
   every benchmark predates it by 2–4 years. The mechanism to worry about is
   DISTRIBUTION SHIFT, not memorization: a judge trained on this data is
   plausibly better calibrated on this distribution with no verbatim recall.
   Our numbers describe judge/benchmark pairs the judge was likely trained on
   and are an UPPER BOUND on performance for a novel benchmark or private eval.
   **Still unknown and worth establishing: the publication date of HELM Safety
   v1.14.0 / Capabilities v1.12.0** — the corpus run dirs carry no execution
   timestamp and a public search did not resolve it.

### WRITTEN BUT NEVER EXECUTED — do not treat as working

- **The kwdagger fan-out.** `eval-audit-schedule-rejudge` /
  `reproduce/open_judge_gpt_oss/55_schedule_rejudge.sh`. Its invocation was
  originally written against a GUESSED kwdagger CLI and was wrong three ways;
  `64d6881` rebuilt it against the verified interface and now shares
  `kwdagger_schedule_argv_from_runtime()` with the working candidate bridge.
  Still unproven: whether kwdagger accepts the `rejudge.*` submatrix keys and
  resolves the pipeline factory.
- **The Omni-MATH annotator, live.** Note carefully: the replay gate does NOT
  exercise it. Replay reattaches the ORIGINAL annotations and runs the OFFICIAL
  metric, so `build_prompt` / `parse_omni_math_report` have never been invoked
  against a real judge. (I stated the opposite twice; it was wrong.)
- **The pytest suites** for the safety trio, Omni-MATH, and the rejudge matrix.
  Written, compile-checked, and partially exercised via a hand-rolled shim on
  the workstation, but never run under pytest — this workstation has no pytest,
  helm, kwdagger, or loguru.

### Exact next commands

    cd ~/code/helm_audit && git pull        # aa153a4
    python -m pytest tests/test_configurable_omni_math.py \
                     tests/test_configurable_safety_trio.py \
                     tests/test_rejudge_matrix.py -q
    cd reproduce/open_judge_gpt_oss
    ./55_schedule_rejudge.sh omni_math --smoke --run   # 3 things on trial: kwdagger
                                                       # graph, omni annotator live,
                                                       # strip_thinking on a 3rd format
    ./55_schedule_rejudge.sh omni_math                 # preview job count + argv
    ./55_schedule_rejudge.sh omni_math --run           # full: 6 judges x 3 reps x 1000

Sizing: Omni-MATH is 1000 instances at a 4096-token budget (the largest in the
suite) = 18,000 judgments for the full matrix. The 27B arm alone is likely
several hours per replicate. `OJ_JUDGES="qwen3_5_2b qwen3_5_4b qwen3_5_9b"`
gives the interesting middle of the curve far cheaper.

### Open items, roughly by value

1. **Contamination test — swap the candidate model** to one released after the
   judge cutoff. The (prompt, response, judgment) triple then cannot have been
   trained on even though the prompts were public. The pipeline is already
   parameterized by candidate, so this is config, not code. Arguably worth more
   than finishing Omni-MATH: it bears on whether the headline survives review.
2. Safety trio missing for 0.8B / 27B / 35B-A3B (completes the grid).
3. Aggregate reports are stale (Jul 19, pre-sweep) — rerun
   `./30_analyze_judges.sh <benchmark>` per benchmark once runs settle.
4. **No timeout on the rejudge step** — a wedged CLI held a GPU for 19 hours on
   2026-07-19. A `timeout` wrapper sized per benchmark would convert that into
   a logged failure and free the card. Not yet implemented.
5. HELM leaderboard publication dates (feeds item 1's caveat).

### Gotchas this project has now hit more than once

- **A config change invisible to a cache key serves stale results.** A parser
  fix that did not move `parser_version` returned a cache-hit; a dead artifact
  that still wrote `DONE` blocked every retry for a day. Any behavior change
  must move its `*_version`, and `judge_spec_hash` rides in job identity for
  the same reason.
- **Thinking judges draft the official answer tags INSIDE their reasoning.**
  Hit three times (safety `<reasoning>/<score>`, WildBench JSON, Omni-MATH
  `##` headings). `strip_thinking` before official parsing is the standing fix;
  it is a strict no-op on non-thinking official responses.
- **Shared mutable state breaks hand-partitioned concurrency.** The sidecar
  directory (last-writer-wins clobbered a judge's deployment registration) and
  the SQLite request cache (two workers on the same cell deadlock). Content
  addressing protected the OUTPUTS and I wrongly generalized that to safety.
  Prefer the scheduler over hand-partitioned tmux panes.
- **Exit code 0 is not success.** A rejudge whose every request failed still
  exits 0; attempts are now health-checked by parse status, and DEAD
  (request_error → infrastructure) is distinguished from DEGRADED (malformed →
  the judge genuinely cannot produce the format, which is a FINDING to keep).

## 2026-07-21 12:55:57 -0400 — Pivot to conceptual planning: adversarial TMLR thesis assessment

**Model/harness:** claude-fable-5[1m] (Fable 5, 1M context) via Claude Code.

**User intent:** Jon pivoted from execution to planning. He wants an
adversarial, reviewer-grade assessment of the paper direction (TMLR
reproducibility track), explicitly not optimized for agreement — including
against a six-question brainstorm from GPT 5.6 that he pasted in. His
motivation is fixed and should anchor everything: credible evaluation with
models ordinary researchers can run; the equity argument against
frontier-API gatekeeping. Deliverable: a coherent thesis, ≤3–4 RQs,
must-run vs. distraction triage, coordination with Edward's candidate
reproduction, and a concrete next-step plan.

**The assessment landed in `docs/planning/tmlr-paper-thesis.md`** — that
file is the durable artifact; read it before this entry. The one-line
verdict: the infrastructure is ahead of the science, and every proposed
headline framing (conclusion preservation, accessibility frontier,
agreement-predicts-conclusions) is currently unsupported for one structural
reason — **all 63 artifacts score a single candidate (gpt-oss-20b), and
every interesting endpoint is defined over a set of candidates.** The fix
is uniquely cheap in our design: official responses AND official judgments
for every leaderboard model already sit in the public corpus we mirror, so
candidate expansion costs zero candidate inference — only judge inference.
Priority inversion: candidates > judge families > benchmarks > judge sizes.
Jon's instincts (gemma4, more benchmarks) had the first two axes reversed.

**Design reasoning worth preserving:**
- *Thesis chosen:* published leaderboard conclusions that depend on
  proprietary judges are/aren't recoverable with open judges on consumer
  hardware; characterize the recoverable region, cost, and failure modes
  via the exact-replay harness. Three RQs: conclusion survival under judge
  substitution; decomposition with Edward (S(O,J) vs S(R,J), never
  claiming the factorial); do standard judge-health metrics predict
  conclusion survival (the red-team 2B cell — 99.9% parse, 25.7%
  agreement, label inversion — is the one-cell preview, and a negative
  answer is the most citable insight available). Plus a bounded RQ-S:
  iso-VRAM quantization (INT4-27B vs BF16-9B at 24 GB) so quantization
  serves the accessibility frontier instead of becoming a substrate paper.
- *Novelty wedge vs. PandaLM/JudgeLM/Prometheus/PoLL:* we train nothing
  and build no harness — we re-instrument the official scoring pipeline of
  a published leaderboard behind a machine-precision replay gate and ask
  whether the leaderboard's conclusions survive. The 6/6 replay at ≤2e-14
  is the methodological signature, not a methods footnote.
- *Rhetorical asset found while triaging limitations:* the unobservable
  cell S(R,J*) is impossible not merely for budget reasons — the official
  judge is a dated proprietary deployment that may no longer exist. The
  missing cell is itself evidence for the thesis.
- *Cuts:* off-HELM expansion, rubric-intervention and escalation-protocol
  studies (both second papers), full substrate grid, remaining Qwen ladder
  cells, any judge added without a named confound it controls.
- *Contamination:* Edward's freshly generated responses design out
  response-level memorization for every S(R,·) cell — the strongest cheap
  control we have, and the brainstorm missed it.

**Caveats on my own grounding.** Jon noted mid-session that not all results
are synced to this workstation — confirmed: local `analysis/` holds only
the two pre-sweep reports and `/data/crfm-helm-public` is absent here. All
results claims in the assessment rest on the journaled 2026-07-20/21
checkpoint (derived on aiq-gpu from the full artifact set), not on fresh
inspection. Two assumptions the plan explicitly requires verifying on
aiq-gpu before committing to it: per-benchmark model coverage in the
public corpus, and whether any corpus candidate postdates Qwen3.5's
2026-02-16 launch (if none, the contamination control lives entirely in
RQ2 via Edward's fresh responses).

**Next steps:** the §8 checklist in the thesis doc (corpus coverage,
post-cutoff candidate existence, HELM release dates, annotator-subset
completeness, Edward's format+list), then §5.1 candidate selection. The
in-flight kwdagger smoke (`./55_schedule_rejudge.sh omni_math --smoke
--run`, fixes in `22f72d5`) remains queued and unexecuted; nothing in this
session changed executable code. `submodules/every_eval_ever` gitlink
remains modified/unstaged per the standing rule.

## 2026-07-21 13:20:00 -0400 — Round 2: GPT 5.6 rebuttal absorbed into the thesis doc

**Model/harness:** claude-fable-5[1m] (Fable 5, 1M context) via Claude Code.

**User intent:** Jon relayed GPT 5.6's response to my adversarial
assessment. It accepted the single-candidate diagnosis and the 3-RQ
structure and pushed back on seven points. My job: adjudicate, verify its
literature claims, and fold what survives into
`docs/planning/tmlr-paper-thesis.md`.

**Literature verification (the load-bearing step).** GPT cited two papers
against our RQ3/novelty framing; one postdated my knowledge cutoff and
carried a ChatGPT-sourced URL, so I refused to treat it as real until
web-searched. Both check out: JuStRank (2412.09569, Dec 2024) benchmarks
48 judges by induced system rankings — the broad "instance metrics ≠
ranking quality" claim is occupied. SLMJury (2606.07810, June 2026)
sweeps 16 SLM judges 0.6B–14B over ten benchmarks — the "consumer-sized
judge sweep" FRAMING is occupied, meaning our size ladder can never be
the headline. Neither touches substitution inside a published
leaderboard's official pipeline under exact replay; the wedge survives,
narrower. Lesson worth keeping: when two models argue about novelty, the
citations are the part to verify first — a hallucinated occupier would
have wrongly shrunk our claim, a real one wrongly ignored would have
sunk the intro.

**Adopted from GPT round 2** (all now in the doc): two-level design
(broad tier = ALL leaderboard candidates on XSTest+WildBench with 2–3
judges, deep tier = pre-registered 8–12 across all six benchmarks);
candidate selection rule frozen before rejudging and computed only from
official public scores; RQ3 narrowed to the exact-replay/published-
conclusions form with out-of-group prediction, demoted to consequence of
RQ1; the conclusions.py statistical spec (predefined estimands, paired
joint bootstrap, MNAR parse-failure sensitivity — never per-model
denominators — no pseudo-replication); iso-VRAM renamed iso-hardware with
resource differences reported; consumer data points measured on the
actual 3090; S(R,J*) unobtainability framed as problem-evidence, never
solution-evidence; Edward's fresh responses stated narrowly as a
response-level-memorization control.

**Where I pushed back (also in the doc):** the broad tier's
one-replicate economy is unsafe for WildBench close pairs — the paired
bootstrap captures instance-sampling noise but not judge
non-determinism, and WildBench instance judgments are 43–46%
replicate-divergent, so broad-tier decisions carry the deep grid's
replicate-flip rate as an uncertainty floor or close pairs get
replicates≥2. Also flagged ops reality GPT skipped: infer-stack has
never been provisioned on the 3090 host; that's a scheduled work item,
not an assumption.

**Converged thesis (verbatim in the doc §4):** exactly reconstruct the
released scoring pipeline of a proprietary-judge-dependent leaderboard;
determine which published model-comparison conclusions remain
independently recoverable with open judges on a single 24 GB GPU;
measure how judge diagnostics, candidate drift, and metric structure
explain the boundary.

**State:** doc updated and committed this session; no executable code
changed; the kwdagger omni_math smoke remains queued and unexecuted;
`submodules/every_eval_ever` gitlink still deliberately unstaged.

## 2026-07-21 15:05:00 -0400 — Round 3: thesis reopened (Socratic round over Edward's draft + PM brief)

**Model/harness:** claude-fable-5[1m] (Fable 5, 1M context) via Claude Code.

**User intent:** Jon reopened the paper thesis before executing the round-2
plan. New evidence: the PM's deep-research brief and Edward's draft
(`uncommitted/`). Explicit instruction: Socratic engagement with ten
questions, challenge premises, report disagreements, do NOT converge; update
the planning doc only after reasoning. The suspicion driving it: the
open-judge work — originally a tack-on to Edward's HELM reproduction — may
have displaced the project that motivated it by being optimized into a
defensible paper.

**What I read before answering** (the discipline that mattered last round):
the full deep-research brief, Edward's main.tex skeleton, and the key
chapters of the 2,179-line claude.tex chronology. The chronology is far
stronger than "debugging stories": a 34,512→1,109 typed-reason census; the
fp32 discovery (unpinned-dtype HuggingFaceClient runs executed at float32
via a transformers 4.x BC default; fp32 recovers official completions
EXACTLY, quasi≈1.0 vs ≈0.17 fp16; 129/148 deployments unpinned → a
falsifiable corpus-wide prediction, tested so far on ONE family at n=12);
and the adaptation-layer pattern (base models reproduce near-exactly,
instruct models drift via chat-template/tokenizer versioning).

**The position I landed on (weakly held, recorded in §0.2 of the thesis
doc):** the strongest object of reproduction is the EXPERIMENT, not the
finding — "a recipe does not identify the experiment" — with the judge work
as the modern-failure-mode section (substrate lost irrecoverably → 
substitution + conclusion-survival), not the spine. The hostile reviews of
the reproduction-first and open-judge directions each patch the other,
which is the best argument they are one paper. But the decision is gated on
facts I cannot resolve from the repo, now recorded as D1–D5: the PM's 2023
constraint (Edward's evidence is later-suite HELM, not 2023), the EEE
authorship/claim boundary (EEE is a submodule here; reviewers will see
overlap), and Edward's timeline (who operates a prospective protocol).

**The design contribution of the round:** converting Edward's flagship from
anecdote to law-like claim via a prospective frozen protocol (§0.4 —
stratified sample from the 1,109, frozen diagnostic ladder, budget +
stopping rule, registered fp32 predictions), and four discriminating pilots
(§0.5) that decide the direction empirically for ~one week of GPU nights +
two zero-compute afternoons, instead of another argument round. P3
(fp32 cross-family) and P4 (one-benchmark judge conclusions pilot) carry
most of the information.

**Reversal to own honestly:** this morning I recommended the
full-leaderboard XSTest/WildBench sweep; round 3 pauses it. The new
information is Edward's draft and the PM framing — holding a sweep is
cheaper than unwinding one. Same lesson as the sankey-header incident from
the 07-20 entry: a decision optimized under an assumption ("the paper is
the judge study") must be re-costed when the assumption moves, not carried
as settled.

**State:** §0 prepended to docs/planning/tmlr-paper-thesis.md marking the
round-2 plan as the contingent branch plan; no executable code changed; the
omni_math kwdagger smoke remains queued and thesis-invariant;
`submodules/every_eval_ever` gitlink remains deliberately unstaged;
`uncommitted/` left uncommitted (it is other people's unvetted material —
that is what the folder name says).

## 2026-07-21 17:10:00 -0400 — Tonight's overnight: evidence ledger + fp32 e2e confirm plan

**Model/harness:** claude-fable-5[1m] (Fable 5, 1M context) via Claude Code.

**User intent:** decide exactly what to run tonight on aiq-gpu. Constraint
reframed by Jon/GPT: the scarce resource is Edward's remaining two weeks and
unexternalized forensic knowledge, not GPU capacity. Human-audit protocol
dropped from the near-term plan (open-judge results = fidelity +
conclusion-preservation, never human validity). Also: results rsync to this
workstation was IN PROGRESS during this session — ledger built from what was
synced plus the draft; late-arriving artifacts could revise details.

**Evidence reconciliation (the requested ledger, from PRIMARY artifacts not
prose).** The "four OLMo models exactly recovered" language overstates: exact
is literal only for OLMoE (HF fp32 eager, 12/12 quasi+exact, first-token
0.917, request knobs ast1-agp0); dense OLMo-2 are vLLM fp32 FLASH_ATTN
MATCH at 10/12, 10/12, 11/12 (7B/13B/32B), all n=12 ifeval probes from the
07-10 overnight sweeps, all probe-only — the confirm step ("full local run
vs official") that Edward's own tool emits per sweep has NEVER been
executed. End-to-end locals (bf16-default vLLM, no dtype pinned in our own
catalog either — same sin as HELM's) show ifeval_strict_accuracy local
ABOVE official by +0.098..+0.126 on all four instruct models. Propositions:
A (unpinned⇒fp32) mechanism-verified, one family; B (fp32 recovers
completions) probe-only; C (recovered config changes aggregates/conclusions)
untested.

**Zero-GPU finding this session:** pairwise-ordering flip analysis over the
existing aggregate_score_diff_headline.json — 4/25 OLMo pairs flip
official-vs-local (gpqa 3/6 incl. 13B↔32B; bbq 1/6), while ifeval with the
LARGEST drift flips none (its +0.10 is ~uniform). Qwen experiment: 1/201.
So procedural drift already flips conclusions in our data, and drift
magnitude does not predict conclusion damage — a preview of both the
claim-level layer and the "diagnostics don't predict conclusions" thesis at
the candidate level. Caveat: gpqa official gaps ≈1.3σ; the paired bootstrap
must decide which flips are statistically real.

**Tonight (recommended; plan pre-registered in
reproduce/olmo_models_combined/deployment_match/overnight_confirm_plan.md):**
PRIMARY = execute the never-run confirm step end-to-end for dense OLMo-2 7B
+ 13B (fp32 dm endpoints from the sweeps' confirm/ catalogs, normal HELM
from-spec path, ONLY dtype+ast moved vs the bf16 baseline) — tests B at
full n and C directly against the +0.098/+0.126 targets. CONCURRENT on the
two free cards: two cross-family deployment-match sweeps chosen by a frozen
census rule with registered per-cell predictions (pinned-dtype candidate
kept as a bidirectional control). 32B (tp2, slow) and OLMoE e2e (HF
in-process routing switch unwired — do not improvise) explicitly excluded.
FALLBACK if fp32 e2e wiring exceeds ~90 min: all four cards to cross-family
sweeps. The ast0 probe-only-knob warning in confirm_plan.md is the known
integration risk; Edward chooses the HELM-native route (tokenizer sibling,
precedent 74ba33d, vs client patch) — precisely the judgment we are
spending his time on.

**Why this over the alternatives:** it is the only option that tests B and C
(not just A), uses infrastructure that already exists end-to-end, needs
Edward before (wiring judgment) and after (forensic interpretation), and
yields a flagship-figure-or-major-course-correction by tomorrow. The
omni_math kwdagger smoke runs only as an optional ~1h early-evening item;
judge expansion, leaderboard sweeps, 3090 work all stay paused per the
scarce-resource framing.

## 2026-07-21 19:55:00 -0400 — Overnight launched; template bug found and fixed live; session close-out

**Model/harness:** claude-fable-5[1m] (Fable 5, 1M context) via Claude Code.

**State at close: three jobs running on aiq-gpu** (Jon confirmed) — the
OLMo-2 7B and 13B fp32+agp0 end-to-end ifeval replays (fresh suites
`audit-allenai-olmo-2-1124-{7b,13b}-instruct-ifeval-fp32`) and the
marin-8b-instruct ifeval deployment-match sweep. Everything scientific
about tonight (propositions A/B/C, probe factorial, registered
predictions, corpus scan facts, interpretation rules) is consolidated in
`docs/helm-reproduction-research-journal.md` under "The fp32/agp0
substrate-recovery experiment"; the frozen protocol + morning readout is
`reproduce/olmo_models_combined/deployment_match/overnight_confirm_plan.md`.
This entry records the session mechanics and lessons.

**Repo state.** Working on `main` from now on (Jon merged his + Edward's
branches; `main..jons/qwen35-extension` is empty). Today's commits through
`1adb0a3` are LOCAL to this workstation clone — it has no GitHub
credentials (https origin, no gh, no credential helper), so nothing is
pushed; aiq-gpu picked the commits up by pulling from this checkout over
the shared home. Someone with credentials should `git push origin main`
from a machine that has them, or the work exists only on two NFS clones.

**The live failure and its lesson.** Both `60_confirm_fp32_e2e.sh`
launches failed at step [1/4]: my template patcher matched the exact
string `{% if add_generation_prompt %}`, but OLMo-2 guards the assistant
tag with a COMPOUND conditional — `{% if loop.last and
add_generation_prompt %}` — so zero matches. Fixed by neutralizing the
bare identifier (word-boundary regex → `false`) and printing each original
context for the operator eyeball; verified against the real template
fetched from the hub before recommitting (`1adb0a3`). Lesson, same genus
as the strip_thinking saga: never match an exact serialized form of
something a third party generates — match the invariant (here, the
identifier) and show your work to the operator. Also: the fail-loudly
step design did its job — nothing half-launched, the operator pasted two
clean FAIL blocks, and the fix took minutes.

**Scan review caught a burned-night hazard.** The frozen rule picked
phi-3-small-8k-instruct as sweep #1, but it requires `trust_remote_code`
and the deployment-match grid hard-pins `trust_remote_code: [False]` —
every serve cell would have failed on GPU2 all night. Deferred with a
typed reason; its CONTROL prediction (torch_dtype:auto ⇒ bf16 wins, fp32
loses) registered before any execution so the pre-declaration is intact.
~10-line grid.py patch tomorrow (sweep the axis or inherit from the
official client args), then run it — the treatment/control pair is worth
having. Also verified from HELM's model_deployments.yaml: gemma-2-it
officials are TogetherClient — the scan's "skip" was CORRECT, not a bug;
Together-served officials belong to the irrecoverable-substrate category
by design.

**Ops facts worth remembering when writing the paper's repro appendix:**
the e2e path needs NO manual acquire (`eval-audit-run --lease`
self-acquires from manifest `lease_endpoint`/`lease_catalog`); the variant
bundle trick is manifest surgery (ifeval-only run_entries, fresh
suite/experiment name so skip_existing never collides, model_deployments
`openai_model_name` pointed at the dm endpoint route, api key from
`infer-stack env LITELLM_MASTER_KEY`); 60/65 scripts are the durable form
of all of it. 13B may still be running in the morning — expected, don't
kill it.

**Tomorrow morning, in order:** (1) run the two compare-pairs per
`confirm/confirm_plan.md`, classify against the frozen outcome classes,
fill the results table in overnight_confirm_plan.md; (2) read
marin's `results/ranking.txt` against its registered prediction; (3) rerun
the pairwise-flip analysis including the fp32 locals — does fp32 restore
the official orderings?; (4) patch grid.py for trust_remote_code and
launch the phi-3-small control cell; (5) push main from a credentialed
machine. The omni_math kwdagger smoke (still queued, still unexecuted)
remains the open-judge thread's next step whenever a card frees up.

## 2026-07-22 morning — Overnight triage: both failure modes diagnosed from synced artifacts, fixed

**Model/harness:** claude-fable-5[1m] (Fable 5, 1M context) via Claude Code.

**Jon's report:** "I think there were failures." Full audit store + large
parts of crfm-helm-public and crfm-helm-audit now rsynced locally, so the
whole triage ran from primary artifacts on this workstation without
touching aiq-gpu.

**Failure 1 — both e2e fp32 runs died at serve; zero artifacts.** Bundles,
templates, and patched catalogs all exist (script reached step 4), but no
`*ifeval-fp32*` suite dirs and NOTHING written after 18:00 anywhere.
Mechanism, confirmed by code-reading infer-stack: `--chat-template` is
passed VERBATIM into the container command (profile_runtime.py), and the
vLLM container mounts ONLY the five cache dirs (compose.py `_vllm_service`)
— the audit-store path I wrote the templates to does not exist inside the
container. vLLM crash/unready → acquire timeout → no run. Corroborated:
the synced confirm catalog still carries the stale
`--chat-template /data/crfm-helm-audit-store/...` in extra_args. Fix in
`60_confirm_fp32_e2e.sh`: write the template to the HOST side of the
hf-cache mount (`$INFER_STACK_DATA_DIR/hf-cache/chat-templates/`),
reference it by CONTAINER path (`/root/.cache/huggingface/...`), scrub any
stale extra_args pair, and set the NATIVE `chat_template` runtime key —
which is in infer-stack's STRUCTURAL_KEYS, so the changed template makes a
distinct deployment instead of coalescing onto the stale one. Also added a
pre-start `infer-stack gc` (a failed launch leaves a leaked lease and a
crash-looping `restart: unless-stopped` container). Patch dry-run
validated against the real synced catalog. **Lesson (recurring genus):
a path handed to a containerized service is a claim about the CONTAINER's
filesystem, not the host's — same family as the fresh-login-shell lease
bug (22f72d5): state you can see is not state the executor can see.**

**Failure 2 — marin sweep completed but is an INVALID test, not a
refutation.** `resolution.json`: `protocol_resolved: False` — the resolver
could not detect chat markers in the marin official (llama3-style template,
unlike OLMo's) and silently DEFAULTED to raw completions, noting it only in
a notes field. All 32 cells (incl. fp32 — the grid was right) probed
wrong-shaped prompts: best cell auto/PARTIAL 0.158, quasi 0.0; snippets
show on-topic-but-divergent completions and an official REFUSAL where all
locals answer — assistant-persona behavior the raw-completions probe
cannot elicit. The registered fp32 prediction is UNTESTED, not falsified;
plan table updated to say exactly that. Fix: `DM_PROTOCOL` passthrough in
`run_deployment_match.sh` (cli already had `--protocol`); rerun into a
fresh `--ifeval-chat-vllm` out dir. **Lesson: a resolver that silently
defaults on failure converts "I don't know" into a wrong experiment; the
sweep should hard-fail on unresolved protocol for instruct models — worth
a follow-up patch.**

**Relaunch (pending Jon):** pull → `./60_confirm_fp32_e2e.sh 7b` /
`13b` → marin rerun with `DM_PROTOCOL=chat`. Predictions unchanged,
registered in overnight_confirm_plan.md. Push of main from a credentialed
machine still outstanding; commits remain NFS-local.

## 2026-07-22 (later) — Relaunch: 7B passed e2e; 13B killed by concurrent infer-stack churn

**Model/harness:** claude-opus-4-8[1m] (Opus 4.8, 1M context) via Claude Code.

**Recipe VALIDATED end to end.** After the container-path + permission fixes,
`60_confirm_fp32_e2e.sh 7b` completed and 13B served successfully for ~54
minutes (19:31→20:25) — endpoint up, agp0 template loaded, HELM grinding
IFEval instances through the fp32 vLLM endpoint. Both the 2026-07-21 template
failure modes are closed.

**13B failed on CONTENTION, not the recipe.** At 20:25:41 the 13B endpoint
returned a 500 "Connection error" (litellm could not reach the vLLM upstream),
retried 5× over ~4 min, then got 404 "model does not exist" (route gone), and
the run died. The teardown compose shows WHY: two other deployments were live
and being (re)placed — `dm-marin-8b-instruct-bf16-attntorch-sdpa` (the marin
chat-protocol sweep, mid-grid) and `qwen3-5-2b-judge`. All three shared ONE
infer-stack. The marin sweep's per-cell acquire→serve→probe→release loop
converges the compose project on every cell; one of those concurrent
converges tore down the long-lived, leased 13B endpoint. The 7B escaped only
because it finished before the churn hit it.

This is the SAME lesson already twice in this journal (shared mutable state +
hand-partitioned concurrency): the judge fan-out moved to kwdagger for exactly
this reason. Running a slow e2e confirm, a deployment-match sweep, AND a judge
against one infer-stack is the hazard. **A held lease on an in-demand endpoint
should be evict-proof against a concurrent converge; that it wasn't is an
infer-stack robustness gap worth a follow-up** (demand refcount / placement
re-solve should never stop a leased container). For now: serialize.

**Fix = run the 13B SOLO.** Let the marin-chat sweep finish (or kill it), then
`./60_confirm_fp32_e2e.sh 13b` alone. The failed run left a partial
`prod_env/cache/vllm.sqlite`; HELM's request-keyed cache means the rerun
RESUMES the ~54 min of completed generations and only redoes the tail, so
solo-and-slow is not a full restart.

**Blocked on a sync.** The fp32 RESULT dirs
(`/data/crfm-helm-audit/audit-allenai-olmo-2-1124-{7b,13b}-instruct-ifeval-fp32/`)
are NOT yet rsynced to the workstation — only the bundles are — so the 7B
`ifeval_strict_accuracy` (the actual science: does D_fp32→0 from the +0.098
gap?) cannot be read here yet. That number is the whole point; requested.

**Side note (not a bug):** the 13B log loads the 7B tokenizer
(`allenai/olmo-2-1124-7b-instruct`) — this is the known HELM quirk where the
13B deployment's tokenizer_name points at the 7B (identical tokenizer across
the OLMo-2 dense family); recorded in the 07-10 entry, expected.

## 2026-07-22 (evening) — fp32 e2e RESULT: precision necessary but NOT sufficient (prediction refuted)

**Model/harness:** claude-opus-4-8[1m] (Opus 4.8, 1M context) via Claude Code.

**Both e2e runs completed and are readable** (pulled to yardrat via the new
`pull-data-from-aiq-gpu.sh`; 7B fresh, 13B completed via cache-resume in the
same helm_id after the 07-22 contention failure). Denominators verified equal:
1082 per-instance rows for both official and local on each model, so the
comparison is valid; ifeval has no judge/parse step, so no MNAR denominator
trap.

**The number the whole thread was driving at:**

| model | official (HF fp32) | local fp32+agp0 (vLLM) | D_fp32 | prior bf16 gap | gap closed |
|-------|------|------|------|------|------|
| OLMo-2-7B  | 0.6929 | 0.7597 | **+0.067** | +0.098 | 32% |
| OLMo-2-13B | 0.7298 | 0.8121 | **+0.082** | +0.126 | 35% |

**The registered prediction (D_fp32 → ~0, full recovery) is REFUTED.** Against
the frozen outcome classes both land in ANOMALOUS/partial: not EXACT (≤0.01),
not MATERIALLY-IMPROVED (≤0.5·bf16), not UNCHANGED (≥0.8·bf16). Directional
prediction (fp32 helps) confirmed; magnitude prediction (recovers the official)
refuted. The local still scores ABOVE official by +0.07-0.08.

**Interpretation (honest, and arguably a BETTER paper result than "fp32 fixes
it"):** precision + old-template rendering are NECESSARY (bf16 was worse, agp1
was far worse) but NOT SUFFICIENT for the OLMo-2 instruct ifeval score. A
systematic residual remains. Leading hypothesis: the **engine gap** — officials
are HELM HuggingFaceClient (`transformers.generate()`) at fp32; our e2e is
vLLM at fp32. The 07-10 "residual puzzle" already documented that same-fp32
forward passes diverge across engine/attention-impl/device-topology. Two tells
that it's systematic not noise: the closure fraction is ~1/3 on BOTH sizes, and
the residual is same-signed (local higher) on both — consistent with the local
following instructions slightly better, e.g. vLLM true-greedy vs HELM's
`do_sample=True, temperature=1e-7`.

**Decisive next test: the HF-in-process fp32 path** — reproduce the official the
SAME way it was produced (in-process transformers.generate at fp32, same engine),
removing the vLLM↔HF variable. The mechanism exists (`hf_inprocess.py` +
`acquire --reserve-gpus`), but the replay routing switch that would make a
HuggingFaceClient official auto-route to it was "scoped but left unwired"
(07-10 journal, chap. substrate). Wiring that switch is now the highest-value
next step — it converts this partial result into a clean yes/no on whether fp32
on the ORIGINAL engine fully recovers the official. Alternatives to rule out:
n=12 probe unrepresentative (now moot — we have the full-run number); the
sampling-mode difference.

**Paper implication.** This strengthens, not weakens, the "recipe does not
identify the experiment" thesis: even after recovering the two variables the
probe flagged (precision, template), a THIRD (engine numerics) still moves the
published metric. Substrate recovery is layered; a single fix is not enough.
For the write-up, the OLMo-2 ifeval cell is "materially reduced, not recovered,
residual attributed to engine" — pending the HF-in-process confirmation.

**Script note.** `pull-data-from-aiq-gpu.sh` gained `credentials.conf` to the
default secret excludes (third per-run secret after model_deployments.yaml /
lease.env); rsync exit 23 correctly reported as ok-partial. The pull moved
75G (audit-store) + 240G (audit) cleanly.

## 2026-07-22 (night) — Overnight setup: HF-fp32 full-N probe (engine-gap test), simple runbook

**Model/harness:** claude-opus-4-8[1m] (Opus 4.8, 1M context) via Claude Code.

**Goal:** a simple, committable, low-risk overnight run that advances the open
question from tonight's e2e result (is the +0.067/+0.082 residual the vLLM<->HF
engine gap?). Chose the HF-fp32 full-N probe over two alternatives:
- HF-in-process FULL metric run (the metric-level engine test): the docker node
  DOES support a reserved GPU (`lease_reserve_gpus` -> `--gpus device=$CUDA_VISIBLE_DEVICES`),
  but `hf_inprocess.py` has NO caller assembling the bundle — the routing switch
  is unwired, so this is new untested assembly. Untested overnight wiring has
  failed three nights running; NOT for unattended use. Deferred to daytime with
  a smoke gate.
- 32B fp32 vLLM e2e (complete the size ladder): proven path but predictable, and
  does not address the engine question.

**Chosen:** `70_hf_fp32_fullN_probe.sh <7b|13b>` + `71_overnight_hf_fp32.sh`
(runs 7b then 13b serially). Thin wrappers over the PROVEN hf-probe path
(run_deployment_match.sh DM_HF_FP32=1, already run at n=12). Pure
transformers.generate at fp32 with decode=helm (do_sample=True, temp->1e-7 —
matches the official's actual decode, NOT true-greedy), max_tokens from the
run_spec (2048, not truncated), across all 541 ifeval instances, scored vs the
official completions. SELF-CONTAINED: no infer-stack, no lease, no vLLM, no
chat-template file, no litellm — so serving/contention/template failure modes
cannot occur. Serial-single-GPU by design (the 13B contention death was a
concurrent converge tearing down a leased endpoint; here there is no lease).

**Interpretation set BEFORE the run:** high completion agreement (quasi ~1.0)
across the full set => the official is faithfully HF-fp32 and the vLLM e2e
residual IS the engine gap (paper: "reproducing on the original engine recovers
the official; vLLM introduces the residual"). Low agreement => a deeper
unrecovered factor even on the original engine. Nuance to separate later with a
decode=greedy variant: our vLLM e2e differed from the official in BOTH engine
(vLLM vs HF) AND decode (greedy vs sample-1e-7); decode=helm here matches the
official on both, so a confirm attributes the residual to that pair jointly, and
an HF decode=greedy run would split engine from decode.

**Note:** hf-probe scores completion agreement, not ifeval_strict_accuracy
directly — but identical completions imply the identical metric, so quasi~1.0 IS
the answer. If a metric-level number is wanted, that needs the HF-in-process
full run (the deferred daytime task).

Scripts committed; run: `./71_overnight_hf_fp32.sh`. Push of main from a
credentialed machine still outstanding (commits NFS-local).

## 2026-07-23 11:00 -0400 — Overnight over-scoped: HF probe was a 16-cell x 541 sweep; corrected to a small-n config search

**Model/harness:** claude-opus-4-8[1m] (Opus 4.8, 1M context) via Claude Code.

**Bug in my overnight setup.** `70_hf_fp32_fullN_probe.sh` pinned only dtype and
decode, leaving the hf-probe's forward-pass axes at their sweep defaults —
attn{eager,sdpa} x device_map{auto,single} x ast{both} x agp{both} = 16 cells,
each generating all 541 ifeval instances at fp32 batch-1 (~24-48h, not a night).
Worse, the sweep starts on agp1, which we already know does NOT match OLMo-2
(agp0 is the effective old-template behavior). rsync looked stalled because
hf-probe writes per-cell and each cell is hours. Jon caught it at 11am, ~11h in,
still early in the sweep.

**Root cause = a misread of the 07-10 finding.** I framed the HF probe as a
faithful full-n reproduction, but 07-10 established that for OLMo-2 DENSE the HF
probe does NOT reproduce the official out of the box: device_map=auto shards a
one-GPU model and changes the fp32 reduction order (first-token agreement 0.42).
So the HF path is a CONFIG SEARCH (which attn x device_map reproduces the
official), and config search is small-n by nature (07-10 used n=12; OLMoE hit
quasi 1.0 there). Full-n was overkill on the wrong thing.

**Correction (committed):** renamed 70->`70_hf_fp32_probe.sh`,
71->`71_hf_fp32_both.sh`. 70 now PINS the known axes (agp0, ast1, fp32,
decode=helm) and sweeps ONLY the two forward-pass axes that move greedy fp32
logits (attn{eager,sdpa} x device_map{auto,single} = 4 cells) at DM_N=32
(~20-40 min). A cell at quasi ~1.0 => HF-fp32-<config> reproduces the official,
so the official IS HF-fp32 and the vLLM e2e residual is the engine gap; then a
full-n confirm of the winner via `DM_HF_DEVMAPS=<w> DM_N=541 ./70... <size>`.
Strong prior from 07-10: device_map=single is the fix.

**Lesson (recurring, now explicit):** when wrapping a tool that SWEEPS by
default, the wrapper must pin every axis it does not intend to sweep — an
unpinned axis silently multiplies the job. Same failure family as the earlier
under-constrained runs; I sized for 1 cell and shipped 16. Killed and corrected;
no result lost (the partial cells were agp1, already known non-matching).

## 2026-07-23 (midday) — RESOLVED: HF-fp32 reproduces the official EXACTLY; the vLLM residual is the engine gap

**Model/harness:** claude-opus-4-8[1m] (Opus 4.8, 1M context) via Claude Code.

**The multi-day substrate question is answered.** Scored the HF-fp32 config
probe's partial cells (n=32) against the official completions by hand (probe's
own ranking.txt lands only at the end):

| model | cell (fp32/eager/agp0/decode=helm) | exact | quasi | first40 |
|-------|------|-------|-------|---------|
| 7B  | device_map=single | 1.00 | 1.00 | 1.00 |
| 7B  | device_map=auto   | 1.00 | 1.00 | 1.00 |
| 13B | device_map=auto   | 1.00 | 1.00 | 1.00 |

**BYTE-IDENTICAL. 32/32 exact on both models.** HF-fp32 with eager attention,
agp0 (old-template rendering), decode=helm reproduces the official OLMo-2
instruct ifeval completions exactly. Conclusions:

1. The official runs ARE HF-fp32 — confirmed to byte-exactness, not inference.
2. The vLLM-fp32 e2e residual (+0.067 7B / +0.082 13B on ifeval_strict_accuracy)
   is ENTIRELY the vLLM<->HF engine gap. Same precision, same prompt, same
   model — different engine → +0.07 on the published metric.
3. device_map=auto == single here (7B/13B fp32 fits one 96GB card, no shard);
   the 07-10 device_map worry was smaller-card hardware. eager=exact, so sdpa
   cells are moot.

**The subtle, paper-worthy part:** our vLLM e2e used the run_spec's
temperature=0.0, which vLLM executes as TRUE greedy (argmax). HELM's
HuggingFaceClient maps temperature==0 -> do_sample=True, temp=1e-7. So the SAME
run_spec (temperature=0.0) produces DIFFERENT decode behavior depending on the
engine — the residual is the engine and its interpretation of a nominally
identical recipe. That is the thesis ("a recipe does not identify the
experiment") demonstrated at the decode level, not just precision.

**Substrate fully decomposed for OLMo-2 instruct ifeval:** precision (fp32,
unpinned default) + template rendering (agp0, transformers-version-dependent) +
engine/decode (HF transformers.generate, temp0->1e-7). Recover all three and the
official reproduces EXACTLY; miss the engine and you get +0.07 on the metric;
miss precision or template and it's far worse. This is the cleanest possible
evidence for the reproduction-first thesis: the published number is exactly
recoverable, but ONLY with the full unrecorded execution substrate.

**Status:** n=32 byte-exact is conclusive at the completion level (chance ~0);
the full-n (541) runs still finishing will report the metric (predicted D_HF~=0
since identical completions => identical ifeval_strict_accuracy). The engine-gap
question needs no further test. Optional decomposition (HF-greedy vs vLLM-helm)
would split engine from decode-semantics but is not needed for the headline.

**For the paper.** OLMo-2 ifeval cell final status: "exactly reproduced on the
original engine (HF-fp32); vLLM introduces a +0.07 metric residual attributable
to the engine and its temperature=0 decode semantics." Strongest single result
of the fp32 thread.

## 2026-07-23 (7B ranking complete) — all 4 HF forward-pass configs match exactly

7B HF-fp32 probe finished (ranking.txt). ALL FOUR cells — {eager,sdpa} x
{auto,single} — are MATCH at composite 1.000 / quasi 1.00 / exact 1.00 /
first-token 1.00 (n=32). So attn impl and device_map are NON-FACTORS for
OLMo-2-7B fp32 on 96GB cards: only engine (HF) + fp32 + agp0 + decode=helm
determine the output, and all four give byte-exact reproduction. This retires
my 07-10 "device_map=auto sharding" concern for this hardware (nothing shards
when the model fits one card) and makes the reproducing recipe unambiguous.
13B still running; expected identical. Optional: a full-n (DM_N=541) confirm of
one winning cell would upgrade "32/32 exact" to "541/541 exact" for the paper.

## 2026-07-23 (13B ranking complete) — fp32/substrate thread CLOSED on both models

13B HF-fp32 probe finished: all 4 cells ({eager,sdpa} x {auto,single}) MATCH
1.000 exact (n=32), same as 7B. The fp32/substrate thread is complete for
OLMo-2 instruct ifeval on both sizes. Final decomposition of the drift:
  bf16 + modern template      -> +0.10 metric (original mystery)
  vLLM fp32 + agp0            -> +0.07 (precision + template recovered)
  HF   fp32 + agp0 + decode=helm -> EXACT (engine recovered)
Each unrecorded substrate layer closes a defined slice; recover all three and
the published number reproduces byte-exactly.

### Recommended next steps (recorded for the resumption)

1. CHEAP/HIGH-VALUE — attribution probe: `DM_HF_DECODE=greedy ./70_hf_fp32_probe.sh 7b`.
   Splits the vLLM residual into ENGINE numerics vs DECODE semantics. We know
   HF-fp32-helm(do_sample/1e-7)==official exactly and vLLM-fp32-greedy left +0.07.
   If HF-fp32-GREEDY also ==official -> the residual is pure engine (vLLM kernels);
   if HF-greedy DIFFERS -> decode semantics (temp=0 true-greedy vs 1e-7-sample)
   is the driver. Sharpens the claim from "engine gap" to the precise cause.
2. OPTIONAL RIGOR — full-n confirm: `DM_HF_DEVMAPS=single DM_N=541 ./70... 7b`
   (and 13b) upgrades "32/32 exact" to "541/541 exact" for the paper.
3. THE PAPER WORK (Edward-led, clock-limited) — the prospective forensic
   protocol (thesis doc s0.4): the substrate-recovery METHOD is now proven
   end-to-end on one cell; apply it to a FROZEN stratified sample across
   families/benchmarks to make it a general result, not one deep case. Highest
   strategic value; needs Edward's context while he is here.
4. GATING DECISIONS — the fp32 flagship strengthens the reproduction-first
   spine; time to resolve round-3 D1-D3 (PM 2023 scope, EEE boundary memo,
   Edward timeline) so the paper structure is locked before writing.

## 2026-07-23 (attribution) — the vLLM residual is PURE ENGINE, not decode

Ran the corrected greedy attribution probe (DM_HF_DECODE=greedy). Result:
- HF-fp32-GREEDY reproduces the official EXACTLY too — all 4 forward-pass cells
  MATCH 1.000 (n=32), same as HF-fp32-helm.
- helm vs greedy completions: 0/32 differ (byte-identical). So HELM's
  temperature=0 -> do_sample=True/temp=1e-7 mapping is equivalent to true greedy
  for these instances — near-zero temp does not flip argmax. The decode-semantics
  nuance I flagged is a non-issue for OLMo-2 ifeval.

**Attribution nailed.** vLLM-fp32 used greedy (temp=0) and left +0.07/+0.082;
HF-fp32-greedy matches the official exactly. Same model, same fp32, same prompt,
same GREEDY decode — only the engine differs (vLLM vs HF). Therefore the residual
is PURE ENGINE NUMERICS (vLLM's fp32 kernels differ from HF's), NOT precision,
template, or decode. This is the strongest form of the claim: vLLM is a genuinely
different measuring instrument from HF at fp32, and to faithfully reproduce an
HF-client official you must run HF (or a numerically-matching engine) — the
inference engine is part of the experiment.

Final decomposition (OLMo-2 instruct ifeval, both 7B/13B):
  bf16 + modern template          -> +0.10  (precision + template both wrong)
  vLLM fp32 + agp0                -> +0.07  (precision + template fixed; ENGINE wrong)
  HF fp32 + agp0 (helm OR greedy) -> EXACT   (engine fixed)

The fp32/substrate thread is now fully closed AND attributed. No further runs
needed for this result.

## 2026-07-27 16:13:25 -0400

**Model/harness:** claude-opus-5[1m] (Opus 5, 1M context) via Claude Code
(VSCode extension).

**User intent:** "Update the latest Master Collaborative Reference document
with all changes that happened from when it was written to now," with a
follow-up correction mid-task: *make sure you use the latest zip*.

**The disambiguation that shaped the task.** Four candidate zips sit in
`docs/`. The newest by mtime is
`Master_Collaborative_Reference_Consensus_ACCEPTED_2026-07-15d.zip` (14:38),
but it is a four-file reply package — an acceptance letter, furnished source
hashes, and a store-provenance audit — with no `main.tex` in it. The document
lives in `..._Consensus_2026-07-15c.zip` (14:20). So "the latest" is neither
one alone: it is 15c *as amended by* 15d. I treated 15d's two substantive
contents (the furnished commit/tree SHAs, and the disk-level audit finding
that the flagship stores are pruned) as edits to apply, not as an appendix to
staple on, and folded the package itself into `validation/` for the audit
trail. Worth recording because "latest file" and "latest document" diverged,
and the mtime answer would have produced the wrong deliverable.

**Deliverable.** `docs/Master_Collaborative_Reference_2026-07-27/` (committed,
`45e00b90`) plus a convenience zip left untracked. Four new chronicle
chapters covering 2026-07-16..27 (127 commits): the Qwen3.5 extension, the
open-judge experiment, closing the substrate question, and paper
direction/manuscript. Two new canonical ledgers. A changelog mapping every
edit back to 15c.

**Three editorial decisions worth keeping.**

1. *Mark superseded rulings in place; never rewrite them.* The 15c consensus
   ruling that the fp32 result was "discovery, not reproduction" is now
   discharged, and the ruling that the dense OLMo-2 HF divergence must keep a
   probe artifact on its candidate list turned out to be exactly right. Both
   are annotated with a resolution, not replaced. The document's own stated
   method is that a reader must not mistake superseded wording for a final
   conclusion — but the inverse failure is worse in a consensus document:
   deleting a ruling erases the fact that a reviewer's caution was vindicated.
   §"The residual puzzle" is now the clearest example in the reference of a
   correction that strengthens the thesis while withdrawing the sentence that
   was supposed to support it.

2. *Report the refutation as the result.* The natural summary of July 21-23 is
   "we proved the officials ran fp32." The honest one, and the one the chapter
   leads with, is that the registered prediction was refuted at full n and the
   byte-exact recovery came only after the engine was added. I wrote the
   chapter in that order deliberately: prediction, refutation, resolution.
   The pre-registration is what makes the layered decomposition a result
   rather than a fit.

3. *Scope every new claim in the same breath as stating it.* Each new
   established claim carries its limit inline — one benchmark/one family/two
   sizes/instruct-only, completion-level on n=32 rather than metric-level, and
   the judge findings' single-candidate and contamination bounds. This is the
   discipline the 15c revision imposed on the older claims; applying it to the
   new ones at authoring time is cheaper than a third interpretive pass.

**Honest gaps in what I shipped.** No LaTeX toolchain on this host, so the
PDFs are the 15c build, renamed with explicit `_2026-07-15c` /
`_SUPERSEDED` suffixes and flagged twice (README + changelog) rather than
silently left in place. No commit ledger regenerated for the new window. And
the load-bearing one: every figure in the two new empirical chapters is read
from journal records, not from re-inspected artifact stores — the stores
themselves are unpreserved, which the new store ledger now says in the
`publication_status` column for each.

**Process note.** A concurrent commit of Edward's swept my staged 41 files
into `ac8eafec` under a TMLR-only message. I asked before touching it rather
than assuming, then split it: soft reset, re-commit the TMLR change with the
original message/author/date verbatim, then commit the package. Verified with
`git diff ac8eafec HEAD --stat` returning empty — the split preserved the tree
exactly. Lesson: in a repo where a human may be committing concurrently,
`git add` of a large tree is not a private operation; stage and commit in one
step, or check `git log -1` immediately before committing.

**Next steps.** Rebuild the PDFs (`pdflatex main.tex` twice, in `chronicle/`
and `strategy/`) on a host with TeX. Regenerate the commit-ledger appendices
for 2026-07-16..27. If the reference is going to a reviewer, the store
preservation actions in `store_status_ledger_2026-07-27.csv` are the blocking
item — the two flagship stores need re-running, and the July 21-23 substrate
artifacts and the judge snapshots need copying and hashing before any of the
new chapters' numbers can be cited as packaged evidence.

## 2026-07-28 12:41:00 -0400

**Model / harness.** Claude Opus 5 (`claude-opus-5[1m]`), Claude Code in the
VS Code extension, on `aivm-2404`.

**User intent.** Two asks, the second arriving mid-turn: (1) the table in the
TMLR paper's taxonomy section runs off the page; (2) conform every table to
TMLR's stated table rules — centered, title *before* the table, one line space
before/after the title and after the table, sentence-case title.

**Constraint that shaped everything: no TeX on this host.** `pdflatex`,
`latexmk`, and `tectonic` are all absent, so I could not compile to read the
overfull-hbox warning and confirm which cell protruded. That pushed me from
"find and patch the one bad cell" to "remove the structural conditions under
which any cell can protrude" — a fix I can justify by reading the source alone.

**Diagnosis.** `tab:identifiability` was
`\begin{tabularx}{\linewidth}{@{}p{0.26\linewidth}p{0.20\linewidth}Y@{}}`.
Two things are wrong with that shape, and neither is the one I first suspected.
The *total* width is fine: tabularx solves for the X column so the columns sum
to exactly `\linewidth`, tabcolsep included, no matter what the fixed fractions
are — so the table box itself cannot be too wide. What can overflow is the
*content* of a cell, and the fixed `p{}` columns are justified. A 0.20\linewidth
column is ~94pt; a word TeX cannot hyphenate there has nowhere to go and sticks
out into the margin as an overfull box. Justified narrow columns are the hazard,
not the arithmetic.

**Alternatives considered.** (a) Shrink the fractions — guesswork without a
compile, and it re-breaks the moment a cell's text changes. (b) `\raggedright`
on the two `p{}` columns only — fixes the symptom but leaves the hand-computed
fractions to drift out of sync with the X column on the next edit. (c) What I
did: convert to *weighted* X columns via two new column types in the preamble,
`Z{w}` (ragged-right) and `C{w}` (centered), each doing `\hsize=#1\hsize` inside
`>{}`. tabularx then allocates the entire width itself and each column's share
is a weight summing to the column count (0.80/0.55/1.65 here). Ragged-right
comes along for free in the column type, so the justification hazard is gone by
construction rather than by remembering to add `\raggedright`. Applied the same
conversion to the two appendix tabularx tables, which had the identical mixed
shape.

**TMLR rules.** Moved `\caption`+`\label` above the body in all four tables and
wrapped the body in `center` — that is the idiom the ICLR/TMLR template itself
uses, and the required line spaces around the title and after the table fall out
of the standard float and `center` spacing rather than from hand-inserted
`\vspace`. Captions were already sentence case. The paper has no figures, so
nothing else needed the caption-position treatment.

**Design insights worth reusing.** (1) In tabularx, a too-wide *table* and a
too-wide *cell* are different bugs with different fixes; the arithmetic of the
column spec only ever explains the first, and mixing `p{f\linewidth}` with `X`
is a smell that usually means the author was debugging the wrong one.
(2) Weighted X columns make the "widths must sum correctly" invariant checkable
by eye (weights sum to the column count) instead of by compile.
(3) When the verification tool is missing, prefer the fix whose correctness is
an argument about the source over the fix that needs a render to confirm.

**Confidence and gaps.** Environment balance is verified by script; the
structural rewrite is standard tabularx practice. But **the change is
uncompiled** — I have not seen the PDF, and if the overflow was something other
than a justified narrow cell (a wide `\file{...}` in the last column, say, or a
different table than the one I assumed), this will not have fixed it. First
thing to do on a host with TeX: `pdflatex main` and grep the log for
`Overfull \hbox` against `taxonomy.tex`, `appendix.tex`, and `cases.tex`. The
committed message says the same thing so the gap is not buried here.

**Next steps.** Compile and check the log as above. If a cell still protrudes,
the remaining suspect is the tt material in the last column (`run\_spec.json`,
`add\_generation\_prompt`) which TeX will not hyphenate — the fix there is a
breakable `\file`/`\code` (e.g. `\seqsplit` or `\allowbreak` after `_`), not a
further width change.

**Addendum (same session).** The first fix failed to compile on Edward's TeX
host: `Package array Error: Illegal pream-token (Z)` at every table, i.e. the
letter was undefined where the table was expanded even though
`\newcolumntype{Z}[1]{...}` sits in `main.tex` after `array`. The parameterised
column type is the documented tabularx idiom and should work, so the likely
cause is a build reading an older copy of the preamble — but I could not
confirm that without a compiler, and "it should work" is not a fix. Inlined the
weights as `>{\raggedright\arraybackslash\hsize=<w>\hsize}X` in each table
preamble and deleted the `Z`/`C` types. Same widths, but the tables now depend
only on `tabularx` + `array`, which the preamble has carried since before this
session, so they compile against *any* copy of it and survive being lifted into
another document. Insight: when a fix adds a preamble dependency that the
document did not previously have, a stale-preamble build turns a formatting fix
into a hard error; preferring the construct that needs no new dependency is
worth the extra verbosity in a paper whose source gets copied between hosts.

**Addendum 2 (same session).** The next error was `Undefined control sequence`
reported at `\end{tabularx}` — tabularx re-reads the body before expanding it,
so a bad macro *inside a cell* surfaces at the environment's end, not at its
line. The macro was `\file`, which `main.tex` has defined since long before
this session. That is the second independent signal (after the undefined `Z`
column type) that Edward's build is not reading this preamble. Rather than keep
diagnosing which copy of `main.tex` a given build sees — which I cannot do
without a compiler — Edward called it: stop using custom macros. Complied
fully: `\code`/`\file` → `\texttt` (108 sites), `\finding`/`\pending`/
`\scaffold` expanded at their 17 use sites, `\eee`/`\eeeshort`/`\newcolumntype{Y}`
deleted as unused, and the two `\definecolor` shades swapped for built-in
xcolor names so the draft markers do not depend on the preamble either. The
source now contains no `\newcommand`, `\newcolumntype`, or `\definecolor`.
Caught two latent bugs while expanding: the old `\scaffold` used `\;`, which is
math-mode-only spacing, in text mode; and my expansion script initially rewrote
a `\scaffold{...}` mention inside a *comment* in the main.tex header, which is
a reminder that a brace-matching rewriter over .tex needs to skip comments.

**Insight worth keeping.** A shared preamble is a hidden coupling between the
document and the build environment, and it fails in a way that points at the
wrong file: both errors this session named `taxonomy.tex` when the actual
divergence was in `main.tex`'s reachability. For a draft that gets copied
between hosts, Overleaf projects, and section-at-a-time compiles, writing the
constructs out at each use site costs verbosity and buys the property that any
single file is self-contained. That is a better trade than it looks — and the
README now records the rule so a future agent does not "helpfully" re-factor
the repetition back into macros.

**Addendum 3 (same session).** Edward asked why the serving engine is an
instrument and not a deployment. The honest answer was that the draft did not
say one thing: §3.1 listed "serving engine" under the instrument, §3.4 cause (2)
called swapping `transformers` for vLLM a *deployment* mismatch, and cause (3)
listed precision and template version --- both §3.1 deployment members --- as
instrument facts. Table 1 already encoded the resolution without the prose
saying so: *Client / deployment class* is "often identifiable, recorded", while
*Engine / package / kernel versions* is "often missing".

**The criterion I made explicit.** Deployment is what fixes the *function to be
computed*; the instrument is what fixes how that function is *numerically
realized*. In exact arithmetic an instrument change is a no-op and a deployment
change is not. That criterion puts the engine *family* in the deployment (HELM
records it; substituting a hosted API for vLLM is an intended change to what
computes the answer) and the engine's *build, kernels, scheduler, batching* in
the instrument (two engines on identical weights, precision, and token ids are
supposed to agree). It is not a bookkeeping preference: the §7 recommendation
only makes sense if the deployment gap is closable by three recorded fields
while the instrument gap needs a container digest, and collapsing the two would
make either half look sufficient alone.

**The subtlety worth publishing.** An engine *supplies deployment values by
default* --- vLLM's `add_special_tokens=True` changed the prompt tokens, an
unpinned `transformers` load chose the precision. In both, the engine is the
proximate cause while the coordinate that differs is the deployment. That is the
mechanism behind two of the three confirmed OLMo attributions, and it is the
argument for recording the *resolved value* rather than the component that chose
it. It also forced a re-label: cases.tex (a) called the EOS-append an
execution-instrument mismatch; it is a deployment mismatch with an instrument
proximate cause. The evidence is unchanged, only the label.

**Two consistency bugs found while doing it.** §3.4 opened "one of six causes ---
one per coordinate, plus a residual", but §3.1 counts the residual as one of the
five coordinates, so the sentence double-counted. And §3.5 plus the table caption
used "coordinate" to mean "table row", i.e. *parameter* --- in a section whose
whole point is that identifiability is per parameter, not per coordinate.

**Process.** Edward froze `introduction.tex` and `related_work.tex` mid-task, so
the three terminology conflicts they carry went into the two review-notes files
as items 6--8 and item 3 rather than being applied. The sharpest is intro line 8:
it calls serving backend, batching, and hardware "deployment properties", which
is precisely the union §3.1 now splits --- a reader arriving at §3.1 has to
unlearn the intro's sense of the paper's most-used technical term.

**Insight.** A definition given as a membership list has no way to answer "which
side does this new thing go on?", so the first genuinely ambiguous member (here,
the engine) gets filed twice by different paragraphs and nobody notices, because
each paragraph is locally plausible. Writing the *criterion* alongside the list
is what makes the taxonomy answer questions it was not explicitly written to
answer --- which is the property you actually want from a taxonomy.

**Addendum 4 (same session).** Edward asked what separates artifact
interpretation from the recipe's scorer --- a fair challenge, since both turn
per-instance records into a published number, and the §3.1 entry for artifact
interpretation was a bare membership list. Rewrote it and added a paragraph
pairing it against the recipe, mirroring the deployment/instrument paragraph.

**The structure that fell out.** The four controllable coordinates are two of
intent and two of realization: recipe and deployment say what is to be measured
and what is to compute it; instrument and artifact interpretation govern how the
outputs are produced and how they are read back. So recipe/interpretation is the
reading-side counterpart of deployment/instrument, and divides on the same
principle --- a recipe change is a declared change of what is measured, so a
different number is its intended consequence, whereas an interpretation change is
supposed to be a no-op, so when it moves a number one of the two readings is
simply wrong. That is a usable test: *design choice or error?*

**The scorer is the recipe's engine.** Just as the engine sits in the deployment
(family) and the instrument (build), the scorer sits in three coordinates under
three aspects: what it is specified to compute (recipe), the code that computes
it (instrument --- which is exactly what the era containers pin), and what that
code is handed and where its output is read from (artifact interpretation). The
classic-model class path is the worked example: rewritten by a migration while
the recipe kept naming the same metric, which is what makes it *evidence about
the producing harness* rather than recipe drift.

**Loose end I did not act on.** §3.3 says target (1) "tests the artifact
interpretation and the scorer, and nothing else." With the scorer's *code* now
explicitly an instrument fact, target (1) is not instrument-free --- it is
serving-instrument-free, and the era containers are what let it hold the scoring
half fixed. Flagged to Edward, not edited; it is his call whether to add the
clause.

**Process note worth keeping.** Two turns earlier I answered a "why is X an
instrument" question by editing the paper, and Edward reverted me: he had asked
for an explanation, not a change. The tell I missed is that a bare "why"/"what is
the difference" question is a request for understanding; the edit request came
one turn later and was explicit ("please rewrite it"). When a user is
interrogating their own draft, answering in prose first is not a slower path to
the edit --- it is what lets them decide whether the edit is the right one.

**Addendum 5 (same session).** Edward kept pushing on artifact interpretation and
was right to: intent-vs-realization did not actually answer his objection. If the
scorer's intent is recipe and its implementation is instrument, what is left for a
fourth coordinate? The honest answer is that the first framing had no reply --- an
interpretation looked like the instrument seen from the reading end.

**What resolves it is timing, not role.** Recipe is fixed at design time,
deployment and instrument at execution time; all three are historical facts no
later decision can alter. The artifact interpretation is fixed at *read* time,
which happens on every occasion anyone reads the record --- and the stored bytes
keep changing after the producing process exits: carried unchanged into a later
suite version, rewritten by a migration, truncated in transit. No property of the
producing instrument accounts for those, because it had already terminated. The
falsifiable signature that follows: this is the only coordinate that can disagree
with *itself* --- one stored run aggregated under two dedup policies gives two
numbers with no second execution anywhere in the comparison. Every other
coordinate needs two runs to exhibit a mismatch.

**He also caught a real error.** I had said target (1) re-executes "only the
aggregation" for judge-dependent metrics. Wrong: the chain is completion →
[judge] → annotation → [metric fn] → stat → [aggregation], and target (1) holds
only the first arrow fixed. Their own `wildbench_annotator_success` bookkeeping
metric --- did the judge request *parse* --- is the proof that the annotation is a
separately stored intermediate. Corrected in the text.

**And I had the judge backwards.** I claimed judge identity was recorded and
recipe-side. `judge_registry.py` exists precisely because it is not: official run
specs name an annotator class with empty args, and the judge models are hard-coded
in the annotator classes per HELM version. So judge identity is a *recipe slot
whose value the instrument version supplies* --- structurally identical to the
chat-template flag whose effect the `transformers` version decides, and the load
precision the loader's default chooses. That is three independent instances of one
mechanism, which is a considerably stronger claim than three unrelated gotchas,
and it is now stated in §3.1.

**Design insight.** Two coordinates that share a role can still be distinct if
they are *fixed at different times*. I reached for role-based separation twice
(intent/realization) and it kept failing on the scorer, because the scorer
genuinely spans roles. The temporal axis --- design time, execution time, read time
--- cuts cleanly where the role axis smears, and it also explains the coordinate's
practical asymmetry: an interpretation is the one coordinate an auditor still
controls, which is why it is a confounder to neutralize rather than a cause to
attribute to.

**Still open.** Cause (4) has no §5 result landing on it. My recommendation stands:
promote the coverage funnel's raw-hash / canonical-hash / logical-key gaps into §5,
since those are already-computed quantitative artifact-interpretation measurements
sitting in Appendix A as a workaround note. Not done --- Edward has not decided.

**Addendum 6 (same session).** Edward said "promote", so the coverage funnel
became §5.1 and cause (4) finally has an outcome.

**The numbers were better than the argument I made for them.** I expected to
report the raw/canonical/logical gap as evidence that schema churn defeats naive
pairing. Reading all twelve funnels in
`/data/crfm-helm-audit-store/virtual-experiments/*/reports/scoped_funnel/` turned
up something stronger: `n_reproduced_recipe_identical` is **0 in every modern
experiment** --- Qwen 0/4723 in scope, OLMo 0/182, gpt-oss 0/11 --- and **2/2 in
exactly one place**, the era-pinned RedPajama replays. Byte-identical recipe
agreement is not what a careful modern replay recovers; it is what an instrument
pinned to the producing era recovers. That is the sharpest quantitative argument
for the era containers in the paper, and it had been sitting in a derived store
layer being used only as an internal diagnostic.

**Freshness discipline applied, not assumed.** Two memories bear directly on
citing these: the `olmo-models` store predates planner fix `a25aac9`, and the
OLMo/GPT-OSS stores keep only derived layers with raw runs pruned. So I used
`olmo-models-combined` (post-fix; its 149/27 differs from the stale store's
144/30, which is itself evidence the fix moved pairing), excluded the phi-2
tutorial fixtures explicitly rather than silently, said the Qwen denominator is
scope and not attempts, and carried a pending marker for regenerating the
OLMo/GPT-OSS rows from preserved raw runs. The paper now states its own store
provenance in the same paragraph as the numbers.

**Design insight.** A taxonomy category with no measurement attached is
indistinguishable from padding, and the fix is usually not to argue harder for
the category --- it is to find the number you are already computing for
operational reasons and promote it. The three-level funnel existed because
pairing needed it to work at all; nobody had noticed it was also the corpus-wide
measurement of a coordinate the paper claims. Diagnostics that a pipeline needs
in order to function are often the cleanest evidence for the model that
motivated the pipeline.

**Still open.** The numbers are read, not recomputed. If the regeneration changes
them, the qualitative finding (0 modern, nonzero only era-pinned) is what I would
expect to survive, but that is a prediction rather than a result.

**Addendum 7 (same session).** Edward rejected the artifact-interpretation
coordinate outright: "once everything has been scored, there should really be not
much to interpret," and the extra paragraphs I had written to defend it were
"more text and more confusion." He was right on both counts, and the failure mode
is worth recording: across four turns I answered escalating confusion about a
category by *adding explanation* rather than by asking whether the category was
wrong. Each addition was individually defensible and the aggregate was a section
40% longer that a stranger could not follow.

**The diagnosis, once I stopped defending.** The coordinate was doing three
unrelated jobs. Schema fields a newer harness wrote, and per-instance row
ordering, are the *instrument* --- the harness generated the record that way,
which is exactly Edward's point that it is drift at generation and not
interpretation. Our own dedup, `null`->`""`, instance fallback, and key
granularity are *audit-tool correctness*: a threat to the validity of our
measurement, not a coordinate of the original run, so they belong in Sec. 8 and
now do. What survives is only post-execution change to the stored artifact ---
migrations applied to stored specs, outputs carried forward unchanged, damage in
transit --- and that is not interpretation at all. It is the record's own history.
Renamed to **record history**; the entry is three sentences with no system
knowledge assumed.

**A correction I had to make against myself.** Two turns earlier I had promoted
the coverage funnel and said it gave cause (4) an outcome. Under the split it is a
*cause (3)* result: the funnel measures harness-version churn, the harness version
is the instrument, and the finding argues for era containers, which is an
instrument argument. Retitled Sec. 5.1 and moved the pointer. The result stands;
my attribution of it did not.

**Design insights.** (1) When a reader keeps failing to understand a category,
the prior should be that the category is wrong, not that the explanation is
insufficient --- and the tell is that each new paragraph has to *defend* rather
than *state*. Three defensive paragraphs is a design smell. (2) A useful test for
whether something is a coordinate of the studied system: could a competent
third party running the same study get it wrong? If the answer is "yes, and that
would be a bug in their tooling," it is a threats-to-validity item, not a
taxonomy category. That single question relocated four of my six examples. (3)
The narrowed coordinate is smaller but load-bearing in a way the broad one was
not: carried-forward officials mean "both eras agree" is one execution reported
twice, and nothing else in the taxonomy covers that.

**Left for Edward's review.** The pass is committed but unreviewed and uncompiled.
The judge-recursion paragraph survived untouched --- it is independent of this
change --- but it is the longest thing left in Sec. 3.1 and is the next candidate
if the section is still too dense.

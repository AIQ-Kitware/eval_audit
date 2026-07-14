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

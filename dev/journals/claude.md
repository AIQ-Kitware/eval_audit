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

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

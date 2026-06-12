# Judge-identity inventory across the closed-judge benchmarks

**Status:** FINDINGS — answers open decision §9.2 of
[`phase3-comparison-core-unification.md`](phase3-comparison-core-unification.md)
(the spike gating the `same_judge` fact specification for sub-stage 4.9).
**Method:** read `run_spec.json` from one public-mirror run of each of the six
`CLOSED_JUDGE_BENCHMARKS`, plus the vendored HELM annotator sources
(`submodules/helm/.../annotation/`). 2026-06-12.

## Findings

### 1. All six run_specs carry an `annotators` list — with empty args

Every inspected run_spec has a top-level `annotators` key shaped
`[{"class_name": "...<Bench>Annotator", "args": {}}]`. The judge **model
identity is not in the run_spec** — it is hard-coded in the HELM annotator
class, i.e. a function of the HELM version that produced the run:

| Benchmark | Annotator class | Judge models (vendored HELM version) |
|---|---|---|
| `wildbench` | `WildBenchAnnotator` | ensemble: `openai/gpt-4o-2024-05-13` + `meta/llama-3.1-405b-instruct-turbo` (a `claude-3-5-sonnet-20241022` member is commented out post-deprecation) |
| `omni_math` | `OmniMATHAnnotator` | same GPT-4o + Llama-405B ensemble |
| `harm_bench` | `HarmBenchAnnotator` | via shared `score_with_reasoning_with_gpt_and_llama` |
| `anthropic_red_team` | `AnthropicRedTeamAnnotator` | same shared helper |
| `simple_safety_tests` | `SimpleSafetyTestsAnnotator` | same shared helper |
| `xstest` | `XSTestAnnotator` | same shared helper |

The shared helper (`model_as_judge.py:score_with_reasoning_with_gpt_and_llama`)
pins exactly: `gpt → openai/gpt-4o-2024-05-13`,
`llama → meta/llama-3.1-405b-instruct-turbo` (deployment
`together/llama-3.1-405b-instruct-turbo`).

**Consequence for the 4.1 extractor:** `extract_judge_models` behaves as
designed on real officials — it returns the class-name basename (e.g.
`("WildBenchAnnotator",)`), capturing the judge *kind* but not the model.
Official judge-model identity requires a **curated (annotator class, HELM
version) → judge models map**, maintained alongside the recipe; it cannot be
parsed out of official artifacts.

### 2. The official judging is already an ensemble that *includes an open judge*

The standard HELM ensemble is GPT-4o (closed) + Llama-3.1-405B (open) — and
HELM records **per-judge sub-scores as separate metrics**: this is exactly why
`safety_gpt_score` and `safety_llama_score` exist as distinct metric names (both
already in our `JUDGE_DEPENDENT_PREFIXES` registry). This materially improves
the extension design:

- **A same-judge control already exists inside the official data.** A local
  re-run that includes Llama-3.1-405B as a judge member can compare
  `safety_llama_score` official-vs-local as a *same-judge* reproduction check —
  no substitution involved — while `safety_gpt_score` official-vs-open-judge is
  the substituted comparison.
- The extension is therefore better framed per-metric than per-run:
  `substitutions` apply to the judge-dependent metrics whose official judge
  member is closed, while open-member metrics are reproduction controls
  alongside the deterministic metrics.

### 3. Implications for `same_judge` (feeds the 4.9 spec)

1. **Granularity:** a single run-level `same_judge` fact is too coarse for
   ensemble judging. Recommended shape: keep run-level `same_judge` (computed
   over the full judge-model set — `no` whenever the sets differ) for the
   comparability table, and add the per-metric judge attribution (which
   ensemble member produced this metric) to the metric-class split, so
   per-metric comparisons label themselves `same-judge` vs `substituted-judge`.
2. **Officials' judge identity** comes from the curated map keyed by annotator
   class + HELM version (suite version is already on the component row).
   Where the map has no entry, judge identity stays `unknown` — the honest
   signal.
3. **Local re-runs record judges explicitly** (we control the recipe): in
   annotator args and/or the `recipe_facts` block (`judge_models`), so the
   local side never depends on the curated map.
4. **Noise control (already in the matrix):** `same_judge` must not appear on
   non-extension runs (matrix 4.9 gate: F1–F8 byte-identical). Pre-annotator
   HELM runs have no `annotators` key at all → extractor returns `None` →
   unknown — so unconditional emission would add
   `comparability_unknown:same_judge` to *every* legacy pair. The fact is
   therefore emitted only when the comparison declares a judge substitution
   (or the planner runs in extension mode).

## Status of the §9.2 decision

The spike is done; `same_judge` is now specifiable. Remaining 4.9 design
inputs unblocked: the curated judge map (small: six annotator classes ×
relevant suite versions), the per-metric judge attribution in
`metric_class_split`, and the `--allow-closed-judge-benchmarks` Stage-1 relax.

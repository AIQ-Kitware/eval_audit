# HELM ↔ EEE metadata: what's there, what's missing, and how to keep more of it

`eval_audit` now supports two parallel comparison surfaces:

- **HELM-driven** (`eval-audit-compare-pair`, `eval-audit-analyze-experiment`,
  …) — consumes HELM run dirs that contain `run_spec.json`, `scenario.json`,
  `stats.json`, `per_instance_stats.json`, and `scenario_state.json`.
- **EEE-driven** (`eval-audit-from-eee`, `eval-audit-compare-pair-eee`) —
  consumes [`every_eval_ever`](../submodules/every_eval_ever/) artifacts:
  one `<uuid>.json` aggregate file plus a sibling `<uuid>_samples.jsonl`
  with per-instance metric values.

Both routes go through the same comparison-intent planner
(`eval_audit/planning/core_report_planner.py`) and the same core-metric
renderer (`eval_audit/reports/core_metrics.py`). They produce the same
`core_metric_report.{txt,json,png}` shape. **The difference is in
the comparability-fact metadata each side can substantiate.**

This document catalogues what HELM has that EEE doesn't, what that means
for the report, and how to preserve the missing metadata when you have
it.

## At a glance

| Comparability fact | Source field | EEE-only outcome | With sidecar `run_spec.json` |
|---|---|---|---|
| `same_model` | EEE `model_info.id` (or HELM `adapter_spec.model`) | ✅ evaluated | ✅ evaluated |
| `same_suite_or_track_version` | from_eee defaults / `experiment_name` | ✅ evaluated (per-CLI defaults) | ✅ evaluated |
| `same_scenario_class` | HELM `run_spec.json:scenario_spec.class_name` | ⚠️ `unknown` | ✅ evaluated |
| `same_benchmark_family` | derived from scenario class taxonomy | ⚠️ `unknown` | ✅ evaluated |
| `same_deployment` | HELM `run_spec.json:adapter_spec.model_deployment` | ⚠️ `unknown` | ✅ evaluated |
| `same_instructions` | HELM `run_spec.json:adapter_spec.instructions` | ⚠️ `unknown` | ✅ evaluated |
| `same_max_eval_instances` | HELM `run_spec.json:adapter_spec.max_eval_instances` | ⚠️ `unknown` | ✅ evaluated |

⚠️ `unknown` shows up in the report as `comparability_unknown:<fact>`
warnings and `<fact>=unknown` caveats — the comparison still runs, but
the planner declines to assert agreement on a fact it cannot evaluate.

## What does *not* depend on HELM metadata

Agreement metrics — and they are what most readers actually care about
— are computed from the EEE-side data alone:

- **Run-level metrics** (`abs_delta` quantiles, agreement curve at every
  tolerance threshold) come from the per-metric scores in the EEE
  aggregate JSON.
- **Instance-level metrics** (per-instance `abs_delta`, agreement
  curves, per-metric breakdowns) come from `<uuid>_samples.jsonl`.
- **Same-model identity** is derived from EEE `model_info`.
- **Logical run key** (`<benchmark>:model=<model_id>`) is derived from
  EEE `evaluation_results[0].source_data.dataset_name` plus
  `model_info.id`.

In other words, the *quantitative* answer to "are these two runs
reproducible?" is the same regardless of whether you have HELM
metadata. The *qualitative* answer to "are they the same kind of
comparison?" is what the missing metadata gives you.

## What HELM has that EEE doesn't (in detail)

### `run_spec.json`

| Field | What it is | Why the planner cares |
|---|---|---|
| `name` | the canonical run-spec slug (`benchmark:k=v,k=v,...`) | logical-run-key fallback when the EEE benchmark string is ambiguous |
| `adapter_spec.model` | model identity | redundant with EEE `model_info` for same-model checks |
| `adapter_spec.model_deployment` | deployment identity (e.g. `huggingface/llama-3-8b`, `vllm/qwen2.5-7b`) | `same_deployment` fact — distinguishes "same model, different serving stack" |
| `adapter_spec.instructions` | exact prompt-prefix string | `same_instructions` fact — one of the biggest sources of cross-run drift in HELM |
| `adapter_spec.max_eval_instances` | scope of the evaluation | `same_max_eval_instances` fact — same model + same instructions, but one ran 100 instances and the other 1000, is *not* a clean comparison |
| `scenario_spec.class_name` | the scenario class (e.g. `helm.MMLUScenario`) | `same_scenario_class` fact + the basis for `same_benchmark_family` |
| `scenario_spec.args` | scenario constructor args (e.g. MMLU subject) | not currently surfaced as a comparability fact, but useful for scenario-aware filtering |

### `scenario.json`

Captures the *resolved* scenario — full benchmark identity including
splits, train/dev/test partition, instance counts, and any scenario
parameters that aren't visible in `run_spec.json`. The planner does not
currently use this directly, but the HELM-driven path consumes it via
`HelmRunDiff` for run-vs-run diagnosis.

### `stats.json` and `per_instance_stats.json`

The HELM-native equivalents of what EEE distills into the
`evaluation_results` block + `<uuid>_samples.jsonl`. The numeric
content is the same; the shape is different. EEE is a normalized
re-shape and intentionally drops HELM's per-stat metadata fields
(`split`, `sub_split`, `perturbation`, etc.). Most reproducibility
analyses do not need these — agreement curves don't change shape based
on which split a metric came from — but if you want to filter agreement
by perturbation or split, you need either HELM raw or an EEE schema
extension.

### `scenario_state.json`

The full prompt+completion record per request. Neither EEE nor the
planner consumes this for reproducibility analysis; it lives in HELM
land for deep-dive debugging only.

## Recommendations

### 1. Ship `run_spec.json` next to your EEE artifacts when you have it

Both `eval-audit-from-eee` and `eval-audit-compare-pair-eee` look for
`run_spec.json` in the same directory as `<uuid>.json`. When present,
the planner reads it via the existing
`eval_audit.indexing.schema.extract_run_spec_fields` helper, and all
five comparability facts that would otherwise be `unknown` evaluate
normally.

This is a no-op cost: the file is already in the HELM run dir that the
EEE converter ran against. Ship it alongside the EEE artifact.

```
<artifact_dir>/
├── <uuid>.json              # EEE aggregate
├── <uuid>_samples.jsonl     # EEE per-instance
└── run_spec.json            # ← optional sidecar; auto-detected
```

### 2. Pin scenario class + deployment in your local pipeline

If you can't ship `run_spec.json` (e.g. your local pipeline doesn't run
HELM directly), the next-best thing is to embed the equivalent fields
in a **conventional** location your tooling can read. Two practical
options:

- **JSON sidecar with the same shape as `run_spec.json`.** Simplest —
  reuses the existing reader. Fields: `adapter_spec.model_deployment`,
  `adapter_spec.instructions`, `adapter_spec.max_eval_instances`,
  `scenario_spec.class_name`.

- **Extend EEE.** Embed the same fields natively in the artifact so
  metadata travels with it rather than as a sidecar. If you're
  already patching the EEE converter for your local pipeline, this is
  cheap.

Both options are supported. The native-block reader landed as
`eval_audit/normalized/recipe_facts.py`: a JSON-encoded `recipe_facts`
entry under `source_metadata.additional_details` in the EEE aggregate,
resolved *before* any sidecar `run_spec.json`. (An upstream EEE schema
slot for it is still pending — see
[`docs/planning/upstream-eee-recipe-facts-issue.md`](planning/upstream-eee-recipe-facts-issue.md);
until then the block rides the existing free-form `additional_details`.)

### 3. When you can't preserve the metadata, surface that to the reader

If your pipeline genuinely doesn't have HELM provenance, the right move
is *not* to fabricate it. Run the EEE-only comparison; the report's
`comparability_unknown:*` warnings + `eee_metadata_caveats.txt`
file accurately tell the reader "we couldn't verify these identity
claims." That's better than a `same_deployment=yes` that quietly
asserts equivalence the data doesn't support.

## Tools that respect this contract

| Tool | Behavior with HELM run dir | Behavior with EEE artifacts | With EEE + sidecar |
|---|---|---|---|
| `eval-audit-compare-pair` | full comparability | n/a (HELM-only) | n/a |
| `eval-audit-compare-pair-eee` | n/a (EEE-only) | 4–5 facts `unknown` | full comparability |
| `eval-audit-from-eee` | n/a (EEE-only) | 4–5 facts `unknown` | full comparability |
| `eval-audit-build-virtual-experiment` | full comparability via `audit_index` + `official_public_index` | full pipeline via `eee_root` / `external_eee` source kinds (4–5 facts `unknown` per row without sidecar) | full comparability |
| `eval-audit-analyze-experiment` | full comparability | accepts EEE rows (e.g. composed by a virtual experiment) and renders per-packet reports | full comparability |
| `eval-audit-build-summary` | full comparability | aggregates the per-pair findings as-is; warnings flow into the summary | sidecar status flows through automatically |

### Virtual experiments over EEE

The virtual-experiment composer accepts two EEE-aware source kinds in
addition to the HELM-driven `audit_index` and `official_public_index`:

```yaml
sources:
  - kind: eee_root
    root: /path/to/eee/tree   # contains official/ and local/ subdirs
    side: both                 # "both" | "official" | "local"
    experiment_name: optional  # defaults to subdir under local/

  - kind: external_eee
    components:
      - id: my-component
        eee_artifact_path: /path/to/<uuid>.json or its dir
        run_entry: "<benchmark>:model=<model_id>"   # pins the planner key
        side: local             # "local" (default) | "official"
        display_name: "..."
        provenance: {tool: inspect-ai, version: ...}
```

Both source kinds materialize into the synthesized indexes the same
way HELM-driven sources do; the compose step applies the manifest's
`scope` filter uniformly across all source kinds. Rows from EEE
sources interleave with HELM rows in the synthesized indexes, and the
planner accepts the mix via the `artifact_format=eee` path. See
[`configs/virtual-experiments/eee-only-demo.yaml`](../configs/virtual-experiments/eee-only-demo.yaml)
for a worked manifest against the checked-in fixture and
[`tests/test_virtual_experiment_eee.py`](../tests/test_virtual_experiment_eee.py)
for an end-to-end test.

## Test fixtures that exercise both modes

- [`tests/fixtures/eee_only_demo/eee_artifacts/`](../tests/fixtures/eee_only_demo/eee_artifacts/)
  — 3 toy models × 3 toy benchmarks of synthetic EEE artifacts
  *without* HELM sidecars. Used by `tests/test_eee_only_demo.py` to
  pin agreement-bucket counts and assert on the `unknown` collapse.

- [`tests/test_compare_pair_eee.py`](../tests/test_compare_pair_eee.py)
  — exercises `eval-audit-compare-pair-eee` against the same fixture
  in both modes (without a sidecar → 4 facts `unknown`; with a
  synthesized sidecar → all facts evaluable).

- [`tests/test_virtual_experiment_eee.py`](../tests/test_virtual_experiment_eee.py)
  — exercises the virtual-experiment composer + analyzer + aggregate
  summary end-to-end against the same fixture. Asserts the engineered
  agreement-bucket counts (6 exact / 2 low / 1 zero) come through the
  `eee_root` source unchanged, and that an `external_eee` component
  materializes into a planner-visible row.

Run them with `pytest --run-slow` (slow-marked because they shell out
to the analysis pipeline).

## Audit vs forensics: scope distinction

EEE is **sufficient for audit** (do two runs of the same recipe agree,
where do they diverge, by how much, on which instances?) but
**insufficient for forensics** (why do they diverge?). That's a
deliberate scope decision, not an oversight: EEE captures the model's
input-output behavior in a framework-neutral way, but does not
preserve the upstream framework's internal state needed to reconstruct
mechanism.

Concretely, given only EEE artifacts (and `recipe_facts` / sidecar
`run_spec.json` when available):

| Question | Answerable from EEE alone? |
|---|---|
| Did these two runs agree? | ✅ |
| By how much do they disagree at instance level? | ✅ |
| At which instances? | ✅ (`sample_hash` join → per-instance abs_delta) |
| What did the model see? | ✅ (`input.raw`) |
| What did the model output? | ✅ (`output.raw`) |
| What did the evaluator extract from the output? | ✅ (`answer_attribution.extracted_value`) |
| What was the run-level mean per metric? | ✅ (`evaluation.score` → run-level mean) |
| What was the inference backend (Together API / vLLM / HF transformers / …)? | ❌ |
| What was the runtime resolution of `model_deployment` when null? | ❌ |
| What inference precision (fp32 / fp16 / bf16) and attention kernel? | ❌ |
| What `transformers` / `pandas` / `numpy` / framework versions were active? | ❌ partial — `eval_library_version` recorded but typically `"unknown"` |
| Why do two runs of the same recipe diverge? | ❌ — typically requires upstream framework artifacts + source |

Surfaced by the slim-heatmap case studies (see
[`docs/historical/neurips-2026-05-01_session_log.md`](historical/neurips-2026-05-01_session_log.md)
for the full investigation):

- **`entity_matching` zero hash overlap**: detected from EEE
  (`sample_hash` set has zero intersection between sides). Mechanism
  required HELM's `run_spec.json`, `scenario_state.json`, and the
  `helm.benchmark.scenarios.entity_matching_scenario` source to
  identify pandas `pd.merge` row-ordering as version-dependent
  between pandas 2.0.x and 2.2.x+. EEE alone could not have surfaced
  the pandas-version mechanism.
- **`synthetic_reasoning_natural × pythia-6.9b` (0.788)**: detected
  from EEE (aggregate scores 0% vs ~20% with high instance-level
  disagreement). Mechanism — Pythia's first generated token is `\n`
  on Together-hosted inference but `'The'` on local HuggingFace
  inference, interacting with `stop_sequences=['\n']` to zero out
  the OFFICIAL completion text — required HELM's `scenario_state.
  json` (raw token stream) and `auto_client.py` deployment routing
  source. EEE preserved the empty completions faithfully but did not
  preserve which backend served the inference.

### Caveats when interpreting micro-averaged instance-level agreement

`agree_ratio = matched / count` averaged across (sample, metric) rows
has two failure modes worth flagging in any reproducibility report:

1. **Degenerate-zero agreement.** When one side's run is degenerate
   (always emits 0 — e.g., the broken Pythia SR-Natural OFFICIAL run
   above), every (sample, metric) row where the other side also
   scored 0 counts as agreement. The "agreement rate" then reads as
   "fraction of cases where the non-degenerate side also failed",
   not "fraction of cases where the model behaviors agree". For SR-
   Natural × Pythia: instance-level `agree_ratio = 0.788` while the
   run-level means are 0.0 (off) vs ~0.20 (loc) — the 0.788 entirely
   reflects 0=0 collisions on the 78% of prompts the local model
   also got wrong.
2. **Stochastic noise floor.** When the recipe specifies
   `temperature > 0` (e.g., WikiFact at temperature=1.0), independent
   runs are not bit-reproducible by design. The agreement rate
   converges to `p² + (1-p)²` for a binary metric with hit rate
   `p` — typically 90-95% for realistic hit rates, regardless of
   any reproducibility issue. WikiFact's ~0.92 across all three
   models in the slim heatmap is the noise floor, not a finding.

Honest reporting therefore wants to surface, alongside `agree_ratio`,
both run-level means (to expose case 1) and the recipe's
`temperature` / sampling configuration (to expose case 2). The
existing per-metric drill-down panels already cover (1) when paired
with the run-level table; case (2) is currently implicit.

## Two different "run-level agreement" numbers — do not compare them (IM-13)

> **Status (2026-07-06): RESOLVED by R-2.** The legacy agreement/tolerance
> half of `helm/diff.py` (`_value_agreement_summary` as a *reported* surface,
> `value_distance_profile`, `instance_summary_dict`, `instance_distance_profile`,
> `tolerance_sweep_summary`, `summarize_instances`) has been retired.
> `reports/pair_report.py` and `reports/pair_samples.py` now source every
> run-level / instance-level / distance / tolerance number from `NormalizedDiff`,
> so **only the right-hand column below is produced**. Two behavior deltas ship
> with the migration and are intentional:
> * **Join granularity** — `pair_report` agreement/distance are now per-metric-handle
>   over the normalized join and **core-metric only** (was per-stat over all metric
>   classes). These are the same numbers the production `core_metric_report`
>   publishes.
> * **Tolerance semantics** — the sweep is pure `abs_tol`; `rel_tol` is dropped,
>   so `tolerance_highlights` and `tolerance_highlights_abs_only` are identical.
>   The `pair_report.json` shape (`strict_summary.value_agreement.overall.agree_ratio`,
>   `strict_summary.instance_value_agreement.means.agree_ratio`, `distance_summary.*`,
>   `tolerance_highlights*`) is preserved for downstream consumers
>   (`analyze_experiment`, the cross-machine curve loader). `HelmRunDiff` is kept
>   only for the run_spec/scenario **semantic diagnosis**; its `_value_agreement_summary`
>   survives solely as the value-drift input to that diagnosis and is no longer
>   exposed on `summary_dict`.
>
> The historical description below is retained for context on *why* the two
> numbers differed.

The repo produced a quantity labelled **"run-level agreement"** from two
independent code paths, and they are **not the same measurement**. Never put
them in the same table, chart axis, or sentence as if they were interchangeable.

| | Legacy `pair_report` / `quantiles` | `NormalizedDiff` `core_metric_report` |
|---|---|---|
| Producer | `HelmRunDiff._value_agreement_summary` (`helm/diff.py`) | `NormalizedDiff` run-level rows (`normalized/diff.py`) |
| Join granularity | **per stat key** — each `InstanceStatKey`/`StatKey`, so a metric split across perturbations, train-trial indices, and sub-splits contributes several separate rows | **per metric handle** — collapsed to one row per `metric_id or metric_name or evaluation_name`; perturbation/split variants of the same metric fold together |
| Tolerance semantics | **combined abs + rel** — the default sweep couples `abs_tol` with a *rising* `rel_tol` (the `pair_report` grid runs `rel_tol` from 0 up to **1.0** at the `xxloose` step, and 10.0 at `extreme`), so two values within 100% of each other count as "agreeing" at the loose end | **abs-only** — `rel_tol` is fixed at 0; a pair agrees at tolerance `t` iff `abs(a - b) <= t` |
| Denominator | number of intersecting **stat keys** | number of intersecting **metric handles** |

Concrete consequences:

- The **same run pair** yields a *different* "run-level agreement at tolerance t"
  from each path. The legacy number is generally **higher** at the loose end of
  the sweep because (a) `rel_tol` up to 1.0 admits large absolute gaps on
  large-valued metrics, and (b) per-stat rows dilute a single divergent metric
  across many agreeing perturbation rows.
- A cross-tool trend line that mixes the two (e.g. legacy `quantiles` output on
  one machine, `core_metric_report` on another) is **an artifact of the join
  granularity and tolerance grid, not a reproducibility signal**. This is the
  same coupling flagged as P1-13 for the cross-machine curve (abs+rel tolerance
  plotted on a pure-`abs_tol` axis).
- When you need a single canonical run-level agreement, use the `NormalizedDiff`
  `core_metric_report` value (abs-only, metric-handle join): it is deterministic
  by construction and its tolerance axis means exactly what the label says.

R-2 (retire the legacy instance/agreement surface of `helm/diff.py` onto
`NormalizedDiff`) has now landed and removed the second definition entirely, so
new output carries only the `NormalizedDiff` number. When reading **pre-R-2**
report stores, still treat any legacy `pair_report`/`quantiles` agreement as a
different quantity with the same unfortunate name.

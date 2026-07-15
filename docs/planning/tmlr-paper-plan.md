# TMLR Paper Plan: Systematic Reproducibility of Open-Weight HELM Evaluation

Status: planning / advisory. Written 2026-07-15. Companion to
[`docs/internship-chronology/`](../internship-chronology/) (the full internship
chronology) and the existing draft
[`docs/papers/tmlr-2026/main.tex`](../papers/tmlr-2026/main.tex) (skeleton).

This document answers two questions: **(1) what should the paper's thesis and
main story be?** and **(2) what work must be done / what gaps closed to make it a
complete, substantial academic paper?**

---

## 0. TL;DR

- **There are two papers, and they must be kept distinct.** The NeurIPS *Every
  Eval Ever* (EEE) paper already owns "a normalized eval format lets you *detect*
  reproducibility gaps" as its Case Study 3 (3-model slim slice; entity-matching /
  SR-natural / wikifact). The internship's work is a **different, larger** paper:
  the TMLR "Systematic Analysis." Do not re-tell Case Study 3.
- **The thesis:** *A carefully defined runnable subset of public HELM is
  reproducible under a corrected local open-weight recipe; most apparent
  irreproducibility is not benchmark noise but an unrecorded execution substrate,
  and once you control it (precision, tokenizer/chat-template, revision, harness
  era) the residual gap collapses.* The paper's job is to **attribute and close**
  the gaps the EEE paper could only detect.
- **The single biggest gap is experimental completeness, not methods.** The
  machinery exists and is unit-tested; the *systematic results table* across model
  families at controlled precision — with tolerance sweeps and at least one
  cross-machine axis — largely still needs to be **run on GPUs and collected**.
- **Second biggest gap is scientific framing:** a precise denominator ("reproducible
  subset of *what*"), controls/ablations that isolate each cause, and a clean
  positive-vs-negative results story.

---

## 1. The thesis and the story

### 1.1 The one-sentence thesis

> Public LLM benchmark results are far more reproducible than a naive re-run
> suggests — but only once the **unrecorded execution substrate** (load precision,
> tokenizer and chat-template version, model revision, and the harness era itself)
> is treated as a first-class part of the recipe. We build a pipeline that replays
> a public HELM run's resolved `run_spec.json` verbatim under an explicitly
> controlled deployment, apply it systematically across open-weight model families,
> and show that the residual disagreement is small, concentrated, and almost
> always attributable to a specific unrecorded parameter rather than to inherent
> benchmark non-determinism.

### 1.2 Why this is the right story (positioning against the EEE paper)

The EEE Case Study 3 appendix ends with exactly the sentence that defines the
opening for this paper:

> "the residual differences are genuine answer changes concentrated in Pythia-6.9B.
> A plausible lead is serving-stack sensitivity, but **the evidence cannot separate
> model strength from checkpoint, quantization, precision, tokenizer, or
> generation-kernel differences.**"

The EEE paper *detects* disagreement and does forensics up to that wall. **This
paper knocks the wall down.** The internship built precisely the instruments that
separate those confounds:

| EEE Case Study 3 could not separate | This paper's instrument that does |
|---|---|
| precision | `float32` discovery + deployment-match dtype sweep + `hf-probe`; the confirmed OLMoE exact-match at fp32 |
| tokenizer | OLMo-7B EOS-append diagnosis; `add_special_tokens` request-time sweep |
| chat-template / generation kernel | `add_generation_prompt` version-drift finding; attn-impl / device-map forward-pass sweep |
| checkpoint (revision) | the "pin the revision" lever (Tier-1 of the unrecorded-params taxonomy) |
| harness version | era-pinned containers (v0.2.4 / v0.3.0) that hold the *measurement instrument* fixed |

So the narrative arc is clean and publishable:

1. **Motivation.** Benchmark scores are load-bearing evidence, yet re-running a
   public HELM eval rarely reproduces the number. Is the benchmark irreproducible,
   or is the recipe under-specified?
2. **The gap is provenance, not noise.** HELM (like every harness) records the
   *recipe* and the model *name* but almost nothing about *how* the numbers were
   computed. We taxonomize the unrecorded execution substrate and rank it by effect
   on a greedy output.
3. **Method: faithful, controlled replay.** From-spec verbatim replay + explicit
   deployment control + era-pinned harness + a "sweep-don't-guess" deployment
   matcher that attributes residual gaps to specific unrecorded knobs.
4. **Systematic results.** Across model families (OLMo ×6, GPT-OSS-20B, Qwen ×8,
   the classic Together models), what fraction reproduces, at what tolerance, and —
   for the misses — which unrecorded parameter is responsible.
5. **The headline finding.** Precision (the unpinned-`torch_dtype` → float32
   default) is the dominant hidden variable; controlling it turns "irreproducible"
   OLMo instruct cells into exact matches. Most other misses are chat-template
   version drift, tokenizer special-token handling, or genuine environment filters
   — not benchmark non-determinism.
6. **Recommendation to the field.** Three fields (`model_revision`, `torch_dtype`,
   `transformers_version`) in a machine-readable provenance block close the majority
   of the gap; we show where they must live so they don't break official↔local
   pairing.

### 1.3 The title / framing options

- **(recommended)** *"The Reproducibility of Open-Weight Model Evaluation is an
  Execution-Provenance Problem"* — makes the thesis the title.
- *"Reproducing HELM: What a Corrected Open-Weight Recipe Can and Cannot Recover"*
  — the current skeleton title, safe and descriptive.
- *"Same Recipe, Same Data, Same Model, Different Score: Attributing and Closing
  the Reproducibility Gap in Public LLM Benchmarks"* — punchy, foregrounds the
  attribute-and-close contribution.

### 1.4 What NOT to claim (scoping discipline)

- Not "all of HELM reproduces." The claim is about a *defined runnable subset*.
- Not "we reproduced hosted/closed models." Those are out of scope by construction
  (a filtering reason, not a failure).
- Not "EEE is required." EEE is the *substrate* the analysis rides on, but the
  paper's contribution is the controlled-replay methodology and the systematic
  attribution — keep the EEE dependency as infrastructure, not as the thesis, or it
  collides with the NeurIPS paper.

---

## 2. Contributions to claim (paper's bullet list)

1. **A taxonomy of the unrecorded execution substrate** (Tiers 1–4 by effect on a
   greedy output) with empirical prevalence (of 148 HF-client deployments in HELM,
   19 pin dtype, 7 pin revision) — the conceptual frame.
2. **A faithful-replay methodology**: verbatim `run_spec.json` replay with explicit
   deployment rewrite (so `same_deployment=no` is honest), era-pinned harness
   containers (so the scoring instrument is a controlled variable), and a
   deployment-match sweep that *attributes* residual disagreement.
3. **The float32 result**: the decisive, quantified demonstration that unpinned
   `torch_dtype` → fp32 default is the dominant hidden variable, with an exact-match
   reproduction of OLMoE and a scope argument covering all OLMo HF-client runs.
4. **A systematic reproducibility characterization** across N open-weight model
   families / M benchmarks, reported as a distribution (same-machine, cross-machine,
   official-vs-local) with per-metric tolerance sweeps and a failure taxonomy that
   separates environment/recipe filters from true reproducibility failures.
5. **A catalogue of concrete HELM reproduction pitfalls (G1–G13)** — the appendix a
   reviewer will value, including the pre-v0.1.0 class-path archaeology (G13).
6. **A concrete provenance recommendation** (three fields, where they live) that the
   HELM/harness community can adopt.

---

## 3. Gap analysis: what stands between here and a complete paper

Organized worst-first. Each item tagged **[BLOCKER]**, **[MAJOR]**, or **[POLISH]**.

### 3.1 Experimental completeness — the dominant gap

- **[BLOCKER] Run the systematic grid end-to-end and collect the numbers.** Most
  pipelines are *wired and unit-tested but not GPU-run* (runbook status labels:
  `gpt_oss_20b_from_spec` = "WIRED, GPU run pending"; qwen and era = analysis-host
  validated, GPU pending; the HF-in-process routing switch is unwired). A paper
  needs a *complete* results table. Deliverable: for each in-scope (model, benchmark)
  cell, a from-spec, containerized, precision-controlled run with a published
  agreement number. See §5 for the concrete run list.
- **[BLOCKER] Regenerate the stale stores before citing any number.** The journals
  flag that the original `olmo-models` store predates a planner fix and reads a flat
  0.0 local aggregate (inflating every drift); the fresh store is
  `olmo-models-combined`, and even it has a stale-local dedupe issue and pruned local
  run dirs. **Every cited number must come from a store regenerated with current
  code.** Track exactly which stores are current (the results-inventory audit in
  progress finalizes this).
- **[BLOCKER] Land the fp32 result on the real HELM path (the "confirm" step).** This
  is the most important nuance the results audit surfaced. The fp32 evidence currently
  exists only as a *deployment-match probe composite* on ifeval (olmoe hf-fp32 = 0.971
  MATCH, olmo-2-7b vLLM fp32 = 0.915, olmo-2-32b fp32-tp2 = 0.961), and **every winning
  cell relies on a probe-only `add_special_tokens=False` knob that a normal HELM run
  does not send.** The deployment-match README/`confirm_plan.md` flags this: the winner
  must be landed the HELM-path-native way (a serve-time `--tokenizer` override or a
  `VLLMClient` patch) and then run end-to-end via `confirm --local-run` /
  `compare-pair`. **No deployment-match MATCH winner has been converted into an
  end-to-end HELM reproduction.** Until that "confirm" step runs, the headline fp32
  claim is a probe result, not a reproduced HELM score. Do this first among the
  headline experiments.
- **[MAJOR] Complete the fp32 evidence base beyond ifeval.** Even after the confirm
  step, the sweep is ifeval-only and instruct-only (`olmo-7b` base and all non-ifeval
  benchmarks were never swept). Extend to full-benchmark exact-match rates for the four
  OLMo instruct models (OLMoE via `hf-probe`, dense OLMo-2 via vLLM fp32 / fp32-TP), and
  add at least one non-ifeval benchmark so the precision finding is not benchmark-bound.
- **[MAJOR] Wire and run the HF-in-process routing switch.** The mechanism exists
  (reserve-GPU lease + fp32 `HuggingFaceClient`) but nothing routes a HuggingFaceClient
  official to it by default — so "reproduce the official the way it was produced" is
  claimed but not yet the default execution path. Land the manifest producer + run the
  OLMoE exact-match acceptance test through the real path.
- **[MAJOR] Execute the era-replay ladder on a GPU host.** Era containers are built
  and unit-tested but the validation ladder (instrument-fidelity on
  `entity_matching`, the ~20%-recovery `synthetic_reasoning_natural × redpajama-3b`
  flagship, a full packet per era) has not been walked on real hardware. The pre-v0.5
  reproducibility story (59% of the corpus) is unproven without it.

### 3.2 Scientific rigor — controls, ablations, statistics

- **[BLOCKER] A precise, defensible denominator.** State exactly what "the reproducible
  subset" is: the filter funnel (§Stage-1) already produces typed exclusion reasons;
  the paper must report the funnel (Universe → runnable → attempted → analyzed) with
  numbers and a Sankey, so "X% reproducible" has an unambiguous X and Y. Reconcile the
  curated candidate set (`run_details.yaml`, 270 classic runs) vs the preset/official-
  index-driven modern families vs the full corpus.
- **[MAJOR] Controlled ablations that isolate each cause.** The story is "control the
  substrate → gap collapses." Prove it with paired ablations: for a fixed
  (model, benchmark), report agreement at {bf16, fp16, fp32} × {template-on,
  template-off} × {EOS-append on/off}, so each knob's marginal effect is a number, not
  an anecdote. This turns the case studies into a factorial result.
- **[MAJOR] The cross-machine axis.** The reproducibility *distribution* claim needs
  same-machine vs cross-machine vs official-vs-local. `machine_compare/` exists and the
  research journal cites yardrat/namek/aiq-gpu subset checks, but a systematic
  cross-machine table is not assembled. Run at least one model family on ≥2 GPU
  architectures and report the cross-machine agreement as the "reproducibility floor."
- **[MAJOR] Statistical treatment of agreement.** Micro-averaged agreement has three
  documented failure modes (degenerate-zero, both-wrong masking, stochastic floor).
  The paper must report agreement *with* its confounds surfaced inline (per cell:
  agree-ratio, official mean, local mean, output-divergence rate) — the EEE technical
  report's own §"Follow-up" item #4. For stochastic (temperature>0) benchmarks, use the
  Bernoulli noise-floor model as the baseline, not raw agreement.
- **[POLISH] A same-recipe repeatability baseline.** Report local-vs-local repeat
  agreement as the noise floor against which official-vs-local drift is judged (the
  inherited BoolQ/Pythia result: r1-vs-r2 ≈ 0.955 vs official-vs-local ≈ 0.463). This
  is the effect-size argument; a couple of repeat runs per family suffices.

### 3.3 Framing / scoping gaps

- **[MAJOR] Decide the model roster and freeze it.** Candidate families: OLMo (6),
  GPT-OSS-20B, Qwen (8), classic Together (gptj/neox/opt/redpajama), Pythia/Vicuna/
  Falcon (the EEE slim slice — reuse or cede to EEE?). Recommendation: lead with OLMo
  (the deepest, cleanest case incl. the fp32 result), add GPT-OSS-20B as the positive
  contrast, Qwen as the scale/breadth axis, and the classic era models as the
  "instrument-provenance" axis. Cede Pythia/Vicuna/Falcon to the EEE paper to avoid
  overlap (or reuse only as the cross-paper bridge).
- **[MAJOR] Define the benchmark roster and the headline metric per benchmark.** The
  headline-metric resolver (HELM `main_name`/`main_split`) exists; the paper needs the
  curated map stated and justified, and the CoT/instance-only benchmarks (ifeval, gpqa,
  mmlu_pro) handled explicitly (the instance-level-fallback ‡ mechanism).
- **[POLISH] Name the reproducibility "grades."** Define the vocabulary the paper will
  use for a cell outcome: `recipe-clean reproduction`, `deployment-boundary artifact`
  (fixable by controlling the substrate), `environment/recipe filter` (not a
  reproducibility result), `genuine-drift`. Map every cell to one grade.

### 3.4 Writing gaps (the draft itself)

- **[BLOCKER] The TMLR draft is a skeleton** (`main.tex`: all sections TODO). Needs
  full drafting. The internship chronology (`docs/internship-chronology/`) is the
  source material; the gotchas/unrecorded-params docs are ready-made appendices.
- **[MAJOR] Figures/tables to produce (see §4).** The headline aggregate-score-drift
  heatmaps exist for OLMo and GPT-OSS; the systematic table, the funnel Sankey, the
  ablation grid, the fp32 exact-match table, and the cross-machine table are the
  missing display items.
- **[POLISH] Related work.** Position against: HELM itself; efficiency/consistency
  reproducibility work; "defeating nondeterminism in LLM inference" (batch-invariance);
  eval-harness comparability (lm-eval-harness, Inspect-AI); and the EEE paper (as the
  companion, not a competitor).

### 3.5 Threats-to-validity to preempt (reviewers will ask)

- "You changed the serving engine (vLLM vs the original hosted API) — how is that a
  reproduction?" → the honest `same_deployment=no` framing + the from-spec + fp32
  argument; and the HF-in-process path as the "produced-the-same-way" control.
- "The official was a black-box hosted API you can't inspect." → true for Together
  models; scope the strong claims to HF-client officials (where the substrate is
  knowable) and treat hosted-API officials as a separate, weaker-claim tier.
- "Single time-point / you didn't call the historical Together API." → acknowledge;
  the era-container work is the mitigation for the *harness* half; the *model-endpoint*
  half is a stated limitation.
- "Model revision drift." → the paper should pin revisions for at least the headline
  runs (the ~3–4 file boundary fix) so the strong claims are revision-controlled.

---

## 4. Display items the paper needs

1. **The provenance-gap taxonomy table** (Tiers 1–4, prevalence counts). [have]
2. **The funnel Sankey / denominator table** (Universe → runnable → analyzed). [have
   machinery; need the numbers regenerated]
3. **The systematic reproducibility heatmap(s)** — model × benchmark, headline metric,
   colored by drift. [have for OLMo + GPT-OSS; need Qwen + classic + regeneration]
4. **The factorial ablation grid** — agreement vs {dtype} × {template} × {EOS} for a
   representative cell. [NEW — needs runs]
5. **The fp32 exact-match table** — the four OLMo instruct models, best HF vs best
   vLLM, at full benchmark size. [have n=12 version; need full]
6. **The cross-machine table** — one family × ≥2 architectures. [NEW — needs runs]
7. **The era-replay fidelity result** — pandas-version instance-identity + the
   redpajama-3b recovery. [NEW — needs GPU ladder run]
8. **A worked "attribute-and-close" figure** — one cell's journey from "irreproducible"
   to exact match as each substrate knob is controlled (OLMo-7B EOS-append or OLMoE
   fp32 is the cleanest). [have the pieces; assemble]

---

## 5. Prioritized work plan

**Phase A — make the existing results citable (days).**
- A1. Audit `/data` stores; enumerate which experiments have current, non-stale report
  trees (in progress). Regenerate `olmo-models-combined` and any stale store with
  current code so the drift plots reflect the dedupe / metric-resolver / instance-
  fallback fixes.
- A2. Freeze the model + benchmark roster and the reproducibility-grade vocabulary
  (§3.3). Write the denominator/funnel numbers.

**Phase B — close the headline experiments (weeks, GPU-bound).**
- B1. Full-benchmark fp32 reproductions for the four OLMo instruct models (the headline
  §5 table); wire + run the HF-in-process routing switch and the OLMoE exact-match
  acceptance test.
- B2. GPU-run `gpt_oss_20b_from_spec` and `qwen_models_combined` to completion; build
  their reports. GPT-OSS is the positive-contrast result; Qwen is breadth.
- B3. Walk the era-replay validation ladder on a GPU host; produce the pandas-identity
  fidelity result and the redpajama-3b recovery number.

**Phase C — rigor experiments (weeks, GPU-bound).**
- C1. The factorial ablation grid (dtype × template × EOS) for ≥2 representative cells.
- C2. The cross-machine axis: one family on ≥2 GPU architectures + a local-vs-local
  repeat baseline.
- C3. Surface the agreement confounds inline (per-cell tuple) and adopt the Bernoulli
  floor for temperature>0 benchmarks.

**Phase D — write (parallel with B/C).**
- D1. Draft `main.tex` from the chronology + these findings; lift `helm-gotchas.md` and
  `helm-unrecorded-deployment-params.md` as appendices.
- D2. Produce the eight display items (§4).
- D3. Related work + threats-to-validity + the provenance recommendation.

**Cross-cutting:** pin model revisions for headline runs; keep the `same_deployment`
honesty and the environment-vs-reproducibility taxonomy front-and-center; every number
from a regenerated store.

---

## 6. Current experimental-results inventory

Grounded in a direct listing of `/data/crfm-helm-audit-store/` on 2026-07-15 (a
fuller packet-level audit refines the counts, but the store roster is definitive).

**Report stores that EXIST** under `virtual-experiments/`:

| Store | Date | Status for the paper |
|---|---|---|
| `olmo-models-combined` | Jul 14 | **Freshest OLMo store** — 6 models × 11 benchmarks = 24 analyzed pairs, 119 core reports. The headline heatmap. **The on-disk numbers are still the stale-local-affected ones** (e.g. olmo-7b MMLU 0.295/0.144, GSM 0.036/0.018, narrative_qa 0.597/0.311 — the un-deduped local halving); **re-run Stage 6 with current code** so the dedupe / metric-resolver / instance-fallback fixes take effect before citing. Local per-instance run dirs were **pruned** — re-analysis must go through the `eee/by-run-path/` conversion store or re-run. |
| `olmo-models` | Jun 17 | **STALE** (predates the planner fix) — do not cite. |
| `gpt-oss-20b-from-spec` | Jul 14 | Fresh; the positive-contrast result (reproduces well). |
| `era-redpajama-v024`, `era-redpajama-v030` | Jul 12 | **Era replay reached the report stage** for both classic eras (redpajama-3b × {mmlu:us_foreign_policy, synthetic_reasoning_natural}) — better than "pending." The flagship recovery is visible: `synthetic_reasoning_natural` shows public 0.0 vs local ≈0.144 (the ~20% recovery). Local per-instance dirs still on disk. Verify the pandas instance-identity fidelity rung was captured too. |
| `e2e-phi2{,-hf,-vllm,-incomparable,-container}` | Jun 18–29 | Instrument-validation experiments (not headline results). |

**Deployment-match (fp32 evidence base):** sweep stores exist for **all four OLMo
instruct models on ifeval** — `olmoe-1b-7b-0125-instruct`, `olmo-2-1124-7b/13b`,
`olmo-2-0325-32b`, each with `-hf` and `-vllm` variants, plus `olmoe-hf-fp32` and
three `_overnight-*` batches (Jul 7–9). This backs Table §5(fp32) at n≈12 ifeval;
**extend to full-benchmark exact-match rates.**

**Public corpus (`/data/crfm-helm-public/`):** comprehensive — includes `classic`
(v0.2.4/v0.3.0 for era replay), `capabilities` (OLMo/GPT-OSS/Qwen), `lite`, `mmlu`,
`safety`, and many others. Corpus coverage is not a blocker.

**NOT yet run (no store):**
- **Qwen (`qwen-models-combined`):** wired + analysis-host validated (775 rows,
  0 ambiguous); no report store → **GPU run pending** (breadth axis).
- **Classic Together (gptj/neox/opt):** era-shim + G13 canonicalization landed; no
  store → **GPU run pending** (instrument-provenance axis).
- **Cross-machine table:** `machine_compare/` runbook exists; **not assembled**.
- **HF-in-process fp32 routing:** mechanism built, routing unwired, not run.

**Net:** the OLMo + GPT-OSS + fp32-ifeval + era-redpajama results are the *most
mature* and can anchor a first complete draft after a clean regeneration; Qwen,
classic-Together, the full-benchmark fp32 table, the ablation grid, and the
cross-machine axis are the runs that still stand between the current state and a
substantial systematic paper.

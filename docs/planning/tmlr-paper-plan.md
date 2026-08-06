# TMLR Paper Plan: Systematic Reproducibility of Open-Weight HELM Evaluation

Status: planning / advisory. Written 2026-07-15. Companion to the internship
chronology (shipped out-of-band; not in this repo) and the existing draft
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
- **The thesis (revised 2026-07-15 after external review):** *Reproducing an LLM
  benchmark is a layered **system-identification** problem. A public recipe is not
  a complete experimental record; the producing instrument spans the recipe, model
  deployment, software stack, hardware-sensitive numerics, and artifact-processing
  history. Controlled reconstruction can **attribute** and sometimes **close**
  apparent gaps — but where provenance was lost or transformed, the original
  experiment may be **non-identifiable**.* The paper's arc is EEE **detects** →
  this work **attributes / closes / bounds identifiability**. (The earlier
  "most residual disagreement is small and attributable" framing is demoted to a
  *hypothesis* — it depends on experiments not yet complete; see §0.5.)
- **The strongest conceptual result is a contrast, not a positive story:** OLMo is
  the **recoverable** case (missing dtype/tokenizer/template can be reconstructed and
  measured); GPT-J/NeoX/OPT are the **non-identifiable** case (G13: the run-spec
  class path resolves in no released HELM, so the producing instrument can't be
  uniquely rebuilt). Recoverable-vs-underdetermined beats "we closed the gaps."
- **The single biggest gap is experimental completeness, not methods** — and within
  that, the top item is **end-to-end OLMo confirmation** (§5): the fp32 result is
  currently a *deployment-match probe* (discovery), not a confirmed HELM
  reproduction on held-out instances.
- **Given the weeks remaining, the realistic near-term product is a hybrid
  position/systems paper** (§5.0) anchored by *one* end-to-end modern case (OLMo) and
  *one* historical forensic case (classic HELM), not the full empirical grid.

---

## 1. The thesis and the story

### 0.5 Response to the external review (2026-07-15)

An external review of the chronology and this plan was evaluated point by point.
**Accepted (and folded in here + into the chronology's new interpretive-revision
appendix):**
(a) demote the "far more reproducible / residual gap small & attributable" thesis to
a hypothesis and adopt the system-identification / **identifiability** framing;
(b) the fp32 result is **configuration discovery** (an oracle-scored probe over
~12 ifeval instances, using a non-default `add_special_tokens=False` knob), **not** a
completed HELM reproduction — do not say "the official was float32 / fp32 reproduced
the official"; require the end-to-end **confirm** step with **discovery/held-out
separation**;
(c) reframe the OLMo-2 HF divergence from "a broken probe, not science" to **an
unresolved systematic divergence under prompt-token equivalence** (itself
execution-sensitivity evidence);
(d) replace the binary failure taxonomy with the finer **six-category** taxonomy and
avoid "true reproducibility failure" when provenance is incomplete;
(e) make the **recoverable (OLMo) vs non-identifiable (GPT-J/NeoX)** contrast the
conceptual spine;
(f) adopt the **hybrid position/systems paper** as the near-term product and make
**OLMo end-to-end confirmation** — not a broad Qwen grid — the top experiment;
(g) reframe Qwen as a bounded **signal-vs-noise** demonstration;
(h) reconcile "GPU pending" runbooks against later decks/stores explicitly rather
than taking the strongest reading (done in §6 and the chronology appendix).

**Nuanced / partially rebutted (kept, with the reviewer's caution incorporated):**
- *The fp32 dtype claim is not merely a probe-fit.* It has a distinct **deductive**
  leg — reading HELM's loader source + the `transformers` version-dependent default
  ("won't rely on config.dtype till v5") — which is stronger than fitting a config to
  outputs. It remains *conditional* on the unrecorded `transformers` version, so it is
  "most likely fp32," not "proven." The review's non-identifiability caution applies
  most to the *full deployment*; **identifiability is per-parameter** (dtype default
  and client class are code/config-readable; tokenizer append is repo-readable; exact
  version/hardware/batch are not). The paper should present identifiability as a
  *spectrum over substrate parameters*, which strengthens the review's own framing.
- *The classic/historical case is more in-hand than the review assumes.* The
  `redpajama-3b` classic replay already ran end-to-end for both eras (reports on disk,
  Jul 12), and its `synthetic_reasoning_natural` result recovers ~14% where the public
  number is 0 — but note this is a **forensic recovery** (the public number is itself
  a serving artifact), not a clean reproduction match. So the recoverable-vs-
  non-identifiable contrast exists *within the classic era*: redpajama recoverable
  (post-refactor, resolves natively) vs GPT-J/NeoX/OPT non-identifiable (G13). The
  historical forensic case is therefore partially available now, which makes the
  hybrid paper more achievable than "classic is unrun" implies.

### 1.1 The thesis statement

> Reproducing a public LLM benchmark result is a layered **system-identification**
> problem, not a re-run. A public HELM recipe fixes what to generate but not *how the
> numbers were computed* — the load precision, model revision, tokenizer and
> chat-template version, attention kernel, software-stack versions, and the harness
> era that processed the artifacts. We build a pipeline (EvalAudit) that replays a
> run's resolved `run_spec.json` verbatim under an explicitly controlled, provenance-
> recorded deployment, and that reconstructs the historical scoring instrument itself
> when needed. Applied to open-weight model families, controlled reconstruction lets
> us **attribute** apparent reproducibility gaps to specific substrate parameters and
> **close** some of them; but we also show cases where the surviving artifacts and
> releases do **not uniquely identify** what faithful reproduction would mean. The
> practical consequence is a proposed **minimum provenance record** that a benchmark
> publication would need for its results to be reconstructible at all.

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
3. **A precision-attribution case study (OLMo)**: a *deductive* argument that HELM's
   unpinned-`torch_dtype` loader defaults to fp32 under the run-consistent
   `transformers` versions, an oracle-scored discovery sweep showing fp32 candidates
   best match the sampled officials, and — the deliverable that makes it citable — an
   **end-to-end confirmed reproduction on held-out instances** (§5.1). Reported as an
   attribution result with its identifiability caveats, *not* "the official was fp32."
4. **The recoverable-vs-non-identifiable contrast** (the conceptual spine): OLMo =
   provenance experimentally recoverable; GPT-J/NeoX = provenance underdetermined by
   surviving artifacts/releases (G13). Plus a **per-parameter identifiability map**
   (which substrate parameters are code/repo-recoverable vs bounded vs non-identifiable).
5. **A six-category disagreement taxonomy** (recipe / deployment / execution-instrument
   / artifact-migration / residual-under-controlled-equivalence / non-identifiable),
   with the upstream run-vs-not-run eligibility gate kept separate — the results-
   organizing device.
6. **A faithful-reconstruction methodology**: verbatim `run_spec.json` replay with
   honest `same_deployment=no`, era-pinned "execution-capsule" containers (the scoring
   instrument as a controlled variable), and a sweep-don't-guess deployment matcher.
7. **A catalogue of concrete HELM reproduction pitfalls (G1–G13)** — the appendix a
   reviewer will value, including the pre-v0.1.0 class-path archaeology (G13).
8. **A proposed minimum provenance record** for benchmark publication (`model_revision`
   + `torch_dtype` + `transformers_version` at minimum, and where they must live so
   they don't break official↔local pairing) — the actionable recommendation.

*(A broad multi-family systematic characterization as a distribution — same-machine /
cross-machine / official-vs-local, with tolerance sweeps — is the **follow-on** paper's
contribution, §5.4, not a near-term claim.)*

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

- **[MAJOR] Decide the model roster and freeze it (revised per review).** Strongest
  focused roster: **OLMo** = the deep modern *attribution/recoverable* case;
  **GPT-OSS-20B** = an artifact-semantics contrast (the null-vs-empty-string chat
  case) and a positive-control-ish comparison; a **verified Qwen subset** = the
  modern breadth / signal-vs-noise application (§5.1, *not* a full 8-model grid);
  **GPT-J + GPT-NeoX** = the historical *non-identifiable* instrument-provenance case
  (G13). **OPT-66B optional** — do not let it consume disproportionate time.
  **Redpajama-3b** = the recoverable historical case that already ran end-to-end
  (the bridge between OLMo and the non-identifiable classics). **Cede
  Pythia/Vicuna/Falcon to the EEE paper** (use only as inherited baselines if at all).
- **[MAJOR] Replace the reproducibility-"grade" vocabulary with the six-category
  disagreement taxonomy (per review).** Do **not** use "true reproducibility failure"
  when provenance is incomplete. Keep the upstream **run-vs-not-run gate**
  (non-runnable = eligibility/filtering reason). For observed disagreements, classify
  each cell into exactly one of: **(1) recipe mismatch, (2) deployment mismatch,
  (3) execution-instrument mismatch, (4) artifact-interpretation/migration mismatch,
  (5) residual disagreement under controlled equivalence, (6) historically
  non-identifiable reproduction.** Only (5) resembles conventional repeatability
  failure; (6) is qualitatively different (the reproduction target is
  underdetermined). This taxonomy *is* the paper's results-organizing device.
- **[MAJOR] Define the benchmark roster and the headline metric per benchmark.** The
  headline-metric resolver (HELM `main_name`/`main_split`) exists; the paper needs the
  curated map stated and justified, and the CoT/instance-only benchmarks (ifeval, gpqa,
  mmlu_pro) handled explicitly (the instance-level-fallback ‡ mechanism). Report
  **output-level** and **task-level** agreement *separately* (they can diverge — two
  valid different completions can both pass, or both fail).
- **[MAJOR] Map identifiability per substrate parameter.** A deliverable table: for
  each unrecorded parameter (dtype, revision, tokenizer/template, attn impl, device
  map, versions, hardware, batch), whether it is **code/config-recoverable**,
  **repo-recoverable**, **inferable-but-bounded**, or **non-identifiable** from the
  surviving artifacts. This operationalizes the identifiability thesis and directly
  yields the minimum-provenance-record recommendation.

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
5. **The fp32 attribution + confirmation table** — clearly split into a *discovery*
   column (the n≈12 ifeval probe sweep, HF vs vLLM per model — have) and a
   *confirmation* column (end-to-end HELM reproduction on held-out instances, §5.1 —
   NEEDS RUN). Label the probe rows as discovery, not reproduction.
6. **The cross-machine table** — one family × ≥2 architectures. [NEW — needs runs]
7. **The era-replay fidelity result** — pandas-version instance-identity + the
   redpajama-3b recovery. [NEW — needs GPU ladder run]
8. **A worked "attribute-and-close" figure** — one cell's journey from "irreproducible"
   to exact match as each substrate knob is controlled (OLMo-7B EOS-append or OLMoE
   fp32 is the cleanest). [have the pieces; assemble]

---

## 5. Prioritized work plan

### 5.0 The realistic near-term product: a hybrid position + systems paper

With only weeks remaining, **do not make the paper depend on the full empirical
program below.** There is already enough for a strong **position / systems-experience
paper**, provided the claims are scoped per §0.5. Recommended structure (per review):

1. EEE exposed residual benchmark disagreement but could not *attribute* it.
2. We built **EvalAudit** to reconstruct and preserve the missing execution substrate.
3. The HELM reproduction effort revealed a **layered taxonomy of disagreement**
   (the six categories, §3.3).
4. **OLMo** demonstrates *recoverable* deployment/precision ambiguity (one end-to-end
   confirmed case — §5.1).
5. **Historical HELM** (GPT-J/NeoX via G13; redpajama as the recoverable bridge)
   demonstrates *non-identifiable* provenance (one forensic case — largely in hand).
6. EvalAudit **operationalizes** exact-spec replay, execution capsules (era
   containers), layered comparison, and controlled reconstruction.
7. A proposed **minimum provenance record** for benchmark publication.

This needs exactly two experiments landed cleanly (§5.1, §5.2) plus writing — not the
full grid. The larger empirical paper (§5.3–5.4) is the follow-on once those complete.

### 5.1 THE top experiment: end-to-end OLMo confirmation (not a broad grid)

The highest-priority run is **not** a Qwen grid; it is converting the fp32 *discovery*
into a *confirmed* reproduction, with discovery and confirmation separated:

1. Select a candidate config on a **discovery** subset (the deployment-match winner).
2. Reproduce its tokenizer + request behavior **in the ordinary HELM path** (land the
   `add_special_tokens`/`--tokenizer` behavior HELM-natively — no probe-only knobs).
3. Execute the **exact public `run_spec.json`** verbatim.
4. Produce **normal HELM artifacts** (`scenario_state.json`/`stats.json`).
5. Compare via the **standard pair-report pipeline**.
6. Evaluate on **held-out instances/benchmarks** (not the discovery sample).
7. Report **output-level** and **task-level** agreement **separately**.

Then a small **staged ablation** over {dtype: bf16/fp16/fp32} × {chat-template:
on/off} × {special-token: on/off} on ≥1 representative cell converts the anecdotes
into quantitative marginal effects. This is the experiment that makes the fp32 claim
citable.

### 5.2 The historical forensic case (largely in hand)

- Verify/refresh the **redpajama-3b** end-to-end era result (recoverable case; already
  ran, reports on disk — confirm the pandas instance-identity fidelity rung and note
  the `synthetic_reasoning_natural` result is a *forensic recovery from a public-side
  serving artifact*, not a clean match).
- Write up **GPT-J/NeoX (G13)** as the non-identifiable case from the existing
  archaeology (no new large run required; the class-path lineage *is* the result).

### 5.3 Make the existing results citable (days, prerequisite to any write-up)

- Regenerate `olmo-models-combined` (and any stale store) with current code so the
  dedupe / metric-resolver / instance-fallback fixes take effect; **every cited number
  from a freshly regenerated store.**
- Freeze the model/benchmark roster (§3.3) and write the denominator/funnel numbers.

### 5.4 The larger empirical program (follow-on paper, not the near-term deadline)

- Qwen as a **bounded signal-vs-noise demonstration**, not a leaderboard: measure the
  Qwen *generational* improvement and compare it to the *variation induced by plausible
  evaluation configurations*. The scientific question: **is the model improvement
  larger than the uncertainty the evaluation system introduces?** This ties the
  historical reproduction work to a current motivation without a second unrelated paper.
- GPU-run `gpt_oss_20b_from_spec` to completion (artifact-semantics contrast).
- The cross-machine axis (one family × ≥2 GPU architectures) + a local-vs-local repeat
  baseline (the effect-size floor).
- Full-benchmark fp32 across the four OLMo instruct models + ≥1 non-ifeval benchmark.
- Surface agreement confounds inline (per-cell tuple) and adopt the Bernoulli floor for
  temperature>0 benchmarks.

**Cross-cutting:** pin model revisions for headline runs; keep `same_deployment`
honesty and the six-category taxonomy front-and-center; separate discovery from
held-out confirmation; every number from a regenerated store.

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

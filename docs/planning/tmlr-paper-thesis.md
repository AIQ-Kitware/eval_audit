# TMLR Paper Thesis: Adversarial Assessment and Experiment Plan

*2026-07-21. Written as the requested adversarial review of the research
direction, triggered by Jon's pivot to conceptual planning and a long
brainstorm from GPT 5.6 (six candidate research questions). This document
is the durable version; the journals carry the session narrative.*

*Revised same day after a second GPT 5.6 round that accepted the
single-candidate diagnosis and the 3-RQ structure and pushed back on seven
points. Adopted: the two-level (broad leaderboard + deep grid) design, a
narrowed RQ3 claim (JuStRank and SLMJury verified real — see §3), a
pre-registered candidate-selection rule, a statistical specification for
`conclusions.py` (§5.3), iso-hardware reframing with measured 3090 runs,
and the problem-vs-solution distinction on the unobtainable S(R,J\*) cell.
Contested: only details, noted inline.*

**Standing motivation (Jon's, verbatim in spirit):** frontier LLMs are
excellent but access is unequal; the research target is credible evaluation
using models ordinary researchers can run. The paper venue is TMLR
(reproducibility track), which requires insight beyond reproduction and
whose reviewers will be adversarial about novelty and overclaiming.

---

## 1. Verdict up front

The infrastructure is genuinely strong and slightly ahead of the science.
The program is sound, but **none of the proposed headline claims are
currently supported by the data we have, for one structural reason: every
rejudge artifact scores a single candidate model (gpt-oss-20b).** All
findings so far are *score-agreement* findings. Every interesting framing
on the table — conclusion preservation, ranking survival, accessibility
frontier, "does agreement predict conclusions" — is defined over a *set*
of candidates, and our set has size one.

The good news: this is the cheapest possible gap to close. The official
HELM corpus we mirror contains frozen responses **and** official judgments
for every leaderboard model. Expanding candidates requires **zero candidate
inference** — only judge inference, which is the thing our pipeline
industrializes. Candidates, not benchmarks, are the scientifically
load-bearing axis, and they cost the same GPU-hours per benchmark that
another benchmark would.

Priority inversion to internalize: **more candidates > more judge families >
more benchmarks > more judge sizes.** The current instinct ("expand
benchmarks, add gemma4") has the first two axes reversed and overweights
the third.

---

## 2. Status audit: GPT 5.6's six questions vs. what actually exists

Legend: **ANSWERED** (data in hand, finding journaled), **PARTIAL** (some
cells exist), **SUPPORTED** (infrastructure ready, no data), **UNSUPPORTED**
(needs new infrastructure), **CUT** (argued below).

| # | Question (compressed) | Status | What exists / what's missing |
|---|---|---|---|
| Q1 | Minimum judge/hardware budget that preserves benchmark conclusions | PARTIAL | Judge-size sweep done (6 judges × 5 benchmarks, 63 artifacts) but endpoint is score agreement for ONE candidate. No conclusion-level endpoints exist anywhere in the codebase. No VRAM/wall-time instrumentation. Quantization field exists in JudgeSpec, never exercised. |
| Q2 | Decompose drift between candidate generation and judging (with Edward) | SUPPORTED | Zero cells run. Infrastructure is ready on our side: snapshots are candidate-parameterized; the EEE path + `detect_helm_sidecars` was built exactly for ingesting externally produced runs. Needs Edward's reproduced run dirs and an agreed candidate overlap. |
| Q3 | Does conventional judge agreement predict conclusion preservation | UNSUPPORTED (analysis-only) | Requires Q1's multi-candidate data first; afterwards it is pure analysis, no GPU. We already hold a one-cell preview: Qwen3.5-2B on anthropic_red_team passes every format-health check (99.9% parse) while inverting the labels (25.7% agreement). |
| Q4 | When does inference substrate change the measuring instrument | PARTIAL | The replicate/batch-composition cell is DONE and is a finding (87–96% raw-text divergence at temp 0; score impact 0.2–0.7% XSTest vs 43–46% WildBench). Quantization, engine, GPU-arch, TP cells absent. Full grid is a distraction; one bounded slice is worth running (§4, RQ-S). |
| Q5 | Which rubric properties determine the minimum viable judge | PARTIAL (observational half only) | XSTest-vs-WildBench-vs-safety-trio contrast is in hand and Omni-MATH adds a third rubric type. But benchmark identity confounds rubric, domain, length, and class balance — with 6 benchmarks we can *describe*, not *attribute*. The interventional half (re-rubric fixed responses) changes the object of study from "reproduce HELM" to "design rubrics" — a different paper. |
| Q6 | Budgeted escalation protocol turning instability into uncertainty | CUT | A methods paper, and its premise depends on Q3's answer. Do not build. |

Also already in hand, and worth more than we've been treating it:
**identity replay 6/6 at ≤2e-14.** No adjacent paper we know of validates
its harness against the published artifact to machine precision before
substituting a component. This is the paper's methodological signature and
should be presented as such (the "replay gate"), not buried in methods.

---

## 3. Novelty: the adjacent literature and the wedge

What a well-read reviewer will cite against us, and the honest wedge:

- **Open/trained judges** (PandaLM, JudgeLM, Prometheus 1/2): train open
  judges to mimic GPT-4 preferences; endpoint is instance-level agreement
  on preference datasets under the authors' own harness. *Wedge: we do not
  train anything and we do not build a harness — we re-instrument the
  official scoring pipeline of a published leaderboard, gate on exact
  replay, and measure whether the leaderboard's conclusions survive.*
- **Panels/juries of small models** (PoLL, Verga et al. 2024): closest in
  spirit (replace GPT-4 judge with cheaper open ensemble). *Wedge: same as
  above plus conclusion-level endpoints, the accessibility frontier, and
  the candidate-side decomposition. If we cannot articulate this contrast
  crisply in the intro, the paper is in trouble — draft it early.*
- **Judge meta-benchmarks** (RewardBench, JudgeBench): score judges against
  gold labels. Complementary — cite to justify judge choices; they do not
  study substitution into an existing benchmark.
- **JuStRank** (Gera et al., arXiv 2412.09569, Dec 2024 — verified real):
  benchmarks 48 judges by the *system rankings* they induce vs. a
  human-grounded ranking, arguing instance-level assessment misses
  system-level bias. *This occupies the broad "instance metrics ≠ ranking
  quality" claim — RQ3 must NOT be advertised that broadly (§4). Wedge:
  JuStRank constructs its own meta-benchmark and ranking; we test the
  prediction question inside a real published pipeline, under exact
  replay, with the historical proprietary judge as the reference, and
  demand out-of-group prediction.*
- **SLMJury** (arXiv 2606.07810, June 2026 — verified real; postdates
  local knowledge, read it before writing related work): 16 SLM judges
  0.6B–14B across four families and ten benchmarks. *Occupies the
  "consumer-sized judge sweep" framing as such — our judge-size ladder can
  never be the headline. It remains a judge meta-benchmark under its own
  harness; the substitution-into-a-published-pipeline territory is still
  open.*
- **LLM-as-judge validity** (MT-Bench/Zheng et al., G-Eval, AlpacaEval
  length-bias line): established that closed judges correlate with humans
  and have biases. We inherit, not compete.
- **Benchmark agreement testing** (Perlitz et al., BenchBench): statistics
  for comparing benchmark-induced rankings. *Borrow their machinery for our
  conclusion-preservation endpoints rather than inventing ad-hoc metrics —
  reviewers from that community will check.*
- **Inference nondeterminism** (batch-composition/temp-0 literature,
  incl. the 2025 "defeating nondeterminism" line): the *existence* of our
  replicate divergence is known. *Position it as the measured noise floor
  under everything else, never as a discovery.*
- **Eval-reproducibility engineering** (lm-eval-harness "Lessons from the
  Trenches", HELM itself, BetterBench): treat inference and prompts as
  reproducibility variables. *The judge as a reproducibility variable
  inside a published leaderboard, with the official pipeline replayed
  exactly, appears genuinely unoccupied.*

**The novelty claim, qualified (post-round-2):** do not claim "first"
until a systematic literature search is done (leaderboard re-evaluation,
judge replacement, system-ranking judge evaluation, benchmark agreement,
benchmark auditing, historical-API reproducibility — a half-day work item,
§8). The safe formulation: *unlike judge meta-benchmarks and newly
constructed evaluation harnesses, we substitute judges inside an existing
published benchmark pipeline after verifying exact reconstruction of its
released results, and we measure survival of the leaderboard's published
conclusions under explicit local-compute constraints.*

On the cell S(R, J*) — Edward's reproduced responses scored by the
*official* judge — keep the problem/solution distinction clean. Its
unobtainability (the official judge is a dated proprietary deployment that
may no longer exist in the same form) is **evidence for the problem
statement** — closed-judge benchmarks cannot be regenerated even in
principle, by anyone — and a reason the full factorial is unidentified,
and motivation for open versioned judges. It is **not evidence that our
open replacement is valid**; validity comes only from the human/objective
anchors (§4).

---

## 4. Thesis and research questions

**Thesis (converged, round 2).** We exactly reconstruct the released
scoring pipeline of a proprietary-judge-dependent leaderboard, then
determine which of its published model-comparison conclusions remain
independently recoverable when the historical judge is replaced by
open-weight evaluators that can execute on a single 24 GB GPU — and
measure how judge diagnostics, candidate-reproduction drift, and benchmark
metric structure explain the boundary of that recoverable region.

**RQ1 — Conclusion survival under judge substitution (the core).**
Which published HELM conclusions — per-model scores within CI, pairwise
orderings, top-k membership, safety classifications — survive replacing
the official judge with open judges, as a function of judge scale and
family? What is the minimum judge (VRAM, wall-time) that preserves them?
*Needs: multi-candidate expansion (§5.1), conclusion-metrics module
(§5.3), one non-Qwen judge family (§5.4), cost instrumentation, and one
end-to-end demonstration on an actual consumer GPU (the 3090).*

**RQ2 — Decomposition with candidate reproduction (with Edward).**
Holding the open judge fixed, how much conclusion drift does locally
reproduced candidate inference add on top of judge substitution — and are
Edward's candidate-fidelity conclusions robust to evaluator choice
(≥2 judge families)? Explicitly a two-observable design, S(O,J) vs
S(R,J); we never claim the factorial. *Bonus: every S(R,·) cell uses
freshly generated responses, so response-level memorization by the judge
is designed out — the strongest cheap contamination control we have.*

**RQ3 — Do standard judge-health metrics predict conclusion survival?**
Parse rate, instance agreement, kappa, replicate stability: do they
predict rank/decision preservation across (benchmark × judge) cells? The
red-team 2B cell says no in one cell; with RQ1's grid this becomes
testable. **Scope narrowed after round 2:** the generic claim "instance
metrics don't certify system decisions" is occupied (JuStRank; BenchBench;
the within-prompt-selection line). The defensible question is whether
standard diagnostics predict preservation of *the specific published
conclusions of an existing leaderboard when its historical proprietary
judge is substituted under exact replay* — and prediction must be
**out-of-group** (leave-one-benchmark-out / leave-one-family-out), not an
in-sample association over pseudo-replicated cells (§5.3). RQ3 is a
consequence of RQ1, not the paper's primary novelty claim. Costs zero GPU
beyond RQ1/RQ2.

**RQ-S (secondary, strictly bounded) — the 24 GB question.**
At fixed consumer hardware, is a quantized larger judge or a
full-precision smaller judge the better instrument? One benchmark pair
(XSTest + WildBench), one candidate subset, INT4-27B vs BF16-9B (both fit
~24 GB), plus the same configs on the 3090 (Ampere) vs a PRO 6000
(Blackwell) to cover the arch cell. **Round-2 corrections adopted:** call
this **iso-hardware, not iso-resource** — the two configs may differ in
wall time, generated-token count, context capacity, and kernel maturity,
and those differences get *reported*, not assumed away. And the consumer
data points used in the central result must be **measured on the actual
3090** (peak allocated/reserved VRAM, wall time, throughput, total judge
tokens, failure rate, quantization + engine details) — not a single
proof-of-fit run, and not a Blackwell with an artificial memory cap. The
Blackwells do sweeps and high-memory references; the paper says so. This
feeds RQ1's frontier plot; it is not its own section. Replicate noise
floor is already measured; do not expand into engines/TP/batching grids.
*Ops prerequisite to schedule early: infer-stack has never been stood up
on the 3090 host — that is a work item, not an assumption.*

**Cross-cutting validity anchor (not an RQ).** A blinded, stratified human
audit (~300–450 items) over agreement/disagreement × replicate-stability
strata on XSTest (refusal is fast to annotate), Omni-MATH (equivalence is
objectively checkable — hand + sympy where possible), and WildBench
disagreements. Sampling protocol written and frozen **before** anyone
inspects disagreement contents (§5.6). Without this, "fidelity to GPT-4o"
is circular and a reviewer will say so; Q1/Q3 claims get scoped to
"fidelity" wherever the audit doesn't reach.

---

## 5. The one-week plan (concrete, tied to the codebase)

### 5.1 Candidate expansion (the critical path) — two-level design
- On aiq-gpu, audit `/data/crfm-helm-public/{safety,capabilities}` for ALL
  models on the six benchmarks (the source-audit tooling already takes a
  root; widen the per-benchmark glob from `model=openai_gpt-oss-20b` to
  all models). **Verify two assumptions the whole plan rests on:** (a) how
  many models the corpus actually holds per benchmark; (b) whether any
  candidate postdates Qwen3.5's launch (2026-02-16) — if none does, the
  contamination control moves entirely into RQ2 via Edward's fresh
  responses, and we should say so in the journal now, not in rebuttal.
- **Broad tier (the leaderboard headline):** ALL available candidates on
  XSTest + WildBench (the two contrasting metric families: label-shaped
  and early-saturating vs scalar-rubric and capacity-hungry), 2–3 core
  judges, one replicate. This answers "why not the whole leaderboard?"
  before a reviewer asks it — candidates cost no candidate inference. If
  the corpus check makes this prohibitively expensive, document the
  token/wall-time arithmetic *before* falling back to a subset.
  **Replicate-noise caveat (mine, round 2):** the paired bootstrap
  captures instance-sampling noise but NOT judge non-determinism, and
  WildBench instance judgments are 43–46% replicate-divergent. Broad-tier
  close-pair decisions on WildBench must carry the replicate-induced
  conclusion-flip rate measured in the deep grid as an uncertainty floor —
  or get replicates≥2 for close pairs specifically.
- **Deep tier:** 8–12 candidates, all six benchmarks, the wider judge
  roster, selected replicates. **Selection rule frozen before any
  rejudging, and computed only from official public scores** (so it is
  selection on pre-existing data, not on our outputs): predefined
  official-score quantiles for coverage, family diversity, overlap with
  Edward's set, a fixed number of nearest-neighbor official-score pairs
  and a fixed number of wide-gap controls. Then analyze ALL pairwise
  relationships in the set, not only pairs that flip — close pairs are
  informative, but choosing them post hoc looks like cherry-picking
  instability.
- Freeze + replay-gate each (05/08/09 runbook steps; the gate is free) and
  fan out via the now-fixed kwdagger path.
- Sizing guard: the deep tier at ~10 candidates × 6 benchmarks × 3 judges
  × 1 replicate is ≈ 10× the v1 sweep's judgments; the broad tier adds
  ~N_leaderboard × (450 + 1000) × judges. Prune: replicates=3 only for one
  candidate per benchmark (noise floor transfers); the full 6-judge ladder
  only for gpt-oss-20b (the frontier plot); everyone else gets the
  3-judge core.

### 5.2 Finish the in-flight validation (already queued)
- `./55_schedule_rejudge.sh omni_math --smoke --run` (kwdagger graph, live
  Omni-MATH annotator, strip_thinking on a third format — all still
  unproven), then the middle-ladder Omni-MATH matrix
  (`OJ_JUDGES="qwen3_5_2b qwen3_5_4b qwen3_5_9b"`).

### 5.3 Conclusion-metrics module (the main new code)

`eval_audit/judging/conclusions.py`, deliberately HELM-free and offline
like `rejudge_matrix.py`. This module is central enough that its estimands
are specified here, before implementation — effectively a mini
pre-registration (statistical spec adopted from GPT 5.6 round 2, all four
points accepted):

- **Predefined conclusion estimands, and only these:** pairwise model
  ordering; top-1/top-k membership; crossing a published safety
  threshold; retention of a statistically supported difference; score
  equivalence within a prespecified practical margin. Not every reported
  number is a "conclusion", and **CI-overlap is never used as a
  hypothesis test**.
- **Paired resampling.** Official and rejudged annotations concern the
  same instances: paired bootstrap over instance IDs, and for pairwise
  comparisons resample common instances *jointly* across candidate A,
  candidate B, official judge, and replacement judge.
- **Parse failures are MNAR, handled explicitly.** A judge fails more on
  hard responses and particular candidate styles (our own data: WildBench
  parse is size-graded 6.8%→90.7%, so "agreement given parse" at small
  sizes scores a tiny biased subset). Report sensitivity under at least:
  complete-common-cases; failure-as-explicit-invalid-judgment; and
  benchmark-specific conservative scoring. **Never compute rankings from
  silently different denominators per model.** Per-class rates everywhere
  (the safety sets are ~99% one-class; naive agreement is an FPR in
  disguise).
- **No pseudo-replication in RQ3.** Observations are nested (candidates
  within benchmarks, sizes within families, conclusions sharing
  instances, replicates sharing a response artifact). A naive regression
  over hundreds of cells overstates evidence; use clustered uncertainty
  or hierarchical structure, and evaluate prediction
  **out-of-group** (leave-one-benchmark-out, leave-one-family-out).
- Borrow benchmark-agreement-testing statistics (BenchBench line) rather
  than inventing.
- This subsumes the stale-aggregate-reports backlog item — rebuild
  `30_analyze_judges.sh` outputs on top of it.

### 5.4 One independent judge family (control, not headline)
- Gemma current-gen at two sizes (one ≤10B, one 20–30B class) + one
  third-family single point (Ministral/Phi/OLMo class). Config + metadata
  + sidecar per the established JudgeSpec pattern (~hours each, incl.
  smoke). NOT six more models; each added judge must have a stated role.
  Defer a Prometheus-style specialized judge unless a reviewer-shaped gap
  remains.

### 5.5 Edward coordination (do this week, before his set drifts)
- Agree the overlap candidate set (his reproduction categories × our
  close/wide pairs) and the artifact handoff: HELM-shaped run dirs snapshot
  directly; anything else goes through the EEE path with `run_spec.json`
  sidecars (`detect_helm_sidecars` was built for exactly this).
- Joint cell to run first: one benchmark (XSTest — cheapest, cleanest
  labels), 3–4 candidates in both O and R forms, 2 judge families.

### 5.6 Human-audit protocol (design before peeking)
- A short doc freezing: strata (agree/disagree × stable/unstable ×
  benchmark), sample sizes per stratum, blinding (annotators see
  prompt+response, never which judge said what), annotator set
  (Jon + Edward + one more), adjudication rule. Freezing this before
  inspecting disagreements is cheap pre-registration and TMLR reviewers
  reward it.

### 5.7 Instrumentation
- Record per-attempt wall-time and judge VRAM footprint (infer-stack knows
  the deployment footprint; stamp it into attempt metadata). Needed for
  the frontier plot; trivial now, painful retroactively. Energy is
  optional — only if nvidia-smi power polling costs nothing.

---

## 6. Explicit cuts (attractive, and wrong for this paper)

1. **Expanding off HELM before submission.** Each new harness re-opens the
   replay-gate engineering. "The complete annotator-dependent HELM
   Safety + Capabilities sub-suite" is a defensible, closed scope claim;
   "assorted benchmarks from three harnesses" is neither. Post-paper.
2. **Reproducing candidate inference ourselves.** Edward's lane. Our lane
   is judging his artifacts. Duplication would burn the exact GPU-hours
   RQ1 needs.
3. **Rubric-intervention study** (GPT 5.6's Q5b). Changes the object of
   study; second paper. Keep the observational rubric contrast only.
4. **Escalation/uncertainty protocol** (Q6). Methods paper whose premise
   depends on RQ3's answer.
5. **Full substrate grid** (engines × batching × precision × arch × TP).
   Only the RQ-S slice.
6. **Filling the remaining Qwen ladder cells** (0.8B/27B/35B safety trio).
   The sweep's shape is established; these cells change no conclusion.
   Only if GPUs would otherwise idle.
7. **A giant judge leaderboard.** Every judge added after §5.4 must name
   the confound it controls.

## 7. Confounds a reviewer will find if we don't

1. **Single candidate** (fatal to conclusion-level claims; §1; fix §5.1).
2. **Single judge family** — size story confounded with the Qwen
   post-training recipe (fix §5.4).
3. **Fidelity ≠ validity** — circularity of "agrees with GPT-4o" (fix
   §4 validity anchor; scope claims to "fidelity" elsewhere).
4. **Contamination** — already caveated with verified dates
   (research journal); controls are the post-cutoff candidate (§5.1b) and
   Edward's fresh responses (RQ2). **State the latter narrowly:** fresh
   responses control *response-level memorization* (the exact
   prompt–response–judgment triple) only — they do not remove familiarity
   with the prompts, the distribution, the rubric, or family/style
   preferences. Distribution shift, not memorization, remains the
   mechanism to discuss; our own size-graded agreement curve and the 2B
   label inversion argue against wholesale memorization and belong in
   that section.
5. **Class imbalance** — safety "agreement" is ~an FPR; per-class rates
   everywhere (§5.3).
6. **Statistical power** — XSTest is 450 items; a 1–2 pt score delta may
   not resolve. Bootstrap CIs on every conclusion endpoint; choose close
   pairs with power in mind (§5.1).
7. **The missing S(R,J*) cell** — name it, explain why it cannot exist,
   and frame it as evidence for the *problem statement*, never as
   evidence that the open replacement is valid (§3, last point).
8. **"Consumer hardware" claims made from a 4×96 GB Blackwell box** — the
   consumer data points in the central result must be measured on the
   actual 3090 with full resource reporting (§4 RQ-S); one proof-of-fit
   run is not a frontier. The Blackwells are for replication and sweeps;
   say so.

## 8. Assumptions to verify before committing the plan (cheap, this week)

- [ ] Model coverage per benchmark in `/data/crfm-helm-public` on aiq-gpu
      (drives candidate selection; §5.1).
- [ ] Existence of any post-2026-02-16 candidate in the corpus (drives
      where the contamination control lives).
- [ ] HELM Safety v1.14.0 / Capabilities v1.12.0 publication dates (still
      unresolved; bounds the judgment-scrape contamination channel).
- [ ] Whether the six benchmarks are in fact the complete annotator-based
      subset of those two HELM releases (if 1–2 more exist, either add or
      state the exclusion rule; "complete sub-suite" is only claimable if
      checked).
- [ ] Edward's artifact format and candidate list.
- [x] JuStRank (2412.09569) and SLMJury (2606.07810) verified real via
      web search 2026-07-21; both must be read in full before the related
      work section is drafted (SLMJury postdates local model knowledge).
- [ ] Systematic literature search before any "first" claim: leaderboard
      re-evaluation, judge replacement, system-ranking judge evaluation,
      benchmark agreement, benchmark auditing, historical-API
      reproducibility (half-day; do before drafting the intro).
- [ ] Token/wall-time budget arithmetic for the broad tier (all
      leaderboard candidates × XSTest+WildBench × 2–3 judges) — computed
      before deciding whether the headline is full-leaderboard or subset.
- [ ] infer-stack stood up on the 3090 host (prerequisite for RQ-S
      measured runs; currently only aiq-gpu is provisioned).

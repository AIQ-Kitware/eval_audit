# TMLR Paper Thesis: Adversarial Assessment and Experiment Plan

*2026-07-21. Written as the requested adversarial review of the research
direction, triggered by Jon's pivot to conceptual planning and a long
brainstorm from GPT 5.6 (six candidate research questions). This document
is the durable version; the journals carry the session narrative.*

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
| Q4 | When does inference substrate change the measuring instrument | PARTIAL | The replicate/batch-composition cell is DONE and is a finding (87–96% raw-text divergence at temp 0; score impact 0.2–0.7% XSTest vs 43–46% WildBench). Quantization, engine, GPU-arch, TP cells absent. Full grid is a distraction; one bounded slice is worth running (§6, RQ-S). |
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

**The one-sentence novelty claim that survives:** *the first exact-replay
substitution study of the proprietary judge inside a published benchmark's
official scoring pipeline, with conclusion-level endpoints, a
consumer-hardware cost frontier, and a candidate-side decomposition.*

A rhetorical point worth keeping: the cell S(R, J*) — Edward's reproduced
responses scored by the *official* judge — is unobtainable not merely
because of budget but because the official judge is a dated proprietary
deployment that may no longer exist. **The impossibility of that cell is
itself evidence for the paper's thesis** (closed-judge benchmarks are
irreproducible by construction on a timescale of months), not only a
limitations bullet.

---

## 4. Thesis and research questions

**Thesis.** Published LLM leaderboard conclusions that depend on
proprietary judges can be independently reproduced with open-weight judges
on consumer-class hardware for some metric families but not others; we
characterize the recoverable region, its hardware cost, and the failure
modes, using an exact-replay harness that isolates the judge as the only
changed variable.

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
red-team 2B cell says no in one cell; with RQ1's grid this becomes a
testable claim. A negative result here is the most citable insight in the
paper: *the metrics everyone uses to validate judges do not certify the
thing benchmarks exist to deliver.* Costs zero GPU beyond RQ1/RQ2.

**RQ-S (secondary, strictly bounded) — the 24 GB question.**
At fixed consumer VRAM, is a quantized larger judge or a full-precision
smaller judge the better instrument? One benchmark pair (XSTest +
WildBench), one candidate subset, INT4-27B vs BF16-9B (both fit ~24 GB),
plus the same configs on the 3090 (Ampere) vs a PRO 6000 (Blackwell) to
cover the arch cell. This feeds RQ1's frontier plot; it is not its own
paper section. Replicate noise floor is already measured; do not expand
into engines/TP/batching grids.

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

### 5.1 Candidate expansion (the critical path)
- On aiq-gpu, audit `/data/crfm-helm-public/{safety,capabilities}` for ALL
  models on the six benchmarks (the source-audit tooling already takes a
  root; widen the per-benchmark glob from `model=openai_gpt-oss-20b` to
  all models). **Verify two assumptions the whole plan rests on:** (a) how
  many models the corpus actually holds per benchmark; (b) whether any
  candidate postdates Qwen3.5's launch (2026-02-16) — if none does, the
  contamination control moves entirely into RQ2 via Edward's fresh
  responses, and we should say so in the journal now, not in rebuttal.
- Select 8–12 candidates: family diversity, close AND wide official score
  gaps (pick close pairs deliberately — they are where conclusion flips
  live), release-date spread, overlap with Edward's reproduction set.
- Freeze + replay-gate each (05/08/09 runbook steps; the gate is free) and
  fan out via the now-fixed kwdagger path.
- Sizing guard: ~10 candidates × 6 benchmarks × 3 judges (4B, 9B, Gemma-mid)
  × 1 replicate ≈ 10× the v1 sweep's judgments. Prune: replicates=3 only
  for one candidate per benchmark (noise floor transfers); the full 6-judge
  ladder only for gpt-oss-20b (the frontier plot); everyone else gets the
  3-judge core.

### 5.2 Finish the in-flight validation (already queued)
- `./55_schedule_rejudge.sh omni_math --smoke --run` (kwdagger graph, live
  Omni-MATH annotator, strip_thinking on a third format — all still
  unproven), then the middle-ladder Omni-MATH matrix
  (`OJ_JUDGES="qwen3_5_2b qwen3_5_4b qwen3_5_9b"`).

### 5.3 Conclusion-metrics module (the main new code)
- `eval_audit/judging/conclusions.py`, deliberately HELM-free and offline
  like `rejudge_matrix.py`: official-vs-rejudged leaderboard comparison —
  rank correlation, pairwise-ordering flips with bootstrap CIs over
  instances, top-k membership, threshold classifications; per-class rates
  everywhere (the safety sets are ~99% one-class; a naive agreement number
  is an FPR in disguise and the paper must never average over that).
  Borrow benchmark-agreement-testing statistics rather than inventing.
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
   (research journal); decisive controls are the post-cutoff candidate
   (§5.1b) and Edward's fresh responses (RQ2). Distribution shift, not
   memorization, is the mechanism to discuss; our own size-graded
   agreement curve and the 2B label inversion argue against wholesale
   memorization and belong in that section.
5. **Class imbalance** — safety "agreement" is ~an FPR; per-class rates
   everywhere (§5.3).
6. **Statistical power** — XSTest is 450 items; a 1–2 pt score delta may
   not resolve. Bootstrap CIs on every conclusion endpoint; choose close
   pairs with power in mind (§5.1).
7. **The missing S(R,J*) cell** — name it, explain why it cannot exist,
   and spend one paragraph turning it into evidence (§3, last point).
8. **"Consumer hardware" claims made from a 4×96 GB Blackwell box** — at
   least one end-to-end run on the 3090, reported with wall-time (§4 RQ1,
   RQ-S). The Blackwells are for replication and sweeps; say so.

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

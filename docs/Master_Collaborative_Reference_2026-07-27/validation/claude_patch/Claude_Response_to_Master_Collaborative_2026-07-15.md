# Response to the Master Collaborative Reference Set (2026-07-15)

**From:** Claude (working in the live `eval_audit` repository, on behalf of Edward Wang)
**Re:** `Master_Collaborative_Reference_Documents_2026-07-15.zip`
**Evidence vantage:** the *live* repo at HEAD `86ec84af` on `impl/run-from-run-spec`,
**including `/data`**, which the source tarball you were given (`b8b858697e7c` = `b8b85869`)
deliberately excludes.

---

> **Status update (2026-07-15b).** The factual/wording/status corrections below
> (**A1, A2, A3, B1, B2, B3**, and the compile-guard half of **B6**) have now been
> **applied inline to the collaborative master document** — see the enclosed
> `Master_Collaborative_corrections_2026-07-15b.diff` and the new
> "Corrections applied in this master revision (2026-07-15b)" section in Chapter 1.
> The separate local chronology has been **removed** and the repository consolidated
> onto the collaborative master doc. Only three *structural* items remain **proposed,
> not applied** (they restructure your document rather than correct a fact): **B4**
> (consolidate the overlapping ledgers), **B5** (demote the vendor-named Chapter 1),
> and the **README/directory** half of **B6**. Please confirm or push back on those.

---

## 0. Overall verdict

The merge is faithful and, in several places, sharper than my revision. I accept the
great majority of it without reservation: the system-identification thesis, the
`F=(R,D,I,A,U)` measurement model, fp32-as-discovery plus the conditional deductive
dtype leg, the six-category taxonomy, the recoverable-vs-non-identifiable spine, the
per-parameter identifiability map, the confirm-step protocol, the hybrid-paper scope
with OLMo confirmation as the pivotal experiment, Qwen-as-demonstration, and the
`model_revision` + `torch_dtype` + `transformers_version` provenance recommendation.
The **evidence-precedence rule** (immutable artifacts > source/git > regenerable
reports > journals > slides > narrative) is exactly right, and everything below
*respects* it — I am supplying the artifacts, not asking you to trust narrative.

This response does three things:

- **Part A** resolves the items you marked *Unresolved / not-verifiable / not-packaged*
  using direct inspection of the live repo and `/data`. Most of those statuses are
  artifacts of your archive cutoff, not genuine evidential gaps — **but one of them,
  once resolved, actually strengthens your caution with hard numbers.**
- **Part B** raises six concerns where the merge over- or under-corrects, or has an
  editorial/packaging defect.
- **Part C** lists what I endorse verbatim, so the accepted surface is explicit.

---

## Part A — Reconciling the archive-access asymmetry

Your archive is a `git archive` at `b8b85869`, which (a) predates even the original
chronology commit (`3e8e73cd`) and (b) omits `/data`, untracked, and ignored paths.
That single fact generated most of the "cannot verify" rulings. Direct inspection
resolves them — and refines them into a **three-way status** that your current
single "locally supported; not packaged" label collapses:

### A1. Commit `86ec84af` — **Verified**, not "Unresolved"
It is the current **HEAD** of `impl/run-from-run-spec` and contains the described
revisions (Appendix D, the fp32 epistemic callout, the OLMo-2 reframing). Lineage:
`b8b85869` (your archive) → `3e8e73cd` (chronology) → `4a3002ef` (paper plan) →
`156ffa09` → `86ec84af` (HEAD). **Proposed change:** in the Chapter-1 validation
table and the claim matrix, move this row from `Unresolved` to
*"Verified against the live branch; post-archive commit."*

### A2. The `/data` stores exist — with **differing freshness that matters for the paper**
This is the important refinement. "Not packaged" is true of all of them (none are in
your archive, none are hashed yet), but their *evidential* status is not uniform, and
flattening them under one label both **under-sells** a real result and **under-warns**
about a stale one:

| Store | On-disk state (verified) | Correct status |
|---|---|---|
| `gpt-oss-20b-from-spec` | Reports regenerated **Jul 14 13:47**, *after* the drift-plot fix commits. Numbers are current-code. | **Fresh result, unpackaged** — copy+hash; do **not** hedge it as "possibly stale." |
| `olmo-models-combined` | Reports carry the **un-deduped stale-local bug**: on-disk headline shows `olmo-7b` MMLU **0.295/0.144**, GSM 0.036/0.018, narrative_qa 0.597/0.311 (local ≈ half). The code fix (`93849b09`) is in HEAD but the store was never regenerated against it. | **Stale — must regenerate.** Your caution is right; here is the proof. |
| `era-redpajama` (v0.2.4 + v0.3.0, full + smoke) | Both-era runs present, **Jul 12**; `container_provenance.json`, `helm/`, `materialized_run_specs/` all present. | **Locally supported** — correct as stated. |
| `qwen-combined` / `qwen-models` | **Absent.** No store on `/data`. | **Genuinely pending** — correct to treat as unfinished. |

**The load-bearing correction for the paper.** The chronology's OLMo heatmap figure
caption cites the *corrected* `olmo-7b` numbers (MMLU **0.295/0.287**, GSM 0.036/0.036,
narrative_qa 0.597/0.595). Those are the **intended post-fix values, not what is on
disk** — the disk still shows the halved 0.144/0.018/0.311. So: the fix is understood
and committed, but **the OLMo base-model reproduction figures cannot be cited until
the store is regenerated.** This vindicates your "regenerate before submission" ruling
and gives it a concrete, checkable failure signature (`local ≈ public/2` on the
`olmo-7b` classic cells).

**Proposed change:** replace the single "locally supported; not packaged" status
wherever it appears with the three-way distinction — **(i) fresh-but-unpackaged
(GPT-OSS)**, **(ii) stale-must-regenerate (OLMo)**, **(iii) genuinely-absent (Qwen)** —
and record the RedPajama both-era stores as *present (Jul 12), pending hash*.

### A3. Provenance of the deployment-match and 59% claims — **sourced**
- The fp32 probe was **ifeval-only across all four OLMo instruct models** (HF and vLLM
  variants), overnight **2026-07-08** (`/data/.../deployment-match/{olmoe,olmo-2-*}--ifeval[-hf]`).
  The table (OLMoE HF-fp32 0.971, OLMo-2-7B vLLM-fp32 0.915, OLMo-2-13B 0.904,
  OLMo-2-32B fp32-tp2 0.961) matches the stored sweeps.
- The "**~59%** of the corpus is pre-v0.5 classic-track" figure is sourced
  (`docs/container-execution.md:146`; `dev/journals/claude.md:629`). It is traceable,
  not free-floating — **proposed change:** add the citation so the authoritative
  reference doesn't present it bare.

---

## Part B — Substantive concerns (proposed modifications)

### B1. Don't over-correct the OLMo-2 HF divergence from "broken probe" into "confirmed finding"
Reframing it away from "just a defective tool" is correct. But the current §"residual
puzzle" lists **only substrate axes** as candidate causes (revision, transformers/torch,
attention impl, device placement, decode path) and silently drops the hypothesis that
**our own dense-model HF probe path is itself misconfigured**. That is an
over-correction in the opposite direction: we swapped "it's only a tool bug" for "it's
only execution-stack sensitivity," when the honest position is that **the cause is not
yet isolated and *both* remain live** — a genuine execution-instrument divergence
*and* a probe/harness artifact specific to dense models are competing explanations
until one is ruled out. The claim "vLLM-fp32 matches but current-HF does not" is
strong evidence *of a divergence*, but not yet of *where* it lives.

**Proposed wording** (append to the candidate list): *"…and, not to be excluded until
tested, a defect in the dense-model HF probe path itself (e.g. tokenizer/template
handling that differs from the OLMoE path on which the probe succeeds). Disambiguating
'genuine execution-stack sensitivity' from 'probe artifact on dense models' is itself
an open item; the reframing rejects only the premature dismissal of the divergence, not
the possibility that our tooling contributes to it."*

### B2. "apparently HF-produced historical result" → tighten to the recorded fact
The OLMo-2 officials are **recorded as `HuggingFaceClient` deployments** — that is the
very basis for the dtype-default argument. Saying "apparently HF-produced" understates
what we actually know and blunts the puzzle. **Proposed wording:** *"a result recorded
as a `HuggingFaceClient` (in-process `transformers`) deployment"* — which makes the
divergence sharper (a *recorded-HF* official that current-HF fails to reproduce while
vLLM-fp32 matches).

### B3. "roughly two orders of magnitude tighter" (GPT-OSS vs OLMo ifeval) — numerically loose
GPT-OSS squared errors are bounded by ≈`4.8e-4`; the OLMo instruct ifeval squared
errors are ≈`1.0e-2`–`1.6e-2`. The ratio is **≈25–35×**, i.e. about **1.5 orders of
magnitude**, not two (~100×). This claim originates in *my* chronology and you inherited
it faithfully — the error is mine. **Proposed change:** "more than an order of magnitude
tighter (≈30×)". I will also correct it in the committed chronology.

### B4. Consolidate the overlapping claim ledgers
The set now carries **five** overlapping status tables (Chapter-1 validation table;
Appendix D corrected-claims; the master claim-matrix appendix; the strategy doc's
evidence table; plus `Collaborative_Claim_Status_Ledger.csv` and
`Claude_Response_Validation_Decisions.csv`). For a document meant to be the
*authoritative* reference, these will drift out of sync. **Proposed change:** designate
**one machine-readable ledger** (the CSV) as source-of-truth, and have the prose tables
either render from it or explicitly cite it as canonical, rather than restating it.

### B5. The "Validation of Claude's response" framing belongs in an appendix, not Chapter 1
Adjudicating "Claude" vs "OpenAI" by vendor name is collaboration-process meta; it
reads oddly at the front of a reference intended to seed an academic paper (which will
have neither vendor in it). The *rulings* are valuable and should stay. **Proposed
change:** demote the validation content to an appendix (or fold it into the
evidence-precedence section) and neutralize the vendor framing to "two independent
reconstructions." Keep Chapter 1 as the evidence-precedence + two-paper-lineage
front-matter only.

### B6. Packaging/compile integrity
The master `.tex` `\input`s `appendices/main_commit_ledger.tex` and
`appendices/submodule_commit_ledger.tex`, which are **absent from the flat zip**, so it
will not compile as delivered; and the `README` describes a
`chronicle/ strategy/ validation/ sources/ data/` tree that the flat archive does not
contain. **Proposed change:** either ship the referenced files and directory layout, or
guard the `\input`s and align the README with the actual flat contents.

---

## Part C — Endorsed verbatim (no change requested)

Thesis (system identification); the `F=(R,D,I,A,U)` model and outcome classes; fp32 as
configuration-discovery plus the *conditional* source-level deductive leg as the
stronger evidence; the six-category disagreement taxonomy with the non-runnable
eligibility gate kept upstream; recoverable-vs-non-identifiable as the conceptual
spine, with OLMo *partially recoverable* pending confirmation and GPT-J/NeoX/OPT as the
non-identifiable counterexample (G13); the per-parameter identifiability map; the
seven-step OLMo confirmation protocol with discovery/held-out separation; the hybrid
position/systems paper as the near-term product; Qwen as a bounded signal-vs-noise
demo and Fable deferred pending data/training provenance; the provenance recommendation
and the preservation checklist; and the evidence-precedence principle itself.

---

## Proposed next actions (I can execute these)

1. **Regenerate `olmo-models-combined`** so `0.295/0.287` (and the other `olmo-7b`
   classic cells) become the on-disk truth, materializing the stale-local dedupe fix.
2. **Copy + hash** the fresh `gpt-oss-20b-from-spec` and both-era `era-redpajama`
   stores into a preservation bundle, so the "not packaged" caveat is retired for the
   results that are actually done.
3. **Errata patch** to the master `.tex` implementing B1–B6, and the A1/A2 status
   refinements.
4. Correct the "two orders of magnitude" claim (B3) in the committed chronology.

**Two decisions I'd ask you to confirm:** (a) the three-way store-status refinement in
A2 (fresh / stale / absent) replacing the single "not packaged" label; and (b) the two
wording changes in B1 (keep "probe artifact on dense models" as a live cause) and B2
("recorded as a HuggingFaceClient deployment"). Everything else I will apply as errata
unless you object.

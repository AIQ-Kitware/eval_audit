# Edward — task queue (substrate reproduction thread)

*2026-07-23. Concrete, ordered tasks. These are execution work that is valuable
under any version of the paper; the paper-structure calls (spine, EEE
positioning) are Jon's and are tracked separately in
`docs/planning/tmlr-paper-thesis.md`. Pick tasks top-down; each is independent
enough to hand off. Record results in the journal + the tables named below.*

## Context you need (2 min)

The OLMo-2 fp32 thread is **closed and attributed** (see `dev/journals/claude.md`
2026-07-23 entries). For OLMo-2 instruct **ifeval**, the published number is
reproduced **byte-exactly** once three unrecorded substrate variables are
recovered, and each accounts for a defined slice of the drift:

| recipe | ifeval_strict_accuracy drift vs official |
|---|---|
| bf16 + modern chat template | +0.10 |
| vLLM fp32 + agp0 | +0.07 (precision + template fixed; **engine** still wrong) |
| HF fp32 + agp0 (greedy or 1e-7 decode) | **exact** |

The +0.07 is **pure vLLM↔HF fp32 engine numerics** (proven: HF-fp32-greedy also
matches exactly, so it isn't decode). Tools live in
`reproduce/olmo_models_combined/deployment_match/` (`70`–`73`,
`run_deployment_match.sh`). All probes are pure `transformers.generate` on one
GPU — no infer-stack/lease/vLLM, so no contention; run them concurrently.

The job now is to turn that **one cell** into a **systematic result**: does it
generalize across families, benchmarks, and the corpus?

---

## Task A — Cross-family generality (READY, ~1–2h, fills 4 GPUs)

**Question:** does "unpinned dtype ⇒ ran fp32 ⇒ HF-fp32 reproduces exactly" hold
for other families, and does a dtype-**pinned** official reproduce at its pinned
dtype (control)?

**Run:**
```bash
./73_family_sweep.sh          # pythia(0) vicuna(1) granite(2) gemma(3), concurrent
# validate one first if you like:  FAMILIES="pythia" ./73_family_sweep.sh
```
Treatment (unpinned): pythia-6.9b, vicuna-7b, granite-4.0-micro → predict fp32
wins, bf16 loses. Control (pinned bf16): gemma-2-9b-it → predict bf16 wins, fp32
loses. (gemma is a gated HF repo — needs Gemma token access; skip if it can't
download.)

**Record** in a new table `docs/reproduction/family-generality.md` (a proposed
new file — the `docs/reproduction/` directory does not exist yet): per family —
winning dtype, exact/quasi match, and whether it confirms the treatment/control
prediction. Any family that does NOT reproduce at fp32/eager/single: broaden the
forward-pass axes (`DM_HF_ATTN=eager,sdpa DM_HF_DEVMAPS=auto,single ./72...`) and
note what it took.

**Done when:** the table has all 4 families with a confirmed/refuted prediction.

## Task B — Cross-benchmark generality on OLMo-2 (~needs a small script tweak)

**Question:** does the substrate story hold beyond ifeval? OLMo-2 also has
bbq, gpqa, mmlu_pro officials.

**Approach:** point the HF-fp32 probe at each OLMo-2 non-ifeval official (find the
run dirs under `/data/crfm-helm-public/.../v1.8.0/<bench>:...olmo-2-1124-7b...`)
via `72_hf_fp32_family.sh <label> <rundir> <gpu>` with the right protocol
(bbq/mmlu are multiple-choice → likely `DM_PROTOCOL=chat` or completions; check
the official prompt shape). Compare HF-fp32 vs official.

**Watch for:** MC benchmarks emit short answers, so "exact match" is easy and less
diagnostic — report the **metric** delta too, not just completion agreement. This
is where the fp32/engine effect may or may not move a *conclusion* (recall the
zero-compute finding: gpqa had 3/6 official-vs-local pairwise flips at bf16).

**Done when:** a table shows, per OLMo-2 benchmark, the bf16→fp32→HF-fp32 drift and
whether fp32 changes the metric materially.

## Task C — Systematic reproduction census (the paper backbone; bigger)

**Question the paper answers:** *of a representative sample of runnable HELM runs,
what fraction reproduce exactly, what fraction only after recovering a named
substrate variable, and what fraction stay unexplained?*

**This must be prospective** — freeze the design before looking at outcomes:
1. **Sample** by a fixed rule from the 1,109 eligible runs
   (`/data/crfm-helm-audit-store/analysis/filter_inventory.json`): stratify by
   family × base/instruct × client type × benchmark family; fixed N per stratum.
   Selection computed only from the census, not from which runs look interesting.
2. **Diagnostic ladder**, applied in the same order to every sampled run:
   from-spec replay → dtype (fp32 default) → tokenizer/template → engine
   (HF vs vLLM) → residual/unresolved.
3. **Outcome buckets**, defined now: EXACT / RECOVERED-WITH-CAUSE(name it) /
   UNRESOLVED / BLOCKED(gated model|dataset|judge). Blocked ≠ irreproducible.
4. **Stopping rule** per run (which ladder rungs to try before calling it
   unresolved).
Then report the distribution + a per-cause breakdown.

**Start small:** freeze the sampling rule + ladder as a one-page spec (Jon
reviews), run the first stratum (~8–12 runs), sanity-check the buckets, then
scale. The `72`/`run_deployment_match.sh` tooling covers most rungs already.

**Done when:** the frozen spec exists and the first stratum is bucketed.

## Task D — Controls / rigor (opportunistic)

- phi-3-small-8k control: needs a ~10-line `dev/tools/deployment_match/grid.py`
  patch to sweep `trust_remote_code` (currently pinned False), then run as a
  dtype-pinned (auto→bf16) control.
- Optional: full-n (541) confirm of one OLMo-2 HF-fp32 cell to upgrade "32/32
  exact" → "541/541 exact" for the paper figure.

---

## Not Edward's tasks (Jon + assistant)

- **EEE boundary memo** — what EEE's reproduction-audit paper already claims vs.
  what we can claim newly (attribution + byte-exact recovery + engine-gap). Gates
  whether the substrate work is a paper or a section. Needs the EEE paper PDF.
- **Paper spine / one-vs-two-papers / PM 2023 scope** — round-3 D1–D5.

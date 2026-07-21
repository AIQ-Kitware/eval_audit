# Overnight 2026-07-21: fp32 end-to-end confirm + cross-family probe pilot

Operational plan for tonight's window on aiq-gpu (4x RTX PRO 6000 96GB).
Pre-registered BEFORE execution; do not edit outcome definitions after launch.
Context: Edward's last two internship weeks; this is the confirm step his own
deployment-match tool emits (`confirm/confirm_plan.md` per sweep) and the
"GPU-host acceptance runs ... remained pending" open thread from the draft.

## Propositions on trial (do not conflate)

- **A.** Historical unpinned dtype => fp32 execution.  Status: mechanism
  verified in transformers 4.x code path; empirically confirmed on ONE family
  (OLMoE exact at n=12). Tonight's sweeps extend A across families.
- **B.** Reproducing fp32 materially improves completion recovery.  Status:
  probe-only (n=12, ifeval, 4 OLMo models). Tonight's e2e runs test B at n≈full.
- **C.** The recovered config changes aggregate metrics / conclusions.  Status:
  UNTESTED. Targets: ifeval_strict_accuracy official/local gaps
  7B +0.098 (0.693/0.791), 13B +0.126 (0.730/0.856).
  Zero-GPU finding recorded today: 4/25 OLMo pairwise orderings already flip
  official-vs-local (gpqa 3/6 pairs; ifeval 0 — its +0.10 drift is ~uniform),
  so procedural drift CAN flip conclusions and drift size does not predict it.
  (gpqa official gaps ≈1.3σ — paired bootstrap must confirm which flips are real.)

## GPU allocation

| Card(s) | Job | Est. fit |
|---|---|---|
| GPU0 | E2E-1: OLMo-2-1124-7B-Instruct, ifeval, fp32 dm endpoint (batch-1, enforce-eager) | likely completes |
| GPU1 | E2E-2: OLMo-2-1124-13B-Instruct, same | may run into morning — acceptable |
| GPU2 | SWEEP-1: non-OLMo family #1, deployment_match `auto` | overnight |
| GPU3 | SWEEP-2: non-OLMo family #2, deployment_match `auto` | overnight |
| (opt, early evening GPU2/3) | omni_math kwdagger smoke (validates fan-out for post-Edward work) | ~1h, release before sweeps |

32B fp32-tp2: NOT tonight (2 cards + slowest; VRAM is why it needs tp2 —
128GB fp32 weights vs 96GB cards; run after 7B/13B verdicts).
OLMoE e2e: NOT POSSIBLE via vLLM at any TP — the Triton fused-MoE kernel
shared-mem OOMs at fp32 and TP does not help ("per-block tiles, not shard
count"; journal 2026-07-10, commit 2698389) — and the HF-in-process routing
switch is unwired. Do not improvise either tonight. Dense fp32 via vLLM is
PROVEN on aiq-gpu's own cards (Jul-10 sweep logs, run as edward.wang,
GPU 0): serving feasibility is not a risk for tonight's 7B/13B cells; the
ast0 HELM-path wiring is the only real one.

## E2E runs (test B + C)

Per `confirm/confirm_plan.md` in each sweep dir under
`/data/crfm-helm-audit-store/deployment-match/<model>--ifeval-vllm/`:

1. Serve: `INFER_STACK_CONFIG_DIR=<sweep>/confirm/serve` then
   `infer-stack acquire dm-<model>-fp32-attnflash-attn --yes --env-file ...`
2. Replay the official run_spec from-spec against that endpoint (ifeval-only
   slice of the combined manifest), normal HELM path.
3. Compare with `eval-audit-compare-pair --run-a <official> --run-b <local>`.

**Pre-flight (Edward judgment, ~30-45 min, BEFORE launch):**
- Confirm baseline runs' dtype from the production catalog (expected: vLLM
  default = checkpoint bf16; catalog pins no dtype — note the irony).
- The ast0 probe-only knob: land it HELM-path-native per the plan's warning
  (tokenizer-sibling override, precedent 74ba33d, or VLLMClient patch). Record
  WHICH route was used in this file's log section.
- agp0 in the winner label: decide whether it was a tie artifact (many cells
  tie) or load-bearing; if load-bearing and not landable tonight, run fp32
  with HELM-default rendering anyway — that isolates PRECISION as the single
  moved factor, and the template layer becomes tomorrow's factor.

**One factor at a time:** tonight's e2e moves ONLY dtype (+ast route) relative
to the existing bf16 locals. Do not also change tokenizer, template, or engine.

## E2E outcome definitions (frozen)

Let D_bf16 = local_bf16 − official (known: +0.098 / +0.126) and
D_fp32 = local_fp32 − official on ifeval_strict_accuracy.

- **EXACT-RECOVERED:** |D_fp32| ≤ 0.01 AND instance quasi-agreement ≥ 0.95.
- **MATERIALLY-IMPROVED:** |D_fp32| ≤ 0.5 · |D_bf16|.
- **UNCHANGED:** |D_fp32| ≥ 0.8 · |D_bf16|.
- **ANOMALOUS:** anything else, or a new failure mode. Preserve, don't reclassify.

Interpretation: EXACT/IMPROVED ⇒ B+C confirmed at scale; flagship figure =
"recovering the unrecorded substrate closes the aggregate gap." UNCHANGED ⇒
probe-scale disconnect: completion match at n=12 does not predict aggregates
at n≈full — itself a finding; Edward lists candidate causes tomorrow (template
at scale, decoding path, length effects) while his context is available.

## Cross-family probe sweeps (extend A/B)

**Frozen selection rule (run the scan BEFORE looking at any discrepancies):**
from `filter_inventory.json` eligible+has_hf_client models, keep those whose
OFFICIAL run's deployment resolves to a HELM HuggingFaceClient (check
run_spec.json / model_deployments under /data/crfm-helm-public), family !=
allenai, params ≤ 13B (fp32 fits one 96GB card), with ≥1 generative-completion
official run (prefer gsm / med_qa / narrative_qa). Rank by run count; take the
top 2 distinct families. If one candidate is dtype-PINNED in HELM's
model_deployments.yaml, keep it deliberately as the CONTROL cell.
Expected (to be confirmed by the scan): gemma-2-9b-it / qwen2.5-7b-instruct /
phi-3.5-mini among {84,74,74,72,44,28,28}-run candidates.

**Registered predictions (write per-cell before launch):** unpinned official ⇒
fp32 cell wins the ranking; pinned official ⇒ the pinned dtype wins and fp32
does NOT. Both directions are tests of A's mechanism.

**Outcome definitions (frozen):** EXACT quasi ≥ 0.99; NEAR quasi ≥ 0.90;
FP32-DECISIVE: best fp32 cell beats best non-fp32 cell by ≥ 0.10 composite;
UNRESOLVED otherwise. Preserve infeasible/failed cells with typed reasons.

Launch pattern: copy `run_deployment_match_olmo2_7b.sh` per model, point at the
selected official run, `DM_PROFILE` per official client class, `auto` phase.

## Edward's morning protocol (~90 min, before anything else)

1. E2E: run the compare-pair step from each confirm plan; read aggregate
   ifeval_strict_accuracy + instance quasi-agreement; classify per the frozen
   definitions; fill the table below.
2. Sweeps: read the ranked verdict lines per sweep log; classify per frozen
   definitions; note any NEW family-specific quirk in one paragraph each —
   these paragraphs are the forensic-knowledge externalization we are buying
   tonight.
3. Recompute the pairwise-flip table including the fp32 locals: does fp32
   restore the official gpqa/bbq orderings? (script: see journal 2026-07-21;
   the flip analysis reads aggregate_score_diff_headline.json.)
4. 30-minute debrief note appended here + journal.

## Results log (fill tomorrow)

| Cell | Prediction | Outcome | Verdict | Notes |
|---|---|---|---|---|
| olmo-2-7b ifeval fp32 e2e | D_fp32 → ~0 | | | |
| olmo-2-13b ifeval fp32 e2e | D_fp32 → ~0 | | | |
| sweep family #1: ______ | | | | |
| sweep family #2: ______ | | | | |

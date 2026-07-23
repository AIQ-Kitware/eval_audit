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

## E2E runs (test B + C) — one command per model

```bash
./60_confirm_fp32_e2e.sh 7b     # own tmux pane; blocks
./60_confirm_fp32_e2e.sh 13b    # own tmux pane; blocks
```

The script automates the sweep's own confirm plan: builds the agp0 chat
template (prints the suppressed text — EYEBALL it), patches the confirm
catalog, generates an ifeval-only fp32 variant bundle under a fresh suite
name, and launches `eval-audit-run --lease` (self-acquires; placement is
infer-stack's job). Written 2026-07-21, syntax-checked, NOT yet executed on
the GPU host — each step fails loudly and independently. Morning compare:
`eval-audit-compare-pair --run-a <official> --run-b <local>` per
`confirm/confirm_plan.md`.

**Pre-flight findings (resolved 2026-07-21 evening from ranking.txt — do not
re-derive):**
- **ast is a NON-FACTOR for OLMo-2.** ast0/ast1 twins score identically in
  every fp32 cell (7B: 0.915=0.915; 13B: 0.904=0.904), and resolution.json
  shows `tokenizer_appends_special: None` — the special-token append issue
  was OLMo-1 only. The confirm plan's ast ⚠️ is the tool being conservative
  about the winner's label. NO client patch, NO tokenizer override.
- **agp is LOAD-BEARING.** fp32+agp0 = MATCH (0.915/0.904); fp32+agp1 =
  PARTIAL (0.158/0.250). And bf16+agp0 also fails (0.157) — so at n=12 the
  probe already gives the factorial: **precision and template rendering are
  each necessary and only jointly sufficient.** Tonight's e2e scales the
  jointly-sufficient cell (fp32 + agp0) to full n and to the metric level;
  the single-factor cells are already established by the probe.
- Serve-side fix (the chat path renders agp1 server-side): give vLLM an
  agp-stripped template via `--chat-template` in the confirm catalog's
  `extra_args` (steps in the command block below / session transcript).
  Edward eyeballs the template diff — that is the remaining judgment item.
- Confirm baseline runs' dtype from the production catalog (expected: vLLM
  default = checkpoint bf16; catalog pins no dtype — note the irony).

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

Launch: `./65_scan_sweep_candidates.sh` applies the frozen rule and PRINTS
the two `run_deployment_match.sh` commands (GPUs 2-3) — write the per-cell
predictions here first, then paste them.

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

| Cell | Prediction (registered 2026-07-21 pre-launch) | Outcome | Verdict | Notes |
|---|---|---|---|---|
| olmo-2-7b ifeval fp32 e2e | D_fp32 → ~0 (from +0.098) | **DONE (1082 inst, denom == official).** official 0.6929, local fp32+agp0 (vLLM) 0.7597, **D_fp32 = +0.067** → gap closed 32% (0.098→0.067). | **ANOMALOUS / partial** (not EXACT ≤0.01; not MATERIALLY-IMPROVED ≤0.049; not UNCHANGED ≥0.078) | prediction of full recovery REFUTED; fp32 helps but is not sufficient |
| olmo-2-13b ifeval fp32 e2e | D_fp32 → ~0 (from +0.126) | **DONE (1082 inst, denom == official; completed via cache-resume after the 07-22 contention failure).** official 0.7298, local fp32+agp0 (vLLM) 0.8121, **D_fp32 = +0.082** → gap closed 35% (0.126→0.082). | **ANOMALOUS / partial** (not EXACT; not MI ≤0.063; not UNCHANGED ≥0.101) | same as 7B; consistent ~1/3 closure across both sizes |

**Reading (registered prediction refuted, honestly).** fp32+agp0 is directionally
right — it closes ~1/3 of the bf16 gap on BOTH models — but a systematic
+0.067/+0.082 residual remains, with the local scoring ABOVE official. So
precision + template rendering are NECESSARY but NOT SUFFICIENT to recover the
OLMo-2 instruct ifeval score. Leading hypothesis: the residual is the **engine
gap** — the officials are HELM HuggingFaceClient (`transformers.generate()`) at
fp32, our e2e local is **vLLM** at fp32; the 07-10 "residual puzzle" already
showed same-fp32 forward passes diverge across engine / attention-impl / device.
The consistent ~1/3 closure and same-direction (local higher) argue for a
systematic execution difference, not noise. **Decisive next test: the
HF-in-process fp32 path** (same engine as the official — `hf_inprocess.py` +
reserve-only lease exists, but the replay routing switch was left unwired;
07-10 journal). Alternatives to rule out: unrepresentative n=12 probe; HELM's
`do_sample=True, temperature=1e-7` vs vLLM true-greedy.
| sweep: marin-8b-instruct / ifeval | official = huggingface/marin-8b-instruct, HuggingFaceClient, NO dtype pinned (device_map:auto only; verified against HELM model_deployments.yaml 2026-07-21) ⇒ **fp32 cell wins**; agp behavior is an open sub-question for a llama-family template | 07-21 night: **INVALID TEST, not a refutation** — protocol resolver could not detect chat markers in the marin official and silently defaulted to raw completions (`protocol_resolved: False` in resolution.json); all 32 cells probed wrong-shaped prompts (best: auto PARTIAL 0.158, quasi 0.0; snippets show on-topic-but-divergent text and the official refusing where locals answer — chat-template behavior the raw probe cannot express). Sweep completed cleanly otherwise; dtype never got a fair test. RERUN with `DM_PROTOCOL=chat` into a fresh `--ifeval-chat-vllm` out dir. | pending rerun (chat) | prediction unchanged; DM_PROTOCOL knob added to run_deployment_match.sh 07-22 |
| sweep: phi-3-small-8k / med_qa — **DEFERRED to 2026-07-22**, typed reason `infeasible:trust-remote-code-not-swept` (grid.py pins trust_remote_code:[False]; phi-3-small requires it — ~10-line tool patch first) | official pins `torch_dtype: auto` ⇒ transformers reads the checkpoint dtype (bf16) ⇒ **bf16 wins, fp32 LOSES** — the control cell, prediction registered before any execution | | | do NOT run tonight |

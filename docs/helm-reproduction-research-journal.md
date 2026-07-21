# HELM Reproduction Research Journal

Date started: 2026-03-27
Project: `/home/joncrall/code/aiq-magnet`
Scope: reproduce historic public HELM results with local `kwdagger` pipelines, explain observed drift, and build reporting that supports both management summaries and maintainer-grade technical diagnosis.

## Research Goal

Determine whether current locally reproduced HELM runs meaningfully differ from official public HELM results, and if so:

- quantify how different they are
- separate ordinary rerun noise from structural drift
- identify likely causes such as deployment/provider changes, metric-spec changes, and execution-spec changes

## Data Sources

- Public historic bundle:
  - `/data/crfm-helm-public`
- Local audit runs:
  - `/data/crfm-helm-audit`
- Audit experiment workflow:
  - `dev/experiments/audit-helm-reproduction`

## Workflow Built During This Research

- Created a reusable audit experiment folder under:
  - `dev/experiments/audit-helm-reproduction`
- Added shell-first operator scripts for:
  - environment validation
  - manifest generation
  - scheduling runs with `kwdagger`
  - comparing completed batches to public HELM runs
  - direct pairwise comparison of two concrete run directories
- Added pairwise reporting support that writes:
  - JSON reports
  - text summaries
  - tolerance sweeps

## Current Direction Shift

We likely do not need more same-hardware repeat runs right now.

- The current evidence is already strong that repeated `kwdagger` runs on the
  same machine are very stable relative to the official-vs-local gap.
- The more interesting next source of variation is now cross-hardware /
  cross-machine drift.
- That means future repeatability work should prioritize:
  - different GPU architectures
  - different VRAM tiers
  - possibly different host environments

This should reduce time spent waiting on redundant same-machine reruns while
improving the causal story for a paper-quality reproducibility analysis.

## Important Audit / kwdagger Notes

- `kwdagger` is the right execution mechanism for these experiments, but it has a few behaviors worth remembering:
  - unset optional params may still be rendered into generated CLI commands
  - list-valued params are best passed as YAML strings, not `nargs='*'`
  - tmux queue name collisions can cause interactive prompts unless queue names are experiment-specific
- Pairwise and entry-based comparison tooling now validates finalized HELM run artifacts before attempting diffs.
  - This avoids misleading Python tracebacks when a job exists but its run directory is incomplete.

See also:
- `dev/codex/kwdagger-notes.md`
- `dev/codex/reproduce-helm-session-v2.md`

## Provenance / Multi-Machine Goal

Current status before this note:

- historical audit results mostly recorded logical run config
- they did **not** explicitly record machine / GPU provenance in a structured,
  analysis-friendly way

This is a problem if we want to:

- merge runs produced on different machines
- compare same-config runs across hardware
- avoid blocking on one machine just to obtain intermediate analyses

Implementation direction:

- record lightweight process provenance per materialized HELM job
- use `kwutil.ProcessContext` to capture:
  - host
  - user
  - cwd
  - OS / Python info
  - memory / CPU metadata when available
- augment that with best-effort `nvidia-smi` GPU details when available

Desired downstream behavior:

- pairwise / aggregate analysis should work against whatever runs exist
- missing runs should reduce coverage, not block intermediate analysis
- when cross-machine drift appears, we should be able to attribute it to a
  known machine / GPU context rather than reconstructing provenance manually

Open goal:

- teach the audit reporting layer to group / filter by machine provenance once
  enough multi-machine runs exist

## Deployment / Registry Findings

- Some target models already have built-in HELM Hugging Face deployments:
  - `eleutherai/pythia-6.9b`
  - `lmsys/vicuna-7b-v1.3`
  - `aisingapore/llama3-8b-cpt-sea-lionv2.1-instruct`
- Some newer or different families appear Together-backed in the built-in registry:
  - example: `meta/llama-3-8b-chat`
- HELM’s experimental Hugging Face registration flags are not reliable enough by themselves for this workflow.
  - robust reproduction work should prefer explicit deployment control, e.g. via custom `model_deployments.yaml`

## Apples-To-Apples Finding

The original smoke batch was useful as a workflow check, but not apples-to-apples because public matches were often at `max_eval_instances=1000` while the reproduced smoke runs used `100`.

We then ran an apples-to-apples control batch aligned to `max_eval_instances=1000`.

Key result:

- the eval-size mismatch confounder was removed
- the remaining observed drift still persisted

That means the disagreement is not explained solely by comparing `100` local examples to `1000` historic examples.

## Representative Structural Drift

For apples-to-apples cases, the main remaining execution-path difference was:

- `adapter_spec.model_deployment`

Representative examples showed:

- historic public runs often record:
  - `adapter_spec.model_deployment = null`
- local reproduced runs often record:
  - `adapter_spec.model_deployment = huggingface/...`

Metric spec drift was also observed:

- historic often uses:
  - `helm.benchmark.metrics.basic_metrics.BasicMetric`
- local reproduced runs often use:
  - `BasicGenerationMetric`
  - `BasicReferenceMetric`
  - `InstancesPerSplitMetric`

This is important because it means the disagreement is not just at the output level. The effective evaluation configuration is also changing.

## Pairwise Comparison Baseline

To separate structural drift from normal stochastic variation, repeated local runs were compared directly.

Case studied in detail:

- `boolq:model=eleutherai/pythia-6.9b,data_augmentation=canonical`

Compared:

- local repeat 1:
  - `/data/crfm-helm-audit/audit-boolq-pythia-r1/helm/helm_id_13jkx9mm4k4n/benchmark_output/runs/audit-boolq-pythia-r1/boolq:model=eleutherai_pythia-6.9b,data_augmentation=canonical`
- local repeat 2:
  - `/data/crfm-helm-audit/audit-boolq-pythia-r2/helm/helm_id_12jr5w48kge7/benchmark_output/runs/audit-boolq-pythia-r2/boolq:model=eleutherai_pythia-6.9b,data_augmentation=canonical`
- official public HELM:
  - `/data/crfm-helm-public/classic/benchmark_output/runs/v0.3.0/boolq:model=eleutherai_pythia-6.9b,data_augmentation=canonical`

### Local Repeatability Result

`r1` vs `r2`:

- diagnosis:
  - `bookkeeping_metric_drift`
- strict run-level agree ratio:
  - `0.9552238805970149`
- strict instance-level agree ratio:
  - `0.9523809523809523`
- run-level max abs delta:
  - `0.0015118565559387176`
- instance-level max abs delta:
  - `0.4418189525604248`

Interpretation:

- local reruns are very close
- residual differences are mostly bookkeeping/runtime style noise, not large task-quality drift

### Official vs Local Result

Official `v0.3.0` vs local `r1`:

- diagnosis:
  - `multiple_primary_reasons`
- primary reason names:
  - `deployment_drift`
  - `execution_spec_drift`
- strict run-level agree ratio:
  - `0.4626865671641791`
- strict instance-level agree ratio:
  - `0.6577333333333333`
- run-level abs p90:
  - `4.0`
- run-level abs max:
  - `11.985`
- instance-level abs p90:
  - `4.0`
- instance-level abs max:
  - `75.73884344100952`

Interpretation:

- official-vs-local drift is much larger than local-vs-local drift
- that makes it very unlikely that ordinary nondeterminism is the main explanation

## High-Level Verdict

For the tested BoolQ/Pythia apples-to-apples case, we now have enough information to say:

- the current locally reproduced HELM result is significantly different from the official public HELM result
- the difference is much larger than the observed local repeatability noise
- the most likely explanation is structural/configurational drift rather than ordinary stochastic rerun variance

Important limitation:

- this claim is solid for the tested case
- it should not automatically be generalized to all HELM benchmarks or model families without more cases

## Tolerance Sweep Findings

The pairwise tool supports tolerance sweeps across several preset thresholds.

Current presets:

- `strict`: `abs_tol=0.0`, `rel_tol=0.0`
- `tiny`: `abs_tol=1e-12`, `rel_tol=1e-6`
- `small`: `abs_tol=1e-9`, `rel_tol=1e-4`
- `medium`: `abs_tol=1e-6`, `rel_tol=1e-3`
- `loose`: `abs_tol=1e-3`, `rel_tol=1e-2`
- `xloose`: `abs_tol=1e-2`, `rel_tol=1e-1`
- `xxloose`: `abs_tol=1e-1`, `rel_tol=1.0`
- `extreme`: `abs_tol=1.0`, `rel_tol=10.0`

For local BoolQ/Pythia repeats:

- similarity becomes nearly perfect by `loose` / `xloose`

For official vs local BoolQ/Pythia:

- similarity stays much lower through `loose` and `xloose`
- it only collapses to `1.0` at the very permissive `xxloose` threshold

Interpretation:

- official-vs-local mismatch is robust to modest tolerance relaxation
- forcing them to look identical requires very permissive tolerances

## Important Metric-Scale Caveat

A single global absolute tolerance is not easy to interpret because the comparison spans different metric families with very different numeric scales.

Observed for official vs local BoolQ/Pythia:

- `core` metrics are approximately `[0, 1]`
  - examples:
    - `exact_match`
    - `prefix_exact_match`
    - `quasi_exact_match`
  - run-level max abs delta in this class was only about:
    - `0.021`
- `bookkeeping` metrics are not bounded to `[0, 1]`
  - examples:
    - `num_bytes`
    - `num_output_tokens`
    - `logprob`
  - observed values included:
    - `num_bytes`: `15.432` vs `3.448`
    - per-instance `num_bytes`: `16.0` vs `3.0`
    - per-instance `num_output_tokens`: `5.0` vs `1.0`
    - per-instance `logprob`: values like `-3.64` vs `0.0`

Implication:

- `abs_tol=1.0` is extremely loose for bounded core metrics
- but it is not necessarily huge for bookkeeping metrics such as byte counts or token counts

Therefore:

- global tolerance sweeps are still useful as diagnostics
- but interpretation should move toward:
  - per metric class tolerances
  - and eventually per metric family tolerances

## Current Reporting Files Worth Inspecting

- Session journal:
  - `dev/codex/reproduce-helm-session-v2.md`
- kwdagger notes:
  - `dev/codex/kwdagger-notes.md`
- Pairwise report, local repeat:
  - `reports/pairwise/boolq-pythia-repeat-wide/pair_report_20260327T011202Z.txt`
- Pairwise report, official vs local:
  - `reports/pairwise/boolq-pythia-historic-wide/pair_report_20260327T011202Z.txt`

## Recommended Next Steps

- Add class-specific tolerance reporting:
  - at least separate `core` from `bookkeeping`
- Add effect-size style summaries:
  - compare official-vs-local distance to local-vs-local baseline distance
- Collect more repeat runs if formal significance estimates are desired
  - current data is enough for a strong effect-size-style argument
  - it is not enough for a stable p-value estimate
- Run a minimal Together-backed control on a representative case
  - this should help separate provider/deployment effects from general HELM evolution

## Direct NarrativeQA/Vicuna Debug Run

We ran a focused local debug job outside kwdagger scheduling:

- suite: `debug-narrative-vicuna-direct`
- run entry: `narrative_qa:model=lmsys/vicuna-7b-v1.3,data_augmentation=canonical`
- max_eval_instances: `20`

Main result:

- The direct run reproduces the same failure mode as the larger local NarrativeQA/Vicuna runs.
- This strongly argues against the issue being a kwdagger scheduling/orchestration bug.

Observed from the raw run:

- request count: `100`
- empty completions: `99`
- non-empty completions: `1`
- mean output token count: `0.13`
- token count histogram:
  - `0`: `99`
  - `13`: `1`

Observed from `stats.json`:

- `num_completion_tokens` mean on `test`: `0.0`
- `num_output_tokens` mean on `test`: `0.0`
- `finish_reason_unknown` mean on `test`: `1.0`
- `exact_match`, `quasi_exact_match`, `f1_score`, `rouge_l`: all `0.0`

Relevant log clues:

- HELM automatically set `apply_chat_template=True`
- HELM removed 4 in-context examples to fit the context window
- HELM logged stop/truncation warnings:
  - `truncate_sequence needs to strip "\\n"`
  - `truncate_sequence needs to strip "</s>"`

Current interpretation:

- Most likely this is a local HELM Hugging Face Vicuna execution/configuration issue.
- The strongest current suspect is chat-template application on a non-chat-style NarrativeQA prompt.
- Secondary suspects are newline stop-sequence handling and/or immediate EOS/empty-generation behavior in the HF Vicuna path.

Current follow-up experiment:

- rerun the same benchmark with a custom `model_deployments.yaml` override that sets:
  - `apply_chat_template: false`

## NarrativeQA/Vicuna Root Cause Update

The `apply_chat_template: false` rerun strongly supports a root-cause diagnosis.

Run:

- suite: `debug-narrative-vicuna-nochat`
- same benchmark/model family as the failing debug run
- same local Hugging Face deployment name
- overridden deployment config:
  - `client_spec.args.apply_chat_template: false`

Observed:

- request count: `500`
- empty completions: `0`
- non-empty completions: `500`
- mean output token count: `12.894`

Aggregate test metrics from the corrected local run:

- `exact_match`: `0.2727`
- `quasi_exact_match`: `0.4026`
- `f1_score`: `0.6422`
- `rouge_l`: `0.6442`
- `bleu_1`: `0.5138`
- `bleu_4`: `0.0722`

These are now close to the official public HELM run for the same benchmark/model pair.

Conclusion:

- The prior NarrativeQA/Vicuna failure was **not** good evidence of irreproducibility.
- It was caused by a local HELM/HuggingFace configuration issue.
- The main culprit appears to be automatic chat-template application for this run.

Implications for the audit:

- For local Hugging Face reproductions, `apply_chat_template` must be treated as an explicit controlled setting.
- Some earlier "failed reproductions" may need to be reinterpreted or rerun if they depended on HELM's automatic chat-template inference.
- The audit/reporting system should surface suspicious signals such as:
  - high empty-completion rate
  - near-zero `num_output_tokens`
  - pervasive `finish_reason_unknown`

## Server Batch Logging Validation

The latest rsynced server-side experiment artifacts confirmed that the new per-job HELM logging capture is working as intended.

Observed in:

- `/data/crfm-helm-audit/audit-vicuna-nochat-server/helm/helm_id_dijo03bfux6g/`
- `/data/crfm-helm-audit/audit-vicuna-nochat-server/helm/helm_id_obr7gu9kxuql/`
- `/data/crfm-helm-audit/audit-vicuna-nochat-server/helm/helm_id_s2ez33ko97jb/`

Each failed job now preserves:

- `helm-run.log`
- `helm-run.debug.log`
- `process_context.json`
- `helm_log_config.yaml`
- `job_config.json`

These logs show that the latest server failures were not new reproducibility failures. All three jobs failed during local Hugging Face model load with the same infrastructure error:

- `torch.OutOfMemoryError`
- attempted allocation: about `172 MiB`
- GPU `0` free memory at failure: about `143.50 MiB`
- competing process `696468` already using about `89.43 GiB`

Interpretation:

- the `audit-vicuna-nochat-server` run failed due to GPU occupancy / infrastructure contention
- this does **not** currently count as evidence for or against HELM reproducibility
- the new logging path is useful and should remain part of all future materialized job outputs

## Cross-Machine Vicuna No-Chat Confirmation

After clearing the server-side GPU occupancy issue, the `audit-vicuna-nochat-server` batch completed successfully and matched the earlier `audit-vicuna-nochat-overnight` batch.

High-level result:

- for the three fixed-config Vicuna runs we tested, the server results were numerically identical to the earlier run on the other machine at the run-level metric summaries we inspected
- this is a meaningful positive signal for cross-machine reproducibility once the chat-template confounder is removed

Benchmarks:

- `boolq:model=lmsys/vicuna-7b-v1.3,data_augmentation=canonical`
- `mmlu:subject=us_foreign_policy,method=multiple_choice_joint,model=lmsys/vicuna-7b-v1.3,data_augmentation=canonical`
- `narrative_qa:model=lmsys/vicuna-7b-v1.3,data_augmentation=canonical`

Observed diagnostics from the successful server run:

- BoolQ:
  - `N = 5000`
  - empty completions: `0`
  - mean output tokens: `1.0064`
- MMLU:
  - `N = 327`
  - empty completions: `0`
  - mean output tokens: `1.0`
- NarrativeQA:
  - `N = 2350`
  - empty completions: `0`
  - mean output tokens: `11.9498`
  - output token quantiles:
    - `p50 = 6`
    - `p90 = 26`
    - `max = 100`

Comparison to official public HELM:

- BoolQ correctness metrics match exactly; the remaining visible drift is shorter output length
- MMLU core metrics match exactly
- NarrativeQA remains very close to official on core metrics, with the main residual difference in answer length rather than answer quality

Interpretation:

- the fixed Vicuna/HuggingFace path appears independently reproducible on the tested core metrics
- at least for these experiments, the newer server run supports a positive cross-machine reproducibility result rather than revealing a hardware-specific failure mode

## Updated Research Goal

The project goal should now be stated more precisely:

- produce a documented recipe for reproducing selected public HELM runs in a repeatable way
- quantify and bound the small remaining differences that are expected from local execution, hardware variation, and nondeterminism
- prefer benchmarks, models, and evaluation paths that are runnable with open weights and realistic "consumer-accessible" hardware constraints

Interpretation:

- this does not necessarily mean laptop-only reproduction
- but it should avoid closed-weight models and giant model families that are not meaningfully checkable by other researchers
- a practical target is open-weight models that can run on a single large consumer GPU or on a modest multi-GPU workstation

Methodologically, this pushes us toward a reproducibility distribution rather than a single point estimate:

- same-machine repeatability
- cross-machine repeatability
- official-vs-local drift
- benchmark-level and collection-level summaries

## Large Grid Status: Runnable Recipe vs Unrunnable Cases

The larger `audit-historic-grid` batch was informative, but many failures do **not** currently count as evidence against reproducibility.

Observed:

- total jobs: `286`
- completed jobs: `141`
- failed or incomplete jobs: `145`

Representative failure buckets:

- dataset loading / access / download issues
  - RAFT requires `trust_remote_code=True`
  - MATH failed to resolve `hendrycks/competition_math`
  - bAbI and Natural Questions failed on direct data downloads via `wget`
- gated or external-service requirements
  - `walledai/XSTest` is gated
  - some annotation-heavy runs require OpenAI credentials
- model-specific packaging issues
  - `aisingapore/sea-lion-7b-instruct` failed while resolving dynamic imports such as `flash_attn_triton.py`
- some broader model families remain insufficiently diagnosed from the raw logs alone and need targeted follow-up

Interpretation:

- the current historic candidate list is too broad to serve directly as a reproducibility benchmark suite
- we need a stricter "runnable recipe" filter:
  - open-weight models
  - locally runnable HF path
  - datasets that are accessible without custom private credentials
  - scenarios that do not require external proprietary annotators
  - configurations that fit the hardware budget we expect others to have

## Namek / Yardrat Subset Check

The small cross-hardware Vicuna subset remains encouraging.

Observed:

- `audit-yardrat-subset`
  - BoolQ: complete
  - MMLU: complete
  - NarrativeQA: complete
- `audit-namek-subset`
  - BoolQ: complete
  - MMLU: complete
  - NarrativeQA: incomplete / still-running at time of inspection

For the completed subset jobs:

- no empty-completion pathology was observed
- Yardrat matched the previously successful Vicuna no-chat behavior:
  - BoolQ core correctness metrics matched official exactly
  - MMLU core metrics matched official exactly
  - NarrativeQA remained very close on core metrics, with residual drift mainly in output length rather than correctness
- Namek BoolQ and MMLU also matched this same pattern

Interpretation:

- the corrected Vicuna recipe appears stable across multiple machines
- this strengthens the case that our main reproducibility result is positive when the configuration is correct and the task is runnable under the local open-weight recipe

---

## Migration note

This document was originally written in the `aiq-magnet` repository and copied into
`helm-reproducibility` during the repository split.

Original context:
- workspace/repo at time of writing: `/home/joncrall/code/aiq-magnet`
- original audit workflow location: `dev/experiments/audit-helm-reproduction`

Current equivalents in this repo:
- repo root: `/home/joncrall/code/helm-reproducibility`
- workflow roots: `configs/`, `reproduce/`, `eval_audit/`, `reports/`

Unless explicitly stated otherwise, historical paths and commands above should be
interpreted as pre-split references.

## 2026-04-01 Status Update After Repository Split

The HELM reproducibility / audit workflow has now been split out of
`aiq-magnet` into its own repository:

- local path: `/home/joncrall/code/helm-reproducibility`
- GitHub: `AIQ-Kitware/helm-reproducibility`

This new repository is now the primary home for:

- configs
- shell scripts
- the packaged Python analysis code
- reports
- historical notes and legacy experiment artifacts

`aiq-magnet` should now keep only the reusable upstream-worthy MAGNeT / HELM
infrastructure changes, not the experiment-specific audit workflow.

### Scientific Baseline At Time Of Split

The project should be framed as asking:

- to what extent are public HELM results independently reproducible under an explicit open-weight local recipe?
- how should residual differences be interpreted?
- what subset is fairly runnable and reproducible under the current recipe?

The project should **not** be framed as asking:

- can we reproduce every HELM run exactly?

### Current Positive Result

The corrected Vicuna no-chat recipe remains the strongest positive result.

Current interpretation:

- same-machine repeatability is good
- cross-machine repeatability is good
- official-vs-local agreement is good on the key validated subset

This remains the main scientific anchor for the project.

### Current Known Root Cause

An earlier major false alarm was caused by the local HuggingFace Vicuna path
incorrectly auto-applying a chat template.

The fix is the local no-chat configuration using:

- `apply_chat_template: false`

This was especially important for:

- `narrative_qa`

### Interpretation Rule To Preserve

Do **not** treat every failing large-grid job as evidence that "HELM is irreproducible".

Many failures belong in scope / recipe / environment buckets instead:

- gated datasets
- missing credentials
- dataset download failures
- model packaging or runtime issues
- resource / OOM / occupancy failures
- scenarios outside the runnable open local recipe

These should be classified carefully rather than folded into a blanket negative result.

### State Of The New Repository

At the time of this update, `helm-reproducibility` already contains:

- packaged analysis code under `eval_audit/`
- `reproduce/`, `configs/`, `reports/`, and docs
- imported legacy report artifacts
- verified end-to-end regeneration of at least one representative core report from:
  - `/data/crfm-helm-audit`
  - `/data/crfm-helm-public`

One practical caveat:

- `reproduce/machine_compare/10_analyze_experiment.sh` is the preferred runbook step
- for indexing, the safer entrypoint may be the module directly:
  - `python -m eval_audit.workflows.index_results`

### Immediate Working Plan

The next planned sequence is:

1. fix the one failing test in `aiq-magnet` for the upstream-worthy subset of changes
2. switch focus back to `helm-reproducibility`
3. inspect failing HELM runs on `aiq-gpu` and classify them properly

### Repo Boundary Reminder

As work continues, the intended split is:

- `helm-reproducibility`:
  experiment logic, configs, analysis, reporting, documentation, and paper-oriented workflow
- `aiq-magnet`:
  reusable MAGNeT / HELM infrastructure that should be upstreamed or maintained as general-purpose support

This boundary should be preserved in future edits.

## Contamination caveat for the open-judge experiment (raised 2026-07-20)

Every open-judge finding must be reported with a training-data contamination
caveat. The judges are **newer than the benchmarks and newer than the public
HELM judgments they are being compared against**, so "the open judge agrees
with the closed judge" has a memorization-shaped alternative explanation.

### Established timeline

| item | date | confidence |
|---|---|---|
| AnthropicRedTeam dataset | 2022 | public long before |
| XSTest, SimpleSafetyTests | 2023 | public long before |
| HarmBench, WildBench, Omni-MATH | 2024 | public long before |
| `openai/gpt-oss-20b` (the candidate) | Aug 2025 | known |
| **Qwen3.5 family launch** | **2026-02-16** | verified (public reporting) |
| **Qwen3.5 0.8B–9B smalls** | **~2026-03-02** | verified (public reporting) |
| Qwen3.6-35B-A3B | ~2026-06 | unverified estimate |
| HELM Safety v1.14.0 / Capabilities v1.12.0 publication | **UNKNOWN** | bounded to Aug 2025 – mid 2026 |

The unknown row is the one that matters and it could not be pinned from the
corpus (the run dirs carry no execution timestamp) or from public sources in a
reasonable search. It should be established before publication — the HELM
leaderboard release history is the place to look.

### Three contamination channels, in increasing severity

1. **Benchmark prompts/labels** — near-certain. Every dataset predates the
   judges by 2–4 years. A judge may recall that a given XSTest prompt is
   safe/unsafe rather than assessing it. This inflates *absolute* judge quality
   but does not by itself explain agreement with a *particular* closed judge.
2. **Official judge outputs** — the dangerous one. HELM publishes the actual
   `gpt_score` / `llama_score` values AND the judges' reasoning text for each
   instance. If the gpt-oss-20b run was published before Qwen3.5's data cutoff
   and scraped, then "open judge agrees with GPT-4o at kappa 0.94" could be
   partly reproduction of memorized verdicts. This directly threatens the
   headline finding. NOTE the window is tight but real: the candidate is Aug
   2025 and the judge launched Feb 2026, so HELM would have had to publish
   within roughly Aug–Dec 2025 and be scraped.
3. **Candidate responses** — least concerning; the responses are gpt-oss-20b's
   and are not themselves the object of comparison.

### The mechanism is DISTRIBUTION SHIFT, not (only) memorization

Sharpened 2026-07-20 (Jon): the concern is not primarily verbatim recall. A
judge trained on this data will plausibly be **better calibrated on this
distribution than it would be out of distribution**, with no memorized string
anywhere. That is both more likely than recitation and much harder to rule out —
and note that the counter-evidence below argues against MEMORIZATION while
leaving distributional advantage untouched.

The practical consequence: our numbers describe judge/benchmark pairs the judge
was very likely trained on. They should NOT be read as an estimate of how an
open judge performs on a novel benchmark, a novel candidate, or a private
eval — which is the use case anyone reading this paper actually cares about.
This is a caveat to state plainly, not a result-killer.

### Empirical evidence against wholesale MEMORIZATION (suggestive; does not address distribution shift)

Our own results contain three patterns that memorization does not predict:

- **The 2B arm is badly miscalibrated.** Same family, same training corpus,
  same benchmarks — yet Qwen3.5-2B calls 740/1000 anthropic_red_team responses
  unsafe where the official ensemble says 989/1000 safe (25.7% agreement at
  99.9% parse). A model reproducing memorized labels would not invert them.
- **Agreement is size-graded** (93.7% → 96.8% → ~98–99% on XSTest), which looks
  like an inference-time capability curve. Memorization would be expected to be
  flatter or more step-like across a family sharing a training corpus.
- **WildBench shows a systematic ~0.8-point offset** below the official
  ensemble rather than convergence on it. A memorizing judge would reproduce
  the official numbers, not sit consistently below them.

### Tests that would actually bound this

Ordered by decisiveness per unit of effort:

1. **Swap the candidate model** to one released after the judge's cutoff. The
   (prompt, response, judgment) triple then cannot have been memorized even
   though the prompts were public. Our pipeline is already parameterized by
   candidate — this is a config change, not new code, and is the cheapest
   strong test available.
2. **Perturbation test** — paraphrase candidate responses so memorized surface
   forms no longer match, and measure agreement degradation. HELM already has
   perturbation machinery.
3. **Direct memorization probe** — ask a judge to recall the official verdict
   for an instance *without* showing it. Success is direct evidence of channel 2.
4. **Post-cutoff benchmark** — judge a benchmark published after the judge's
   release. Cleanest, but requires a benchmark with official closed-judge
   annotations, which is the scarce ingredient.

Until at least (1) is run, every agreement number in this experiment should be
reported as an **upper bound** on true independent agreement.

## The fp32/agp0 substrate-recovery experiment (launched 2026-07-21 overnight)

Paper-facing record of what was established today and what went on trial
tonight. Session narrative in `dev/journals/claude.md` (2026-07-21 entries);
frozen protocol in
`reproduce/olmo_models_combined/deployment_match/overnight_confirm_plan.md`.

### Three propositions, kept distinct

- **A. Unpinned dtype ⇒ historical fp32 execution.** Mechanism verified in
  the transformers 4.x code path (no `torch_dtype` kwarg → fp32 BC default,
  ignoring the checkpoint config); 129/148 HuggingFaceClient deployments in
  HELM's `model_deployments.yaml` are unpinned. Empirically confirmed on one
  family (OLMo) before tonight.
- **B. Reproducing the recovered substrate recovers the completions.**
  Probe-level only before tonight (n=12 ifeval): OLMoE exact 12/12 (HF fp32
  eager); dense OLMo-2 10/12, 10/12, 11/12 (vLLM fp32 FLASH_ATTN, batch-1).
- **C. The recovered config changes aggregate metrics / conclusions.**
  Untested before tonight. Targets: local bf16/modern-template runs sit
  ABOVE the officials on ifeval_strict_accuracy by +0.098 (7B: 0.791 vs
  0.693) and +0.126 (13B: 0.856 vs 0.730).

### The probe factorial (n=12, already established — a paper figure)

From the 2026-07-10 sweep rankings, OLMo-2-7B ifeval composites:
fp32+agp0 = 0.915 MATCH; fp32+agp1 = 0.158; bf16+agp0 = 0.157 (13B:
0.904 / 0.250 / ~0.16). **Precision (fp32) and chat-template rendering
(no generation prompt, the old-transformers behavior) are each necessary
and only jointly sufficient** to reproduce the official completions.
`add_special_tokens` is a non-factor for OLMo-2 (ast0/ast1 identical in
every cell; the OLMo-1 `<|endoftext|>`-append issue does not apply).

### Conclusion-level finding from EXISTING data (zero new compute)

Pairwise-ordering flip analysis over `aggregate_score_diff_headline.json`:
**4/25 OLMo pairwise orderings invert between official and local** (gpqa
3/6 pairs, incl. 13B↔32B; bbq 1/6), while ifeval — the LARGEST aggregate
drift — flips none, because its +0.10 shift is nearly uniform across
models. Qwen experiment: 1/201 flips. Two implications for the paper:
(1) procedural drift can flip published model comparisons; (2) drift
magnitude does not predict conclusion damage. Caveat: gpqa official gaps
are ≈1.3σ at n≈450 — a paired bootstrap over instances must decide which
flips are statistically supported before any of them is claimed.

### Tonight's runs and REGISTERED predictions (frozen before execution)

1. **E2E confirm, OLMo-2-1124-7B + 13B, ifeval, full n** — the sweeps'
   fp32 endpoints + an agp0 chat template served via vLLM
   `--chat-template`, replayed through the normal HELM from-spec path
   under fresh suite names (`audit-allenai-olmo-2-1124-{7b,13b}-instruct-ifeval-fp32`).
   Prediction: D_fp32 → ~0 (published aggregates recovered). Outcome
   classes frozen in the plan file (EXACT-RECOVERED / MATERIALLY-IMPROVED /
   UNCHANGED / ANOMALOUS). Note the design moves dtype AND template
   together deliberately — the probe factorial already established each
   factor alone fails; tonight scales the jointly-sufficient cell.
2. **Cross-family treatment cell: marin-community/marin-8b-instruct,
   ifeval, deployment-match sweep.** Official is
   `huggingface/marin-8b-instruct` (HuggingFaceClient, `device_map: auto`
   only, NO dtype — verified against HELM main-branch
   model_deployments.yaml; era-tag check pending). Prediction: **the fp32
   cell wins the ranking.** Same benchmark + suite era (v1.8.0
   capabilities) as the OLMo cells — family is the only major moved factor.
3. **Cross-family control cell: microsoft/phi-3-small-8k-instruct, med_qa
   — DEFERRED (typed: `infeasible:trust-remote-code-not-swept`;
   deployment-match grid pins trust_remote_code=[False] and phi-3-small
   requires it).** Prediction registered NOW, before any execution:
   official pins `torch_dtype: auto` ⇒ checkpoint bf16 ⇒ **bf16 wins and
   fp32 LOSES.** A confirmed control would show the sweep tracks the
   RECORDED substrate when one exists, both directions of the mechanism.

### Corpus facts from the frozen selection scan (2026-07-21, aiq-gpu)

Eligible ≤13B non-OLMo models with a HuggingFaceClient official AND
generative-completion runs are RARE: phi-3-small-8k-instruct (84 runs) and
marin-8b-instruct (3 runs) were the only two. gemma-2-9b/27b-it officials
are TogetherClient (correctly outside the fp32 story — irrecoverable-
substrate category); qwen2.5-7b-instruct and phi-3.5-mini eligible runs
are MedHELM scenarios only (many gated); gpt2 is mmlu_clinical_afr /
winogrande_afr. Consequence: the cross-family test of proposition A is
evidence-limited by corpus composition — worth stating in the paper.

### Interpretation rules for tomorrow

E2E EXACT/IMPROVED ⇒ flagship claim at full scale: published results are
recoverable once the unrecorded execution context is recovered. UNCHANGED
⇒ the n=12 probe does not predict full-run aggregates — itself a finding;
enumerate residual factors while Edward is available. Marin fp32 win ⇒
proposition A generalizes beyond OLMo; loss ⇒ scope A to "confirmed for
OLMo, open elsewhere" and say so.

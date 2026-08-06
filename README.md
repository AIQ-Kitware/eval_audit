# eval_audit

`eval_audit` is the workflow around HELM benchmark *audit* runs: indexing
the public HELM corpus, running local reproductions, comparing local vs.
public results at instance and metric level, and writing publication-quality
report bundles.

Both halves are active. **Analysis** — composing virtual-experiment slices over
already-existing audit runs and producing reproducibility reports — ran
throughout 2026 Q1–Q3. **Execution** ran most recently **2026-07-13 … 07-15**,
producing the OLMo, Qwen, GPT-OSS, and RedPajama audit results the paper draft
reports, through the from-spec / era-pinned path:
`export-benchmark-bundle` → `eval-audit-run` → `kwdagger` → a digest-pinned
container → `helm-run` against a leased vLLM endpoint. Containerization is
**mandatory** as of that work; see
[`docs/container-execution.md`](docs/container-execution.md).

What has *not* been re-validated is the **older** execution shape:
`eval-audit-make-manifest` for manifest building (superseded by
`export-benchmark-bundle`) and the KubeAI / LiteLLM serving variants. Those
remain marked **UNSURE** below.

Late-April / May 2026 work added the **EEE-only reproducibility heatmap**
(paper Case Study 3) at
[`reproduce/eee_only_reproducibility_heatmap/`](reproduce/eee_only_reproducibility_heatmap/),
the Falcon-7B / Qwen-2.5 / gpt-oss / LLaMA-2-70B grid extensions
([`extend_grid_falcon_7b/`](reproduce/extend_grid_falcon_7b/),
[`finish_qwen25_gptoss/`](reproduce/finish_qwen25_gptoss/),
[`llama2_70b_helm_audit/`](reproduce/llama2_70b_helm_audit/)),
and the cross-harness comparability stress in
[`inspectai_helm_eee_compare/`](reproduce/inspectai_helm_eee_compare/).
The paper sources moved from `dev/paper/` to
[`docs/papers/`](docs/papers/) on 2026-05-02.

> If you only want the active path, jump to [Analysis runbooks](#analysis-runbooks-actively-maintained).

## What lives where

```
eval_audit/                 the Python package (renamed from helm_audit on 2026-04-28)
├── cli/                    argparse entrypoints — most CLIs are thin wrappers
├── workflows/              end-to-end sequencing (analyze, index, build summary, …)
├── reports/                pair report, core metrics, aggregate summary, paper labels
├── virtual/                virtual-experiment composer (recent, actively maintained)
├── normalized/             normalized comparison layer (EEE-aware)
├── planning/               comparison-intent planner used by core metrics
├── manifests/              manifest builders / presets  [legacy; superseded by
│                           integrations/infer_stack's export-benchmark-bundle]
├── helm/                   HELM-specific diff helpers (analysis.py, diff.py,
│                           analysis_report.py, instance_stats.py)
├── judging/                open-judge extension: judge audit, replay, rejudge
├── packaging/              transfer packaging (crawl + pack analyses for hand-off)
├── indexing/               run-spec hash + schema helpers
├── infra/                  paths, env, yaml IO, logging, plotly env
├── integrations/           kwdagger_bridge.py (scheduling + container pinning)
│                           + infer_stack/ (bundle export, leases)  [active]
├── utils/                  sankey renderer, hashers, misc helpers
├── eras.py                 era registry: pre-v0.5 HELM images, one per era
├── pipelines/              kwdagger pipeline factories (modern / era docker)
├── compat/                 backward-compat shims
├── run_entries.py          run-entry parsing + canonical logical keys
├── judge_registry.py       curated annotator-class → judge-model map
├── metrics_taxonomy.py     framework-free metric classification
├── hf_inprocess.py         HF in-process runner (parked; see its plan doc)
└── model_registry.py
```

External directories the workflow depends on:

- `reproduce/` — runbooks; one folder per scenario, in three generations. The
  **current** ones are numbered `00_check_env` → `40_build_summary` and run
  from-spec through a pinned container
  ([`olmo_models_combined`](reproduce/olmo_models_combined/),
  [`qwen_models_combined`](reproduce/qwen_models_combined/),
  [`gpt_oss_20b_from_spec`](reproduce/gpt_oss_20b_from_spec/),
  [`classic_together_combined`](reproduce/classic_together_combined/) for eras).
  The **analysis-only** ones
  ([`pythia_mmlu_stress`](reproduce/pythia_mmlu_stress/),
  [`open_helm_models_reproducibility`](reproduce/open_helm_models_reproducibility/))
  compose + summarize existing results. The **legacy** ones
  (`10_make_manifest` / `20_run` shape) are **UNSURE** as of 2026-04 and can no
  longer run unchanged, since containerization is now required.
- `configs/` — checked-in manifests and overrides only; generated state lives
  outside the repo.
- `docs/` — supporting docs. Several are **STALE** and need triage; see
  [Documentation status](#documentation-status) below.
- The **publication surface** — a single folder named `reports/` with
  `filtering/`, `core-run-analysis/`, and `aggregate-summary/` subdirs
  (ADR 3). It is *not* checked into the repo: `publication_root()`
  defaults to `$AUDIT_STORE_ROOT/reports/`, so these artifacts land under
  the audit store at runtime. Override `HELM_AUDIT_PUBLICATION_ROOT` to
  relocate it (e.g. to `<repo>/reports` for the legacy in-repo layout).

The big mutable working tree is on the data store, not in the repo:

```
$AUDIT_STORE_ROOT  (default: /data/crfm-helm-audit-store)
├── configs/                    generated run_specs.yaml, manifests/, run_details.yaml
├── indexes/                    audit_results_index_*.csv|jsonl|txt + official index
├── eee/local/<exp>/<run>/      EEE-converted local audit artifacts
├── crfm-helm-public-eee-test/  EEE-converted public HELM corpus (stress sweep)
├── analysis/                   per-experiment analysis (core-reports, eee-readiness, …)
├── virtual-experiments/<exp>/  virtual-experiment composition outputs
└── local-bundles/              per-bundle deployment YAMLs / process_context

$AUDIT_RESULTS_ROOT  (default: /data/crfm-helm-audit)
└── <experiment>/helm/helm_id_<hash>/...   raw local HELM run outputs
```

## Analysis runbooks (actively maintained)

These are what the 2026 Q1–Q2 commits exercise. They consume already-existing
audit runs and produce reproducibility reports. **No model is run; no
benchmark is downloaded.**

```bash
# Pythia × MMLU slice — 5 subjects, 5 packets, 4,536 instances
./reproduce/pythia_mmlu_stress/compose.sh
./reproduce/pythia_mmlu_stress/build_summary.sh

# Wider open-weight × benchmark slice — 121 packets, 431,605 instances
./reproduce/open_helm_models_reproducibility/compose.sh
./reproduce/open_helm_models_reproducibility/build_summary.sh
```

Each runbook is a thin wrapper over `eval-audit-build-virtual-experiment`
and `eval-audit-build-summary`, working from a checked-in YAML manifest at
`configs/virtual-experiments/<name>.yaml`. Outputs land at
`$AUDIT_STORE_ROOT/virtual-experiments/<name>/`.

The corresponding reproducibility narratives are in
[`reproduce/pythia_mmlu_stress/REPRODUCIBILITY_REPORT.md`](reproduce/pythia_mmlu_stress/REPRODUCIBILITY_REPORT.md)
and
[`reproduce/open_helm_models_reproducibility/REPRODUCIBILITY_REPORT.md`](reproduce/open_helm_models_reproducibility/REPRODUCIBILITY_REPORT.md).

The HELM-specific gotchas surfaced while building the comparison pipeline are
catalogued in [`docs/helm-gotchas.md`](docs/helm-gotchas.md) — that file is
current.

## Execution runbooks

These are the original framing of the project: schedule a local HELM run via
`kwdagger`, point HELM at a model deployment, then compare.

The **current** shape replays each official `run_spec.json` verbatim inside a
digest-pinned container, with model inference out-of-process on a leased vLLM
endpoint. It last ran **2026-07-13 … 07-15** across four runbooks, producing
every audit result the paper draft reports. `export-benchmark-bundle` writes the
manifest (there is no `make-manifest` step), and `eval-audit-run --lease --run=1
--container-image <ref>` schedules it. See
[`docs/pipeline.md`](docs/pipeline.md#execution-from-spec-replay) for the flow and
the shared step ladder, and [`docs/container-execution.md`](docs/container-execution.md)
for the pinning and era mechanics.

Earlier, on **2026-04-28**, the [`pythia12b_mmlu_smoke`](reproduce/pythia12b_mmlu_smoke/)
runbook validated the pre-container chain on aiq-gpu — pythia-12b-v0 × MMLU
abstract_algebra, 1000 instances, HELM `huggingface/*` HuggingFaceClient
deployment — reproducing the public HELM v0.2.4/v0.3.0 reference *exactly*
(1.000 agreement, max |Δ| = 0.0 across all 8 metrics). That runbook predates the
container requirement and would need a pinned image to run today.

The remaining runbooks bring in serving stacks (KubeAI, LiteLLM) and
scenario-specific assumptions (server URLs, deployment YAML, namespace setup)
that **have not been re-validated**. Pick one, run it, and update its README
before claiming it's still good.

| runbook | what it claims to do | status |
|---|---|---|
| `reproduce/olmo_models_combined/` | from-spec replay of six OLMo models as one multi-deployment fan-out | **WORKING** (2026-07, execution) |
| `reproduce/qwen_models_combined/` | from-spec replay of the Qwen family; source of the 703-comparison base rate | **WORKING** (2026-07-15, execution) |
| `reproduce/gpt_oss_20b_from_spec/` | from-spec replay of the 4 ungated-judge public gpt-oss-20b rows (bbq, ifeval, mmlu_pro, gpqa) | **WORKING** (2026-07-13, execution; store unhashed) |
| `reproduce/classic_together_combined/` | era-pinned replay of gpt-j / gpt-neox / opt across `v0.2.4` + `v0.3.0` | **WORKING** (2026-07-13, execution; RedPajama arm complete) |
| `reproduce/open_judge_gpt_oss/` | rejudge gpt-oss-20b XSTest/WildBench with open-weight judges | **WORKING** (2026-07-17 through the identity-replay gate) |
| `reproduce/gpt_oss_20b_core_grid/` | gpt-oss-20b core-benchmark grid | **UNSURE** (superseded by `_from_spec`) |
| `reproduce/qwen35_small_vllm/` | small-model vLLM smoke for the qwen3.5 line | **UNSURE** (vLLM-side) |
| `reproduce/pythia12b_mmlu_smoke/` | pythia-12b-v0 × abstract_algebra via HF transformers + kwdagger | **WORKING** (2026-04-28); pre-container, needs a pinned image today |
| `reproduce/pythia_mmlu_stress/` | analysis-only pythia × MMLU slice | **WORKING** (analysis) |
| `reproduce/open_helm_models_reproducibility/` | analysis-only open-weight × benchmark slice | **WORKING** (analysis) |
| `reproduce/eee_only_demo/` | tutorial: pure-EEE comparison via `eval-audit-from-eee` against checked-in 3×3 fixture | **WORKING** (2026-04-29) |
| `reproduce/eee_only_reproducibility_heatmap/` | EEE-only model × benchmark agreement heatmap (paper Case Study 3) | **WORKING** (2026-05) |
| `reproduce/pythia_smoke_eee_only/` | EEE-only counterpart to `pythia12b_mmlu_smoke/` (no execution; pythia-6.9b on MMLU/BoolQ) | **WORKING** (2026-05) |
| `reproduce/inspectai_helm_eee_compare/` | EEE-only comparability stress: HELM-shaped + InspectAI-shaped artifacts in one bundle | **WORKING** (2026-05) |
| `reproduce/extend_grid_falcon_7b/` | local Falcon-7B reproduction extending the heatmap grid | **WORKING** (2026-05, execution side) |
| `reproduce/finish_qwen25_gptoss/` | close the Qwen 2.5 + gpt-oss audit gaps surfaced by Case Study 3 | **WORKING** (2026-05, with documented gated-dataset caveats) |
| `reproduce/llama2_70b_helm_audit/` | local LLaMA-2-70B reproduction (4×96 GB, vLLM tp=2) for Case Study 3 | **IN PROGRESS** (2026-05) |
| `reproduce/smoke/` | minimal end-to-end sanity run | **UNSURE** |
| `reproduce/apples/` | apples-to-apples reproduction control | **UNSURE** |
| `reproduce/historic_grid/` | regenerate a historic public-run manifest grid | **UNSURE** |
| `reproduce/machine_compare/` | cross-machine indexing + pairwise comparison | **UNSURE** |
| `reproduce/qwen35_vllm/` | local vLLM smoke for `qwen/qwen3.5-9b` | **UNSURE** (vLLM-side) |
| `reproduce/qwen2_72b_vllm/` | vLLM smoke + EWOK historic grid for qwen2-72b | **UNSURE** (vLLM-side) |
| `reproduce/gpt_oss_20b_vllm/` | LiteLLM-fronted vLLM batch for gpt-oss-20b | **UNSURE** (vLLM/LiteLLM-side) |
| `reproduce/small_models_kubeai/` | KubeAI overnight batch (qwen2.5-7b + vicuna-7b) | **UNSURE** (KubeAI-side) |
| `reproduce/setup/` | one-time host setup scripts | **UNSURE** but harmless |

Re-validating any of these is its own piece of work — the assumptions in
their READMEs (server URLs, KubeAI namespaces, LiteLLM keys, deployment YAML
shape) drift fast. Pick one, run it, and update its README before claiming
it's still good.

## CLI

Entry points are declared in [`pyproject.toml`](pyproject.toml#L74). Active /
dormant breakdown:

**Active (exercised by the analysis runbooks):**

- `eval-audit-build-virtual-experiment` — compose a virtual-experiment slice from a YAML manifest. Source kinds: `audit_index`, `official_public_index` (HELM-driven), `eee_root` (walk an `every_eval_ever` tree), `external_eee` (cherry-pick individual EEE artifacts). All four can mix in one manifest; the planner accepts the synthesized index regardless of artifact format.
- `eval-audit-build-summary` — build the publication surface (sankeys, prioritized examples, coverage matrix, README)
- `eval-audit-analyze-experiment` — per-experiment analysis (delegates to packet planner + core metrics)
- `eval-audit-analyze-many` — batched experiment analysis
- `eval-audit-analyze-index-snapshot` — snapshot the audit-results index
- `eval-audit-rebuild-core` — rebuild the per-packet core metric report
- `eval-audit-report-core` — single-packet core-metric reporting
- `eval-audit-compare-pair` — pair-level comparison
- `eval-audit-index` — build the audit-results index
- `eval-audit-index-historic` — Stage 1: discover historic public-HELM runs, apply the eligibility filters, and emit the filter report + sankey (what was kept/dropped and why)
- `eval-audit-portfolio-status` — multi-experiment status snapshot
- `eval-audit-lint-store` — lint a store for packets whose numbers depended on
  an unrecorded choice (see `docs/pipeline.md` and `docs/helm-gotchas.md` §G14)
- `eval-audit-verify-provenance` — check that a packet's recorded inputs are
  still present and digest-clean (see `docs/pipeline.md`)
- `eval-audit-crawl-analyses` / `eval-audit-package-analyses` — inventory and
  package analysis trees for transfer (driven by `package-analyses.sh`; see
  [`docs/transfer-packaging.md`](docs/transfer-packaging.md))
- `eval-audit-prepare-eee` — prepare EEE artifacts for downstream analysis
- `eval-audit-from-eee` — **EEE-only tutorial path.** Walks an
  ``official/`` + ``local/`` tree of `every_eval_ever` artifacts, runs the
  planner, renders per-packet core-metric reports, and (with
  ``--build-aggregate-summary``) produces a cross-packet aggregate
  report. Skips Stage-1 filter discovery and the HELM execution chain —
  the inputs *are* the scope. See
  [`reproduce/eee_only_demo/README.md`](reproduce/eee_only_demo/README.md)
  for a worked tutorial against a checked-in 3×3 fixture.
- `eval-audit-compare-pair-eee` — **EEE-only single-pair report.**
  The EEE analogue of `eval-audit-compare-pair`. Takes one official EEE
  artifact and one local EEE artifact, produces the same shape of
  core-metric report ``eval-audit-from-eee`` writes per pair. If you
  ship the original ``run_spec.json`` next to the EEE artifact, the
  HELM-side comparability facts (scenario class, deployment,
  instructions, max_eval_instances) flip from `unknown` to `yes`/`no`.
  See [`docs/eee-vs-helm-metadata.md`](docs/eee-vs-helm-metadata.md)
  for the full HELM↔EEE field mapping and recommendations.

**Execution path (exercised 2026-07-13 … 07-15 by the four from-spec / era runbooks):**

- `eval-audit-check-env` — host-environment preflight (light; works)
- `python -m eval_audit.integrations.infer_stack export-benchmark-bundle` — the
  manifest producer: freezes the official run specs for a preset and writes
  `<bundle-root>/{smoke,full}_manifest.yaml`. Not a console script.
- `eval-audit-run` — preview/execute a kwdagger experiment from a manifest
  (default is preview; `--run=1` to execute, `--lease` to acquire GPUs,
  `--container-image` to pin). A manifest with no pinned image is **refused**.

**Judge substitution (open-judge extension, 2026-07):**
`eval-audit-audit-judge-sources`, `eval-audit-build-response-snapshot`,
`eval-audit-verify-judge-replay`, `eval-audit-rejudge-helm`,
`eval-audit-analyze-judges`, `eval-audit-schedule-rejudge`,
`eval-audit-export-judge-bundle`, `eval-audit-judge-prompt-lengths` — driven by
[`reproduce/open_judge_gpt_oss/`](reproduce/open_judge_gpt_oss/).

**UNSURE:**

- `eval-audit-make-manifest` — `historic` and `preset` subcommands read from
  `$STORE_ROOT/configs/run_specs.yaml`. Superseded by `export-benchmark-bundle`
  and not exercised by any current runbook; it also cannot set a container
  image, so its output no longer schedules.

## Install

```bash
uv pip install -e .
```

Then the CLI scripts above are on `$PATH`. For analysis-only work this is
all you need.

For Plotly JPG/PNG sidecars on a headless Ubuntu 24.04 VM, install the Chrome
dependency once with
[`reproduce/setup/10_install_plotly_chrome_ubuntu2404.sh`](reproduce/setup/10_install_plotly_chrome_ubuntu2404.sh)
(also UNSURE — it has not been re-validated on the current images, but it's a
straightforward apt invocation).

## Documentation status

| file | status | note |
|---|---|---|
| [`docs/pipeline.md`](docs/pipeline.md) | **CURRENT** | the canonical "how do I run this": the four analysis stages plus the from-spec / era execution half (added 2026-08-04); the pre-EEE version is preserved at [`docs/historical/pipeline-pre-eee-refactor.md`](docs/historical/pipeline-pre-eee-refactor.md) |
| [`docs/container-execution.md`](docs/container-execution.md) | **CURRENT** | digest pinning, the era images, and why containerization is mandatory |
| [`docs/helm-unrecorded-deployment-params.md`](docs/helm-unrecorded-deployment-params.md) | **CURRENT** | which execution parameters the public record does not carry |
| [`docs/vllm-vs-huggingface-deployment-match.md`](docs/vllm-vs-huggingface-deployment-match.md) | **CURRENT** | the deployment-search results behind the OLMo attributions |
| [`docs/helm-gotchas.md`](docs/helm-gotchas.md) | **CURRENT** | running ledger of HELM-specific behaviors hit during analysis |
| [`docs/helm-reproduction-research-journal.md`](docs/helm-reproduction-research-journal.md) | **CURRENT** | research context, failure taxonomies |
| [`docs/eee-vs-helm-metadata.md`](docs/eee-vs-helm-metadata.md) | **CURRENT** | what HELM has that EEE doesn't, what `unknown` comparability facts mean, how to ship sidecar metadata so they evaluate normally |
| [`docs/transfer-packaging.md`](docs/transfer-packaging.md) | **CURRENT** | packaging analysis trees for hand-off (`package-analyses.sh`, crawl/pack CLIs, repoint) |
| [`docs/eee-only-hard-split-todo.md`](docs/eee-only-hard-split-todo.md) | **SUPERSEDED** | historical record of the hard-split proposal; the concerns were resolved differently (declared `--instance-source` policy + `normalized/diagnose.py` + `recipe_facts.py`) |
| [`docs/papers/`](docs/papers/) | **ACTIVE** | paper drafts: `neurips-2026/` (`technical_report.tex`, `case_study_3*.tex`) and `tmlr-2026/` (`main.tex`); renamed from `dev/paper/` on 2026-05-02 |
| [`docs/kwdagger-notes.md`](docs/kwdagger-notes.md) | **UNSURE** | small file, may still be accurate |
| [`docs/helm-null-completion-text-patch-proposal.md`](docs/helm-null-completion-text-patch-proposal.md) | **UNSURE** | pre-EEE patch proposal; outcome unclear |
| [`docs/architecture.md`](docs/architecture.md) | **CURRENT** | core ADRs (raw vs derived, reports/, filesystem-as-interface); its concrete module/CLI/env-var references were re-verified against the tree on 2026-08-06; moved from repo-root `ARCHITECTURE.md` on 2026-06-11 |

Moved into [`docs/historical/`](docs/historical/) on 2026-04-28 (preserved
verbatim — they may still be useful as records of *how* a problem was
approached at the time):

- `historical/pipeline-pre-eee-refactor.md` — the older end-to-end pipeline doc
- `historical/helm-reproduction-status-checkpoint.md`
- `historical/open-model-helm-reproduction-master-plan.md`
- `historical/reproduce-helm-session-v2.md`
- `historical/helm-reproduction-agent-brief.md`

## Caveats / things to verify before relying on a claim here

- **STALE** / **UNSURE** annotations above mean "nobody has confirmed this file
  is still correct." It does not mean the file is wrong. This README was last
  swept against the tree on **2026-08-06**.
- The `eval-audit-run` execution path is exercised (2026-07). What remains
  unverified is the *legacy* surface it used to drive: `make-manifest` output,
  the KubeAI/LiteLLM serving variants, and the bare-venv (uncontainerized) path,
  which has been removed outright.
- The `crfm-helm-audit-store` and `crfm-helm-audit` data-store paths are
  preserved verbatim from the pre-rename world (HELM-the-benchmark naming);
  see [`docs/helm-gotchas.md`](docs/helm-gotchas.md).
- The `eval_audit_local` source-organization tag is the rename of
  `helm_audit_local`. Existing on-disk EEE artifacts that pre-date the rename
  still carry the old tag.

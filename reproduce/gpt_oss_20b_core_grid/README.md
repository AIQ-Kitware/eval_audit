# gpt_oss_20b_core_grid — gpt-oss-20b on the 14 core reproducibility benchmarks

This runbook runs `openai/gpt-oss-20b` on the same 14 benchmarks where
`eleutherai/pythia-6.9b` and `lmsys/vicuna-7b-v1.3` are already complete,
enabling a direct cross-model comparison on a shared evaluation substrate.

**Motivation (TMLR paper context):** Classic HELM did not include gpt-oss-20b.
Running it on an identical benchmark set — with the same HELM recipe, same
max_eval_instances, same prompt format — goes "beyond the original experiments
in a direction that provides new scientific insight": specifically, how a
modern 20B-parameter completions model compares to the smaller open-weight
models HELM originally evaluated.

Because there is no official HELM public data to pair against, these runs
appear in the analysis as **local-only** entries. The cross-model comparison
is the primary output, not a reproducibility agreement ratio.

## Hardware assumption

Requires a single GPU with ≥96 GB VRAM (e.g., H100/A100-80G) or two GPUs
with ≥48 GB each (`tensor_parallel_size: 2`). On 24 GB-class GPUs the model
will not fit; use the existing `gpt_oss_20b_vllm` profile which targets larger
hardware, or adjust `tensor_parallel_size` in the vllm_service profile.

## Steps

```bash
./00_check_env.sh         # eval-audit-check-env + verify GPU layout
./05_write_bundle.sh      # write the eval-audit benchmark bundle (injects LiteLLM endpoint)
./15_validate_server.sh   # smoke-test the LiteLLM router with $LITELLM_MASTER_KEY
./20_run_smoke.sh         # eval-audit-run --run=1 on the smoke manifest (boolq, 5 instances)
./30_preview_full.sh      # dry-run the 41-entry full manifest
./40_run_full.sh          # execute the full manifest (long; 41 entries × 1000 instances)
./60_rebuild_reports.sh   # index + analyze + summary reports
```

Each step is idempotent (`compute_if_missing` skips DONE markers).

## Inputs and outputs

```
$AUDIT_STORE_ROOT/local-bundles/gpt_oss_20b_core_grid/
├── full_manifest.yaml       # eval-audit-run input (full, 41 entries)
├── smoke_manifest.yaml      # eval-audit-run input (boolq only, 5 instances)
└── model_deployments.yaml   # LiteLLM endpoint + API key (injected at write time)

$AUDIT_RESULTS_ROOT/audit-gpt-oss-20b-core-grid/   # full run output
$AUDIT_RESULTS_ROOT/audit-gpt-oss-20b-core-grid-smoke/
$AUDIT_STORE_ROOT/indexes/audit_results_index.csv   # refreshed by 60_rebuild_reports.sh
```

## Override knobs

- `AUDIT_STORE_ROOT` (default `/data/crfm-helm-audit-store`)
- `AUDIT_RESULTS_ROOT` (default `/data/crfm-helm-audit`)
- `LITELLM_BASE_URL` (default `http://localhost:14000`)
- `LITELLM_ENV_FPATH` (default `/data/service/service-repo/vllm/generated/.env`)
- `MAX_EVAL_INSTANCES` (default `1000`)

## Benchmarks covered (41 run entries across 14 families)

| Benchmark | Variants |
|---|---|
| boolq | 1 |
| civil_comments | 9 demographics |
| entity_data_imputation | Buy, Restaurant |
| entity_matching | Abt_Buy, Beer, Dirty_iTunes_Amazon |
| gsm | 1 |
| imdb | 1 |
| lsat_qa | all tasks, multiple_choice_joint |
| mmlu | 5 subjects, multiple_choice_joint |
| narrative_qa | 1 |
| quac | 1 |
| synthetic_reasoning | induction, pattern_match, variable_substitution |
| synthetic_reasoning_natural | easy, hard |
| truthful_qa | mc_single, multiple_choice_joint |
| wikifact | 10 subjects |

# reproduce/

This directory is the operator runbook layer for `eval_audit`. One folder per
scenario; the shell files are intentionally thin runbook steps, not the
implementation — each delegates to an `eval_audit` Python CLI such as
`eval-audit-run`, `eval-audit-index`, `eval-audit-analyze-experiment`, or
`eval-audit-build-virtual-experiment`. For `eval-audit-run`, preview is the
default; pass `--run=1` when you actually want to execute the scheduled
`kwdagger` job.

## The numbered-script idiom

Most execution-shaped scenarios are short numbered sequences (the original
convention):

- `00_*` — environment checks or indexing setup
- `10_*` — manifest generation or analysis selection
- `20_*` — execution or rebuild step
- `30_*` — comparison or follow-on reporting

A scenario matches this layout only when it actually has those phases.
Analysis-only or EEE-only runbooks operate over pre-existing artifacts and use
descriptive script names instead. The convention is a guideline, not a rule.

## Scenarios

Status labels are carried over from the runbook table in the top-level
[`README.md`](../README.md); scenarios absent from that table are marked
*status unrecorded*. **WORKING** / **UNSURE** / **IN PROGRESS** mean only what
that table's author could (or couldn't) confirm at the time — re-validate
before relying on a claim.

| scenario | purpose | status |
|---|---|---|
| `apples/` | apples-to-apples reproduction control (check_env → make_manifest → run → compare) | **UNSURE** |
| `eee_only_demo/` | self-contained EEE-only tutorial: compare official vs local EEE artifact trees via `eval-audit-from-eee` against a checked-in 3×3 fixture | **WORKING** (2026-04-29) |
| `eee_only_reproducibility_heatmap/` | model × benchmark instance-agreement heatmap (paper Case Study 3), entirely in EEE format — no GPU/internet at report time | **WORKING** (2026-05) |
| `extend_grid_falcon_7b/` | local Falcon-7B reproduction across the heatmap's 14 benchmarks (HELM HF backend, single GPU) | **WORKING** (2026-05, execution side) |
| `finish_qwen25_gptoss/` | close the Qwen 2.5 7B + gpt-oss audit gaps surfaced by Case Study 3 (re-run public run_specs with prompt prefix intact) | **WORKING** (2026-05, gated-dataset caveats) |
| `gpt_oss_20b_core_grid/` | run `openai/gpt-oss-20b` on the 14 core reproducibility benchmarks for a cross-model comparison (TMLR paper context) | status unrecorded |
| `gpt_oss_20b_from_spec/` | faithful **from-spec** replay of the 4 ungated-judge public gpt-oss-20b rows (bbq, ifeval, mmlu_pro, gpqa); single-model analogue of `olmo_models_combined/` | **WIRED** (2026-07; discovery 4/4 RESOLVED, GPU run pending) |
| `gpt_oss_20b_vllm/` | LiteLLM-fronted vLLM smoke + overnight batch for `openai/gpt-oss-20b` | **UNSURE** (vLLM/LiteLLM-side) |
| `historic_grid/` | regenerate a historic public-run manifest grid and rebuild reports | **UNSURE** |
| `inspectai_helm_eee_compare/` | EEE-only comparability stress: HELM-shaped + InspectAI-shaped artifacts in one bundle; probes what the planner can conclude | **WORKING** (2026-05) |
| `llama2_70b_helm_audit/` | local LLaMA-2-70B reproduction (vLLM tp=2, 2×96 GB) to add a 4th model to the Case Study 3 heatmap | **IN PROGRESS** (2026-05) |
| `machine_compare/` | cross-machine indexing, per-experiment analysis, and pairwise compare | **UNSURE** |
| `olmo_models/` | six AllenAI OLMo models: smoke + full grids folded into one grouped virtual-experiment report (from-spec) | status unrecorded |
| `olmo_models_combined/` | sibling of `olmo_models/` — a single multi-deployment preset fans five OLMo models across GPUs under one schedule | status unrecorded |
| `open_helm_models_reproducibility/` | virtual experiment over existing audit data: how reproducible are the open-weight public-HELM models (analysis + publication only) | **WORKING** (analysis) |
| `pythia12b_mmlu_smoke/` | local `pythia-12b-v0` run through the `eval-audit-run` → `kwdagger` → `magnet` → `helm-run` execution chain | **WORKING** (2026-04-28) |
| `pythia_mmlu_stress/` | virtual-experiment slice (Pythia × MMLU) over already-executed audit data; analysis + publication only, no execution step | **WORKING** (analysis) |
| `pythia_smoke_eee_only/` | EEE-only counterpart to `pythia12b_mmlu_smoke/` (no execution; `pythia-6.9b` on MMLU/BoolQ) | **WORKING** (2026-05) |
| `qwen2_72b_vllm/` | local vLLM smoke + full EWOK historic-grid batch for `qwen/qwen2-72b-instruct` | **UNSURE** (vLLM-side) |
| `qwen35_vllm/` | local vLLM smoke for `qwen/qwen3.5-9b` through `kwdagger` + the materialized HELM path | **UNSURE** (vLLM-side) |
| `setup/` | one-time host setup (e.g. install Chrome for Plotly/Kaleido static image export on Ubuntu 24.04) | **UNSURE** but harmless |
| `small_models_kubeai/` | KubeAI overnight batch keeping `qwen2.5-7b` + `vicuna-7b` resident together; emits one combined benchmark bundle | **UNSURE** (KubeAI-side) |
| `smoke/` | minimal end-to-end sanity run (check_env → make_manifest → run → compare) | **UNSURE** |

## Conventions & assumptions

Generated manifests referenced by these runbooks default to
`$AUDIT_STORE_ROOT/configs/manifests/`, with
`AUDIT_STORE_ROOT=/data/crfm-helm-audit-store` as the fallback. Checked-in
`configs/` files remain source-controlled inputs and overrides, not a sink for
generated experiment state.

The serving-backed runbooks (`qwen35_vllm/`, `qwen2_72b_vllm/`,
`gpt_oss_20b_vllm/`, `small_models_kubeai/`) carry their operational
assumptions — local server URLs, LiteLLM keys, KubeAI namespaces, deployment
profiles — in their own scripts and in the presets/configs they reference
(`eval_audit/integrations/infer_stack/presets.py`, `configs/local_models/`);
e.g. gpt-oss deliberately uses the legacy completions protocol
(`protocol_mode: completions`) because its chat path returned
`message.content: null`. Those assumptions drift fast; read the scripts before
running. Run every runbook from the repo root, e.g.:

```bash
export KUBEAI_NAMESPACE=default
export KUBEAI_BASE_URL=http://127.0.0.1:8000/openai/v1
bash reproduce/small_models_kubeai/99_run_tonight.sh   # one-command overnight entrypoint
```

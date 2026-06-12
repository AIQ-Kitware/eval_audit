# OLMo models — smoke grid + grouped report

Runs the **smoke** manifest for the six AllenAI OLMo presets and folds the
results into a **single grouped report** via a virtual experiment. The goal is a
fast end-to-end exercise of the run path *and* the grouping path — not a full
reproducibility sweep.

The grouping is deliberately **additive**: each preset keeps its own
`experiment_name`/`suite` and runs as an isolated job; the virtual experiment
re-stamps their index rows under one name (`olmo-models-smoke`) for reporting.
The six per-model experiments remain independently analyzable. (Sharing a literal
`experiment_name` across the presets would be destructive — Stage 5 filters by
exact `experiment_name`, so per-model reports would no longer be addressable.)

## The six targets

Each has an infer-stack profile `<preset>-single`. The presets declare
`access_kind: vllm-direct`, but **this runbook routes through the LiteLLM gateway
(openai-compatible)** — the run script overrides the access kind at export time
(see below). Ordered smallest → largest (the grid runs them in this order):

| preset | profile | smoke `experiment_name` |
|---|---|---|
| `allenai-olmoe-1b-7b-0125-instruct` | `allenai-olmoe-1b-7b-0125-instruct-single` | `audit-allenai-olmoe-1b-7b-0125-instruct-smoke` |
| `allenai-olmo-7b` | `allenai-olmo-7b-single` | `audit-allenai-olmo-7b-smoke` |
| `allenai-olmo-1-7-7b` | `allenai-olmo-1-7-7b-single` | `audit-allenai-olmo-1-7-7b-smoke` |
| `allenai-olmo-2-1124-7b-instruct` | `allenai-olmo-2-1124-7b-instruct-single` | `audit-allenai-olmo-2-1124-7b-instruct-smoke` |
| `allenai-olmo-2-1124-13b-instruct` | `allenai-olmo-2-1124-13b-instruct-single` | `audit-allenai-olmo-2-1124-13b-instruct-smoke` |
| `allenai-olmo-2-0325-32b-instruct` | `allenai-olmo-2-0325-32b-instruct-single` | `audit-allenai-olmo-2-0325-32b-instruct-smoke` |

The presets and their smoke `run_entries` already live in
[`eval_audit/integrations/infer_stack/adapter.py`](../../eval_audit/integrations/infer_stack/adapter.py)
— this runbook adds no preset code.

## Serving profiles: shipped here, but verify the HF ids

The presets reference infer-stack profiles named `<preset>-single`, which are
**not** in the repo's `submodules/infer_stack` builtin catalog. This runbook
therefore ships its own infer-stack config:

- [`config/infer_stack/models.yaml`](config/infer_stack/models.yaml) — the six
  OLMo `vllm_models` + their `<preset>-single` profiles, in the simple
  `services:`/`router:` form (each model on vLLM, fronted by LiteLLM). No
  `benchmark_transport` block: the bundle's **default** access is
  openai-compatible (the LiteLLM router), which is what this runbook uses.
- [`config/infer_stack/config.yaml`](config/infer_stack/config.yaml) — points
  `user_models_file` at the models.yaml.

`_lib.sh` sets `INFER_STACK_CONFIG_DIR` to that dir by default. All six profiles
have been validated to resolve to contracts whose default access is
openai-compatible via the real infer-stack resolver.

**Still verify before a real run** (these are best-effort defaults, flagged in
the models.yaml comments):

- **`hf_model_id`** — the OLMo-2 / OLMoE ids are high-confidence; the
  OLMo-1 / OLMo-1.7 `-hf` ids are lower-confidence — confirm on HF Hub.
- **GPU sizing** — the 32B is set to `tp=2`; adjust `preferred_gpu_count` /
  `resource_profile` to your hardware.
- **HELM aliases** — `logical_model_name`/`tokenizer_name`
  (`allenai/olmo-7b`, …) must be registered in HELM's
  `submodules/helm/.../config/model_metadata.yaml` + `tokenizer_configs.yaml`;
  `export-benchmark-bundle` asserts this.

`05_check_profiles.sh` validates profile presence up front and fails with
guidance if any is missing.

> **Known blocker (pre-existing, not OLMo-specific):** on a checkout where
> `infer_stack` is not pip-installed, the adapter loads the vendored
> `submodules/infer_stack`, whose `load_profile_contract()` does not accept the
> `root` kwarg the adapter passes — so `export-benchmark-bundle` raises
> `TypeError` for *any* vllm preset (qwen/gpt-oss/olmo). Resolve the
> adapter↔submodule version skew (e.g. `uv pip install -e submodules/infer_stack`
> with a compatible version) before running the grid.

## Steps

```bash
./00_check_env.sh         # eval-audit-check-env
./05_check_profiles.sh    # verify the six <preset>-single profiles are defined
./06_check_hf_auth.sh     # verify a HuggingFace token (gated gpqa dataset needs it)
./10_run_smoke_grid.sh    # per model: switch profile -> wait-ready -> export bundle -> run smoke
./20_index_local.sh       # eval-audit-index -> audit_results_index.csv
./30_compose.sh           # build the virtual experiment (the grouping step)
./40_build_summary.sh     # aggregate publication surface across all six
```

The grouping manifest is checked in at
[`configs/virtual-experiments/olmo-models-smoke.yaml`](../../configs/virtual-experiments/olmo-models-smoke.yaml).

## Knobs (env vars)

- `AUDIT_STORE_ROOT` (default `/data/crfm-helm-audit-store`)
- `AUDIT_RESULTS_ROOT` (default `/data/crfm-helm-audit`)
- `VEXP_MANIFEST` — override the grouping manifest path
- `INFER_STACK_CONFIG_DIR` — infer-stack config providing the OLMo profiles
- `OLMO_KEEP_GOING=1` — in `10_run_smoke_grid.sh`, attempt every model and report
  failures at the end instead of stopping on the first error (default: fail-fast)
- `EVAL_AUDIT_SKIP_LOCAL_REPEAT=1`, `EVAL_AUDIT_GROUP_STRIP=1` — set by `_lib.sh`,
  matching the e2e-test convention (one local attempt per model, group prefix
  stripped)

## What this assumes / produces

- **Local-only by default.** The manifest has no `official_public_index` source,
  so the report is the union of the six local smoke runs; comparability facts a
  public counterpart would supply collapse to `status=unknown` and surface as
  `comparability_unknown:*` warnings — expected for a local-only smoke, not a bug.
  To compare against public HELM, uncomment the `official_public_index` source in
  the manifest (needs the public index + Stage-1 filter inventory present).
- **LiteLLM / openai-compatible transport.** `10_run_smoke_grid.sh` resolves the
  LiteLLM endpoint + master key via `infer-stack env --key`, and overrides the
  presets' declared `vllm-direct` access with
  `--access-kind openai-compatible --base-url <litellm>/v1 --api-key-value <key>`
  (mirrors `dev/e2e-tests/e2e-phi_2-vllm-philosophy.sh`). HELM talks to the
  LiteLLM gateway, which routes to each model's vLLM backend.
- **One model at a time.** `infer-stack switch` tears down the previous model;
  the grid spans a 1B-active MoE to a 32B dense model, which will not co-host.
- **Gated datasets need HuggingFace auth.** The presets include every candidate
  run from `candidate_runs.json`, including ones tagged `requires-gated-dataset`
  — `gpqa` on the OLMo-2 / OLMoE instruct models (and the **smoke** entry for
  `allenai/olmo-2-1124-7b-instruct`). `_lib.sh` exports `HF_TOKEN` /
  `HUGGING_FACE_HUB_TOKEN` from the env or a cached `huggingface-cli login` so
  HELM can download them; `06_check_hf_auth.sh` verifies a token is present. The
  token's account must have accepted the gated dataset's terms (e.g.
  [Idavidrein/gpqa](https://huggingface.co/datasets/Idavidrein/gpqa)). The five
  non-gated smoke entries still run without a token; only the
  `olmo-2-1124-7b-instruct` smoke requires one.

## Output layout

```
$AUDIT_STORE_ROOT/virtual-experiments/olmo-models-smoke/
├── manifest.yaml
├── indexes/                 # synthesized index slice (rows re-stamped olmo-models-smoke)
├── analysis/
│   ├── core-reports/<one per model packet>/
│   └── experiment_summary.{json,csv,txt}
└── reports/aggregate-summary/   # the grouped publication surface
```

# OLMo models — smoke + full grids + grouped report

Runs the OLMo presets for the six AllenAI models in two passes — a cheap
**smoke** preflight and the **full** candidate sweep — and folds the **full**
results into a **single grouped report** via a virtual experiment. The smoke grid
is a fast end-to-end exercise of the run path; the full grid is the actual
reproducibility batch, and the downstream index → compose → summary steps operate
on it.

The grouping is deliberately **additive**: each preset keeps its own
`experiment_name`/`suite` and runs as an isolated job; the virtual experiment
re-stamps their index rows under one name (`olmo-models`) for reporting.
The six per-model experiments remain independently analyzable. (Sharing a literal
`experiment_name` across the presets would be destructive — Stage 5 filters by
exact `experiment_name`, so per-model reports would no longer be addressable.)

## The six targets

Each has an infer-stack profile `<preset>-single`. The presets declare
`access_kind: vllm-direct`, but **this runbook routes through the LiteLLM gateway
(openai-compatible)** — the run script overrides the access kind at export time
(see below). Ordered smallest → largest (the grids run them in this order). Each
preset has a `-smoke` and a `-full` experiment; the grouped report uses `-full`:

| preset | profile | full `experiment_name` |
|---|---|---|
| `allenai-olmoe-1b-7b-0125-instruct` | `allenai-olmoe-1b-7b-0125-instruct-single` | `audit-allenai-olmoe-1b-7b-0125-instruct-full` |
| `allenai-olmo-7b` | `allenai-olmo-7b-single` | `audit-allenai-olmo-7b-full` |
| `allenai-olmo-1-7-7b` | `allenai-olmo-1-7-7b-single` | `audit-allenai-olmo-1-7-7b-full` |
| `allenai-olmo-2-1124-7b-instruct` | `allenai-olmo-2-1124-7b-instruct-single` | `audit-allenai-olmo-2-1124-7b-instruct-full` |
| `allenai-olmo-2-1124-13b-instruct` | `allenai-olmo-2-1124-13b-instruct-single` | `audit-allenai-olmo-2-1124-13b-instruct-full` |
| `allenai-olmo-2-0325-32b-instruct` | `allenai-olmo-2-0325-32b-instruct-single` | `audit-allenai-olmo-2-0325-32b-instruct-full` |

The presets and their smoke/full `run_entries` already live in
[`eval_audit/integrations/infer_stack/adapter.py`](../../eval_audit/integrations/infer_stack/adapter.py)
— this runbook adds no preset code.

## Serving endpoints: shipped here, but verify the HF ids

The presets reference infer-stack catalog endpoints named `<preset>-single`,
which are **not** in the repo's `submodules/infer_stack`. This runbook therefore
ships its own infer-stack config (new catalog/leasing schema):

- [`config/infer_stack/catalog.yaml`](config/infer_stack/catalog.yaml) — the six
  OLMo `models` (backing HF source) + their `<preset>-single` `endpoints` (each
  served on vLLM, fronted by the LiteLLM gateway). HELM-domain facts
  (model/tokenizer alias, protocol mode) live in the eval_audit presets, not
  here — the catalog only owns transport (served name + context window).
- [`config/infer_stack/settings.yaml`](config/infer_stack/settings.yaml) —
  durable leasing settings (`litellm: true`, `ui: false`, `backend: compose`).

`_lib.sh` sets `INFER_STACK_CONFIG_DIR` to that dir by default (and
`INFER_STACK_DATA_DIR`, so `infer-stack env` and `acquire` agree on where the
managed `.env`/ledger live — C-2). All six endpoints have been validated to
resolve via the real `infer_stack.leasing.Catalog`.

**Still verify before a real run** (these are best-effort defaults, flagged in
the catalog.yaml comments):

- **`source` (HF id)** — the OLMo-2 / OLMoE ids are high-confidence; the
  OLMo-1 / OLMo-1.7 `-hf` ids are lower-confidence — confirm on HF Hub.
- **GPU sizing** — the 32B endpoint sets `runtime.tensor_parallel_size: 2`;
  adjust the `runtime.*` block to your hardware (C-5: sizing is static catalog
  state now, not resolver-inferred).
- **HELM aliases** — the presets' `helm_model_name`/`helm_tokenizer_name`
  (`allenai/olmo-7b`, …) must be registered in HELM's
  `submodules/helm/.../config/model_metadata.yaml` + `tokenizer_configs.yaml`;
  `export-benchmark-bundle` asserts this.

`05_check_profiles.sh` validates endpoint presence up front and fails with
guidance if any is missing.

> **Pin the submodule.** The adapter imports the vendored
> `submodules/infer_stack`; make sure the `infer-stack` CLI on `PATH` matches it
> (`uv pip install -e submodules/infer_stack`) so the leasing verbs the grid
> calls (`acquire`/`wait`/`release`/`env`/`catalog`) are the same code the adapter
> resolves against (C-8).

## Steps

```bash
./docker/build.sh         # build eval-audit-helm-runner:dev (containerized HELM is ON by default)
./00_check_env.sh         # eval-audit-check-env
./05_check_profiles.sh    # verify the six <preset>-single endpoints are defined
./06_check_hf_auth.sh     # verify a HuggingFace token (gated gpqa dataset needs it)
./07_check_container_image.sh  # verify docker + the container image (no-op when OLMO_CONTAINER=0)
./10_run_smoke_grid.sh    # preflight: per model release -> serve -> wait -> export bundle -> run smoke
./15_run_full_grid.sh     # per model: same, but run the FULL manifest (the reproducibility batch)
./20_index_local.sh       # eval-audit-index -> audit_results_index.csv (verifies the -full run dirs)
./30_compose.sh           # build the virtual experiment from the -full runs (the grouping step)
./40_build_summary.sh     # aggregate publication surface across all six
```

`./docker/build.sh` is a prerequisite only for the default containerized path
(see below); skip it if you run with `OLMO_CONTAINER=0`.

The smoke preflight (`10`) is optional once you trust the path — `15` is the run
that feeds `20`/`30`/`40`. The grouping manifest is checked in at
[`configs/virtual-experiments/olmo-models.yaml`](../../configs/virtual-experiments/olmo-models.yaml)
(the `-smoke` variant,
[`olmo-models-smoke.yaml`](../../configs/virtual-experiments/olmo-models-smoke.yaml),
is kept for grouping the smoke preflight instead).

## Containerized HELM ("docker pipeline") — ON by default

By default this runbook runs **HELM inside the pinned `eval-audit-helm-runner`
image** (Stage 3 containerized execution, the "docker pipeline") instead of the
host venv. This pins HELM's software environment so it stops being a confounding
variable in the reproducibility comparison (the core research question — see
[`docs/helm-reproduction-research-journal.md`](../../docs/helm-reproduction-research-journal.md)).
Full background:
[`docs/container-execution.md`](../../docs/container-execution.md).

**The model is still served on the host.** vLLM behind the LiteLLM gateway runs
on the host exactly as before; only *where HELM runs* changes. The in-container
HELM client reaches the host LiteLLM endpoint via Docker `--network host`, which a
default bridge container could not see. This is declared by the presets'
`container_network: host` (in
[`eval_audit/integrations/infer_stack/adapter.py`](../../eval_audit/integrations/infer_stack/adapter.py));
the same blocks set `container_gpus: none` because the OLMo run entries
(commonsense / gsm / legalbench / med_qa / mmlu / gpqa) are
multiple-choice / exact-match / classification metrics with **no LLM-judge
annotator that loads a local HF model** — so the HELM container needs no GPU and
must stay off the serving GPUs (`INFER_STACK_ALLOWED_GPUS`, default 2,3).

The experiment names are unchanged (`audit-<preset>-{smoke,full}`), so the
downstream index → compose → summary stages need no changes — runs just gain a
per-run `container_provenance.json` sidecar.

**How the toggle works.** The recipe-level container fields live in the presets
but are *inert* until a run supplies an image; the **image is the on/off switch**,
passed at run time via the existing `eval-audit-run --container-image` flag:

| `OLMO_CONTAINER` | grid passes | HELM runs in |
|---|---|---|
| `1` (default) | `eval-audit-run … --container-image "$OLMO_CONTAINER_IMAGE"` | the **container** ("docker pipeline") |
| `0` | `eval-audit-run …` (no flag) | the **host venv** (container fields inert) |

Build the image first with `./docker/build.sh`; `07_check_container_image.sh`
verifies it is present (and is a no-op when `OLMO_CONTAINER=0`). The gated **gpqa**
dataset still works in-container: `_lib.sh` exports `HF_TOKEN` /
`HUGGING_FACE_HUB_TOKEN`, which the docker pipeline forwards into the container.

To run on a host without docker (or to A/B the container against the host venv),
set `OLMO_CONTAINER=0` — the grids omit `--container-image` and HELM runs in the
host venv, leaving the presets' container fields inert.

## Knobs (env vars)

- `OLMO_CONTAINER` (default `1`) — `1` runs HELM in the pinned container ("docker
  pipeline", the default); `0` runs HELM in the host venv (the fallback). The
  model is served on the host either way; only where HELM runs changes (see the
  containerized-execution section above)
- `OLMO_CONTAINER_IMAGE` (default `eval-audit-helm-runner:dev`) — the image
  passed to `eval-audit-run --container-image` when `OLMO_CONTAINER=1`; build it
  with `./docker/build.sh`, or point at a pushed digest for cross-machine pinning
- `AUDIT_STORE_ROOT` (default `/data/crfm-helm-audit-store`)
- `AUDIT_RESULTS_ROOT` (default `/data/crfm-helm-audit`)
- `VEXP_MANIFEST` — override the grouping manifest path
- `INFER_STACK_CONFIG_DIR` — infer-stack config providing the OLMo endpoints
- `INFER_STACK_DATA_DIR` — where the managed LiteLLM `.env` + lease ledger live
  (C-2); defaults to the XDG location, override to a big-disk path per host
- `LITELLM_PORT` — LiteLLM gateway host port (default `14042`)
- `OLMO_KEEP_GOING=1` — in `10_run_smoke_grid.sh` / `15_run_full_grid.sh`, attempt
  every model and report failures at the end instead of stopping on the first
  error (default: fail-fast)
- `OLMO_FORCE_RERUN=1` — in `10_run_smoke_grid.sh` / `15_run_full_grid.sh`, clear
  each model's prior result dir (`$AUDIT_RESULTS_ROOT/audit-<preset>-smoke` resp.
  `-full`) before running. `eval-audit-run` schedules with kwdagger
  `skip_existing=1`, so a model whose previous run already wrote its `DONE`
  sentinel is otherwise skipped on re-invocation (default: reuse)
- `EVAL_AUDIT_SKIP_LOCAL_REPEAT=1`, `EVAL_AUDIT_GROUP_STRIP=1` — set by `_lib.sh`,
  matching the e2e-test convention (one local attempt per model, group prefix
  stripped)

## What this assumes / produces

- **Local-only by default.** The manifest has no `official_public_index` source,
  so the report is the union of the six local full runs; comparability facts a
  public counterpart would supply collapse to `status=unknown` and surface as
  `comparability_unknown:*` warnings — expected for a local-only batch, not a bug.
  To compare against public HELM, uncomment the `official_public_index` source in
  the manifest (needs the public index + Stage-1 filter inventory present).
  - **BBQ prompt drift (recipe hurdle).** When comparing against public HELM, the
    BBQ runs on the four instruct models flag `comparability_drift:same_instructions`
    because public HELM injects an `output_format_instructions` run-expander that is
    invisible in the run name. The presets now match it; see
    [`NOTES-bbq-instructions-drift.md`](NOTES-bbq-instructions-drift.md) for the full
    write-up and the generalizable "the run name is not the recipe" lesson.
- **LiteLLM / openai-compatible transport.** `10_run_smoke_grid.sh` /
  `15_run_full_grid.sh` read the master key via `infer-stack env
  LITELLM_MASTER_KEY` (after the model's `acquire`, since the managed `.env` is
  written on first bring-up), and override the presets' declared `vllm-direct`
  access with
  `--access-kind openai-compatible --base-url <litellm>/v1 --api-key-value <key>`
  (default-B; mirrors the phi-2 e2e grid). HELM talks to the LiteLLM gateway,
  which routes to each model's vLLM backend.
- **One model at a time.** Each iteration runs `infer-stack release --all
  --evict` before `acquire` so only the current model holds GPUs (C-1: `acquire`
  *accumulates*, unlike the old `switch` which replaced); the grid spans a
  1B-active MoE to a 32B dense model, which will not co-host.
- **Gated datasets need HuggingFace auth.** The presets include every candidate
  run from `candidate_runs.json`, including ones tagged `requires-gated-dataset`
  — `gpqa` on the OLMo-2 / OLMoE instruct models (and the **smoke** entry for
  `allenai/olmo-2-1124-7b-instruct`). `_lib.sh` exports `HF_TOKEN` /
  `HUGGING_FACE_HUB_TOKEN` from the env or a cached `huggingface-cli login` so
  HELM can download them; `06_check_hf_auth.sh` verifies a token is present. The
  token's account must have accepted the gated dataset's terms (e.g.
  [Idavidrein/gpqa](https://huggingface.co/datasets/Idavidrein/gpqa)). The
  non-gated entries still run without a token; the gated ones (incl. the
  `olmo-2-1124-7b-instruct` smoke) require one.

## Output layout

```
$AUDIT_STORE_ROOT/virtual-experiments/olmo-models/
├── manifest.yaml
├── indexes/                 # synthesized index slice (rows re-stamped olmo-models)
├── analysis/
│   ├── core-reports/<one per model packet>/
│   └── experiment_summary.{json,csv,txt}
└── reports/aggregate-summary/   # the grouped publication surface
```

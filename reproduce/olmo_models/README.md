# OLMo models — smoke + full grids + grouped report

Runs the OLMo presets for the six AllenAI models in two passes — a cheap
**smoke** preflight and the **full** candidate sweep — and folds the **full**
results into a **single grouped report** via a virtual experiment. The smoke grid
is a fast end-to-end exercise of the run path — and deliberately carries two
recipe **canaries**: `wmt_14` (on `olmo-7b`) loads a `huggingface_hub`-version
sensitive dataset and scores via `sacrebleu`, and `ifeval` (on
`olmo-2-1124-7b-instruct`) imports `langdetect` — both from `crfm-helm[metrics]`.
A mis-built or stale runner image (`[heim]` instead of `[all]`, or a floated hub)
therefore fails this cheap preflight rather than dying deep in the full grid. The
full grid is the actual reproducibility batch, and the downstream index → compose
→ summary steps operate on it.

> **Faithful replay (from-spec) is the default.** Each local run **replays the
> official HELM `run_spec.json` verbatim** rather than reconstructing the recipe
> from a hand-authored run-entry. The grids pass `--from-spec` to
> `export-benchmark-bundle` unconditionally (every OLMo preset is comparable —
> there is no temperature negative control), so the local reproduction differs
> from the official **only by model execution**: a metric difference isolates the
> model, not recipe drift. See [From-spec replay](#from-spec-replay) below.

The grouping is deliberately **additive**: each preset keeps its own
`experiment_name`/`suite` and runs as an isolated job; the virtual experiment
re-stamps their index rows under one name (`olmo-models`) for reporting.
The seven per-experiment results remain independently analyzable. (Sharing a literal
`experiment_name` across the presets would be destructive — Stage 5 filters by
exact `experiment_name`, so per-experiment reports would no longer be addressable.)

## The targets — seven experiments over six models

Each has an infer-stack profile `<preset>-single`. The presets declare
`access_kind: vllm-direct`, but **this runbook routes through the LiteLLM gateway
(openai-compatible)** — the run script overrides the access kind at export time
(see below). Ordered smallest → largest (the grids run them in this order). Each
preset has a `-smoke` and a `-full` experiment; the grouped report uses `-full`:

| preset | profile | full `experiment_name` | `precomputed_root` (from-spec source) |
|---|---|---|---|
| `allenai-olmoe-1b-7b-0125-instruct` | `allenai-olmoe-1b-7b-0125-instruct-single` | `audit-allenai-olmoe-1b-7b-0125-instruct-full` | `/data/crfm-helm-public` |
| `allenai-olmo-7b-mmlu` | `allenai-olmo-7b-single` | `audit-allenai-olmo-7b-mmlu-full` | `/data/crfm-helm-public/mmlu` |
| `allenai-olmo-7b-lite` | `allenai-olmo-7b-single` | `audit-allenai-olmo-7b-lite-full` | `/data/crfm-helm-public/lite` |
| `allenai-olmo-1-7-7b` | `allenai-olmo-1-7-7b-single` | `audit-allenai-olmo-1-7-7b-full` | `/data/crfm-helm-public/mmlu` |
| `allenai-olmo-2-1124-7b-instruct` | `allenai-olmo-2-1124-7b-instruct-single` | `audit-allenai-olmo-2-1124-7b-instruct-full` | `/data/crfm-helm-public` |
| `allenai-olmo-2-1124-13b-instruct` | `allenai-olmo-2-1124-13b-instruct-single` | `audit-allenai-olmo-2-1124-13b-instruct-full` | `/data/crfm-helm-public` |
| `allenai-olmo-2-0325-32b-instruct` | `allenai-olmo-2-0325-32b-instruct-single` | `audit-allenai-olmo-2-0325-32b-instruct-full` | `/data/crfm-helm-public` |

**Why seven rows for six models:** `allenai/olmo-7b` was run by HELM under **two
official suites** — the full-MMLU suite (`/mmlu` v1.1.0) and HELM-Lite (`/lite`
v1.2.0). The per-subject MMLU runs exist in *both* (the Lite dir name is a
token-subset of the MMLU one), so a shared `precomputed_root` would make from-spec
discovery ambiguous. Splitting olmo-7b into **`-mmlu`** and **`-lite`** — each with
its own per-suite root — lets every discovery key resolve to exactly one official
run. Both experiments serve the same model via the one `allenai-olmo-7b-single`
endpoint. (`08_check_discovery.sh` enforces 0 NO_MATCH / 0 AMBIGUOUS across all
seven.)

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

`_lib.sh` sets `INFER_STACK_CONFIG_DIR` to that dir by default and resolves
`INFER_STACK_DATA_DIR` **once** (then exports it, so `infer-stack env` and the
bracket's `acquire` agree on where the managed `.env`/ledger live — C-2). The data
dir is also **bind-mounted into the vLLM/LiteLLM containers** (HF weight cache +
gateway route table), so it must live on a docker-mountable big disk; the
resolution order is `INFER_STACK_DATA_DIR` env > the `data_dir:` pin in
[`config/infer_stack/settings.yaml`](config/infer_stack/settings.yaml) >
`${INFER_STACK_DATA_ROOT:-/data/service}/infer-stack`, and `_lib.sh` warns loudly
(rather than silently relocating to an NFS `$HOME`) if the chosen dir is unwritable
or on NFS/autofs. All six endpoints have been validated to resolve via the real
`infer_stack.leasing.Catalog`.

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
./05_check_profiles.sh    # verify the <preset>-single endpoints are defined
./06_check_hf_auth.sh     # verify a HuggingFace token (gated gpqa dataset needs it)
./07_check_container_image.sh  # verify docker + the container image (required; containerization is mandatory)
./08_check_discovery.sh   # from-spec preflight: every run-entry resolves 1:1 to an official run_spec.json (0 NO_MATCH / 0 AMBIGUOUS)
./10_run_smoke_grid.sh    # preflight: gc -> gateway bootstrap -> per model (export --from-spec bundle -> run smoke --lease)
./15_run_full_grid.sh     # per model: same, but run the FULL manifest (the reproducibility batch)
./20_index_local.sh       # eval-audit-index -> audit_results_index.csv (verifies the -full run dirs)
./30_compose.sh           # build the virtual experiment from the -full runs (the grouping step)
./40_build_summary.sh     # aggregate publication surface across all seven
```

`./docker/build.sh` is a required prerequisite — containerization is mandatory
(see below), and every run is pinned to the built image.

The smoke preflight (`10`) is optional once you trust the path — `15` is the run
that feeds `20`/`30`/`40`. The grouping manifest is checked in at
[`configs/virtual-experiments/olmo-models.yaml`](../../configs/virtual-experiments/olmo-models.yaml)
(the `-smoke` variant,
[`olmo-models-smoke.yaml`](../../configs/virtual-experiments/olmo-models-smoke.yaml),
is kept for grouping the smoke preflight instead).

## Containerized HELM ("docker pipeline") — MANDATORY

This runbook runs **HELM inside the pinned `eval-audit-helm-runner` image**
(Stage 3 containerized execution, the "docker pipeline"). Containerization is
mandatory — the host-venv path has been removed (`build_schedule_params` requires
a container image). This pins HELM's software environment so it stops being a
confounding variable in the reproducibility comparison (the core research
question — see
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
must stay off the serving GPUs. Serving placement is **unrestricted by default**
— infer-stack uses every detected GPU; export `INFER_STACK_ALLOWED_GPUS=<csv>` to
pin it to specific cards on a shared machine.

The experiment names are unchanged (`audit-<preset>-{smoke,full}`), so the
downstream index → compose → summary stages need no changes — runs just gain a
per-run `container_provenance.json` sidecar.

**How it's wired.** The recipe-level container fields (`container_network: host`,
`hf_cache_dir`, `container_gpus: none`) live in the presets; the grids always
pass the image at run time via `eval-audit-run --container-image
"$OLMO_CONTAINER_IMAGE"`. Build the image first with `./docker/build.sh`;
`07_check_container_image.sh` verifies it is present **and** probes its python env
(langdetect importable from `crfm-helm[all]`, `huggingface_hub==0.36.2`) so a
stale digest built before a recipe fix fails the preflight instead of mid-grid (a
required preflight — there is no host-venv fallback). The gated **gpqa** dataset works in-container: `_lib.sh`
exports `HF_TOKEN` / `HUGGING_FACE_HUB_TOKEN`, and the scheduler writes that token
into the mounted HF cache (`<hf_cache_dir>/token`) so the in-container HELM reads
it at `$HF_HOME/token` — the docker node's bare `-e HF_TOKEN` does not survive
kwdagger's fresh tmux pane, so this on-disk hand-off is what carries auth.

Leasing is the **orthogonal** axis (always on via `--lease`): the container pins
where the HELM client runs; the lease acquires the served model's GPU. See
[`eval_audit/pipelines/lease_bracket.py`](../../eval_audit/pipelines/lease_bracket.py).

## From-spec replay

Faithful replay is the **default and only** path for this runbook (the grids pass
`--from-spec` to `export-benchmark-bundle` unconditionally — every OLMo preset is
comparable, so unlike the phi-2 e2e there is no negative-control carve-out). The
chain:

1. **Discovery.** Each preset's `run_entries` are reduced to bare *discovery keys*
   (benchmark stem + `model=` + only the disambiguating tokens that appear in the
   official dir name). The exporter resolves each key against the preset's
   `precomputed_root` with a token-subset matcher and locates the **one** official
   HELM run dir, whose `run_spec.json` is the authoritative recipe.
   `08_check_discovery.sh` runs this same matcher offline (CPU-only) and fails on
   any NO_MATCH (nothing to replay) or AMBIGUOUS (the suite split / root scoping
   failed to disambiguate) — run it after any preset edit.
2. **Replay.** The bundle manifest carries `from_run_spec: true` +
   `precomputed_root`; the kwdagger bridge selects the replay pipeline from that
   field and drives HELM from the discovered `run_spec.json` **verbatim** — method,
   instructions (incl. any opt-in run-expander the run name doesn't show), CoT,
   temperature, token limits all come from the official spec, not the entry. This
   is what makes the [BBQ instruction drift](NOTES-bbq-instructions-drift.md) and
   the [dropped run-expander keys](NOTES-dropped-run-expander-keys.md) vanish by
   construction.
3. **Deployment rewrite.** The official spec names the official endpoint
   (`together/olmo-7b`, `huggingface/olmo-2-…`, …). The exporter sets the
   manifest's `model_deployment` to the bundle's **own** local name
   (`vllm/allenai-<model>`) and the replay rewrites `adapter_spec.model_deployment`
   to it — so the produced run records the *local* endpoint and the audit reports
   `same_deployment=no` (the one recipe fact that legitimately differs is the
   engine). A pure by-name replay would instead record the official name and mask
   the substitution; the rewrite is what keeps it honest.

The methodological payoff: a metric difference between the local and official run
isolates **model execution**, because every other recipe fact is the official one,
replayed verbatim. See
[`docs/planning/olmo-from-run-spec-migration-plan.md`](../../docs/planning/olmo-from-run-spec-migration-plan.md).

## Knobs (env vars)

- `OLMO_CONTAINER_IMAGE` (default `eval-audit-helm-runner:dev`) — the image the
  grids pass to `eval-audit-run --container-image` (containerization is
  mandatory); build it with `./docker/build.sh`, or point at a pushed digest for
  cross-machine pinning
- `AUDIT_STORE_ROOT` (default `/data/crfm-helm-audit-store`)
- `AUDIT_RESULTS_ROOT` (default `/data/crfm-helm-audit`)
- `VEXP_MANIFEST` — override the grouping manifest path
- `INFER_STACK_CONFIG_DIR` — infer-stack config providing the OLMo endpoints
- `INFER_STACK_DATA_DIR` — where the managed LiteLLM `.env` + lease ledger live
  AND the dir bind-mounted into the containers (C-2); resolution is env > the
  `settings.yaml` `data_dir:` pin > `${INFER_STACK_DATA_ROOT:-/data/service}/infer-stack`.
  Must be a docker-mountable big disk (never NFS `$HOME`); `_lib.sh` warns if it
  isn't
- `INFER_STACK_DATA_ROOT` (default `/data/service`) — relocates just the *parent*
  of the default data dir for hosts whose big disk isn't `/data/service`
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
  so the report is the union of the seven local full runs; comparability facts a
  public counterpart would supply collapse to `status=unknown` and surface as
  `comparability_unknown:*` warnings — expected for a local-only batch, not a bug.
  To compare against public HELM, uncomment the `official_public_index` source in
  the manifest (needs the public index + Stage-1 filter inventory present).
  - **BBQ prompt drift — resolved by from-spec.** Public HELM injects an
    `output_format_instructions` run-expander into the four instruct models' BBQ
    runs that is invisible in the run name, so a run-entry reconstruction sent a
    *different prompt* (`comparability_drift:same_instructions`). Faithful replay
    makes this vanish by construction: the BBQ `adapter_spec.instructions` now comes
    from the matched official `run_spec.json`, so `same_instructions` holds without
    any hand-added token. See [`NOTES-bbq-instructions-drift.md`](NOTES-bbq-instructions-drift.md)
    (kept as the historical case study for the generalizable "the run name is not
    the recipe" lesson).
- **LiteLLM / openai-compatible transport.** `10_run_smoke_grid.sh` /
  `15_run_full_grid.sh` read the master key once via `infer-stack env
  LITELLM_MASTER_KEY` at a one-time gateway bootstrap (the managed `.env` is
  written on first bring-up), and override the presets' declared `vllm-direct`
  access with
  `--access-kind openai-compatible --base-url <litellm>/v1 --api-key-value <key>`
  (default-B; mirrors the phi-2 e2e grid). HELM talks to the LiteLLM gateway,
  which routes to each model's vLLM backend.
- **Per-run GPU leasing (one model at a time, no pre-serve).** Each scheduled
  HELM run self-acquires its model's lease (`eval-audit-run --lease`:
  `acquire --queue` before, release after); the catalog's `reclaim: stop` frees
  the GPU on the last release and a final `infer-stack gc` reclaims any leaked
  lease. infer-stack's admission queue serializes the models (the grid spans a
  1B-active MoE to a 32B dense model, which will not co-host). There is no
  per-model serve loop and no blunt `release --all --evict` (which tore down the
  shared docker-compose project, killing co-tenants' models) — start and end use
  the scoped, leaked-lease `gc`, which never touches another user's active
  leases. Leasing is the orthogonal axis to the (now mandatory) containerization:
  the container pins where the HELM *client* runs, the lease acquires the served
  model's GPU (client runs with `container_gpus: none`).
- **Gated datasets need HuggingFace auth.** The presets include every candidate
  run from `candidate_runs.json`, including ones tagged `requires-gated-dataset`
  — `gpqa` on the OLMo-2 / OLMoE instruct models (and the **smoke** entry for
  `allenai/olmo-2-1124-7b-instruct`). `_lib.sh` exports `HF_TOKEN` /
  `HUGGING_FACE_HUB_TOKEN` from the env or a cached `huggingface-cli login`, and
  the scheduler writes that token into the mounted HF cache so the in-container
  HELM reads it at `$HF_HOME/token` (the bare `-e HF_TOKEN` does not survive
  kwdagger's tmux pane); `06_check_hf_auth.sh` verifies a token is present. The
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

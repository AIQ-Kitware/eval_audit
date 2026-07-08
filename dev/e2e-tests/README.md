# phi-2 e2e — smoke + full grids + per-scenario reports

End-to-end exercises of the audit pipeline on Microsoft **phi-2**, restructured
into the same runbook shape as
[`reproduce/olmo_models_combined/`](../../reproduce/olmo_models_combined/)
(whose lineage traces back to these scripts). Runs the three phi-2 scenarios in
two passes — a cheap **smoke** preflight and the **full** batch — then composes
and reports the **full** results as **one virtual experiment per scenario**. The
smoke grid is a fast end-to-end exercise of the run path; the full grid is the
batch the downstream index → compose → summary steps operate on.

These are pipeline tests, not a reproducibility claim: phi-2 + MMLU-philosophy is
small and fast, and the comparable/incomparable pair is a positive/negative
control. Each scenario is compared against the public microsoft/phi-2
mmlu:philosophy run (via the `official_public_index` source in its manifest).

> **Before you run:** see [`NOTES.md`](NOTES.md) for run-time gotchas not enforced
> in code — chiefly the `.venv` pin (`transformers<5` + `huggingface_hub==0.36.2`)
> whose absence shows up as "completed not analyzed".

## The three scenarios

Each keeps its own `experiment_name`/`suite` and runs as an isolated job, and is
composed + reported as its **own** virtual experiment (one static manifest per
scenario in [`configs/virtual-experiments/`](../../configs/virtual-experiments/):
`e2e-phi2-vllm`, `e2e-phi2-incomparable`, `e2e-phi2-hf`).
Composing one scenario at a time keeps a single local recipe per report, so each
pairs cleanly with the public run instead of all three pooling into one packet
(same model + scenario → same canonical key). **Ordered HF-direct first, then the
vLLM scenarios:** the HF target
loads `microsoft/phi-2` onto the GPU itself, so it runs while the GPU is free —
the grids reclaim leaked leases at the start (`infer-stack gc`, scoped — not the
old blunt `release --all --evict`) and run HF first, before any vLLM scenario
self-acquires phi-2, so the direct load can't OOM against a GPU-resident server.
The vLLM scenarios use per-run GPU leasing (`eval-audit-run --lease`): each
scheduled run acquires phi-2's lease and releases it after, so there is no
standing pre-served stack.

| scenario (`name`) | transport | full `experiment_name` | what it exercises |
|---|---|---|---|
| `e2e-phi_2-huggingface-philosophy` | hf | `e2e-phi_2-huggingface-philosophy-full` | HELM loads `microsoft/phi-2` directly from HuggingFace (no infer-stack); runs first, on a free GPU |
| `e2e-phi_2-vllm-philosophy` | vllm | `e2e-phi_2-vllm-philosophy-full` | comparable baseline: phi-2 on vLLM via LiteLLM |
| `e2e-phi_2-vllm-philosophy-incomparable` | vllm | `e2e-phi_2-vllm-philosophy-incomparable-full` | negative control: same, but `temperature=1` (a deliberate recipe deviation the planner should flag) |

The two vLLM presets and their smoke/full `run_entries` live in
[`eval_audit/integrations/infer_stack/adapter.py`](../../eval_audit/integrations/infer_stack/adapter.py);
the HF scenario is driven by the checked-in manifests under
[`manifests/`](manifests/). Each scenario has a `-smoke`
(`max_eval_instances=5`) and a `-full` (`max_eval_instances=1000`) experiment;
the per-scenario reports use `-full`.

## Faithful replay (from-spec) — the default

The two **comparable** scenarios (`hf`, `vllm`) replay the **official**
`microsoft/phi-2` mmlu:philosophy `run_spec.json` verbatim instead of
reconstructing the recipe from the run-entry string. This is the **default and
only path** — there is no run-entry opt-out — so the local reproduction differs
from the official run only by model *execution*, never by a re-derived recipe.
Mechanically: the run-entry becomes just the discovery key (it locates the
official run dir under `precomputed_root=/data/crfm-helm-public/mmlu`), and the
matched `run_spec.json` drives execution. The recipe names the official
deployment `together/phi-2`; a local override registers the engine that actually
serves the run under a **local** deployment name (`huggingface/phi-2-local` for
`hf` via `model_deployments_fpath`; the bundle's native `vllm/phi-2-local` for
`vllm`), and the replay **rewrites** `adapter_spec.model_deployment` to that local
name (the manifest's `model_deployment` field threads it in). So the produced run
records the served endpoint and the audit reports `same_deployment=no` — surfacing
the engine substitution instead of masking it. See the migration plan
[`docs/historical/planning/e2e-from-run-spec-migration-plan.md`](../../docs/historical/planning/e2e-from-run-spec-migration-plan.md)
and the deployment-rewrite plan
[`docs/historical/planning/from-spec-deployment-rewrite-plan.md`](../../docs/historical/planning/from-spec-deployment-rewrite-plan.md).

The **incomparable** control is the sole carve-out (`e2e_uses_from_spec` in
`_lib.sh`): it stays on the run-entry path because from-spec replays the official
recipe verbatim and would erase the `temperature=1` deviation it exists to flag.

## Transports

The grid branches per scenario on its `transport` (the second field of each
`E2E_TARGETS` row in [`_lib.sh`](_lib.sh)):

- **`vllm`** — phi-2 is served on vLLM behind the LiteLLM gateway, and each
  scheduled HELM run self-acquires phi-2's GPU lease for the run
  (`eval-audit-run --lease`; no per-scenario pre-serve). `export-benchmark-bundle`
  materializes the bundle from the preset. The phi-2 presets already declare
  `access_kind: openai-compatible`, so the export passes only the LiteLLM
  base-url + master key — **no** `--access-kind`
  override (unlike `reproduce/olmo_models_combined`, whose presets declare `vllm-direct`).
- **`hf`** — no infer-stack: HELM loads `microsoft/phi-2` IN-PROCESS, and the run
  is the checked-in `manifests/<experiment>.yaml`. The manifest is from-spec (see
  above), so the in-process client is registered by the `model_deployments_fpath`
  override (which rebinds the official `together/phi-2` deployment) rather than by
  `enable_huggingface_models`.

## Serving endpoint

The vLLM scenarios use the infer-stack catalog endpoint `phi2-single`, shipped
here (new catalog/leasing schema):

- [`config/infer_stack/catalog.yaml`](config/infer_stack/catalog.yaml) — the
  `phi-2` model + the `phi2-single` endpoint (single GPU, fronted by LiteLLM).
  HELM-domain facts (alias, protocol) live in the preset, not here.
- [`config/infer_stack/settings.yaml`](config/infer_stack/settings.yaml) —
  durable leasing settings (`litellm: true`, `ui: false`, `backend: compose`).

`_lib.sh` sets `INFER_STACK_CONFIG_DIR` to that dir by default (and
`INFER_STACK_DATA_DIR`, C-2). `05_check_profiles.sh` validates the endpoint is
present before the grid runs.

## Containerized execution (mandatory)

**Every** scenario runs each HELM run-entry inside the pinned
`eval-audit-helm-runner` image (Stage 3 containerized execution; the host-venv
path has been removed — see [`docs/container-execution.md`](../../docs/container-execution.md)).
This pins HELM's software environment so it stops being a confounding variable in
the reproducibility comparison. Build the image first (`./docker/build.sh`); the
grids always pass `eval-audit-run --container-image "$E2E_CONTAINER_IMAGE"`, and
`06_check_container_image.sh` is a required preflight.

The container config differs by transport — the only knob that varies is whether
the scenario reaches a host-served model or loads its own:

| transport | container networking | GPU | model |
|---|---|---|---|
| `vllm` | **`--network host`** | `container_gpus: none` (HTTP client) | served on the host (vLLM behind LiteLLM), leased per run |
| `hf` | default bridge | real GPU (loads in-process) | loaded inside the container from HuggingFace |

For the `vllm` scenarios the **model is served on the host** (published on the
host's `localhost`); a default-bridge container's `localhost` is its own
namespace, so **`--network host`** shares the host namespace and the baked-in
`localhost` base URL resolves. (Why host vs. `host.docker.internal` / a shared
docker network: host is Linux-only but matches our GPU run hosts, keeps one base
URL, and avoids coupling to infer-stack's compose internals.) The `hf` scenario
loads phi-2 in-process, so it needs no host endpoint (default bridge) and a real
GPU — it takes no lease, which is the only thing distinguishing it from the
served scenarios. All these fields are declared by the presets / hf manifests, so
`export-benchmark-bundle` and the checked-in manifests carry them; the image is
supplied at run time via `--container-image`.

## Steps

```bash
./00_check_env.sh             # eval-audit-check-env
./05_check_profiles.sh        # verify the phi2-single endpoint is defined (vLLM scenarios)
./06_check_container_image.sh # verify the runner image exists AND probe its python env (langdetect + huggingface_hub==0.36.2); required, containerization is mandatory
./10_run_smoke_grid.sh        # preflight: gc -> gateway bootstrap -> per scenario (vllm: export bundle -> run smoke --lease; hf: run smoke)
./15_run_full_grid.sh         # per scenario: same, but run the FULL manifest (the batch)
./17_rsync_from_aiq_gpu.sh    # OPTIONAL: ran 10/15 on aiq-gpu instead? pull its mirrored /data roots back here
./20_index_local.sh           # eval-audit-index -> audit_results_index.csv (verifies the -full run dirs)
./30_compose.sh               # compose ONE virtual experiment per scenario (loops E2E_TARGETS)
./40_build_summary.sh         # build one publication surface per scenario
./42_rsync_summaries_from_aiq_gpu.sh  # OPTIONAL: ran 40 on aiq-gpu? pull just its per-scenario summary dirs back here
```

The smoke preflight (`10`) is optional once you trust the path — `15` is the run
that feeds `20`/`30`/`40`. Step `17` is also optional: it's only needed when the
grid actually ran on the **aiq-gpu** GPU box rather than locally. Because
aiq-gpu's `/data` roots mirror this host (same absolute paths), it's a straight
mirrored `rsync` of `RESULTS_ROOT` (and, for a full pull, `STORE_ROOT`) back here
before indexing — pull the whole roots, or pass experiment names to narrow:
`./17_rsync_from_aiq_gpu.sh audit-historic-grid-gpt-oss-20b-vllm`. It honors the
same `AUDIT_RESULTS_ROOT`/`AUDIT_STORE_ROOT` overrides; set `AIQ_GPU_HOST`
(or `~/.ssh/config`), and `DRY_RUN=1` to preview. It never deletes local-only
files unless you opt in with `DELETE=1`. `30`/`40` loop over the scenarios in `E2E_TARGETS`,
composing/summarizing each scenario's own manifest
([`e2e-phi2-vllm.yaml`](../../configs/virtual-experiments/e2e-phi2-vllm.yaml),
[`-incomparable`](../../configs/virtual-experiments/e2e-phi2-incomparable.yaml),
[`-hf`](../../configs/virtual-experiments/e2e-phi2-hf.yaml)) into its
own report dir. Set `VEXP_MANIFEST=<path>` to compose/summarize just one.

Step `42` is the targeted counterpart to `17` for the **final** reports: when
`40` ran on **aiq-gpu**, it pulls each scenario's results back here (discovered
the same way `40` builds them — looping `E2E_TARGETS`, honoring `VEXP_MANIFEST`),
rather than the whole `STORE_ROOT`. By **default** it pulls each scenario's entire
`<output.root>` (indexes + analysis + reports) and mirrors it with `--delete`, so
the local copy exactly matches aiq-gpu and a previous run's stale files (e.g. an
out-of-date `analysis/core-reports/.../core_metric_report.*`) don't linger. Narrow
to just `reports/aggregate-summary` with `SYNC_FULL_OUTPUT=0`, keep local-only
files with `DELETE=0`, and preview either with `DRY_RUN=1` (rsync shows what it
would transfer **and** delete). Shares `17`'s host knobs (`AIQ_GPU_HOST`, …) via
`_rsync_lib.sh`.

## Knobs (env vars)

- `AUDIT_STORE_ROOT` (default `/data/crfm-helm-audit-store`)
- `AUDIT_RESULTS_ROOT` (default `/data/crfm-helm-audit`)
- `VEXP_MANIFEST` — compose/summarize a single per-scenario manifest instead of
  looping over all scenarios in `E2E_TARGETS` (e.g. just `e2e-phi2-vllm.yaml`)
- `INFER_STACK_CONFIG_DIR` — infer-stack config providing the `phi2-single` profile
- `E2E_KEEP_GOING=1` — in `10_run_smoke_grid.sh` / `15_run_full_grid.sh`, attempt
  every scenario and report failures at the end instead of stopping on the first
  error (default: fail-fast)
- `E2E_FORCE_RERUN=1` — in `10_run_smoke_grid.sh` / `15_run_full_grid.sh`, clear
  each scenario's prior result dir (`$AUDIT_RESULTS_ROOT/<experiment>`) before
  running. `eval-audit-run` schedules with kwdagger `skip_existing=1`, so a
  scenario whose previous run already wrote its `DONE` sentinel is otherwise
  skipped on re-invocation. The **smoke** grid force-reruns by default (cheap
  preflight); the **full** grid keeps it opt-in.
- `EVAL_AUDIT_SKIP_LOCAL_REPEAT=1`, `EVAL_AUDIT_GROUP_STRIP=1` — set by `_lib.sh`,
  carried verbatim from the original e2e scripts (one local attempt per scenario,
  group prefix stripped)
- `E2E_CONTAINER_IMAGE` (default `eval-audit-helm-runner:dev`) — the image the
  grids pass to `eval-audit-run --container-image` (containerization is mandatory;
  there is no host-venv fallback) and that `06_check_container_image.sh` verifies.
  Build it with `./docker/build.sh`; set to a pushed digest for cross-machine
  pinning.
- `E2E_HF_CACHE_DIR` (default `$HOME/.cache/eval-audit-hf`) — dedicated audit HF
  cache for the container runs (the container runs as root; keeps downloads
  consistently owned).

## What this assumes / produces

- **Public comparison, one report per scenario.** Each per-scenario manifest
  declares an `official_public_index` source, so every scenario is paired against
  the public microsoft/phi-2 mmlu:philosophy run by canonical logical key. Each
  scenario is composed alone, so the three never pool into one packet. The
  incomparable scenario deviates the recipe (`temperature=1`) — HELM doesn't
  encode temperature in the run name, so it still matches the public run, and the
  comparison is the intended recipe-drift check (the planner flags the deviation
  per-packet). To make a scenario local-only, drop the `official_public_index`
  source from its manifest.
- **LiteLLM / openai-compatible transport for the vLLM scenarios.**
  `10`/`15` read the master key once via positional `infer-stack env
  LITELLM_MASTER_KEY` at a one-time gateway bootstrap (the managed `.env` is
  written on first bring-up) and hand it + the gateway base-url (port `14042`
  default) to `export-benchmark-bundle`. HELM talks to the LiteLLM gateway, which routes to
  phi-2's vLLM backend.
- **No HuggingFace token required.** `microsoft/phi-2` and the MMLU dataset are
  public, so no scenario needs a token — there is no HF-auth preflight, unlike
  `reproduce/olmo_models_combined`, whose gated `gpqa` runs do. (The `06` slot here is
  instead the container-image preflight.)

## Output layout

```
$AUDIT_STORE_ROOT/virtual-experiments/e2e-phi2/
├── manifest.yaml
├── indexes/                 # synthesized index slice (rows re-stamped e2e-phi2)
├── analysis/
│   ├── core-reports/<one per scenario packet>/
│   └── experiment_summary.{json,csv,txt}
└── reports/aggregate-summary/   # the grouped publication surface
```

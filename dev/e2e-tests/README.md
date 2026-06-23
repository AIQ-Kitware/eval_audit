# phi-2 e2e — smoke + full grids + per-scenario reports

End-to-end exercises of the audit pipeline on Microsoft **phi-2**, restructured
into the same shape as [`reproduce/olmo_models/`](../../reproduce/olmo_models/)
(which was itself derived from these scripts). Runs the three phi-2 scenarios in
two passes — a cheap **smoke** preflight and the **full** batch — then composes
and reports the **full** results as **one virtual experiment per scenario**. The
smoke grid is a fast end-to-end exercise of the run path; the full grid is the
batch the downstream index → compose → summary steps operate on.

These are pipeline tests, not a reproducibility claim: phi-2 + MMLU-philosophy is
small and fast, and the comparable/incomparable pair is a positive/negative
control. Each scenario is compared against the public microsoft/phi-2
mmlu:philosophy run (via the `official_public_index` source in its manifest).

## The three scenarios

Each keeps its own `experiment_name`/`suite` and runs as an isolated job, and is
composed + reported as its **own** virtual experiment (one static manifest per
scenario in [`configs/virtual-experiments/`](../../configs/virtual-experiments/):
`e2e-phi2-vllm`, `e2e-phi2-incomparable`, `e2e-phi2-hf`, `e2e-phi2-container`).
Composing one scenario at a time keeps a single local recipe per report, so each
pairs cleanly with the public run instead of all three pooling into one packet
(same model + scenario → same canonical key). **Ordered HF-direct first, then the
vLLM scenarios:** the HF target
loads `microsoft/phi-2` onto the GPU itself, so it runs while the GPU is free —
the grids free any vLLM stack at the start (`infer-stack release --all --evict`)
and run HF before bringing vLLM up, so the direct load can't OOM against a
GPU-resident server.

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

## Transports

The grid branches per scenario on its `transport` (the second field of each
`E2E_TARGETS` row in [`_lib.sh`](_lib.sh)):

- **`vllm`** — `infer-stack acquire` brings phi-2 up on vLLM and fronts it with
  the LiteLLM gateway; `export-benchmark-bundle` materializes the bundle from the
  preset. The phi-2 presets already declare `access_kind: openai-compatible`, so
  the export passes only the LiteLLM base-url + master key — **no** `--access-kind`
  override (unlike `reproduce/olmo_models`, whose presets declare `vllm-direct`).
- **`hf`** — no infer-stack: HELM's HuggingFace path loads `microsoft/phi-2`
  directly (the manifest's `enable_huggingface_models`), and the run is the
  checked-in `manifests/<experiment>.yaml`.

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

## Containerized execution example (on by default)

One extra scenario exercises the **containerized HELM execution** path (Stage 3
runs each HELM run-entry inside the pinned `eval-audit-helm-runner` image instead
of the host venv; see [`docs/container-execution.md`](../../docs/container-execution.md)).
It runs **by default**; set `E2E_INCLUDE_CONTAINER=0` to skip it. Because it needs
the image built first (`./docker/build.sh`) and a working docker, opt out on hosts
that lack them (otherwise `06_check_container_image.sh` fails the preflight):

| scenario (`name`) | transport | full `experiment_name` | container networking |
|---|---|---|---|
| `e2e-phi_2-vllm-philosophy-container` | vllm | `…-container-full` | **`--network host`** |

This is the intended containerized workflow: the **model is served on the host**
(phi-2 on vLLM behind LiteLLM, published on the host's `localhost`) and HELM runs
in the container. A default-bridge container's `localhost` is its own namespace,
not the host's, so the HELM client could not reach the endpoint; **`--network
host`** shares the host namespace so the baked-in `localhost` base URL resolves.
(Why this is the right fix vs. `host.docker.internal` or a shared docker network:
host is Linux-only but matches our GPU run hosts, keeps a single base URL
identical to the host-venv run, and avoids coupling to infer-stack's compose
internals.)

It reuses the existing `vllm` `run_one` branch **unchanged** — the container
behavior is entirely declarative. The container fields (incl. `container_network:
host`) are declared by the `e2e-phi_2-vllm-philosophy-container` **preset** in
`adapter.py`, so `export-benchmark-bundle` writes them into the generated bundle
manifest. The image tag is `eval-audit-helm-runner:dev`; for cross-machine
pinning, push it and override with `eval-audit-run --container-image <digest>`
(and set `E2E_CONTAINER_IMAGE` to match for the preflight). This groups via
[`configs/virtual-experiments/e2e-phi2-container.yaml`](../../configs/virtual-experiments/e2e-phi2-container.yaml)
(point `VEXP_MANIFEST` at it for `30`/`40`).

```bash
# The container scenario runs as part of the normal grid (10/15) by default, and
# 30/40 compose/summarize it (along with the other scenarios) by default too.
# To run JUST the container scenario's compose+report, point VEXP_MANIFEST at
# e2e-phi2-container.yaml for 30/40:
./docker/build.sh
./06_check_container_image.sh
./10_run_smoke_grid.sh      # (or 15 for the full batch)
VEXP_MANIFEST="$PWD/../../configs/virtual-experiments/e2e-phi2-container.yaml" \
  ./30_compose.sh && \
VEXP_MANIFEST="$PWD/../../configs/virtual-experiments/e2e-phi2-container.yaml" \
  ./40_build_summary.sh
```

## Steps

```bash
./00_check_env.sh             # eval-audit-check-env
./05_check_profiles.sh        # verify the phi2-single endpoint is defined (vLLM scenarios)
./06_check_container_image.sh # verify the runner image exists (no-op only if E2E_INCLUDE_CONTAINER=0)
./10_run_smoke_grid.sh        # preflight: per scenario -> (vllm: serve -> wait -> export bundle) -> run smoke
./15_run_full_grid.sh         # per scenario: same, but run the FULL manifest (the batch)
./20_index_local.sh           # eval-audit-index -> audit_results_index.csv (verifies the -full run dirs)
./30_compose.sh               # compose ONE virtual experiment per scenario (loops E2E_TARGETS)
./40_build_summary.sh         # build one publication surface per scenario
```

The smoke preflight (`10`) is optional once you trust the path — `15` is the run
that feeds `20`/`30`/`40`. `30`/`40` loop over the scenarios in `E2E_TARGETS`,
composing/summarizing each scenario's own manifest
([`e2e-phi2-vllm.yaml`](../../configs/virtual-experiments/e2e-phi2-vllm.yaml),
[`-incomparable`](../../configs/virtual-experiments/e2e-phi2-incomparable.yaml),
[`-hf`](../../configs/virtual-experiments/e2e-phi2-hf.yaml),
[`-container`](../../configs/virtual-experiments/e2e-phi2-container.yaml)) into its
own report dir. Set `VEXP_MANIFEST=<path>` to compose/summarize just one.

## Knobs (env vars)

- `AUDIT_STORE_ROOT` (default `/data/crfm-helm-audit-store`)
- `AUDIT_RESULTS_ROOT` (default `/data/crfm-helm-audit`)
- `VEXP_MANIFEST` — compose/summarize a single per-scenario manifest instead of
  looping over all scenarios in `E2E_TARGETS` (e.g. just `e2e-phi2-container.yaml`)
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
- `E2E_INCLUDE_CONTAINER=0` — skip the containerized example (vllm-container),
  which is **on by default**. The default needs the `eval-audit-helm-runner`
  image built (`./docker/build.sh`) and docker available; set this to `0` on
  hosts without them.
- `E2E_CONTAINER_IMAGE` (default `eval-audit-helm-runner:dev`) — image tag the
  `06_check_container_image.sh` preflight verifies; set to a pushed digest for
  cross-machine pinning.
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
  `10`/`15` read the master key via positional `infer-stack env
  LITELLM_MASTER_KEY` (after `acquire`, since the managed `.env` is written on
  first bring-up) and hand it + the gateway base-url (port `14042` default) to
  `export-benchmark-bundle`. HELM talks to the LiteLLM gateway, which routes to
  phi-2's vLLM backend.
- **No HuggingFace token required.** `microsoft/phi-2` and the MMLU dataset are
  public, so no scenario needs a token — there is no HF-auth preflight, unlike
  `reproduce/olmo_models`, whose gated `gpqa` runs do. (The `06` slot here is
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

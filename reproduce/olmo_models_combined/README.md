# OLMo models — combined multi-model fan-out

Faithful from-spec reproduction of the AllenAI OLMo HELM runs, scheduled as a
**single multi-model fan-out**: instead of running the presets one at a time in a
serial loop, this runbook runs a **single multi-deployment preset** —
`allenai-olmo-combined` — exported with `--freeze-rel-paths` and scheduled with
`eval-audit-run --tmux-workers N`, so **five OLMo models fan out across GPUs under
one schedule**. The sixth model, the base `olmo-7b`, can't join that bundle (see
below), so it runs as two extra single-model suites folded into the **same virtual
experiment** — the grouped report covers all six OLMo models.

See [`docs/historical/planning/olmo-multi-model-from-spec-plan.md`](../../docs/historical/planning/olmo-multi-model-from-spec-plan.md)
§4.4/§4.7 for the design.

## Design at a glance

- **Presets** — one multi-deployment preset (`allenai-olmo-combined`) covering the
  five models that resolve 1:1 under the shared corpus root, plus olmo-7b as two
  extra single-model suites.
- **Scheduling** — a single `eval-audit-run` over the combined manifest, with
  `--tmux-workers N` driving N concurrent leased runs (vs. one `eval-audit-run`
  per model).
- **Export** — `--from-spec --freeze-rel-paths` (exact-path replay), with a
  **per-run** inline `model_deployment=<local>` rewrite target (vs. a single
  manifest-level target).
- **Experiments** — three (`audit-allenai-olmo-combined-full` + olmo-7b
  `-mmlu`/`-lite`), folded into one virtual experiment via
  [`olmo-models-combined.yaml`](../../configs/virtual-experiments/olmo-models-combined.yaml).

**Why olmo-7b rides separately.** `allenai/olmo-7b` was run by HELM under two suites
(`/mmlu` and `/lite`) whose per-subject MMLU dirs are token-subsets of each other,
so it is **ambiguous under the shared parent root** the combined bundle freezes
against. The other five (`olmo-1.7-7b` + the four OLMo-2 / OLMoE instruct models)
all resolve 1:1 under `/data/crfm-helm-public`, so they share one bundle cleanly.
olmo-7b instead runs as its two single-model suites (`allenai-olmo-7b-mmlu` /
`-lite`) against their narrow `/mmlu` and `/lite` roots — exported + scheduled by
`10`/`15` right after the combined bundle (`OLMO_COMBINED_EXTRA_PRESETS` in
`_lib.sh`) — and all three experiments are folded into the one virtual experiment.

**Why `--freeze-rel-paths` is mandatory here.** A multi-deployment bundle has no
single manifest-level rewrite target — each run needs its own. The exact-path
exporter freezes, per run, the official rel-path + the **local** rewrite target +
the lease endpoint (from the inline `model_deployment=vllm/allenai-<model>` token
each run_entry carries). The plain `--from-spec` *discovery* path can't express
that, so it is not used here. The materializer applies the substitutions host-side
before kwdagger; no in-container token-subset discovery runs.

## Setup (self-contained)

This runbook ships everything it needs — no dependency on a sibling runbook:

- [`config/infer_stack/`](config/infer_stack/) — the shipped OLMo catalog with the
  `<model>-single` endpoints (`catalog.yaml`) + durable leasing settings
  (`settings.yaml`). `_lib.sh` points `INFER_STACK_CONFIG_DIR` here.
- `_lib.sh` — resolves the repo root, the store/results roots, the
  docker-mountable `INFER_STACK_DATA_DIR` (env > `settings.yaml` pin > big-disk
  default), `OLMO_CONTAINER_IMAGE`, HuggingFace-token resolution, and the
  `EVAL_AUDIT_*` group-strip conventions, then defines the combined-specific bits
  (grouping manifest, preset name, the five endpoints, fan-out width).
- `06_check_hf_auth.sh` / `07_check_container_image.sh` — self-contained
  preflights (the container image is built at the repo root via `./docker/build.sh`).
- [`deployment_match/`](deployment_match/) — optional diagnostic that searches the
  best local serving recipe (dtype/tokenizer/…) reproducing one public HELM run;
  see [`deployment_match/run_deployment_match.sh`](deployment_match/run_deployment_match.sh).

## Steps

```bash
../../docker/build.sh            # build eval-audit-helm-runner:dev (containerization is ON)
./00_check_env.sh                # eval-audit-check-env
./05_check_profiles.sh           # verify the six <model>-single endpoints are defined
./06_check_hf_auth.sh            # verify a HuggingFace token (gated gpqa dataset needs it)
./07_check_container_image.sh    # verify docker + the pinned image (required)
./08_check_discovery.sh          # freeze the bundle (resolves 1:1 or fails) + validate every frozen run_spec.json exists
./10_run_smoke.sh                # smoke: gc -> gateway bootstrap -> export --freeze-rel-paths -> run smoke --lease --tmux-workers N
./15_run_full.sh                 # full reproducibility batch (same, full manifest)
./20_index_local.sh              # eval-audit-index -> audit_results_index.csv (verifies the combined-full run dir)
./30_compose.sh                  # build the virtual experiment from all three full runs
./40_build_summary.sh            # aggregate publication surface
./50_rsync_from_aiq_gpu.sh       # (from the analysis host) pull the outputs back from aiq-gpu
```

The smoke preflight (`10`) is optional once you trust the path — `15` is the run
that feeds `20`/`30`/`40`.

`50` is the only step run **from the analysis host** rather than on aiq-gpu: when
the `10`–`40` steps ran on the aiq-gpu GPU box, it mirrors the finished outputs
back to this host (aiq-gpu's `/data` roots share identical absolute paths). It
pulls the vexp `output.root` by default; `SYNC_RESULTS=1` also fetches the raw
run dirs and the shared index. Preview with `DRY_RUN=1`.

## Knobs (env vars)

Base setup (from `_lib.sh`): `OLMO_CONTAINER_IMAGE`, `AUDIT_STORE_ROOT`,
`AUDIT_RESULTS_ROOT`, `INFER_STACK_*`, `LITELLM_PORT`, `OLMO_FORCE_RERUN`, ….
Combined-specific:

- `OLMO_TMUX_WORKERS` (default `4`) — fan-out width: the max concurrent HELM
  client runs cmd_queue drives. Each run self-leases its model's GPU; infer-stack
  co-hosts what fits on `INFER_STACK_ALLOWED_GPUS` and **queues** the rest, so this
  is not a GPU count and may exceed the number of cards. The 32B (`tensor_parallel_size: 2`)
  can't co-host, so it serializes against the smaller models. Within a model, its
  run_entries share one deployment via ref-counting, so raising this mostly
  parallelizes *across* models.
- `OLMO_COMBINED_VEXP_MANIFEST` — override the grouping manifest path.
- `OLMO_FORCE_RERUN` — default **on** in `10_run_smoke.sh` (cheap preflight),
  **off** in `15_run_full.sh` (expensive); clears the prior result dir so
  kwdagger's `skip_existing` re-executes.

## Status / caveats

- **Exporter + freeze + fan-out are wired here; the GPU end-to-end run is the
  remaining verification.** `08_check_discovery.sh` proves the freeze resolves 1:1
  against the corpus (CPU-only); the first `15_run_full.sh` on GPUs is what
  confirms multiple models co-host / serialize under leasing and that the produced
  runs record `model_deployment=vllm/allenai-<model>` (`same_deployment=no`).
- **Runbook-level discovery preflight uses the freeze**, not bare-key
  `check_precomputed_discovery --preset`: the combined preset's inline
  `model_deployment=<local>` tokens would NO_MATCH the bare-key matcher. Teaching
  the `--preset` mode the same local-strip (plan §4.3) would let `08` skip the
  scratch export; until then the freeze-then-existence-check is the faithful gate.
- **Compared against public HELM.** The grouping manifest's `official_public_index`
  source pairs each local run with its official counterpart by logical run key.
  Comment that source out in
  [`olmo-models-combined.yaml`](../../configs/virtual-experiments/olmo-models-combined.yaml)
  to make the report local-only.

## Output layout

```
$AUDIT_STORE_ROOT/virtual-experiments/olmo-models-combined/
├── manifest.yaml
├── indexes/                 # synthesized index slice (rows re-stamped olmo-models-combined)
├── analysis/
│   ├── core-reports/<one per model packet>/
│   └── experiment_summary.{json,csv,txt}
└── reports/aggregate-summary/   # the grouped publication surface
```

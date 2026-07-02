# OLMo models — combined multi-model fan-out

Sibling of [`../olmo_models`](../olmo_models). Same research goal (faithful
from-spec reproduction of the AllenAI OLMo HELM runs), same serving / leasing /
containerization, **different scheduling**: instead of running the presets one at
a time in a serial bash loop, this runbook runs a **single multi-deployment
preset** — `allenai-olmo-combined` — exported with `--freeze-rel-paths` and
scheduled with `eval-audit-run --tmux-workers N`, so **five OLMo models fan out
across GPUs under one schedule**.

See [`docs/planning/olmo-multi-model-from-spec-plan.md`](../../docs/planning/olmo-multi-model-from-spec-plan.md)
§4.4/§4.7 for the design.

## What's different from `../olmo_models`

| | `../olmo_models` (single-model) | `olmo_models_combined` (this) |
|---|---|---|
| Presets | seven single-model (`OLMO_TARGETS` loop) | one multi-deployment (`allenai-olmo-combined`) |
| Models | six (olmo-7b split into `-mmlu`/`-lite`) | **five** (olmo-7b excluded — see below) |
| Scheduling | serial: one `eval-audit-run` per preset | **one** `eval-audit-run` over the combined manifest |
| Concurrency | one model served at a time | `--tmux-workers N` → N concurrent leased runs |
| Export | `--from-spec` (discovery replay) | `--from-spec --freeze-rel-paths` (exact-path replay) |
| Deployment target | one manifest-level rewrite target | **per-run** inline `model_deployment=<local>` |
| Experiments | seven (`audit-<preset>-full`) | **one** (`audit-allenai-olmo-combined-full`) |
| Grouping manifest | [`olmo-models.yaml`](../../configs/virtual-experiments/olmo-models.yaml) | [`olmo-models-combined.yaml`](../../configs/virtual-experiments/olmo-models-combined.yaml) |

**Why five models, not six.** `allenai/olmo-7b` was run by HELM under two suites
(`/mmlu` and `/lite`) whose per-subject MMLU dirs are token-subsets of each other,
so it is **ambiguous under the shared parent root** this bundle freezes against. It
keeps the narrow per-suite roots and stays in the single-model runbook. The other
five (`olmo-1.7-7b` + the four OLMo-2 / OLMoE instruct models) all resolve 1:1
under `/data/crfm-helm-public`, so they share one root and one bundle cleanly.

**Why `--freeze-rel-paths` is mandatory here.** A multi-deployment bundle has no
single manifest-level rewrite target — each run needs its own. The exact-path
exporter freezes, per run, the official rel-path + the **local** rewrite target +
the lease endpoint (from the inline `model_deployment=vllm/allenai-<model>` token
each run_entry carries). The plain `--from-spec` *discovery* path can't express
that, so it is not used here. The materializer applies the substitutions host-side
before kwdagger; no in-container token-subset discovery runs.

## Shared setup (inherited, not duplicated)

`_lib.sh` **sources `../olmo_models/_lib.sh`** and overrides only the
combined-specific bits (the grouping manifest, the preset name, the five
endpoints, the fan-out width). Everything else — `INFER_STACK_CONFIG_DIR` (the
shipped OLMo catalog with the `<model>-single` endpoints), `INFER_STACK_DATA_DIR`
resolution, `INFER_STACK_ALLOWED_GPUS`, `OLMO_CONTAINER_IMAGE`, HuggingFace token
resolution, and the `EVAL_AUDIT_*` group-strip conventions — is inherited verbatim
(one source of truth, no drift). The serving endpoints and container image are the
**same** ones the single-model runbook uses, so no separate `config/` or `docker/`
is shipped here.

`06_check_hf_auth.sh` and `07_check_container_image.sh` are target-independent and
**delegate** to the sibling's implementations.

## Steps

```bash
../olmo_models/docker/build.sh   # build eval-audit-helm-runner:dev (shared image; containerization is ON)
./00_check_env.sh                # eval-audit-check-env
./05_check_profiles.sh           # verify the five <model>-single endpoints are defined
./06_check_hf_auth.sh            # verify a HuggingFace token (gated gpqa dataset needs it)
./07_check_container_image.sh    # verify docker + the pinned image (required)
./08_check_discovery.sh          # freeze the bundle (resolves 1:1 or fails) + validate every frozen run_spec.json exists
./10_run_smoke.sh                # smoke: gc -> gateway bootstrap -> export --freeze-rel-paths -> run smoke --lease --tmux-workers N
./15_run_full.sh                 # full reproducibility batch (same, full manifest)
./20_index_local.sh              # eval-audit-index -> audit_results_index.csv (verifies the combined-full run dir)
./30_compose.sh                  # build the virtual experiment from the combined-full run
./40_build_summary.sh            # aggregate publication surface
```

The smoke preflight (`10`) is optional once you trust the path — `15` is the run
that feeds `20`/`30`/`40`.

## Knobs (env vars)

Everything the single-model runbook documents applies (inherited from its
`_lib.sh`: `OLMO_CONTAINER_IMAGE`, `AUDIT_STORE_ROOT`, `AUDIT_RESULTS_ROOT`,
`INFER_STACK_*`, `LITELLM_PORT`, `OLMO_FORCE_RERUN`, …). Combined-specific:

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
  source pairs each local run with its official counterpart by logical run key
  (mirroring `olmo-models.yaml`). Comment that source out in
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

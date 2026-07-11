# Qwen text models — combined multi-model fan-out

Faithful from-spec reproduction of the public HELM **Qwen text** runs, scheduled as
a **single multi-model fan-out**: instead of running the presets one at a time in a
serial loop, this runbook runs a **single multi-deployment preset** —
`qwen-combined` — exported with `--freeze-rel-paths` and scheduled with
`eval-audit-run --tmux-workers N`, so **eight Qwen models fan out across GPUs under
one schedule**. The grouped report covers all eight models.

This is the direct port of
[`reproduce/olmo_models_combined/`](../olmo_models_combined/) and its
`allenai-olmo-combined` preset. See
[`docs/planning/qwen-models-combined-fanout-plan.md`](../../docs/planning/qwen-models-combined-fanout-plan.md)
for the design.

## Scope — the 8 models

| # | HELM model id | official protocol | local HF weights (serve) | tp | rows |
|---|---|---|---|---|---|
| 1 | `qwen/qwen1.5-7b`                  | completions (base) | `Qwen/Qwen1.5-7B`          | 1 | 85 |
| 2 | `qwen/qwen1.5-14b`                | completions (base) | `Qwen/Qwen1.5-14B`         | 1 | 85 |
| 3 | `qwen/qwen1.5-32b`                | completions (base) | `Qwen/Qwen1.5-32B`         | 2 | 85 |
| 4 | `qwen/qwen1.5-72b`                | completions (base) | `Qwen/Qwen1.5-72B`         | 2 | 85 |
| 5 | `qwen/qwen1.5-110b-chat`          | chat               | `Qwen/Qwen1.5-110B-Chat`   | 4 | 85 |
| 6 | `qwen/qwen2-72b-instruct`        | chat               | `Qwen/Qwen2-72B-Instruct`  | 2 | 86 |
| 7 | `qwen/qwen2.5-7b-instruct-turbo` | chat               | `Qwen/Qwen2.5-7B-Instruct`  | 1 | 132 |
| 8 | `qwen/qwen2.5-72b-instruct-turbo`| chat               | `Qwen/Qwen2.5-72B-Instruct` | 2 | 132 |

**775 run_entries total.** VL/Omni/Audio Qwen ids are out of scope
(packaging-incompatible); `qwen/qwen3.5-9b` is out of scope (no public run to
replay). The **protocol** column is confirmed from HELM's `model_deployments.yaml`:
base Qwen1.5 were served by `TogetherClient` (completions), the rest by
`TogetherChatClient` (chat). The catalog `protocol:` and each preset's
`protocol_mode:` agree with this.

## Design at a glance

- **Presets** — one multi-deployment preset (`qwen-combined`) covering all eight
  models, composed in `presets.py` from the eight single-model from-spec members in
  `preset_configs.yaml`. Every member's `run_entries` are **generated** from
  `official_public_index.csv` filtered to the reproducible whitelist (classic core +
  ungated-judge capabilities), model token normalized `qwen_<id>` → `qwen/<id>`,
  dir-existence verified against `/data/crfm-helm-public`.
- **One bundle; no splits** — all eight resolve **1:1** under the shared parent root
  (verified: 775 whitelisted run dirs, all distinct basenames, 0 AMBIGUOUS). Model
  size (110B tp=4, the 72Bs tp=2) only affects scheduling throughput, never
  membership. The `08` freeze is the authoritative gate; if a corpus refresh ever
  introduces an ambiguity it hard-fails and that member rides as its own suite (the
  olmo-7b pattern, via `QWEN_COMBINED_EXTRA_PRESETS` — empty by default).
- **Scheduling** — a single `eval-audit-run` over the combined manifest, with
  `--tmux-workers N` driving N concurrent leased runs.
- **Export** — `--from-spec --freeze-rel-paths` (exact-path replay), with a
  **per-run** inline `model_deployment=vllm/<model>` rewrite target.
- **Experiment** — one (`audit-qwen-combined-full`), grouped via
  [`qwen-models-combined.yaml`](../../configs/virtual-experiments/qwen-models-combined.yaml).

**Why `--freeze-rel-paths` is mandatory here.** A multi-deployment bundle has no
single manifest-level rewrite target — each run needs its own. The exact-path
exporter freezes, per run, the official rel-path + the **local** rewrite target +
the lease endpoint (from the inline `model_deployment=vllm/<model>` token each
run_entry carries). The plain `--from-spec` *discovery* path can't express that, so
it is not used here.

**Why from-spec matters for Qwen (the headline benefit).** The reproducibility
report has `qwen/qwen2.5-7b-instruct-turbo` at **2/38 recipe-clean**
(`execution_spec_drift`) purely because the old run-entry audit didn't replicate the
public **prompt prefix**. From-spec replay pulls the official `run_spec.json` —
prefix intact — so this fan-out is the mechanism that closes that gap. Keep
qwen2.5-7b in the first executed batch even if the run is otherwise scoped small.

## Setup (self-contained)

This runbook ships everything it needs — no dependency on a sibling runbook:

- [`config/infer_stack/`](config/infer_stack/) — the shipped Qwen catalog with the
  eight `<model>-single` endpoints (`catalog.yaml`; per-model `tensor_parallel_size`
  and `protocol`) + durable leasing settings (`settings.yaml`). `_lib.sh` points
  `INFER_STACK_CONFIG_DIR` here.
- `_lib.sh` — resolves the repo root, the store/results roots, the docker-mountable
  `INFER_STACK_DATA_DIR` (env > `settings.yaml` pin > big-disk default),
  `QWEN_CONTAINER_IMAGE`, HuggingFace-token resolution, and the `EVAL_AUDIT_*`
  group-strip conventions, then defines the combined-specific bits (grouping
  manifest, preset name, the eight endpoints, fan-out width).
- `06_check_hf_auth.sh` / `07_check_container_image.sh` — self-contained preflights
  (the container image is built at the repo root via `./docker/build.sh`).

## Steps

```bash
../../docker/build.sh            # build eval-audit-helm-runner:dev (containerization is ON)
./00_check_env.sh                # eval-audit-check-env
./05_check_profiles.sh           # verify the eight <model>-single endpoints are defined
./06_check_hf_auth.sh            # verify a HuggingFace token (gated gpqa on the turbo models needs it)
./07_check_container_image.sh    # verify docker + the pinned image (required)
./08_check_discovery.sh          # freeze the bundle (resolves 1:1 or fails) + validate every frozen run_spec.json exists
./10_run_smoke.sh                # smoke: gc -> gateway bootstrap -> export --freeze-rel-paths -> run smoke --lease --tmux-workers N
./15_run_full.sh                 # full reproducibility batch (same, full manifest)
./20_index_local.sh              # eval-audit-index -> audit_results_index.csv (verifies the combined-full run dir)
./30_compose.sh                  # build the virtual experiment from the full run
./40_build_summary.sh            # aggregate publication surface
```

The smoke preflight (`10`) is optional once you trust the path — `15` is the run
that feeds `20`/`30`/`40`. `08` is CPU-only and needs no GPU/serving, so run it on
the analysis host first.

## Knobs (env vars)

Base setup (from `_lib.sh`): `QWEN_CONTAINER_IMAGE`, `AUDIT_STORE_ROOT`,
`AUDIT_RESULTS_ROOT`, `INFER_STACK_*`, `LITELLM_PORT`, `QWEN_FORCE_RERUN`, ….
Combined-specific:

- `QWEN_TMUX_WORKERS` (default `4`) — fan-out width: the max concurrent HELM client
  runs cmd_queue drives. Each run self-leases its model's GPU; infer-stack co-hosts
  what fits on `INFER_STACK_ALLOWED_GPUS` and **queues** the rest, so this is not a
  GPU count and may exceed the number of cards. The 72Bs (`tp=2`) and the 110B
  (`tp=4`) can't co-host, so they serialize against the smaller models. On a 4×80GB
  node the 110B blocks the whole node while it runs — schedule it first or last.
- `QWEN_COMBINED_VEXP_MANIFEST` — override the grouping manifest path.
- `QWEN_FORCE_RERUN` — default **on** in `10_run_smoke.sh` (cheap preflight),
  **off** in `15_run_full.sh` (expensive); clears the prior result dir so kwdagger's
  `skip_existing` re-executes.
- **Scale knobs.** To prove the path on a subset first, scope the run to a couple of
  members (e.g. qwen2.5-7b + qwen1.5-7b/14b) by editing the combined preset's member
  list, or trim `max_eval_instances`. Then widen.

## Status / caveats

- **Preset + catalog + freeze + fan-out are wired here; the GPU end-to-end run is
  the remaining verification.** `08_check_discovery.sh` proves the freeze resolves
  1:1 against the corpus (CPU-only); the first `15_run_full.sh` on GPUs confirms
  models co-host / serialize under leasing and that the produced runs record
  `model_deployment=vllm/qwen-<model>`.
- **T1/T2 confirmed / to-verify.** Protocol (T1) is confirmed from HELM's
  `model_deployments.yaml`. HF weight ids (T2, `source:` in `catalog.yaml`) carry a
  `# verify` caveat — confirm each repo on the HF Hub before a real run (base-vs-chat
  and local-Instruct-vs-Together-turbo are the easy places to pick the wrong repo).
- **Compared against public HELM.** The grouping manifest's `official_public_index`
  source pairs each local run with its official counterpart by logical run key.
  Comment that source out in
  [`qwen-models-combined.yaml`](../../configs/virtual-experiments/qwen-models-combined.yaml)
  to make the report local-only.

## Output layout

```
$AUDIT_STORE_ROOT/virtual-experiments/qwen-models-combined/
├── manifest.yaml
├── indexes/                 # synthesized index slice (rows re-stamped qwen-models-combined)
├── analysis/
│   ├── core-reports/<one per model packet>/
│   └── experiment_summary.{json,csv,txt}
└── reports/aggregate-summary/   # the grouped publication surface
```

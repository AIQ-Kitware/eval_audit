# Qwen3.5-9B-Base — leased infer-stack serving + HELM smoke (extension, not reproduction)

First **net-new** benchmark path for a model with **no public HELM run to
replay**: `Qwen/Qwen3.5-9B-Base` (Qwen3.5 Small series, released 2026-03-02),
served via **infer-stack** (vLLM behind LiteLLM, GPU acquired per-run via
`--lease`) and executed through the containerized `eval-audit-run` pipeline —
the same serving/leasing shape as
[`qwen_models_combined`](../qwen_models_combined/), with exactly one axis
flipped: **compute instead of reproduce** (no `--from-spec`, no corpus freeze,
`precomputed_root: null`). This is the
[`qwen36-core-new-results-plan.md`](../../docs/planning/qwen36-core-new-results-plan.md)
design, single non-thinking member (a base model has no thinking toggle),
smoke-scoped first.

**Run these scripts on the GPU host (yardrat), not the analysis VM.**

## How the new model id is registered (no HELM edit)

`qwen/qwen3.5-9b-base` is unknown to upstream HELM. Its registration ships as
**registry sidecars** — `model_metadata.yaml` + `tokenizer_configs.yaml` in
[`configs/local_models/qwen35_9b_vllm/`](../../configs/local_models/qwen35_9b_vllm/) —
declared by the preset (`model_metadata_fpath` / `tokenizer_configs_fpath` in
`PRESET_CONFIGS['qwen35_9b_base_vllm']`), forwarded into the exported bundle
manifests, mounted into the runner container, and copied into `prod_env` where
HELM's own `register_configs_from_directory` merges them. No HELM-source edit,
no runner-image rebuild per new model. The `model_deployments.yaml` half is
**generated** by the bundle exporter from the preset + catalog (nothing
hand-written).

## Why base ⇒ completions (not chat)

`Qwen/Qwen3.5-9B-Base` is pre-trained only: no chat template. The preset pins
`protocol_mode: completions` and the catalog endpoint `protocol: completions`
— serving a base model as chat would template every prompt into garbage (the
OLMo-7B failure mode), and infer-stack's readiness probe would poll
`/chat/completions`, which never answers. This matches how the base Qwen1.5
officials were served, making **base-3.5 vs base-1.5** the clean comparison.

## Known risks (check these first when something fails)

- **vLLM architecture support.** Qwen3.5 is a Gated-DeltaNet + sparse-MoE
  hybrid — a new architecture. If the serve fails at model load with an
  unrecognized-architecture error, upgrade vLLM in the infer-stack serving
  container before debugging anything else.
- **Turing precision.** The RTX 8000 (sm_75) has no native bf16; the catalog
  pins `dtype: float16` so the downcast is an explicit, recorded substrate
  fact. On Ampere+ hosts you may serve native bf16 — record it if you do.

## Steps

```bash
../../docker/build.sh          # once: build eval-audit-helm-runner:dev
./00_check_env.sh              # eval-audit-check-env (+ leasing env resolution)
./05_check_registration.sh     # CPU-only: preset <-> sidecar registration consistent
./06_check_profiles.sh         # endpoint qwen3-5-9b-base-single in the active catalog
./07_check_container_image.sh  # docker + pinned image + stale-digest probe
./10_run_smoke.sh              # gc -> gateway bootstrap -> export (compute) -> run --lease
./15_run_full.sh               # full manifest (PLACEHOLDER scope — see below)
./40_verify_artifacts.sh <run-dir>   # run_spec.json records the base model + local deployment
```

`00`–`07` are CPU-safe preflights. `10` is the end-to-end smoke: the scheduled
HELM run self-acquires the model's GPU lease (`acquire --queue`), infer-stack
brings up vLLM + LiteLLM, the containerized HELM client (no GPU,
`--network host`) drives `mmlu:anatomy` + `boolq` at 5 instances each, and
`reclaim: stop` frees the GPU on release.

## Success criteria

1. `05`/`06`/`07` all print `OK`.
2. `10` leases the endpoint, vLLM loads the model (the architecture-support
   gate), and both smoke runs land `stats.json` + `per_instance_stats.json`.
3. `40` confirms `adapter_spec.model=qwen/qwen3.5-9b-base` and
   `model_deployment=vllm/qwen3.5-9b-base-local`.

## Knobs (env vars)

- `QWEN35_CONTAINER_IMAGE` (default `eval-audit-helm-runner:dev`)
- `QWEN35_TMUX_WORKERS` (default `1` — one model, one endpoint)
- `QWEN35_FORCE_RERUN` (default on for smoke, off for full)
- `INFER_STACK_CONFIG_DIR` / `INFER_STACK_DATA_DIR` / `INFER_STACK_ALLOWED_GPUS`
  — standard infer-stack knobs; the shipped config lives in
  [`config/infer_stack/`](config/infer_stack/). On yardrat pin serving to the
  48GB card with `INFER_STACK_ALLOWED_GPUS=0` if GPU 1 (16GB) grabs the lease.
- `AUDIT_STORE_ROOT` / `AUDIT_RESULTS_ROOT` (defaults under `/data`)

## What comes next (not this runbook)

The `full_manifest` is currently a **placeholder** (the smoke pair at 1000
instances). The real breadth target is the authored ~85-entry classic/Lite
core (qwen36 plan §6.1) with the model token swapped to
`qwen/qwen3.5-9b-base`, grouped as a **local-only** virtual experiment (no
official side exists) and reported beside the reproduced Qwen1.5/2/2.5
numbers. Later arcs: post-trained Qwen3.5-9B (chat protocol), and LoRA
fine-tunes such as `Achilles1089/fable-coder-35B-A3B` (35B-A3B MoE — needs
quantization to fit 48GB; separate serving design).

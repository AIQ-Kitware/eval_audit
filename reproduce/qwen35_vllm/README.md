# Qwen3.5-9B-Base — vLLM serving + HELM smoke (extension, not reproduction)

First **net-new** benchmark path for a model that has **no public HELM run to
replay**: `Qwen/Qwen3.5-9B-Base` (Qwen3.5 Small series, released 2026-03-02),
served locally through vLLM and driven by the same `eval-audit-run` manifest
machinery the reproduction runbooks use. This is the smoke-scope proof of the
serving + run + artifact path; the full classic/Lite-core grid (comparable to
[`qwen_models_combined`](../qwen_models_combined/)) comes after this passes —
see [`docs/planning/qwen36-core-new-results-plan.md`](../../docs/planning/qwen36-core-new-results-plan.md)
for the compute-fan-out design this feeds into.

**Run these scripts on the GPU host (yardrat parent box), not the analysis
VM.** The repo is mirrored onto yardrat via a virtiofs mount, so the same
paths work there. GPU 0 = Quadro RTX 8000 (48GB, the target card); GPU 1 =
RTX 5000 (16GB, too small).

## Why base ⇒ completions (not chat)

`Qwen/Qwen3.5-9B-Base` is the pre-trained model: no chat template, no thinking
mode. The deployment therefore uses `VLLMClient` (legacy completions), the same
protocol the base Qwen1.5 officials used — which makes **base-3.5 vs base-1.5**
the clean apples-to-apples comparison. (`Qwen/Qwen3.5-9B` without `-Base` is the
post-trained variant; comparing that against the Qwen2.5 instruct-turbo pair is
a separate, chat-protocol exercise.)

## Prerequisites (on the GPU host)

- A venv with `eval_audit` (`pip install -e .`), **helm installed from the
  vendored submodule** (`pip install -e submodules/helm`) — the
  `qwen/qwen3.5-9b-base` registration lives there, a pip `crfm-helm` will not
  have it (`05` checks exactly this) — and a **recent vLLM**.
- **vLLM architecture support is the #1 risk.** Qwen3.5 uses a hybrid
  Gated-DeltaNet + sparse-MoE architecture (with a vision encoder in the
  packaging); older vLLM cannot load it. If `10_start_vllm.sh` fails at model
  load with an unrecognized-architecture error, upgrade vLLM before debugging
  anything else.
- **Precision note (Turing).** The RTX 8000 is sm_75 with no native bf16;
  Qwen3.5 ships bf16 weights. `start_vllm.sh` pins `--dtype float16`
  (overridable via `DTYPE=`) so the downcast is an explicit, recorded choice —
  exactly the kind of substrate fact this project exists to pin down. Record it
  wherever these numbers are cited.
- ~20GB free on GPU 0; HF cache space for the ~10B download; no HF token needed
  (model and both smoke datasets are ungated).

## Steps

```bash
./00_check_env.sh          # eval-audit-check-env
./05_check_registration.sh # CPU-only: venv helm resolves qwen/qwen3.5-9b-base (+tokenizer)
./10_start_vllm.sh         # terminal 1: vLLM serves Qwen/Qwen3.5-9B-Base on :8000 (GPU 0)
./15_validate_vllm.sh      # terminal 2: one completions-API request returns text
./20_preview.sh            # dry-run the smoke manifest (prints the helm-run plan)
./30_run.sh                # execute: mmlu:anatomy + boolq, 5 instances each
./40_verify_artifacts.sh <run-dir>  # asserts run_spec.json records the base model + deployment
```

`00`/`05`/`20` are CPU-safe. `10` holds the GPU until you stop it; `30` runs
against the live server. Find `<run-dir>` for `40` under the experiment output
(`.../audit-qwen35-9b-base-vllm-smoke/.../runs/audit-qwen35-vllm-smoke/`).

## Success criteria

1. `05` prints the vendored-helm path and `OK`.
2. `15` prints a non-empty completion (settles the vLLM-arch-support risk).
3. `30` lands both runs with `stats.json` + `per_instance_stats.json`.
4. `40` confirms `adapter_spec.model=qwen/qwen3.5-9b-base` and
   `model_deployment=vllm/qwen3.5-9b-base-local`.

## Knobs

`MODEL_NAME`, `PORT`, `TP_SIZE`, `MAX_MODEL_LEN`, `GPU_MEM_UTIL`, `DTYPE`,
`CUDA_VISIBLE_DEVICES` — all env overrides on `10_start_vllm.sh` (defaults:
`Qwen/Qwen3.5-9B-Base`, `:8000`, tp1, 32768, 0.9, `float16`, GPU `0`).

## What comes next (not this runbook)

Widening smoke → the authored ~85-entry classic/Lite core (the
`qwen36-core-new-results-plan.md` compute fan-out, single non-thinking mode
since base models have no thinking toggle), grouped as a **local-only** virtual
experiment (no `official_public_index` source — nothing official to pair
against) and reported beside the reproduced Qwen1.5/2/2.5 numbers. Later arcs:
post-trained Qwen3.5 (chat protocol), and LoRA fine-tunes such as
`Achilles1089/fable-coder-35B-A3B` (35B-A3B MoE — needs quantization to fit
48GB; separate serving design).

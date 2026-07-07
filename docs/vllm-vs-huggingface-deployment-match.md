# Matching a vLLM deployment to a HuggingFace deployment

When we reproduce a public HELM run locally, we serve the model with **vLLM**
(via infer-stack), but many public HELM runs were produced by HELM's
`HuggingFaceClient` — i.e. `transformers.generate()` on a local GPU. "Reproducing
the recipe" therefore includes making a vLLM generation match a HF `generate()`
generation. This note inventories the knobs that separate the two engines, which
the deployment-match tool
([`dev/tools/deployment_match/`](../dev/tools/deployment_match/)) already sweeps,
and which it currently ignores.

It is a companion to the tool's design plan
([`docs/planning/deployment-match-search-plan.md`](planning/deployment-match-search-plan.md))
and README ([`dev/tools/deployment_match/README.md`](../dev/tools/deployment_match/README.md)).

## First: is HF even the right target?

The tool resolves `official_client_class` from HELM's `model_deployments.yaml`
([`registry.py:resolve_official_deployment`](../dev/tools/deployment_match/registry.py))
but **does not branch on it**. That field is exactly what tells you whether
matching-to-HF is the right goal:

- `HuggingFaceClient` → official was `transformers.generate()` on a local GPU →
  the knobs below apply. (E.g. `huggingface/olmoe-…`, the `.sh` default.)
- `TogetherClient` / other hosted → official was *someone else's* accelerated
  service (vLLM/TGI-like) → you are matching one fast engine to another, and
  "match HF-eager" is the wrong target.

Recommendation: have the grid read `official_client_class` and only chase the
HF-execution knobs (Tier A below) when the official actually was
`HuggingFaceClient`.

## What the grid varies today

Defined in [`grid.py` `DEFAULT_AXES`](../dev/tools/deployment_match/grid.py):

| Tier | Knob | Values swept |
|---|---|---|
| serve-time | `dtype` | auto, float16, bfloat16, float32 |
| serve-time | `tokenizer` | model default (+ sibling if EOS-append detected) |
| serve-time | `max_model_len` | auto (`min(official+1, model ceiling)`) |
| serve-time | `trust_remote_code` | False |
| request-time | `add_special_tokens` | True, False |
| request-time | `protocol` | auto (completions / chat) |

Fixed runtime ([`grid.py` `DEFAULT_RUNTIME`](../dev/tools/deployment_match/grid.py)):
`gpu_memory_utilization=0.85`, `max_num_batched_tokens=2048`, `max_num_seqs=16`,
`enforce_eager=True`.

This covers the *coarse* recipe. The knobs that actually separate a vLLM
generation from a HF `generate()` — engine-execution and prompt-construction — are
mostly held fixed or ignored.

## Tier A — engine numerics (serve-time; the biggest vLLM↔HF gap)

These change the **reduction order** of the math, so even greedy (temperature=0)
decoding diverges from HF.

| Knob | Why it matters | Current state |
|---|---|---|
| **Attention backend** (`VLLM_ATTENTION_BACKEND`: FLASH_ATTN / FLASHINFER / XFORMERS / TORCH_SDPA) | The single largest numeric divergence. HF's `attn_implementation` (eager/sdpa/flash_attention_2) computes attention differently than vLLM's PagedAttention kernels. Which one reproduces HF is **empirical** — a backend *name* match doesn't imply a kernel-numeric match, and HF's own default is version/model-dependent. | **Implemented** — infer-stack `runtime.attention_backend` endpoint option; `hf-match` **sweeps** `{None (vLLM default), FLASH_ATTN, XFORMERS, TORCH_SDPA}` and lets the scorer decide |
| **Chunked prefill** (`enable_chunked_prefill`) | vLLM V1 defaults it ON; it splits prefill across steps → different logits than HF's single-pass prefill. | **Not controlled** — force **off** to match HF |
| **Prefix caching** (`enable_prefix_caching`) | vLLM V1 defaults ON; reused KV blocks perturb numerics and make output order-dependent. HF has none. | **Not controlled** — force **off** |
| **`tensor_parallel_size`** | TP>1 changes all-reduce order → differs from single-GPU HF. | infer-stack picks GPUs; **pin TP=1** if official was single-GPU HF |
| **`max_num_seqs`** / batch invariance | vLLM kernels are *not* batch-invariant — the same prompt yields different logits depending on batch composition (the "defeating nondeterminism in LLM inference" result). HF ran one sequence at a time. | Fixed at 16; **max_num_seqs=1** gives a stricter (slower) match |
| **TF32 / matmul precision** (`torch.backends.cuda.matmul.allow_tf32`, cuDNN) | Especially for `dtype=float32`, vLLM vs HF may disagree on whether matmuls run in true fp32 or TF32. | Environment-level; not represented |
| **`kv_cache_dtype`** | fp8 KV would diverge hard. | Default `auto` ✓ — just never set fp8 |

`enforce_eager=True` already disables CUDA-graph capture — that one is correctly
handled.

## Tier B — tokenization & prompt construction

| Knob | Why it matters |
|---|---|
| **`tokenizer_mode`** (fast vs slow) | If HELM's HF client used a slow tokenizer (`use_fast=False`), fast-vs-slow can differ on edge tokens. The grid varies tokenizer *identity* but not fast/slow mode. |
| **Chat-template exactness** (instruct models) | The `protocol` axis {completions, chat} is coarse. The real divergence is *which* template string, `add_generation_prompt`, and whether BOS is injected. HELM often renders the prompt itself and sends it via `completions`; sending the same text through vLLM's `chat` path re-templates it and silently diverges. |
| **`skip_special_tokens` / `spaces_between_special_tokens`** (detokenization) | These change the output *text string that gets scored*, independent of token ids — a source of scoring mismatches even when generation matched. |

## Tier C — sampling replay (request-time; a concrete probe gap)

[`probe.py` `_recipe_body`](../dev/tools/deployment_match/probe.py) forwards only
`max_tokens`, `temperature`, `stop`, `top_p`. It does **not** replay `top_k`,
`min_p`, `repetition/frequency/presence_penalty`, `seed`, or `n`/`best_of` from
the official recipe. Two consequences:

- For non-greedy scenarios, unreplayed penalties/top_k change the distribution.
- **Stop-sequence semantics differ**: vLLM applies `stop` at detokenization; HF
  via `StoppingCriteria` on token ids → different truncation point → different
  scored text even when the raw generation matched. Greedy tie-breaking on equal
  logits can also differ.

## Does the winner get emitted as a servable config?

Yes, in two forms:

- **`best_deployment.yaml`** ([`report.py` `best_deployment`](../dev/tools/deployment_match/report.py)) —
  a knob *summary* (serve-time vs request-time, native vs probe-only). A report
  artifact, not directly servable.
- **`serve/catalog.yaml`** ([`confirm.py` `_winner_catalog`](../dev/tools/deployment_match/confirm.py)) —
  a real one-endpoint infer-stack catalog you can `infer-stack acquire`. `auto`
  chains through confirm by default.

Two caveats that matter for a faithful HF match:

1. **The catalog only captures serve-time knobs.** A winning **request-time** knob
   (classically `add_special_tokens=False`) is *not* in `catalog.yaml`; the plan
   flags it and tells you to land it the HELM-path-native way (a `--tokenizer`
   sibling override or a `VLLMClient` patch).
2. **The runtime is hardcoded, not carried from the winner.** `_winner_catalog`
   bakes in `gpu_memory_utilization=0.85`, `max_num_batched_tokens=2048`,
   `max_num_seqs=16` regardless of the winning cell. That is exactly where the
   Tier-A determinism knobs would need to live — right now they would be silently
   absent from the generated catalog even if added as grid axes.

## Minimal example: the `hf-match` profile (IMPLEMENTED)

The smallest fold-in that turns the existing tool into a vLLM↔HF matcher is a
built-in **grid profile** that pins the Tier-A determinism knobs and lets the
tool's existing oracle path (score vs the official completions) do the rest — so
it only applies when the official run was itself a `HuggingFaceClient` run.

Selected with `--profile hf-match` on any grid-building subcommand
(`dry-run` / `grid` / `auto`), or via `DM_PROFILE=hf-match` on the runbook:

```bash
# CPU dry-run — inspect the recipes/serve lines first (no GPU):
PYTHONPATH=submodules/infer_stack .venv/bin/python \
  dev/tools/deployment_match/cli.py dry-run --profile hf-match \
  --run <HuggingFaceClient-run-dir> --n 12 --out /tmp/dm-hf

# GPU host, end-to-end (the default OLMoE ifeval run IS a HuggingFaceClient run):
DM_PROFILE=hf-match \
  reproduce/olmo_models_combined/deployment_match/run_deployment_match.sh
```

What it does ([`grid.py` `BUILTIN_PROFILES["hf-match"]`](../dev/tools/deployment_match/grid.py)):

- Pins the **determinism knobs** as fixed constants — `enforce_eager=True`,
  `enable_chunked_prefill=False`, `enable_prefix_caching=False`, `max_num_seqs=1`.
  These are *confounder-removal*, not value-matching: HF has no CUDA-graphs,
  chunked prefill, prefix cache, or in-flight batching, so disabling them moves
  vLLM toward HF regardless of model — high-confidence, so not swept. They render
  as `--enforce-eager --no-enable-chunked-prefill --no-enable-prefix-caching
  --max-num-seqs=1`. infer-stack already emits `--tensor-parallel-size=1` for a
  single GPU, and `kv_cache_dtype` stays `auto`.
- **Sweeps** `attention_backend` over `{None (vLLM default), FLASH_ATTN, XFORMERS,
  TORCH_SDPA}`. Unlike the determinism knobs, the HF-matching backend is *not*
  known on theory (a backend name-match doesn't imply kernel-numeric equality, and
  vLLM's `TORCH_SDPA` may be a poor GPU path on some versions), so the scorer
  decides empirically — consistent with the tool's "sweep, don't prune on theory"
  design. A backend that can't serve just scores `NO_DATA` and drops out. Each
  value is a separate `vllm serve`, so the profile raises the cell `cap` to 128.
- Leaves `dtype`, `tokenizer`, and `add_special_tokens` as the other search axes —
  the recipe knobs HELM itself could vary. Narrow the backend set (or any axis)
  with `--grid {axes: {attention_backend: [...]}}`.
- [`cli.py` `_warn_if_not_hf_client`](../dev/tools/deployment_match/cli.py) warns
  when the resolved `official_client_class` is not a `HuggingFaceClient` (or is
  unknown), so the profile isn't silently used against a hosted-API official.

The profile merges *under* any `--grid` YAML, so a hand-written spec still
overrides it per key.

## The attention-backend env plumbing (IMPLEMENTED in infer-stack)

Because vLLM selects the attention backend via the `VLLM_ATTENTION_BACKEND`
**env var** (not a `vllm serve` flag), exposing it needed a small change in
infer-stack, so it is now a first-class endpoint option usable by any catalog:

- `runtime.attention_backend` on a vLLM endpoint is carried into
  `vllm_service_dict` and rendered as the container env var — the compose backend
  ([`leasing/compose.py` `_vllm_service`](../submodules/infer_stack/infer_stack/leasing/compose.py)
  → `environment`) and the kubeai backend
  ([`backends/kubeai.py`](../submodules/infer_stack/infer_stack/backends/kubeai.py)
  → the Model CR's `env` map) both set it, never as a CLI arg.
- It is part of the vLLM **compat key**
  ([`leasing/models.py` `vllm_structural`](../submodules/infer_stack/infer_stack/leasing/models.py)),
  so two endpoints differing only in backend are distinct deployments and never
  coalesce onto one process.

The deployment-match grid drives it through the `attention_backend` axis /
`ServeRecipe`, and `confirm._winner_catalog` propagates a winning backend into
the confirm catalog.

## Pruning the grid beforehand: feasibility vs. relevance

The grid is a sweep on purpose — the tool exists *because* we were wrong pruning
on theory before (the OLMo-7B fp16 mis-diagnosis). So "exclude settings
beforehand" splits into two kinds, treated oppositely:

- **Feasibility pruning (safe — do it).** A recipe that *physically can't serve*
  on this environment teaches nothing and wastes a full serve cycle. These are
  dropped a priori with a **typed reason**, mirroring the pipeline's Stage-1
  filter pattern (`no-local-helm-deployment`-style). Implemented in
  [`grid.py` `_dtype_infeasible`](../dev/tools/deployment_match/grid.py) as an
  extensible preflight table; `build_grid` records each drop in `Grid.pruned`
  (`{axis, value, reason, n_recipes}`) and a note, and the CLI prints the count.
- **Relevance pruning (risky — keep swept).** "This knob probably won't change the
  output" is exactly what the tool refuses to guess. Those stay in the sweep
  unless the user narrows an axis explicitly (`--dtypes` / `--attention-backends`
  / `--grid`).

### Preflight feasibility rules (current)

- **`float32` on a MoE model → `infeasible:moe-fp32-shared-mem`.** vLLM's Triton
  fused-MoE kernel needs ~128 KiB shared memory/block in fp32 (double bf16's),
  over the ~99 KiB/SM cap on workstation cards like the RTX PRO 6000 Blackwell →
  `triton ... out of resource: shared memory, Required: 131072, Hardware limit:
  101376`; the engine fails to start. No serve-arg shrinks the MoE block sizes.
  Fits on an H100's 228 KiB, so it's GPU-specific — pass `--allow-moe-fp32` (or
  `allow_moe_fp32: true` in `--grid`) to keep it. MoE is detected from the model's
  `config.json` (`num_experts`-family / `*Moe*` architecture), with a name-based
  fallback when config.json isn't cached
  ([`registry.py` `model_is_moe`](../dev/tools/deployment_match/registry.py)).
- *Candidates to add next (same table, same typed-reason shape):* VRAM-OOM from a
  size×dtype estimate vs. the GPU (infer-stack's `model_memory_estimator` already
  exists); an attention backend not compiled into the container image.

### A related caveat (not a feasibility prune)

- **Disabling chunked prefill is only a *warning* on MoE.** hf-match sets
  `--no-enable-chunked-prefill`; vLLM warns "This model does not officially support
  disabling chunked prefill ... may cause the engine to crash or produce incorrect
  outputs." It generally still serves, but treat a MoE hf-match ranking with some
  caution — if a backend's outputs look wrong, re-check with chunked prefill left
  on (`--grid {runtime: {enable_chunked_prefill: true}}`). The scorer's
  `collapse`/`NO_DATA` verdicts catch gross breakage, not subtle numeric drift.

## Still open (not in the minimal example)

- **Full sampling replay — deliberately NOT done.** Replaying the entire official
  `SamplingParameters` in [`probe.py` `_recipe_body`](../dev/tools/deployment_match/probe.py)
  would mean honoring `n`/`best_of` > 1, which multiplies every cell's generation
  cost across the whole grid — too slow for a *cheap ranking* pass. The probe
  stays single-completion greedy (`max_tokens`/`temperature`/`stop`/`top_p`),
  which already reproduces the common HELM case (`temperature=0`, `n=1`); the
  winner is confirmed authoritatively by the full `compare-pair` regardless. (The
  zero-runtime-cost params — `top_k`/penalties/`seed` — could still be forwarded
  if a non-greedy scenario ever needs them, but that is not on by default.)
- ~~Propagate the *full* winning runtime through `confirm._winner_catalog`~~
  **(FIXED).** The confirm catalog now reproduces the winning serving numbers
  instead of grid defaults. The runtime numbers (`max_num_seqs`,
  `gpu_memory_utilization`, `max_num_batched_tokens`) thread `ServeRecipe.runtime`
  → `Cell.serve` (grid.py) → `serve_time_knobs` (report.py) →
  [`_winner_catalog`](../dev/tools/deployment_match/confirm.py) (confirm.py, with
  the old constants kept only as fallback for pre-fix `best_deployment.yaml`). So
  an `hf-match` winner served at `max_num_seqs=1` now confirms at `1`, not the
  batch-non-invariant `16`. (CLI-flag knobs — `--enforce-eager` /
  `--no-enable-chunked-prefill` / `--no-enable-prefix-caching` / `--dtype` — were
  always carried via `extra_args`.)

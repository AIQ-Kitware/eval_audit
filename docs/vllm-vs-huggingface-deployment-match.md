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
([`docs/historical/planning/deployment-match-search-plan.md`](historical/planning/deployment-match-search-plan.md))
and README ([`dev/tools/deployment_match/README.md`](../dev/tools/deployment_match/README.md)).
For the broader inventory of deployment parameters HELM never records (and the
effort to pin an HF revision), see
[`helm-unrecorded-deployment-params.md`](helm-unrecorded-deployment-params.md).

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

> **Update (2026-07-08): the resolver this recommends now exists — but only the
> mechanism is wired, not the routing.** [`eval_audit/hf_inprocess.py`](../eval_audit/hf_inprocess.py)
> resolves `official_client_class` from `model_deployments.yaml`
> (`official_is_huggingface_inprocess`) and builds the matched reproduction entry
> (`hf_inprocess_deployment_entry` — HELM's own official entry with
> `torch_dtype: torch.float32` pinned). The execution path is also built: infer-stack
> can now hold a GPU without serving (`acquire --reserve-gpus N`) and the pipeline can
> run HELM's in-process `HuggingFaceClient` on it (lease bracket + docker-node GPU
> pinning). **What is NOT yet wired is the switch itself:** nothing in the default
> replay path calls the resolver to route a `HuggingFaceClient` official to the HF
> path, so **a public run is still reproduced with vLLM by default**. The remaining
> piece — an HF-in-process manifest producer — plus the full design and the GPU-host
> OLMoE acceptance test are in
> [`docs/planning/huggingface-in-process-reserved-gpu-plan.md`](planning/huggingface-in-process-reserved-gpu-plan.md).
> That plan is about the **main replay pipeline** (reproduce the official *by
> running the same in-process HF client*), which is a different target from this
> tool's job of *matching a vLLM generation to an HF one* — the two are
> complementary, not the same switch.

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
| **Chat-template / protocol** (instruct models) | **`HuggingFaceClient` applies the tokenizer's chat template** when the model has one — `apply_chat_template` auto-infers `True` from `tokenizer.chat_template` ([huggingface_client.py](../submodules/helm/src/helm/clients/huggingface_client.py) `get_prompt`), so for OLMoE-instruct the official model saw `apply_chat_template([{role:user, content: request.prompt}], add_generation_prompt=True)`, tokenized with `add_special_tokens=True`. `scenario_state` stores the **raw** `request.prompt`, not that templated string. So: an instruct model must be reproduced **with** the template (resolved `protocol=chat`, where vLLM re-applies the same template — *not* by sending the raw prompt via completions). A base model (no chat template → `apply_chat_template=False`) is the verbatim-completions case. The per-model protocol resolution already picks correctly; **do not force completions on a chat model.** For byte-exact control, replicate `get_prompt` (apply the template in-tool, send via completions with `add_special_tokens=True`) — an open enhancement. |
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
- **Keeps the per-model resolved protocol** (does *not* force completions). HELM's
  `HuggingFaceClient` applies the tokenizer's chat template for chat models, so an
  instruct model like OLMoE-instruct must be reproduced *with* the template
  (resolved `chat`); a base model uses completions. See the Tier-B row above and
  "Why the OLMoE-ifeval sweep can't localize the difference" below.
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
  101376`; the engine fails to start. No *single-GPU* serve-arg shrinks the MoE
  block sizes. Two ways to keep fp32, both of which lift this prune: (1) a
  big-shared-mem card (H100 228 KiB) — pass `--allow-moe-fp32` (or `allow_moe_fp32:
  true` in `--grid`); or (2) **tensor parallelism** — `--fp32-tensor-parallel-size
  2` (`fp32_tensor_parallel_size: 2` in `--grid`, `DM_FP32_TP=2` on the runbook)
  serves the fp32 recipes with `tensor_parallel_size=2`, sharding the fused-MoE
  kernel across 2 GPUs so each shard's tiles fall under the per-SM cap. TP is not a
  guarantee (the per-shard tile may still exceed the cap on a small card); it's a
  "let it try" — a cell that still OOMs scores `NO_DATA` and drops out. infer-stack
  allocates the extra GPU (`required_gpu_count = tp·pp·dp`) and threads
  `--tensor-parallel-size` through to `vllm serve`; the winning TP is carried into
  the confirm catalog so the full run re-serves the same way. MoE is detected from
  the model's `config.json` (`num_experts`-family / `*Moe*` architecture), with a
  name-based fallback when config.json isn't cached
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

## Chat-template *version* drift: `add_generation_prompt` (OLMoE)

A subtle, real reproducibility trap surfaced on OLMoE-instruct. HELM's `get_prompt`
always *calls* `apply_chat_template(..., add_generation_prompt=True)` — but that
flag only does anything if the **chat template implements it**
(`{% if add_generation_prompt %}<|assistant|>\n{% endif %}`). The OLMoE template
shipped with HELM's **older transformers ignored it**, so `add_generation_prompt`
was effectively **False** — the model was fed the prompt **without** the trailing
`<|assistant|>\n`. Modern transformers/vLLM ship an updated template that honors
it, so the same call now **appends** the suffix → a different prompt → a different
output on every cell. (Empirically confirmed: rendering with
`add_generation_prompt=False` on modern transformers moves the output back toward
the official.)

Implications:

- **This is a chat-template-version dependency**, not a serving knob. Reproducing a
  HELM run needs the prompt rendered the way *that* transformers version rendered
  it.
- **In vLLM**, `/chat/completions` honors an `add_generation_prompt` field
  (default `True`); send `add_generation_prompt=false` to match the old effective
  render. `hf-match` now **sweeps `add_generation_prompt` {True, False}** as a
  cheap request-time knob (chat only) and lets the scorer pick — for OLMoE it
  picks False. Verify the exact render with `compare_prompt.py
  --add-generation-prompt false`.
- **Fully version-proof alternative:** pre-render with the template HELM used
  (or `--chat-template <old.jinja>` on `vllm serve`) and send via completions.

## The official OLMoE run used float32 — the one dtype vLLM can't serve for a MoE

The single most consequential OLMoE finding, because it turns the fp32-MoE
feasibility prune above from harmless into decisive.

> **CONFIRMED (2026-07).** Reproducing the official on the HF side at **float32**
> (`eval-audit`'s `deployment_match hf-probe`, i.e. `transformers.generate()` at
> fp32, `add_generation_prompt=false`) reproduces the official completions
> **exactly** — quasi/exact match ≈ 1.0, not the ~0.17 the best fp16 vLLM cell
> reached. This settles the earlier "float32 vs auto" uncertainty empirically: the
> official was float32, and matched precision → matched output. The transformers
> `<5` inference below is corroborated by the exact match, not merely assumed.

**The official was float32.** HELM's `HuggingFaceClient` loads the model with only
whatever kwargs the deployment config supplies, and
[`model_deployments.yaml`](../submodules/helm/src/helm/config/model_deployments.yaml)
gives `huggingface/olmoe-1b-7b-0125-instruct` exactly `args: {device_map: auto}` —
**no `torch_dtype`**. The client injects none of its own (it only *converts* a
`torch_dtype` string if the config already has one —
[`huggingface_client.py` `_process_huggingface_client_kwargs`](../submodules/helm/src/helm/clients/huggingface_client.py)).
So `AutoModelForCausalLM.from_pretrained(..., device_map="auto")` is called with no
dtype, and **every transformers 4.x** defaults that to `torch.float32` for
backward-compat, *ignoring the checkpoint's bf16 config*. Auto-detecting the
config dtype is a **v5** change — even late 4.x (verified in 4.57.6
`modeling_utils.py`) still hits `else: set fp32 as the default dtype for BC` and
carries the comment *"we … won't rely on config.dtype till v5."* The OLMoE
architecture only landed in transformers ~4.45 (late 2024), so a Jan-2025 run is
squarely in the fp32-default regime. (Residual: the run dir captures no
environment, so the exact version isn't readable from artifacts — only bounded. If
a run were ever produced under transformers ≥5 the default flips to bf16 and this
conclusion moves.)

**Why that's decisive.** The matching precision (fp32) is exactly the one cell that
can't run: vLLM's Triton fused-MoE kernel OOMs in fp32 (the shared-memory limit in
"Preflight feasibility rules" above — a *kernel* limit, not a VRAM limit). So every
runnable local cell is reduced-precision (fp16/bf16), and none reproduces an fp32
reference. In the OLMoE-ifeval store the fp16 cells win (composite 0.494) over
bf16/auto (0.391), and **fp16 winning is coincidental, not a precision match** —
fp16's finer mantissa (10 bits vs bf16's 7) flips fewer greedy near-ties against a
high-precision reference. That ranking is itself corroboration: *if the official
were bf16, local bf16 would match best.* fp16 > bf16 says the reference is
higher-precision than bf16 — consistent with fp32.

**Framing:** this is a **recipe/deployment-boundary failure**, not pure numeric
irreproducibility — the official recipe implies a precision the local vLLM engine
cannot easily provide for this MoE architecture. Three ways to attempt closing it:
(1) **tensor-parallel fp32 in-grid** — `--fp32-tensor-parallel-size 2`
(`DM_FP32_TP=2` on the runbook) shards the fp32 MoE kernel across 2 GPUs so it can
serve without an H100 (see "Preflight feasibility rules"); the sweep then scores an
actual fp32 vs the official — the true apples-to-apples precision match. (2) serve
fp32 on a big-MoE-shared-mem card (H100) at TP=1. (3) reproduce the official side
under HF `transformers.generate()` in fp32 rather than vLLM (what `--profile
hf-match` targets — but see the "HF fits, vLLM doesn't" note). Options (1)/(2) are
the ones that put a *matched-precision* cell in the ranking instead of only
reduced-precision approximations. **Option (3) is what actually landed the exact
match** (the `hf-probe` path) — for a MoE, HF fp32 is the reliable route since
vLLM's Triton kernel blocks fp32 regardless of TP (TP shards experts, not the
per-block shared-memory tile; empirically TP=2/4 still OOM on a workstation card).

### Scope: this affects the OLMo-2 reproductions too, not just OLMoE

The dtype gap is **not** an OLMoE quirk — it is the default for *every* OLMo
HuggingFaceClient run. All of these public deployments pin **no `torch_dtype`**
(verified in `model_deployments.yaml`), so each official ran **float32**:

| Model | Official deployment | dtype | Arch | fp32 reproduction path |
|---|---|---|---|---|
| OLMoE-1B-7B-0125-Instruct | `huggingface/olmoe-…` | fp32 | **MoE** | HF fp32 only (`hf-probe`) — vLLM MoE kernel blocks fp32 |
| OLMo-2-1124-7B-Instruct | `huggingface/olmo-2-…7b…` | fp32 | dense | HF fp32, or vLLM fp32 (~28 GB, fits 40/80 GB) |
| OLMo-2-1124-13B-Instruct | `huggingface/olmo-2-…13b…` | fp32 | dense | HF fp32, or vLLM fp32 (~52 GB, needs 80 GB / TP2) |
| OLMo-2-0325-32B-Instruct | `huggingface/olmo-2-…32b…` | fp32 | dense | HF fp32, or vLLM fp32 (~128 GB, needs TP2×80 GB) |
| OLMo-1.7-7B | `huggingface/olmo-1.7-7b` | fp32 | dense | HF fp32 (no public run confirmed here) |
| OLMo-7B | **`together/olmo-7b`** | Together-hosted | dense | **N/A — not an HF run**; see the EOS-append case, bf16 |

So any OLMo-2 reproduction served at bf16/fp16 is a **precision mismatch**, and a
bf16-vs-official disagreement there is a *deployment-boundary* artifact, not a
reproducibility failure — re-run at fp32 (the dense ones can even do it in vLLM).
**OLMo-7B is the lone exception**: its official was Together-hosted, so its target
is a hosted service's precision, not HF fp32 — the relevant issue there is the
tokenizer EOS-append, not dtype.

### "But direct HuggingFace deployment fits on the GPU and doesn't use float32"

Two separate things, both consistent with the above:

- **Fitting is not evidence of bf16.** OLMoE-1B-7B is ~6.9B total params → fp32
  weights ≈ 28 GB, which fits comfortably on a 40/80 GB card (and `device_map=auto`
  would shard/offload if not). The vLLM fp32 failure is **not** a weight-VRAM OOM —
  it's the Triton fused-MoE **kernel** exceeding per-SM shared memory (~131 KiB
  required vs ~99 KiB cap on workstation cards). So HF fp32 (just holds weights in
  VRAM) fits while vLLM fp32-MoE dies at the kernel — different resources, both true
  at once.
- **Whether a *direct* load shows bf16 depends on your load path, which differs
  from HELM's old one.** Raw `from_pretrained(device_map="auto")` with **no
  `torch_dtype`** on **pre-v5 transformers** yields fp32 (HELM's path). You get bf16
  instead if any of: you're on transformers ≥5 (default now reads config dtype); you
  pass `torch_dtype="auto"` or `torch_dtype=torch.bfloat16` (explicitly or via a
  helper/`pipeline`); or you deploy through a serving stack (vLLM/TGI) that defaults
  to the config dtype. Confirm what you actually loaded with
  `print(next(model.parameters()).dtype)` — if it's bf16 you changed at least one of
  those three vs. the archival HELM run.

## Why the OLMoE-ifeval sweep can't localize the difference

Two compounding reasons, beyond any single knob:

1. **Long-form greedy generation is chaotic.** ifeval generates up to 2048 tokens.
   Greedy decoding is deterministic *given identical logits*, but the smallest
   cross-engine logit difference (dtype/kernel/attention-backend — the very things
   we sweep) flips one argmax, and from that token on the two sequences **diverge
   and never resync**. So two "correct" runs can share the first N tokens and
   differ on the remaining ~2000. Exact text reproduction of long-form generation
   across vLLM↔HF is essentially infeasible — unlike short-answer QA (narrative_qa's
   " Diana"), which is 1–3 robust tokens. The tool was built for the QA case.
2. **The scorer is QA-tuned.** `composite` = quasi (SQuAD exact-match) 0.45 +
   first-word 0.35 + similarity 0.20. On a 2000-token ifeval response quasi≈0 and
   first-word is a coin-flip, so even a faithful recipe scores low. `similarity`
   is the only long-form-meaningful term, and chaotic divergence drags it down too.

**Implications for using the tool on long-form benchmarks:**

- Prefer a **short-answer benchmark to *find* the recipe** (MMLU/narrative_qa,
  where reproduction is tractable and the scorer discriminates), then apply the
  winning serve-recipe to the long-form run.
- Judge long-form reproduction by the **benchmark's own metric** (ifeval's
  instruction-following pass rate via the real `analyze-experiment`/`compare-pair`
  pipeline), not raw completion-text similarity — two different-but-valid responses
  can both pass ifeval while sharing little text.
- If you must score text on long-form, weight **first-token / first-K-token**
  agreement (robust to the chaotic tail) over full-string similarity.

## Inspecting the prompt vLLM actually received

To verify the prompt vLLM tokenizes matches HELM's `get_prompt` (esp. the chat
template), two options:

- **Request logging** — `--log-requests` (or `DM_LOG_REQUESTS=1`) adds
  `--enable-log-requests --max-log-len 100000` to every endpoint, so each request's
  **post-chat-template** prompt + sampling params print in the vLLM container logs.
  View with the infer-stack TUI logs pane or `docker compose logs <vllm-service>`.
  (`--extra-serve-args '<flags>'` passes arbitrary `vllm serve` args if your vLLM
  spells the flag differently.)
- **`compare_prompt.py` (does the diff for you).** Reconstructs HELM's `get_prompt`
  (chat template + `add_special_tokens=True` tokenization) for one grid cell and,
  with a live vLLM `/tokenize` URL, diffs the exact token-id sequences — printing
  the first divergence:
  ```bash
  # local reconstruction only (no GPU): shows the render + BOS/add_special_tokens effect
  .venv/bin/python dev/tools/deployment_match/compare_prompt.py \
      --grid-dir /tmp/dm-olmoe --cell '<cell_id>'
  # authoritative: diff against what a served endpoint tokenizes
  ... --tokenize-url http://<vllm-host>:<port>/tokenize --served-model <name>
  ```
  For OLMoE-instruct this shows the chat template already embeds the BOS
  (`bos_token = |||IP_ADDRESS|||`), so `add_special_tokens` is a **no-op** under
  chat — i.e. BOS is not the confound, consistent with the chaotic-tail
  explanation above.
- **`/tokenize` by hand** — the same query, if you prefer curl:
  ```bash
  curl -s http://<vllm-host>:<port>/tokenize -H 'Content-Type: application/json' -d '{
    "model": "<served-name>",
    "messages": [{"role":"user","content":"<request.prompt from oracle.json>"}],
    "add_generation_prompt": true
  }' | jq
  ```

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

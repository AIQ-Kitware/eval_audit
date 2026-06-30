# OLMo-7B deployment matrix (MWE for the "repeats nonsense regardless of prompt" bug)

## The symptom

During from-spec reproduction of the official `together/olmo-7b` HELM runs, the
local `vllm/allenai-olmo-7b` deployment emits **prompt-independent** pretraining
boilerplate for every instance, e.g. (from `narrative_qa`):

```
The first thing you need to do is to make sure that you have a g…hat you
understand the rules of the game before you start playing.
```

The same string comes back for *different* inputs (id1150 and id1162 in the
report), while the official run conditions on the prompt normally. Crucially the
diff shows `prompts_equal=True` — the prompt text reaches vLLM intact — and the
local run's `result.completions.0.logprob` is hugely negative
(`-67.25`, `-59.76`) while the official's is `0`. The model is *running*, getting
the *right input*, and still ignoring it. That is a **recipe/deployment failure**
(a filtering reason), not a reproducibility failure of OLMo itself.

## The diagnosis

OLMo-1 7B (the Feb-2024 model the official run used) is **numerically unstable in
float16**. Its activation outliers exceed float16's ~6.5e4 dynamic range and
overflow to inf/NaN in attention; the model then stops conditioning on its prompt
and collapses to the highest-prior pretraining-boilerplate attractor shown above.

The live `allenai-olmo-7b-single` endpoint
(`../../config/infer_stack/catalog.yaml`) passes **no `--dtype`**, so vLLM
`--dtype auto` reads OLMo-7B-hf's `config.torch_dtype: float32` and **downcasts
float32 → float16** (vLLM's documented auto rule), which triggers the collapse.
The fix is to serve in **bfloat16** (wide exponent range, no overflow). The
`protocol: completions` setting is already correct (consistent with
`prompts_equal=True`), so chat-templating is *not* the cause here — but it is a
*second*, independent way to get garbage from this base model, so the matrix
covers it too.

This directory is the minimum working example that **proves** the above by
serving every deployment variant of OLMo-7B and comparing their outputs.

## All the ways OLMo-7B can be deployed (the axes)

| Axis | Options | Why it matters |
|---|---|---|
| **dtype** | `auto`(→fp16), `float16`, **`bfloat16`**, `float32` | The decisive axis. fp16 ⇒ collapse; bf16/fp32 ⇒ healthy. |
| **weights / checkpoint** | `allenai/OLMo-7B-hf` (Feb-2024, HF conv.), `allenai/OLMo-7B` (Feb-2024, native ai2-olmo fmt, needs `--trust-remote-code`), `allenai/OLMo-7B-0724-hf` (Jul-2024 re-release, **different checkpoint**) | The official run = Feb-2024 weights. 0724 is a deliberate non-match control. |
| **protocol / prompt format** | **`completions`** (raw prompt), `chat` (chat-templated) | narrative_qa is a base completion task. Chat-framing a base model (no chat template) mangles the prompt → garbage. |
| **engine** | vLLM (these endpoints), HF transformers (`olmo_hf_reference.py`, the oracle), Together API (the official run, external), Ollama (GGUF — omitted, see note) | Isolates kernel/engine effects from recipe effects. |
| **context / tokenizer** | `max_model_len` (2048 = official `max_sequence_length` 2047), `--trust-remote-code`, BOS handling | Held fixed at the official value here; surfaced as knobs for completeness. |

The **vLLM** combinations are shipped as infer-stack endpoints in
[`catalog.yaml`](catalog.yaml). The **HF-transformers** engine is the ground-truth
oracle ([`olmo_hf_reference.py`](olmo_hf_reference.py)). **Together** is the
official artifact the audit already compares against. **Ollama** is intentionally
omitted: faithful OLMo-1 *base* GGUFs are scarce and quantization would confound
the dtype comparison.

## The endpoints (what each isolates, expected verdict)

| Endpoint | model | dtype | protocol | Expected |
|---|---|---|---|---|
| `olmo7b-dbg-auto` | OLMo-7B-hf | auto→fp16 | completions | **COLLAPSED** (reproduces the bug) |
| `olmo7b-dbg-fp16` | OLMo-7B-hf | float16 | completions | **COLLAPSED** (proves it's fp16, not "auto") |
| `olmo7b-dbg-bf16` | OLMo-7B-hf | bfloat16 | completions | **HEALTHY** (the fix) |
| `olmo7b-dbg-fp32` | OLMo-7B-hf | float32 | completions | **HEALTHY** (full-precision control) |
| `olmo7b-dbg-chat-bf16` | OLMo-7B-hf | bfloat16 | chat | **COLLAPSED/garbage** (wrong recipe, right dtype) |
| `olmo7b-dbg-orig-bf16` | OLMo-7B (native) | bfloat16 | completions | **HEALTHY** (conversion-path control) |
| `olmo7b-dbg-0724-bf16` | OLMo-7B-0724-hf | bfloat16 | completions | **HEALTHY but wrong checkpoint** (won't match official numbers) |

Each endpoint has a distinct served name so they do **not** coalesce onto one
container (infer-stack coalesces by a structural compat-key that excludes
`extra_args`; the distinct names are what keep these separate — see the long
comment in `catalog.yaml`). Confirm the rendered launch commands with
`render_commands.py` (below).

## How to run

### 0. Inspect the exact `vllm serve` commands (no GPU needed)
From the eval_audit repo root, using the repo `.venv` (has pyyaml + ubelt):
```bash
PYTHONPATH=submodules/infer_stack .venv/bin/python \
  reproduce/olmo_models/debug/olmo7b_deploy_matrix/render_commands.py
```
Verify each line carries the intended `--dtype …` (and `--trust-remote-code` for
the native checkpoint).

### 1. (Recommended) generate the HF-transformers ground-truth oracle
On a GPU host with `torch`+`transformers` installed:
```bash
cd reproduce/olmo_models/debug/olmo7b_deploy_matrix
python olmo_hf_reference.py --dtype bfloat16 --out results/hf-bf16.json
# optional: show the bug off-vLLM too, and an engine/GPU-independent control
python olmo_hf_reference.py --dtype float16  --out results/hf-fp16.json
python olmo_hf_reference.py --dtype float32 --device cpu --out results/hf-fp32-cpu.json
```

### 2. Run the vLLM matrix (GPU host with the infer-stack CLI + docker)
```bash
cd reproduce/olmo_models/debug/olmo7b_deploy_matrix
INFER_STACK_ALLOWED_GPUS=0 ./run_matrix.sh           # all variants
./run_matrix.sh olmo7b-dbg-auto olmo7b-dbg-bf16      # just a couple
```
The driver brings each endpoint up one at a time (`acquire` → query → `release
--evict`), writes one `results/<endpoint>.json` per variant, then prints the
comparison with `results/hf-bf16.json` (if present) as the reference.

### Manual single-endpoint query (already-running gateway)
```bash
python compare_deployments.py query \
  --base-url http://localhost:14042/v1 --model olmo7b-dbg-bf16 \
  --api-key "$(infer-stack env LITELLM_MASTER_KEY)" \
  --out results/olmo7b-dbg-bf16.json
python compare_deployments.py report results/ --reference hf-bf16
```

## How to read the report

```
variant                verdict     uniq/n  meanXsim  boiler  degen   ~ref
olmo7b-dbg-auto        COLLAPSED      1/4     1.000       4      0   0.07
olmo7b-dbg-bf16        HEALTHY        4/4     0.155       0      0      —
```
- **uniq/n** — distinct completions across the n distinct prompts. A healthy base
  model conditions on each prompt (≈ n/n); a collapsed one returns one string
  (1/n). This is the direct, automated form of "repeats nonsense regardless of
  prompt".
- **meanXsim** — mean cross-prompt completion similarity. ≥0.80 ⇒ collapse.
- **boiler / degen** — completions matching known fp16 boilerplate / degenerate
  single-token repeats.
- **~ref** — mean per-prompt similarity to the reference (HF bf16 oracle): how
  close a vLLM variant is to "what OLMo actually says".
- **verdict** — `HEALTHY` / `SUSPECT` / `COLLAPSED` / `NO_DATA`. `report` exits
  non-zero if a variant whose name is **not** flagged expected-bad
  (`auto`/`fp16`/`chat`) collapses — a regression gate for the fix.

## The production takeaway

The matrix is expected to show: `auto`, `fp16`, and `chat-bf16` **COLLAPSED**;
`bf16`, `fp32`, `orig-bf16` **HEALTHY** and high-agreement with the HF oracle;
`0724-bf16` healthy but lower agreement (different checkpoint). That pins the live
bug on **dtype**, and the fix is to add `--dtype bfloat16` (or `float32`) to the
production `allenai-olmo-7b-single` endpoint in
`../../config/infer_stack/catalog.yaml`:

```yaml
    runtime:
      max_model_len: 2048
      gpu_memory_utilization: 0.85
      max_num_batched_tokens: 2048
      max_num_seqs: 16
      enable_prefix_caching: true
      extra_args: ['--dtype', 'bfloat16']     # <-- the fix this MWE confirms
```
(The same fix applies to any fp16-unstable OLMo-1 model; OLMo-1.7 and OLMo-2
fixed the training-stability issue and do not need it.) This MWE is the evidence;
applying the production change is a separate, deliberate step.

## Caveats

- **GPU host required** to actually serve. This directory was authored and
  statically validated (catalog parse, command render, analysis self-test) on a
  CPU box; the serving runs on yardrat/namek/aiq-gpu.
- **Verify HF ids/revisions** on the Hub before citing numbers, matching the
  existing caution in the production catalog. `OLMo-7B-hf`/`OLMo-7B` are the
  Feb-2024 weights; `OLMo-7B-0724-hf` is a different checkpoint.
- **Shared compose project**: see the warning in `settings.yaml` — don't run this
  concurrently with a production OLMo grid.

# What HELM doesn't record: the reproduction provenance gap

HELM's run artifacts serialize the **recipe** (what to generate) and the model's
*name* — but almost nothing about the **execution substrate** (how the numbers
were actually computed). Reproducing a public run therefore means guessing, or
sweeping, a pile of parameters HELM never wrote down. This note inventories that
gap, ranks the parameters by how much they move a greedy output, and records what
the deployment-match tool does about it today and could do next.

Companion to
[`vllm-vs-huggingface-deployment-match.md`](vllm-vs-huggingface-deployment-match.md)
(the vLLM↔HF knob inventory) and
[`eee-vs-helm-metadata.md`](eee-vs-helm-metadata.md) (the HELM↔EEE field mapping
and how to ship metadata via the `recipe_facts` block). The concrete case that
motivated this — OLMoE ran in float32
because no `torch_dtype` was pinned — is written up in the deployment-match doc's
"The official OLMoE run used float32" section.

## What IS captured vs. what is NOT

**Captured** (in the run dir):

- `run_spec.json` — the adapter recipe (prompts/prefixes, `max_tokens`,
  `temperature`, `stop_sequences`, `num_outputs`, `max_eval_instances`, the
  `model` and `model_deployment` *names*), scenario + metric specs.
- `scenario_state.json` — per-request `prompt` (the **raw** prompt, not the
  chat-templated string), sampling params (`temperature`, `top_p`,
  `top_k_per_token`, `max_tokens`, `stop_sequences`, penalties), and the
  completions.
- The `model_deployment` name, pointing at `model_deployments.yaml`.

**Not captured** — the execution substrate. Empirically, of **148
HuggingFaceClient deployments** in HELM's `model_deployments.yaml`, only **19 pin
`torch_dtype`** (all `torch.bfloat16`) and only **7 pin a model `revision`**. The
rest — OLMoE included — leave precision and exact weights to whatever the runtime
defaulted to.

## The taxonomy, ranked by effect on a greedy (temperature=0) output

Most HELM runs are greedy, so "does it change the argmax" is the right yardstick.

### Tier 1 — changes the weights or the graph (largest effect)

| Parameter | Why it matters | Recoverable? |
|---|---|---|
| **Model weight revision (HF commit SHA)** | Same name ≠ same weights. Authors re-upload checkpoints, patch configs/tokenizers under one repo name. 7/148 pin it. | Pinnable in the deployment config; almost never is. **The master key** (below). |
| **Load precision / dtype** (fp32/bf16/fp16) | Different logits → greedy flips. 129/148 unpinned → the default is *transformers-version-dependent* (pre-v5 = float32, ignoring the checkpoint's bf16 config). **Confirmed decisive**: HF fp32 reproduces OLMoE *exactly* (vs ~0.17 at fp16), and the same unpinned default applies to **all** OLMo-2 HF deployments (`vllm-vs-huggingface-deployment-match.md` → "Scope"). | Deployment config *can* carry `torch_dtype`; usually doesn't. Else inferred from the transformers version. |
| **Quantization** (GPTQ/AWQ/fp8/bitsandbytes) | Changes the weights outright. A quantized repro of an fp16 official is a different model. | Sometimes in the deployment config; mostly unspecified. |
| **Software stack versions** (transformers, torch, tokenizers, engine) | A *meta-parameter*: the transformers version sets the fp32-vs-bf16 default **and** whether the chat template honors `add_generation_prompt`. Engine/flash-attn versions change kernels. | Nowhere in the run dir. Lost unless separately logged. |

### Tier 2 — changes the numerics (greedy can still diverge)

| Parameter | Why it matters |
|---|---|
| **Attention implementation** (eager / sdpa / flash_attention_2 / PagedAttention) | Different reduction order → different logits at temp=0. HF's default is version/hardware-dependent. |
| **Matmul precision flags** — TF32 (`allow_tf32`), fp16 reduced-precision reduction, cuDNN determinism | Especially for fp32: true-fp32 vs TF32 matmuls change results. Never recorded. |
| **Hardware / GPU model** | Determines available kernels, TF32 behavior, and whether fp32 MoE even fits. A100 ≠ H100 ≠ workstation card. |
| **Device topology / tensor-parallelism** | Multi-GPU all-reduce order differs from single-GPU. `device_map:auto` is in the config but the GPU count that ran it isn't. |
| **Batch composition** | vLLM/HF kernels aren't batch-invariant — the same prompt yields different logits depending on batch neighbours. Actual batch sizes aren't recorded. |

### Tier 3 — changes the prompt or the scored text (often binary, can be huge)

| Parameter | Why it matters |
|---|---|
| **Tokenizer + chat-template version** | The `add_generation_prompt` drift: the *templated* prompt is never stored (only the raw `request.prompt`), and the template lives in the model repo and changes over time. |
| **`add_special_tokens` / BOS handling** | Whether BOS is prepended (the OLMo-7B EOS-append case). Tokenizer-default- and version-dependent. |
| **Tokenizer fast vs slow** (`use_fast`) | Edge-token differences on some models. |
| **`generation_config.json` defaults from the model repo** | Fills unspecified fields (repetition_penalty, eos/pad ids, sometimes default temperature). HELM sends explicit sampling params but generation_config can inject the rest — and its version isn't recorded. |
| **Stop-sequence + detokenization semantics** | HF applies stops via token-id `StoppingCriteria`; vLLM via string-match at detokenization → different truncation. `skip_special_tokens` / `spaces_between_special_tokens` change the *scored text* even when generation matched. |
| **Truncation** (left vs right, when prompt+gen exceeds `max_sequence_length`) | Not specified per-run. |

### Tier 4 — mostly for sampling (temperature > 0)

RNG **seed** (for non-greedy scenarios, and for few-shot exemplar sampling *if*
reproducing from `run_spec` rather than replaying the stored prompt) and
**KV-cache dtype** (fp8) when reproducing through a serving engine.

## The one non-obvious lever: pin the revision

Several Tier-3 gaps collapse into Tier-1 #1. The tokenizer, chat template, and
`generation_config.json` all live *inside the model repo* — so **pinning the model
revision (commit SHA) recovers all of them as-of-then.** You don't need HELM to
have recorded the template if you can reconstruct the repo state it saw. The
revision is the highest-leverage single field HELM (or a machine-readable
`recipe_facts` block — see "Where a provenance block should live" below) could add:
`model_revision` + `torch_dtype` + `transformers_version` together close the
majority of the gap, because those three transitively fix the
tokenizer/template/generation_config state and the precision default.

## What deployment-match does about this today, and what it should do next

The deployment-match tool
([`dev/tools/deployment_match/`](../dev/tools/deployment_match/)) exists *because*
these parameters are unrecorded — it sweeps the cheap-to-vary ones and lets the
scorer pick the combination that reproduces the official. Today it sweeps
**dtype, attention_backend, add_special_tokens, add_generation_prompt, tokenizer
identity**, and (new) **fp32 tensor-parallelism**. Gaps and proposed improvements,
most-valuable first:

1. **Read the official `client_spec.args` from `model_deployments.yaml`.**
   `registry.resolve_official_deployment` currently pulls only `tokenizer_name`,
   `max_sequence_length`, and `client_class` — it *ignores* `args`, so the tool
   never sees whether the official pinned `torch_dtype`, `quantization`, or
   `device_map`. Surfacing `args` lets the tool:
   - **pin/seed the dtype axis** to the official `torch_dtype` when it's declared
     (19/148 runs), instead of blindly sweeping four dtypes;
   - **emit the fp32-default fact as a note** when `torch_dtype` is absent (the
     OLMoE finding) so the operator knows the official was float32 — turning an
     implicit assumption into a printed diagnosis;
   - **flag quantization** so a reproduction matches it rather than serving full
     precision against a quantized official.
   Low effort (one YAML read + a `Resolution` field + a grid seeding rule); high
   value — it removes guessing where the config already answers.

2. **Record and warn on the model revision.** The tool assumes "latest on the
   repo" — the single biggest silent risk (Tier-1 #1). At minimum, record the HF
   snapshot commit actually served in `resolution.json` / `best_deployment.yaml`
   for provenance. Better: warn when the repo has commits newer than the official
   run's date (drift risk), and accept a `--revision` to pin it (see the effort
   estimate below).

3. **Emit a provenance / "unknowns" block.** For each unrecorded parameter, print
   what the tool *assumed* (dtype default, latest revision, latest
   tokenizer/template, vLLM-default attention, TF32 on) — a
   `comparability_unknown:*`-style report, so a low score can be attributed to a
   specific unrecorded axis rather than a vague "irreproducible." Mirrors the
   pipeline's existing `comparability_unknown` pattern for EEE-only inputs.

4. **Record the local `transformers` version** (and torch / vLLM) into the run
   artifacts, with a note that the fp32-default and chat-template behavior depend
   on it. This is the meta-parameter; capturing it makes the dtype and template
   findings self-explanatory in the report.

5. **TF32 / matmul-precision as a sweepable env knob** (the doc's Tier-A "not
   represented" row). For an fp32 target especially, `allow_tf32` on/off is a real
   fork; expose it like `attention_backend` and let the scorer decide.

6. **Surface `generation_config.json`.** Read the served model's generation_config
   and note any defaults (repetition_penalty, eos/pad ids) that the HELM request
   did not override — a source of divergence the recipe alone doesn't reveal.

7. **Capture + diff the resolved chat template.** `compare_prompt.py` already
   reconstructs HELM's `get_prompt`; extend it to record the resolved template
   string and warn when it differs from the revision the official likely used.

## Where a provenance block should live (existing machinery)

"Provenance sidecar" is loose shorthand — the codebase already has this machinery,
with precise names. Three real things exist today:

- **The `recipe_facts` resolver**
  ([`eval_audit/normalized/recipe_facts.py`](../eval_audit/normalized/recipe_facts.py),
  `resolve_recipe_facts`) answers "what recipe produced this run?" in priority
  order: a **native `recipe_facts` block** JSON-encoded inside an EEE aggregate's
  `source_metadata.additional_details["recipe_facts"]` → a **sidecar
  `run_spec.json`** next to the artifact (or the HELM run dir's own) → **unknown**
  (facts collapse to `status='unknown'` and the pipeline emits
  `comparability_unknown:*`). It resolves the *recipe* fields — `model`,
  `model_deployment`, `scenario_class`, `benchmark_group`, `instructions`,
  `max_eval_instances`, `run_spec_hash`, `judge_models`.
- **`container_provenance.json`**
  ([`eval_audit/integrations/docker_provenance.py`](../eval_audit/integrations/docker_provenance.py),
  `write_container_provenance`) records the container image + digest a run executed
  in — which, because the container env is frozen at build time, transitively pins
  the software stack (transformers / torch / vLLM versions).
- **`provenance.json`** (analyze-experiment / build-virtual-experiment) records
  what the *pipeline* composed (sources, rows discarded) — pipeline provenance,
  not model execution.

The execution-substrate fields this doc is about — dtype, revision, quantization,
attn implementation, TF32, transformers version — are recorded by **none** of these
at the per-model level. The natural home is the **native `recipe_facts` block**:
its `RecipeFacts.extra` slot already preserves unknown keys
([`_facts_from_native_block`](../eval_audit/normalized/recipe_facts.py)), so a
converter can emit `{"torch_dtype": ..., "model_revision": ...,
"transformers_version": ...}` **today with no schema change** and the resolver
round-trips them. So "a provenance block" here means concretely: populate the
execution-substrate fields into the existing `recipe_facts` slot — not a new file
format. Keep them **out** of the run-spec name / `adapter_spec.model` /
`model_deployment` identity strings, which are the pairing key and the
comparability facts (`same_model` / `same_deployment`): a SHA in those breaks
official↔local pairing, whereas a SHA in the `recipe_facts` block is invisible to
pairing and comparability.

## How much change to pin a specific HF revision?

Question: what would it take to run a HELM reproduction against an exact model
**revision** (commit SHA) instead of "latest on the repo"? Answer: **surprisingly
little — the two engines that load weights already accept `revision` as a
data-only edit; the only code gap is that eval_audit's boundary layer drops it.**

### The reproduction has two model-loading paths

- **Path A — vLLM-served (the from-spec preset path, e.g. OLMo/OLMoE).**
  infer-stack serves the weights from its `catalog.yaml`; HELM only points a
  `VLLMClient` at the endpoint and never loads the weights itself.
- **Path B — HELM-direct (`HuggingFaceClient`).** HELM loads weights + tokenizer
  itself via `AutoModelForCausalLM.from_pretrained`.

### What already supports revision (zero code)

- **infer-stack, end-to-end.** A `models:` entry accepts `revision:` — it flows
  through `Model.revision`
  ([`catalog.py:84`](../submodules/infer_stack/infer_stack/leasing/catalog.py),
  parsed `:188`), is a **compat-key member** (`models.py:72`, so revisions don't
  coalesce), and renders as `--revision=<sha>` on the `vllm serve` command
  (`profile_runtime.py:20`, `compose.py:216`). So Path A's *served weights* pin
  with a one-line `catalog.yaml` edit and no code.
- **HELM `HuggingFaceClient`, implicitly.** It forwards arbitrary
  `client_spec.args` straight to `from_pretrained`
  ([`huggingface_client.py:63,92`](../submodules/helm/src/helm/clients/huggingface_client.py)),
  which natively accepts `revision`; same for the tokenizer load. So Path B pins
  with a one-line `model_deployments.yaml` `client_spec.args.revision` edit and no
  HELM code.

### The one real gap: eval_audit drops revision at the boundary

There is **zero** `revision` handling anywhere under `eval_audit/`. When eval_audit
*generates* the HELM deployment bundle from an infer-stack catalog, it loses the
pin:

- [`serving_facts.py:67-81`](../eval_audit/integrations/infer_stack/serving_facts.py)
  — `ServingFacts` has no `revision` field, and `resolve_serving_facts` reads only
  the endpoint's `served`/`capacity` payload, which does **not** include revision
  (revision lives in the model `spec`, `catalog.py:398`).
- [`bundle_export.py:33`](../eval_audit/integrations/infer_stack/bundle_export.py)
  `_model_deployment_entry` never emits a revision into the generated
  `model_deployments.<hash>.yaml`.
- [`manifests/models.py:8`](../eval_audit/manifests/models.py) `ManifestSpec` and
  [`run_spec_materializer.py:50`](../eval_audit/manifests/run_spec_materializer.py)
  `RunSpecSource` have no `revision` field, so a pin can't be recorded in the
  materialized `run_spec.json` provenance either.

### Effort, tiered

| Goal | Change | Effort |
|---|---|---|
| Pin **Path A served weights** | add `revision: <sha>` to `reproduce/*/config/infer_stack/catalog.yaml` `models.<name>` | **0 code** (data edit) |
| Pin **Path B (HELM-direct) weights + tokenizer** | add `revision: <sha>` to `model_deployments.yaml` `client_spec.args` | **0 code** (data edit) |
| Pin **deployment-match's own probe** | add `--revision` → `ServeRecipe` → the `models` entry in `Grid.to_catalog` (infer-stack consumes it) | **~1 field + 1 CLI flag** |
| Make eval_audit's **generated bundles** carry the pin (so Path A's *tokenizer* load is also pinned and the SHA is recorded for provenance) | `ServingFacts` + `resolve_serving_facts` must surface `spec['revision']` (the wrinkle: it currently reads `served`, which omits it); `_model_deployment_entry` emits it into `client_spec.args` + tokenizer args | **small, ~3–4 files** |
| Make revision a **first-class manifest/recipe field** (recorded in `run_spec.json`, threaded through the scheduler) | add to `ManifestSpec`, `RunSpecSource` + substitution key, and the `kwdagger_bridge` matrix (mirrors `model_deployments_fpath`) | **+2–3 files** |

**Bottom line:** pinning a revision *today* is a data-only edit on either path — no
code. The worthwhile code investment is the ~3–4-file boundary fix so eval_audit's
generated bundles carry the pin automatically (covering Path A's HELM-side
tokenizer load) and record it in provenance. The one non-trivial spot is
`resolve_serving_facts`, which must start reading the model `spec` (where revision
lives) rather than only the `served` payload (where it doesn't). Everything
downstream of that already honors it.

### Recommended sequence

1. **Now:** pin via `catalog.yaml` / `model_deployments.yaml` data edits for any
   run you want reproducible — this is already correct and needs no release.
2. **Small PR:** thread `revision` through `ServingFacts` →
   `_model_deployment_entry` so generated bundles pin both the vLLM weights and
   HELM's tokenizer, and record the resolved SHA in the manifest/provenance.
3. **Pair it with deployment-match improvement #2** (record + warn on revision) so
   the tool reports the SHA it actually served and flags drift against the official
   run date.

## Cross-references

- [`vllm-vs-huggingface-deployment-match.md`](vllm-vs-huggingface-deployment-match.md)
  — the per-knob vLLM↔HF inventory and the OLMoE float32 case study.
- [`eee-vs-helm-metadata.md`](eee-vs-helm-metadata.md) — the HELM↔EEE field
  mapping; the native `recipe_facts` block / sidecar `run_spec.json` are where
  shipped metadata lands (see "Where a provenance block should live" above).
- [`helm-gotchas.md`](helm-gotchas.md) — other HELM reproduction traps.

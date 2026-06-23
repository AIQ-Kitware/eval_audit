# Reference: HELM run-expander keys that "drop" out of run names

**Why this matters.** A HELM run entry is a string like
`bbq:subject=all,method=multiple_choice_joint,output_format_instructions=mcqa,max_train_instances=0,model=...`.
Each `key=value` pair either feeds a scenario/adapter argument or maps to a
**run-expander** (`RUN_EXPANDERS` in
[`run_expander.py`](../../submodules/helm/src/helm/benchmark/run_expander.py)).
Some expanders rewrite `run_spec.name` so the annotation survives a round-trip
through run names; others mutate the materialized `adapter_spec` /
`metric_specs` but leave the name untouched. The second kind is **silently
dropped** when you reconstruct run entries from run names or run directory names
— which is exactly how the OLMo `candidate_runs.json` lost the BBQ
`output_format_instructions` expander. See the companion writeup
[`NOTES-bbq-instructions-drift.md`](NOTES-bbq-instructions-drift.md) for that case
study; this document is the full key-by-key reference.

> **The rule:** *the run name is not the recipe.* When verifying comparability,
> diff the materialized `run_spec.json` `adapter_spec` (and `metric_specs`), not
> the run-entry strings or directory names.

## How each key was classified

Read class-by-class in `run_expander.py`: a key is **name-safe** if its
`expand()` returns a `RunSpec` with a rewritten `name=f"{run_spec.name},..."`
(or via the `ReplaceValueRunExpander` / `ScenarioSpecRunExpander` base classes,
which do this), and **droppable** if the returned `RunSpec` keeps
`name=run_spec.name` (explicitly or by omission) while changing the adapter spec
or metrics.

## Name-safe keys (survive the run name — low reproducibility risk)

These cannot be silently dropped, because the expander writes the key back into
the run name:

`instructions`, `prompt`, `newline`, `stop`, `global_prefix`,
`data_augmentation`, `model`, `model_deployment`, `eval_split`,
`num_train_trials`, `max_train_instances`, `max_eval_instances`, `num_outputs`,
`num_trials`, `tokenizer`, `num_prompt_tokens`, `num_output_tokens`.

(Base classes: `ReplaceValueRunExpander` → `num_train_trials`,
`max_train_instances`, `max_eval_instances`, `num_outputs`, `num_trials`,
`model_deployment`; `ScenarioSpecRunExpander` → `tokenizer`,
`num_prompt_tokens`. `model`, `eval_split`, `num_output_tokens`,
`instructions`, `prompt`, `newline`, `stop`, `global_prefix`,
`data_augmentation` each rewrite the name in their own `expand()`.)

## Droppable keys (NOT written to the run name)

Reconstructing entries from run names loses every key below. Each *can* impact
reproducibility if the official run used it and the local replay omits it,
because each mutates the prompt, the decoding parameters, or the scoring.

| key | class | what it mutates | impact if dropped | model-gated? |
|---|---|---|---|---|
| `output_format_instructions` | `OutputFormatInstructions` | prepends to `instructions`, or (with a `_suffix` scenario) appends to `global_suffix` | **High** — changes the prompt | no |
| `chatml` | `ChatMLRunExpander` | rewrites `instructions` / `*_prefix` / `*_suffix` / `stop_sequences` into ChatML | **High** — changes the prompt | no |
| `format_prompt` | `FormatPromptRunExpander` | sets `input_prefix` / `output_prefix` | **High** — changes the prompt | no |
| `follow_format_instructions` | `FollowFormatInstructionsRunExpander` | adds `global_prefix` / `global_suffix` (generation only) | **High** — changes the prompt | yes — `instruct` tag |
| `process_output` | `ProcessOutputRunExpander` | replaces `metric_specs` with an `OutputProcessingMetric` wrapper | **High** — changes scoring | no |
| `add_to_stop` | `AddToStopRunExpander` | appends one stop sequence | **Med** — changes truncation | no |
| `temperature` | `TemperatureRunExpander` | sets decoding `temperature` | **Med/High** — changes sampling | no |
| `increase_temperature` | `IncreaseTemperatureRunExpander` | adds to `temperature` | **Med/High** — changes sampling | no |
| `increase_max_tokens` | `IncreaseMaxTokensRunExpander` | adds to `max_tokens` | **Med** — generation length / truncation | no |
| `anthropic` | `AnthropicClaude2RunExpander` | Anthropic-specific prompt/stop fixes | **High** but model-gated | yes — Anthropic |
| `claude_3` | `AnthropicClaude3RunExpander` | drops whitespace-only stop sequences | **High** but model-gated | yes — Anthropic |
| `amazon-nova` | `NovaRunExpander` | adds a Nova `global_prefix` | **High** but model-gated | yes — Nova |
| `idefics_instruct` | `IDEFICSInstructRunExpander` | IDEFICS prompt formatting | **High** but model-gated | yes — VLM |
| `llava` | `LlavaRunExpander` | LLaVA prompt formatting | **High** but model-gated | yes — VLM |
| `open_flamingo` | `OpenFlamingoRunExpander` | OpenFlamingo prompt formatting | **High** but model-gated | yes — VLM |

Model-gated expanders fire only for a specific model family (Anthropic, Nova,
the vision-language models) or, for `follow_format_instructions=instruct`, only
for models carrying the instruction-following tag. They can never apply to the
open-weight text models in this audit, so they are not a reproducibility risk
for the OLMo corpus — but they *are* a risk if you extend the recipe to those
families.

## Empirical finding for the OLMo corpus

Every official OLMo `run_spec.json` was scanned for the *signatures* of the
droppable expanders — `temperature`, `max_tokens`, `stop_sequences`,
`global_prefix`, ChatML markers (`<|im_start|>`), an `OutputProcessingMetric`
in `metric_specs`, and non-default `input_prefix` / `output_prefix`. Result:

- All values are the **base-spec defaults** implied by the name-visible
  parameters. The only droppable expander actually applied anywhere in the OLMo
  officials was `output_format_instructions` on **bbq** (now fixed in
  [`adapter.py`](../../eval_audit/integrations/infer_stack/adapter.py)).
- The `gpqa` / `mmlu_pro` `temperature=1`, the `"Let's think step by step…"`
  `global_suffix`, and the `max_tokens` values all come from
  `get_gpqa_spec` / `get_mmlu_pro_spec` under `use_chain_of_thought=true`
  (capabilities_run_specs.py) — params the adapter passes verbatim — **not** from
  dropped expanders. (HELM's *capabilities conf* does drive gpqa/mmlu_pro through
  the droppable `output_format_instructions=gpqa_suffix` + `increase_max_tokens`
  form, but the OLMo officials used the `use_chain_of_thought` parametrization
  instead, so that path does not apply here.)
- No `chatml`, `process_output`, `format_prompt`, `follow_format_instructions`,
  `add_to_stop`, `increase_max_tokens`, or `temperature` override was present on
  any OLMo official.

So after the bbq fix the OLMo adapter entries match the public recipe on every
targeted scenario. When extending this audit to other models or tracks, re-run
the same `run_spec.json` signature scan rather than trusting run names.

## How to find the exact public recipe a run used

The run name is unreliable at **every** level — the public index
`run_spec_name`, the run **directory** name, *and* the `name` field **inside**
`run_spec.json` are all the same expander-blind string (e.g.
`gpqa:...,num_output_tokens=2048,model=...` with no record of the
`output_format_instructions` / `temperature` / `chatml` that may have run).
Don't reconstruct a recipe from any of them. Use the artifacts in the public run
directory instead.

A public HELM run directory (e.g.
`/data/crfm-helm-public/<track>/benchmark_output/runs/<version>/<run-name>/`)
contains:

```
run_spec.json            # the materialized recipe (adapter_spec, scenario_spec, metric_specs)
scenario_state.json      # the fully-assembled prompts actually sent to the model
display_requests.json    # human-readable rendered prompts (same ground truth, easier to read)
display_predictions.json # model outputs
scenario.json            # scenario instances
stats.json, per_instance_stats.json
```

### Trust hierarchy (most → least authoritative)

1. **Rendered prompt — `display_requests.json` / `scenario_state.json`.** The
   actual text the model received, fully assembled. Whatever any expander did is
   baked in here. Use this to answer "did the official and my local run send the
   same prompt?" — diff the rendered request strings.
2. **Materialized recipe — `run_spec.json` → `adapter_spec`** (plus
   `metric_specs`, `scenario_spec`). The structured recipe *after* all expanders
   applied. Every droppable expander lands in these fields:
   - prompt expanders (`output_format_instructions`, `chatml`, `format_prompt`,
     `follow_format_instructions`) → `instructions` / `global_prefix` /
     `global_suffix` / `input_prefix` / `output_prefix`
   - decoding expanders (`temperature`, `increase_max_tokens`, `add_to_stop`) →
     `temperature` / `max_tokens` / `stop_sequences`
   - `process_output` → `metric_specs` (an `OutputProcessingMetric` wrapper)

   Read `adapter_spec`; **never** read `name`.
3. **(Do NOT trust) the `name` string** — index `run_spec_name`, the run dir
   name, and `run_spec.json["name"]`. All three are the same incomplete string.

### Recovering a re-runnable *entry string* (with the expander keys)

`run_spec.json` gives the *result*, not *which expanders* produced it, so you
generally cannot invert it back into the entry string. To get the literal entry
(e.g. `...,output_format_instructions=mcqa,...`) go to the **public run-entry
config** that generated the run. The run path names the track and version:

```
/data/crfm-helm-public/capabilities/benchmark_output/runs/v1.8.0/...
                        ^track/suite                     ^version
```

→ grep the matching config — `run_entries_capabilities*.conf`,
`run_entries_safety.conf`, `run_entries_lite_*.conf`, … — for the scenario. The
`{description: "<entry>", priority: N}` line is the authoritative recipe with
all annotations. (Mind track variants: e.g. safety BBQ uses
`output_format_instructions=mcqa`, the palmyra variant uses `mcqa_no_period`;
capabilities gpqa has both a `use_chain_of_thought` form and an
`output_format_instructions=gpqa_suffix` form — confirm which one your target
run actually used via step 2.)

### The airtight verification

Combine both directions: take the candidate entry string from the `.conf`,
regenerate the spec locally through HELM's `run_spec_factory`, and **diff its
`adapter_spec` against the official `run_spec.json`'s `adapter_spec`**. Identical
adapter specs ⇒ your recipe is exact, proven at the materialized level the name
cannot capture. (For BBQ this is precisely what caught the gap: the official
`adapter_spec.instructions` carried the single-letter prefix while the name did
not.)

### Practical rule

> **To verify comparability:** diff `adapter_spec` (or the rendered prompt in
> `display_requests.json`) between the official and local `run_spec.json`.
> **To reproduce:** take the entry string from the track's `run_entries_*.conf`,
> never from any name.

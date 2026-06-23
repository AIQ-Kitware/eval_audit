# Reproduction hurdle: BBQ `output_format_instructions` (the silent prompt drift)

**TL;DR.** Public-HELM BBQ runs prepend `"Answer with only a single
letter."` to the prompt via an *opt-in* run-expander
(`output_format_instructions=mcqa`) that does **not** appear in the
persisted run name. A naive local replay of `bbq:...,method=multiple_choice_joint`
omits the expander and sends a *different prompt* — same scenario, same
method, same model, different instructions. This surfaces as a
`comparability_drift:same_instructions` warning and is a genuine
recipe-comparability hurdle, not a numeric reproducibility failure. The
OLMo presets in
[`adapter.py`](../../eval_audit/integrations/infer_stack/adapter.py) now
carry `output_format_instructions=mcqa` on the BBQ entries to match.

## Symptom

The grouped report flags four packets — the BBQ scenario
(`bbq:subject=all,method=multiple_choice_joint,max_train_instances=0`)
on each of the four **instruct** models
(`olmo-2-0325-32b-instruct`, `olmo-2-1124-13b-instruct`,
`olmo-2-1124-7b-instruct`, `olmoe-1b-7b-0125-instruct`) — with:

```
comparability_drift:same_instructions
  caveat: same_instructions=no values=[
    'Answer with only a single letter.\n\nThe following are multiple choice questions (with answers).',  # official
    'The following are multiple choice questions (with answers).'                                         # local
  ]
```

The base models (`olmo-7b`, `olmo-1.7-7b`) do not hit this — they are on
the lite/classic tracks, not the safety track that carries BBQ.

## Why it happens (the trap)

The instruction text is injected by HELM's `OutputFormatInstructions`
run-expander
([`submodules/helm/.../run_expander.py`](../../submodules/helm/src/helm/benchmark/run_expander.py),
class `OutputFormatInstructions`, `name = "output_format_instructions"`).
Two non-obvious facts make this easy to miss:

1. **`method=multiple_choice_joint` does not trigger the expander.** Run-entry
   `key=value` pairs split into two categories in
   [`run_spec_factory.py`](../../submodules/helm/src/helm/benchmark/run_spec_factory.py)
   (~L62–64):

   ```python
   expanders = [RUN_EXPANDERS[key](value) for key, value in args.items() if key in RUN_EXPANDERS]
   args      = dict((key, value) for key, value in args.items() if key not in RUN_EXPANDERS)
   ```

   `method` and `subject` are **not** in `RUN_EXPANDERS` — they are scenario/adapter
   arguments passed to `get_bbq_spec(subject=..., method=...)`. The expander is
   created **only** when the entry contains the separate key
   `output_format_instructions=<scenario>`. The
   `if run_spec.adapter_spec.method == ADAPT_MULTIPLE_CHOICE_JOINT:` check *inside*
   the expander is a guard that selects the *wording* once the expander is already
   running — it is not what makes the expander run.

2. **The expander rewrites the prompt but not the run name.**
   `OutputFormatInstructions.expand()` returns
   `replace(run_spec, adapter_spec=replace(..., instructions=...))` — it mutates
   `adapter_spec.instructions` (prepending its text, joined with `\n\n`) and leaves
   `run_spec.name` untouched. So the official run's persisted name is just
   `bbq:subject=all,method=multiple_choice_joint,max_train_instances=0,model=...`
   with **no visible annotation**, even though its materialized prompt carries the
   prefix. Reconstructing the recipe from the run name alone silently drops the
   expander.

The base BBQ spec
([`classic_run_specs.py`](../../submodules/helm/src/helm/benchmark/run_specs/classic_run_specs.py),
`get_bbq_spec`) sets only
`instructions="The following are multiple choice questions (with answers)."` —
exactly the local value. The official safety-track run-entry config
([`run_entries_safety.conf`](../../submodules/helm/src/helm/benchmark/presentation/run_entries_safety.conf))
adds `output_format_instructions=mcqa`, and the `mcqa` branch yields
`"Answer with only a single letter."`.

## The fix

The four BBQ run entries (smoke **and** full manifests) in
[`adapter.py`](../../eval_audit/integrations/infer_stack/adapter.py) now read:

```
bbq:subject=all,method=multiple_choice_joint,output_format_instructions=mcqa,max_train_instances=0,model=allenai/<instruct-model>
```

`mcqa` (not `mmlu`) matches the canonical safety track in
`run_entries_safety.conf`; both produce identical instruction text here, but
`mcqa` is what public HELM actually used for these runs.

## How the correct public recipe was identified

The drift caveat tells you *what* differs (`"Answer with only a single
letter."` is prepended on the official side) but not *which expander* or
*which value* produces it. The trail:

1. **Grep the offending string in HELM source** to find what emits it:

   ```
   grep -rn "Answer with only a single letter" submodules/helm/src/helm
   # -> run_expander.py: the OutputFormatInstructions expander, several scenario branches
   ```

   Multiple branches (`mmlu`, `mcqa`, the `else` fallback) all yield that exact
   string — so the string alone does not pin the annotation value.

2. **Find which expander value public HELM actually used for BBQ** by grepping the
   checked-in run-entry configs (these *are* the public recipe):

   ```
   grep -rIn "bbq" submodules/helm/.../presentation/*.conf | grep output_format_instructions
   # run_entries_safety.conf:         output_format_instructions=mcqa          <- the canonical safety track
   # run_entries_safety_reasoning.conf: output_format_instructions=mcqa
   # run_entries_safety_palmyra_x5.conf: output_format_instructions=mcqa_no_period
   ```

   BBQ lives on the **safety** track (confirmed independently: the official
   component id in the comparison manifest is `official::safety::v1.10.0::bbq:...`),
   so `run_entries_safety.conf` is the authoritative source → `mcqa`. The
   `mcqa_no_period` palmyra variant and the reasoning track are different surfaces;
   the OLMo officials are the plain safety track.

3. **Confirm the materialized prompt matches.** The `mcqa` branch in
   `run_expander.py` sets `instructions = "Answer with only a single letter."`, and
   the expander joins it as `f"{instructions}\n\n{run_spec.adapter_spec.instructions}"`
   — reproducing the official string
   `"Answer with only a single letter.\n\nThe following are multiple choice questions (with answers)."`
   exactly, given the base `get_bbq_spec` instructions.

So the value is not guessed from the prompt text — it is read from the public
track's run-entry config and cross-checked against (a) the official component's
track/version in the comparison manifest and (b) the materialized instruction
string.

## Was anything else affected? (full sweep — no)

Because the adapter entries were sourced from a `candidate_runs.json` that
carries **zero** `output_format_instructions` keys, the obvious worry is that
*every* multiple-choice scenario silently dropped the expander (HELM ships a
`run_entries_lite_20240424_output_format_instructions.conf` that adds it to
mmlu / legalbench / commonsense / narrative_qa / med_qa / wmt_14 / natural_qa).
That worry was checked against the **materialized** official OLMo run specs on
disk (`/data/crfm-helm-public/.../run_spec.json`, the authoritative recipe), not
against run names:

- For every scenario the OLMo presets actually target (mmlu, legalbench,
  commonsense, gsm, med_qa, narrative_qa, wmt_14, gpqa, mmlu_pro, ifeval), the
  official `adapter_spec.instructions` is the **base** scenario text — the OLMo
  officials did **not** use the `output_format_instructions` lite variant.
  Example: official mmlu instructions are
  `"The following are multiple choice questions (with answers) about <subject>."`
  with no single-letter prefix.
- The only OFI-bearing official scenarios were **bbq** (fixed here) and
  **omni_math** — and omni_math is **not** in the OLMo presets (it needs an
  LLM-as-jury annotator and is commented out).
- gpqa / mmlu_pro carry a `"Let's think step by step…"` `global_suffix`, but that
  is produced by `use_chain_of_thought=true`, which the adapter entries already
  include — recipe-driven, not a missing expander.
- A normalized annotation-set diff (adapter entries vs official run specs, per
  model+scenario) found only two residual differences, both benign: (a) the
  `output_format_instructions=mcqa` we just added to bbq — which is *absent from
  the official run name yet present in the official `adapter_spec.instructions`*,
  the exact name-vs-recipe gap described above, confirming the fix is right; and
  (b) a `groups=mmlu_<subject>` token that appears in the index's
  `run_spec_name` for mmlu but is **not** a run-expander and **not** in the
  run_spec.json `name` (whose `groups` field is just `["mmlu"]`) — leaderboard
  metadata, not a recipe parameter, correctly omitted.

Conclusion: after the bbq fix, the OLMo adapter entries match the public recipe
on every targeted scenario.

## Which run-expander keys are "droppable" (invisible in the run name)

`output_format_instructions` is one of a family of run-expanders that mutate the
materialized recipe without writing themselves into the run name, so
reconstructing entries from run names (as `candidate_runs.json` did) silently
loses them. The full key-by-key reference — name-safe vs droppable, what each
mutates, reproducibility impact, and the empirical finding that bbq was the only
one actually applied to the OLMo officials — lives in
[`NOTES-dropped-run-expander-keys.md`](NOTES-dropped-run-expander-keys.md).

## Generalizable lesson for HELM reproduction

**The run name is not the recipe.** Any HELM run-expander that edits the
adapter spec without re-stamping the run name (`output_format_instructions`,
`global_prefix`, several chat-template/format expanders) is invisible if you
reconstruct run entries from run names or scenario+method alone. When a
`comparability_drift:same_instructions` (or `:same_*`) warning appears,
diff the **materialized `adapter_spec`** of both sides — not the run names —
and check the official track's `run_entries_*.conf` for opt-in expanders the
local recipe is missing. This is a recipe/comparability hurdle (fixable by
aligning the recipe), distinct from a true reproducibility failure (same
recipe → divergent metrics).

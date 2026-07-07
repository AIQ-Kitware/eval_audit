# Deployment-match search — plan

Status: IMPLEMENTED (all phases, 2026-07-01) at `dev/tools/deployment_match/`.
Phase 1 core+dry-run (ab9ccce), Phase 2 serve+probe driver (86ad959), Phase 3
confirm/compare-pair (aee205b), Phase 4 pytest (792ce21). Serving (Phase 2 `run`)
needs a GPU host; everything else is CPU-validated. Axes approved via
AskUserQuestion 2026-07-01.

## Goal

A general, reusable tool that takes **one public HELM run**, sweeps a grid of
local serving recipes for that run's model, evaluates each on a **small sample**
of the run's own instances, and ranks them by **agreement with the official
completions** — so we can answer "which local deployment most closely reproduces
the HELM original?" for *any* model, not just OLMo.

It generalizes the OLMo-7B MWE at
[`reproduce/olmo_models/debug/olmo7b_deploy_matrix/`](../../reproduce/olmo_models/debug/olmo7b_deploy_matrix/):
same catalog + acquire/query/release shape, but (a) parameterized by an arbitrary
HELM run instead of hand-written OLMo endpoints, and (b) scored against the
**official outputs** rather than a self-consistency heuristic.

## Motivating context (what we learned from OLMo-7B)

The OLMo-7B "repeats nonsense regardless of prompt" bug went through *two*
diagnoses, and that history is the whole reason this tool should exist:

1. **fp16 instability** (a prior session) — prescribed `--dtype bfloat16`.
   **Refuted**: the live endpoint was already bf16 and the bug persisted.
2. **Tokenizer EOS-append** (confirmed end-to-end, committed as `74ba33d`) —
   `allenai/OLMo-7B-hf`'s `tokenizer.json` has a `TemplateProcessing`
   post-processor that appends `<|endoftext|>` to every sequence; vLLM's
   `/v1/completions` default `add_special_tokens=True` terminates the prompt with
   EOS, and the base model reads it as a document boundary → prompt-independent
   continuation. Fix in production: serve OLMo-7B weights with
   `allenai/OLMo-1.7-7B-hf`'s tokenizer (post-processor removed).

**Lesson driving the design:** a theory-driven single-knob guess is unreliable;
an **empirical sweep scored against the official outputs** is what actually
localizes the deployment fault. So the grid must keep *every* plausible knob as an
axis and let the data decide — including `--dtype bfloat16`, even though the
confirmed cause turned out to be the tokenizer (explicit user instruction:
"even if the issue was not bfloat16, if that is an option that is possible in the
grid, it should be evaluated in case").

## Locked-in decisions (2026-07-01)

- **Grid scope = knobs on one resolved repo.** Sweep serving knobs on the single
  registry-resolved HF source for the model; do **not** enumerate alternate repo
  checkpoints (no `-0724`/native-vs-hf axis by default). `--extra-sources` stays
  available as an escape hatch but is off by default.
- **Scoring = direct probe + optional confirm.** Per cell, do a lightweight
  direct OpenAI-compatible probe on the sampled prompts and score
  completion-text agreement vs official. Then run **one** full
  `eval-audit-compare-pair` on the *winning* cell as authoritative confirmation.
- **Packaging = dev tool generalizing the MWE.** Lives at
  `dev/tools/deployment_match/` (scripts + a thin importable lib), run ad-hoc;
  not a first-class `eval_audit/cli` command (yet). `dev/tools/` is a new peer
  category alongside `dev/scripts/` and `dev/analysis/` — the right neighborhood
  for a general, reusable developer diagnostic. Deliberately **not** under
  `reproduce/` (that tree is per-model reproduction runbooks, e.g.
  `reproduce/olmo_models/`, not model-agnostic tooling), nor next to the
  OLMo-specific MWE it generalizes
  ([`reproduce/olmo_models/debug/olmo7b_deploy_matrix/`](../../reproduce/olmo_models/debug/olmo7b_deploy_matrix/)),
  nor in `dev/oneoff/` (throwaway single scripts) or `dev/poc/` (undersells a
  reusable tool). It imports `eval_audit` internals (loaders, diff, pair_report)
  directly, which works from `dev/` since the package is installed editable.

## Inputs

```
eval-audit-deployment-match \
  --run <helm_run_dir>            # e.g. /data/crfm-helm-public/lite/benchmark_output/runs/v1.2.0/narrative_qa:model=allenai_olmo-7b
  --n 16                          # sample size ("small number of prompts")
  --grid grid.yaml               # axes+values (defaults provided; see below)
  --source hf://...              # optional override of the registry-resolved HF repo
  --mode infer-stack|direct|dry-run
  --out results/
```

## Stage 1 — Oracle extraction (from the official run)

Read the run dir (shape verified against `/data/crfm-helm-public`):

- **Recipe held FIXED** (replayed verbatim, from-spec — [[all-reproductions-must-be-from-spec]]): per `request_state.request` — `prompt`, `max_tokens`, `temperature`, `stop_sequences`, `num_completions`, `echo_prompt=False`. We vary **only deployment knobs**, never the recipe.
- **Ground truth**: `result.completions[0].text` per sampled instance (+ per-token `tokens[].text/.logprob` when present — note Together stores `logprob: 0`, so text is the primary signal).
- **Official deployment facts** from `submodules/helm/.../model_deployments.yaml` + `tokenizer_configs.yaml`: `tokenizer_name`, `max_sequence_length`, client class → seed grid defaults (`max_model_len = min(max_sequence_length + 1, model max_position_embeddings)` — HELM's `max_sequence_length` is sometimes the full window and sometimes window−1, and vLLM refuses to start above the model-derived ceiling; default tokenizer).
- **Sampling** (`--n`): deterministic head **plus** the few shortest prompts (MC-like). The EOS failure looked different on long-gen (`"The first thing…"`) vs short (`"The"`); the sample must span both response lengths.

Reader: `eval_audit/normalized/loaders.py::HelmRawLoader` (or read `scenario_state.json` directly — the confirmed schema is `request_states[i].request.prompt` / `.result.completions[0].text`).

## Stage 2 — Grid generation (one repo, two tiers)

The grid is a cross-product of knobs on the single resolved HF repo. Split by
cost, because it changes how many containers we launch:

**Tier A — serve-time (each combo = its own `vllm serve` / infer-stack `acquire`):**
- `dtype`: {auto, float16, bfloat16, float32}  *(kept per user instruction)*
- `tokenizer` override: {default, `<sibling tokenizer without special-token post-processor>`}  *(the OLMo fix class; auto-suggest the sibling when the model's tokenizer.json has a `TemplateProcessing` post-processor)*
- `max_model_len`: {official `max_sequence_length+1`, larger}
- `trust_remote_code`, `quantization`: off by default

**Tier B — request-time (varied per request against the *same* container; the LiteLLM gateway forwards them):**
- `add_special_tokens`: {true, false}  *(the confirmed decisive OLMo axis; reachable only via direct probe — HELM's `openai_client` does not set it, grep-confirmed)*
- `protocol`: {completions, chat}  *(chat only when a chat template exists)*

Loop: **outer = serve-recipes (expensive, one container each) → inner = request-variants (cheap, many per container).** For OLMo-7B a sane default ≈ `{4 dtype × 2 tokenizer} = 8` containers `× {2 add_special_tokens} = 16` cells — capped and axis-restrictable via `grid.yaml`.

Emit an infer-stack `catalog.yaml` of the Tier-A serve-recipes (distinct served
names so they don't coalesce — reuse the MWE's `render_commands.py` to verify the
exact `vllm serve` line and confirm distinct compat-keys).

Knob mechanics (from `profile_runtime.vllm_args`): only a fixed flag set +
`runtime.extra_args` reach the command line, so every serve-knob goes in
`extra_args`; Tier-B knobs go in the probe request body.

## Stage 3 — Per-cell scoring (reuse the comparison core)

For each cell, run the N sampled prompts (recipe params fixed to official) and,
per instance, compare candidate vs official completion:
- normalized **exact / quasi-exact match** + **text similarity** — reuse
  `eval_audit/helm/diff_primitives._walker_diff` (the `request_state_diff` from
  the original report) and/or `ub.indexable_diff` (`similarity`, `num_differences`).
- **first-token match** (candidate `tokens[0].text` vs official) — the cheap,
  decisive discriminator (official ` Diana` vs broken `The`).
- optional: recompute the run's HELM metric (e.g. `f1_score`/`exact_match`) on
  candidate text → "does it reproduce the official per-instance score?"
- **diagnostics (the *why*), reusing the MWE analyzer + an echo probe:** prompt
  tokenization via `echo+logprobs` (is the last prompt token a special token the
  official didn't have?), plus prompt-independence / boilerplate / degenerate
  detectors from `compare_deployments.py`.

Aggregate → composite match score per cell → ranking.

## Stage 4 — Confirm the winner

Run one full local from-spec HELM run for the top cell (existing pipeline:
`eval-audit-make-manifest` → `eval-audit-run` → `eval-audit-index`) and compare
to the official via `eval-audit-compare-pair` (`build_pair_report` →
`core_metric_report.{txt,json,png}`). The search is the cheap funnel; compare-pair
is the authoritative metric-level finish.

## Outputs

- `results/<cell>.json` — completions + diagnostics per cell.
- `ranking.{txt,json}` — cell → composite score vs official, exact-match rate,
  first-token-match rate, collapse verdict, and the winning knob values.
- `best_deployment.yaml` — the serve knobs + request knobs that best matched,
  ready to fold into a production catalog/preset.
- `report.txt` — human summary (which knob mattered; per-prompt snippet matrix).

## Serving modes

- **infer-stack** (primary; matches house): generated catalog → acquire → probe
  all Tier-B variants → release --evict, serial (one container at a time), `gc`
  bracketed. Generalize the MWE's `run_matrix.sh`.
- **direct** (`vllm serve` per serve-recipe on :8000): for hosts without
  infer-stack.
- **dry-run** (CPU, no GPU): extract the sample + render the grid/commands +
  validate — CPU-validatable here against `/data` runs.

## Reuse vs new

| Reuse | New |
|---|---|
| `HelmRawLoader` / direct `scenario_state.json` read | oracle extraction + stratified sampler |
| `_walker_diff` / `ub.indexable_diff` (per-instance diff) | official-agreement scorer + composite ranking |
| MWE `compare_deployments.py` analyzer, `render_commands.py`, `run_matrix.sh` | parameterized grid generator (axes → catalog + request-variant list) |
| `build_pair_report` / `eval-audit-compare-pair` (winner confirm) | model→grid resolver (registry defaults + tokenizer-sibling suggestion) |

## Model → grid resolution (registry)

- HELM model name → official deployment facts: `model_deployments.yaml`
  (`tokenizer_name`, `max_sequence_length`, client class), `model_metadata.yaml`.
- HELM model → local HF source + `protocol_mode`: `eval_audit` adapter
  `PRESET_CONFIGS` + `reproduce/*/config/infer_stack/catalog.yaml` if present;
  else a small built-in map; else `--source` (required if unresolved).
- Tokenizer-sibling suggestion: if the resolved tokenizer's `tokenizer.json` has a
  `TemplateProcessing` post-processor that injects specials, offer a sibling
  tokenizer without it as a Tier-A candidate (this is exactly the OLMo fix).

## Phasing

1. ✓ **Core lib + CLI + dry-run** (`ab9ccce`): oracle reader/sampler, grid
   generator, scorer, report; `sample`/`grid`/`dry-run`/`score`. CPU-validated on
   `/data` public runs.
2. ✓ **infer-stack serving driver** (`86ad959`): `serve.py` + `cli run` — the
   two-tier acquire→probe→release loop (one container per serve-recipe, request
   variants probed per container). `--dry` prints the plan on CPU.
3. ✓ **Winner confirmation** (`aee205b`): `confirm.py` + `cli confirm` — winning
   single-cell catalog + plan + `build_pair_report(official, local)`; probe-only
   caveat surfaced.
4. ✓ **Tests** (`792ce21`): 11 pytest cases — oracle extraction from the gpt2
   HELM fixture, official-fact lookup, the pure tokenizer predicate, grid shape +
   distinct compat-keys, scorer ranking.

## Risks / open items

- **A found request-time fix (`add_special_tokens=false`) does not automatically
  land in production HELM runs** — HELM's `openai_client`/`VLLMClient` don't send
  it. Landing it needs either a `VLLMClient` change or the serve-time tokenizer
  override (the route production took in `74ba33d`). The tool should say which of
  its winning knobs are HELM-path-native (serve-time: tokenizer/dtype/max_len) vs
  probe-only (request-time: add_special_tokens) so the fix is applied correctly.
- **Chat-protocol on a base model** has no chat template → expect 400s/garbage;
  keep it as a labeled negative, not a candidate for the winner.
- **GPU required** to serve; only dry-run + scoring on recorded outputs run on CPU.
- Sampling `--n` small by design; the winner is *confirmed* by a full run
  (Stage 4), so the cheap sample only needs to *rank*, not to be authoritative.

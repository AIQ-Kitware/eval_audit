# deployment-match

Find the local serving recipe that most closely **reproduces a public HELM run**.

Given one HELM run directory, this tool extracts a small instance sample + the
official completions (the oracle), sweeps a grid of local serving recipes for
that model, runs each on the sample, and ranks them by **agreement with the
official outputs** — surfacing both the winner and *why* the losers diverge.

It generalizes the OLMo-7B deploy-matrix MWE (the since-retired
`olmo7b_deploy_matrix` debug harness): same catalog + acquire/query/release
shape, but parameterized by an arbitrary HELM run and scored against the
official completions instead of a self-consistency heuristic.
Design: [`docs/planning/deployment-match-search-plan.md`](../../../docs/planning/deployment-match-search-plan.md).

## Why

The OLMo-7B "repeats nonsense regardless of prompt" bug was first mis-diagnosed
as fp16 instability (`--dtype bfloat16`) and only later pinned on the tokenizer
appending EOS (fixed in `74ba33d` by serving OLMo-1.7's tokenizer). The lesson:
**don't guess a single knob — sweep the grid and score against the official
outputs.** This tool is that sweep, made general.

## Layout

| file | role |
|---|---|
| `oracle.py`   | read a public HELM run → recipe + stratified N-instance sample (prompt + official completion) |
| `registry.py` | resolve HELM model → local HF source / protocol + official deployment facts; detect EOS-append tokenizers + suggest a sibling |
| `grid.py`     | two-tier grid → serve-recipes (dtype/tokenizer/max_len) × request-variants (add_special_tokens/protocol) → cells + an infer-stack catalog |
| `probe.py`    | OpenAI-compatible client; sends the recipe verbatim + request-time knobs (incl. `add_special_tokens`) |
| `score.py`    | candidate-vs-official scorer (exact/quasi/similarity/first-word) + composite ranking + collapse diagnostic |
| `report.py`   | ranking table, per-instance snippet matrix, `best_deployment.yaml` |
| `serve.py`    | Phase 2 driver: two-tier acquire→probe→release over the grid (infer-stack + `probe`) |
| `confirm.py`  | Phase 3: winning single-cell catalog + plan + `build_pair_report(official, local)` |
| `cli.py`      | `auto` (one-shot) · `sample` / `grid` / `dry-run` / `run` / `score` / `confirm` / `selftest` |

Stdlib-only core; `eval_audit` (request_state_diff, presets) and `infer_stack`
(command render) are optional enrichment. Run under the repo `.venv` (needs
pyyaml).

## The grid (two tiers)

- **serve-time** (one `vllm serve` / infer-stack endpoint each): `dtype`
  {auto, float16, bfloat16, float32}, `tokenizer` override (a sibling without the
  EOS-append post-processor is auto-added when detected), `max_model_len`,
  `trust_remote_code`.
- **request-time** (many per running container; the gateway forwards them):
  `add_special_tokens` {true, false}, `protocol` {completions, chat}.

A *cell* is one (serve-recipe × request-variant); scoring ranks cells. `dtype`
stays a full axis by design — the search decides empirically, nothing is pruned
on theory.

### Profiles: `--profile hf-match`

To search for the vLLM recipe that best matches a HELM **`HuggingFaceClient`**
run (official = local `transformers.generate()`), pass `--profile hf-match`. It
pins the vLLM engine's determinism knobs — `--enforce-eager
--no-enable-chunked-prefill --no-enable-prefix-caching --max-num-seqs=1` — so the
sweep varies only the recipe HELM itself could (dtype / tokenizer /
add_special_tokens), not vLLM's scheduler. It merges *under* any `--grid` YAML,
and warns if the official `client_class` isn't HuggingFace. Full rationale +
still-open knobs (attention backend, sampling replay):
[`docs/vllm-vs-huggingface-deployment-match.md`](../../../docs/vllm-vs-huggingface-deployment-match.md).

## Usage

### One-shot: `auto` (chains everything)
The whole pipeline — dry-run (sample+grid) → run (serve+probe) → score →
confirm(plan) — in one command. Run it on a GPU host:
```bash
dev/tools/deployment_match/cli.py auto \
  --run "/data/.../narrative_qa:model=allenai_olmo-7b" --n 12 --out /tmp/dm-olmo
```
GPU selection is infer-stack's job: `run`/`auto` use `acquire --queue`, so
placement picks any available GPU (and waits when the fleet is busy) — you do not
request specific GPUs. Pass `--allowed-gpus 0,1` only to *restrict* placement (an
operator override; an exported `INFER_STACK_ALLOWED_GPUS` is honored either way).

Add `--dry` to stop after emitting the grid + serve plan (CPU-only, no GPU) so you
can inspect the recipes before committing GPU time; `--skip-confirm` to skip the
confirm-plan step. The individual subcommands below are the same steps if you want
to run them piecemeal (e.g. score on a different machine than you served on).

### Dry-run (CPU, no GPU) — Phase 1
Extract the sample, resolve the model, render the grid + exact `vllm serve` lines:
```bash
PYTHONPATH=submodules/infer_stack .venv/bin/python \
  dev/tools/deployment_match/cli.py dry-run \
  --run /data/crfm-helm-public/lite/benchmark_output/runs/v1.2.0/narrative_qa:model=allenai_olmo-7b \
  --n 12 --out /tmp/dm-olmo
```
Writes `oracle.json`, `catalog.yaml`, `cells.json`, `grid.json`, `resolution.json`.

### Serve + probe (GPU host)
Bring up each `catalog.yaml` endpoint one at a time and probe every request
variant against it (two-tier loop: `infer-stack acquire` → `probe.query_cell` for
each cell → `release --evict`), writing one result JSON per cell:
```bash
dev/tools/deployment_match/cli.py run --grid-dir /tmp/dm-olmo
# preview the acquire/probe/release plan with no GPU:
dev/tools/deployment_match/cli.py run --grid-dir /tmp/dm-olmo --dry
# (optional) restrict placement to specific GPUs:
dev/tools/deployment_match/cli.py run --grid-dir /tmp/dm-olmo --allowed-gpus 0,1
```

### Score
Rank the probed cells against the oracle:
```bash
.venv/bin/python dev/tools/deployment_match/cli.py score \
  --oracle /tmp/dm-olmo/oracle.json --results /tmp/dm-olmo/results \
  --cells /tmp/dm-olmo/cells.json --out /tmp/dm-olmo/results
```
Writes `ranking.txt`, `snippets.txt`, `scored.json`, `best_deployment.yaml`.

### Confirm the winner (authoritative)
Emit a one-endpoint catalog + a plan to reproduce the winner at full scale, and —
once you have a full local run dir — compare it to the official run with the
audit's `build_pair_report`:
```bash
dev/tools/deployment_match/cli.py confirm \
  --best /tmp/dm-olmo/results/best_deployment.yaml \
  --run  /data/.../narrative_qa:model=allenai_olmo-7b \
  --local-run <full_local_run_dir> --out /tmp/dm-olmo/confirm
# omit --local-run to just emit serve/catalog.yaml + confirm_plan.md
```

## Tests

`tests/test_deployment_match.py` (11 cases): run under the repo `.venv`
(`.venv/bin/python -m pytest tests/test_deployment_match.py -o addopts=""`).

## Reading the output

- **composite** — weighted agreement with official (quasi-match 0.45 + first-word
  0.35 + similarity 0.20); 1.0 = reproduces the official completions.
- **quasi** — SQuAD-normalized exact-match rate; **first** — first-word-match rate
  (the ` Diana` vs `The` discriminator); **sim** — mean normalized similarity.
- **collapse** — prompt-independence flag (few unique completions / high
  cross-prompt similarity = "ignores the prompt").
- **verdict** — MATCH / PARTIAL / COLLAPSED / NO_DATA.
- **best_deployment.yaml** splits the winning knobs into **serve-time**
  (HELM-path-native — reached by a normal HELM run) vs **request-time**, and
  flags any non-default request-time knob (e.g. `add_special_tokens=False`) as
  *probe-only*: landing it in production needs a `VLLMClient` change or an
  equivalent serve-time fix (the `--tokenizer` override route OLMo took).

## Caveats

- **GPU** required to actually serve (Phase 2); dry-run + score run on CPU.
- Resolution is best-effort: `--source` / `--protocol` override an unresolved
  model; the winner is only *confirmed* by a full `eval-audit-compare-pair`
  (plan Stage 4), so the cheap sample only needs to *rank*.
- HF ids/revisions want Hub verification before citing numbers.

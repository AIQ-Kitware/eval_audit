# classic Together open-weight — combined era-pinned reproduce runbook

End-to-end reproduction of **all official runs** for three classic-era,
Together-hosted, open-weight models —

| model | HELM name | HF serve id | era tokenizer | serving |
|---|---|---|---|---|
| GPT-J 6B | `together/gpt-j-6b` | `EleutherAI/gpt-j-6b` | `EleutherAI/gpt-j-6B` | 1 GPU (~12 GB) |
| GPT-NeoX 20B | `together/gpt-neox-20b` | `EleutherAI/gpt-neox-20b` | `EleutherAI/gpt-neox-20b` | TP=2 (~40 GB) |
| OPT 66B | `together/opt-66b` | `facebook/opt-66b` | `facebook/opt-66b` | TP=4 (~132 GB) |

— replayed **era-pinned** through the two era-supported classic suites
(`helm-v0.2.4`, `helm-v0.3.0`). Each run is replayed **from-spec** (its own frozen
`run_spec.json`) inside that era's CPU-only HELM image; model inference stays
out-of-process on modern vLLM. Structurally this is `dev/era-tests` generalized to
three models × all-runs, in the combined multi-model shape of
`reproduce/olmo_models_combined`.

Each model has **~226 official runs per suite** → 6 targets (model × era),
~1,356 from-spec run_entries total.

## Why era-pinned (not modern like olmo)

These are classic models: all their runs live in pre-v0.5 suites. Running a
classic `run_spec` through modern HELM would score it with different
tokenization/scenario code, conflating era drift with reproduction drift. So —
unlike the modern olmo runbook — this one pins the *measurement instrument* to the
era (v0.2.4/v0.3.0 HELM images + the era shim) and varies only the deployment.
Only these two eras have images; the same models' v0.2.2/v0.2.3 runs are deferred
(they'd need new era images — see `docker/eras.yaml`).

## Presets are generated

The ~1,356 from-spec run_entries are **generated from the corpus** by
`gen_presets.py` into `config/presets.yaml` (regenerate if the corpus scope
changes), and merged into `PRESET_CONFIGS` via `INFER_STACK_EXTRA_PRESET_FILES`
(set in `_lib.sh`) — they stay out of the shared `preset_configs.yaml`.

```bash
python gen_presets.py            # rewrites config/presets.yaml from the corpus
```

## Steps

```bash
./00_check_env.sh              # eval-audit-check-env
./05_check_profiles.sh         # the three <model>-single endpoints are defined
./06_check_era_images.sh       # per era: image present + org.aiq.era label + shim + ENV
./10_run_smoke.sh              # preflight: per target, export(freeze) -> run smoke --lease (5 inst)
./15_run_full.sh               # the batch: per target, FULL manifest (all ~226 runs, 1000-cap)
./20_index_local.sh            # eval-audit-index -> audit_results_index.csv
./25_index_official_classic.sh # per-suite official index + inventory (canonical one has 0 classic rows)
./30_compose.sh                # compose ONE virtual experiment per era (folds all 3 models)
./40_build_summary.sh          # one publication surface per era
```

## Invariants

- **One manifest = one era = one image = one measurement instrument.** The bridge
  guards the image's `org.aiq.era` label against the manifest era at schedule time.
- **Verbatim by-name replay.** A pre-v0.5 `adapter_spec` has no `model_deployment`;
  the era shim registers a deployment under the exact official model name (with the
  era tokenizer alias, so windowing/tokenization match the official run).
- **`same_deployment` resolves `unknown`** for era pairs (both sides lack the field).
- **Per-era corpus view.** These models' runs exist at both suites with identical
  run-dir names, so freezing against the broad classic root is AMBIGUOUS; the grid
  overrides `--precomputed-root` with a per-era suite-scoped view (`era_corpus_view`).
- **All runs means all runs.** Scenarios whose data no longer fetches from the 2026
  Hub, or need credentialed judges/APIs (e.g. toxicity via Perspective), surface as
  environment/recipe *filters* in the reports — not reproducibility failures.

## Serving / GPU knobs

`config/infer_stack/catalog.yaml` sets `tensor_parallel_size` (GPT-NeoX=2, OPT-66B=4).
OPT-66B requires a multi-GPU host; lower `gpu_memory_utilization` if a card OOMs.
Narrow a full run with `TARGETS_OVERRIDE="<row> <row>"` (see `_lib.sh :: TARGETS`).

## Building the era images

```bash
ERA=helm-v0.2.4 ./docker/build.sh
ERA=helm-v0.3.0 ./docker/build.sh
```

CPU-only (`ubuntu:22.04`) — no GPU needed to build. `06_check_era_images.sh`
verifies presence + validity.

# classic-era (pre-v0.5) replay — dev runbook

End-to-end exercise of the audit pipeline on **together/redpajama-incite-base-3b-v1**
replayed through the two **classic HELM eras** (`helm-v0.2.4` and `helm-v0.3.0`), in the
same runbook shape as [`dev/e2e-tests/`](../e2e-tests/) (the phi-2 e2e). Each era
runs its replay inside its **own era-pinned, CPU-only HELM image** (HELM checked
out at the era's release commit, era Python, era dep pins); model inference stays
out-of-process on modern vLLM. Holding the *measurement instrument* fixed at the
era while serving the model on a modern engine is the cleanest form of the
audit's question: instrument fixed, deployment the only variable.

This replaces `reproduce/classic_era_replay/` — the validation-ladder gates moved
here (`07_run_gate.sh`) and the end-to-end path is now a turnkey grid driven by
checked-in presets, so a full run needs essentially no per-user setup.

Design + rationale:
[`docs/planning/era-tests-dev-runbook-plan.md`](../../docs/planning/era-tests-dev-runbook-plan.md)
and [`docs/planning/era-pinned-helm-containers-plan.md`](../../docs/planning/era-pinned-helm-containers-plan.md).
Registry: [`docker/eras.yaml`](../../docker/eras.yaml). Shim:
[`docker/era_shim/`](../../docker/era_shim/).

## The grid

Two targets — one per era — each carrying **both** scenarios (they have distinct
logical run keys, so one per-era experiment composes cleanly; contrast the phi-2
e2e, whose three variants of *one* scenario each need their own manifest). Each
row in `ERA_TARGETS` (in [`_lib.sh`](_lib.sh)) is `name:era:endpoint`:

| era | preset | scenarios | what it exercises |
|---|---|---|---|
| `helm-v0.2.4` | `era-redpajama_3b-v0_2_4` | `synthetic_reasoning_natural:easy` (generation) + `mmlu:us_foreign_policy` (multiple_choice_joint) | the era shim's generation + logprob paths under the v0.2.4 image |
| `helm-v0.3.0` | `era-redpajama_3b-v0_3_0` | (same two) | same, under the v0.3.0 image |

`redpajama-incite-base-3b-v1` is the **smallest** audit-corpus model with a full
official packet at **both** classic eras (74 runs each), so the same model lands
naturally on both — and at ~2.8B params (~5.6 GB fp16) it serves on a single 8 GB
GPU (`pythia-6.9b`, the earlier subject, needed ~14 GB). The generation scenario
exercises the era shim client's generation path; the multiple-choice scenario
stresses its logprob fidelity.

## Invariants (read before running)

- **One manifest = one era = one image = one measurement instrument.** The bridge
  guards the image's `org.aiq.era` label against the manifest era at schedule time.
- **Verbatim replay.** A pre-v0.5 `adapter_spec` has no `model_deployment` field;
  routing to vLLM is purely by-name (the era shim registers a deployment under the
  exact official model name). No deployment rewrite.
- **`same_deployment` resolves `unknown`** for era pairs (both sides lack the
  field). Correct, not a bug — no Stage 5/6 changes.
- **Per-era corpus view.** `redpajama-3b` runs exist at both v0.2.4 and v0.3.0 with
  identical run-dir names, so freezing against the broad classic root is
  AMBIGUOUS. The grid overrides `--precomputed-root` with a per-era suite-scoped
  view (`era_corpus_view` in `_lib.sh`) that exposes exactly one suite while
  keeping the `classic/benchmark_output/...` layout era resolution needs.

## Steps

```bash
./00_check_env.sh              # eval-audit-check-env
./05_check_profiles.sh         # verify the redpajama3b-single endpoint is defined
./06_check_era_images.sh       # per era: image present + org.aiq.era label + shim + ENV (Finding 6)
./07_run_gate.sh               # pre-v0.5 gates: tier 0 pytest + rung 2 fidelity + rung 5 hf-fetch
./10_run_smoke_grid.sh         # preflight: gc -> gateway bootstrap -> per era: export (freeze) -> run smoke --lease
./15_run_full_grid.sh          # per era: same, FULL manifest (the batch)
./20_index_local.sh            # eval-audit-index -> audit_results_index.csv
./25_index_official_classic.sh # per-era official index + inventory (the canonical one has 0 classic rows)
./30_compose.sh                # compose ONE virtual experiment per era
./40_build_summary.sh          # one publication surface per era
```

The smoke grid (`10`) is a fast preflight; `15` is the batch that feeds
`20`→`40`. There is **no separate make-manifest step**: the era export
(`--freeze-rel-paths`) bakes `from_run_spec` + frozen `run_spec_sources` + `era:`
directly into runnable smoke/full manifests.

## Building the era images

Not auto-built (a build invalidates caching — a deliberate act). Build each once:

```bash
ERA=helm-v0.2.4 ./docker/build.sh
ERA=helm-v0.3.0 ./docker/build.sh
```

These are CPU-only (`ubuntu:22.04`, no CUDA) — no GPU needed to build them. After
the first green build, freeze the environment (`docker/README.md`) and rebuild so
the image is reproducible. `06_check_era_images.sh` verifies presence + validity.

## Knobs (env vars)

- `AUDIT_STORE_ROOT` (default `/data/crfm-helm-audit-store`),
  `AUDIT_RESULTS_ROOT` (default `/data/crfm-helm-audit`)
- `PRECOMPUTED_ROOT` (default `/data/crfm-helm-public/classic`) — the classic
  corpus mirror; must contain `benchmark_output/runs/<suite>`
- `ERA_OUT` (default `<repo>/ladder-out`) — gate logs, corpus views, stage-1 scratch
- `ERA_IMAGE_<key>` (e.g. `ERA_IMAGE_helm_v0_2_4=repo@sha256:…`) — pin a
  digest-pinned era image for a cross-machine run instead of the local `:dev` tag
- `LADDER_ERAS` — space-separated era keys the gate validates (default: both)
- `ERA_KEEP_GOING=1` — attempt every era in the grids and report failures at the
  end instead of stopping on the first
- `VEXP_MANIFEST=<path>` — compose/summarize a single per-era manifest
- `EVAL_AUDIT_ERA_API_KEY` (default `EMPTY`) — per-deployment credential forwarded
  into the era container (vLLM ignores it; v0.2.4 merely requires it to exist)
- `INFER_STACK_CONFIG_DIR` (default `config/infer_stack` here) — the catalog with
  the `redpajama3b-single` endpoint

## What stays genuinely manual (by design)

The scripts produce evidence; these decisions are research: interpreting a rung-2
instrument-fidelity divergence (choosing new era pins), committing a constraints
freeze, judging the rung-3/flagship result against the ≫0%-recovery expectation,
and deciding pre-warm-vs-filter for a rung-5 HF-fetch failure (a family that no
longer fetches cleanly from the 2026 Hub is an environment/recipe filter reason,
not a reproducibility failure — pre-warm or mount-vendor its data; never patch the
image at run time).

## Output layout

```
$AUDIT_STORE_ROOT/
├── indexes/era-tests/<suite>/official_public_index.csv     # step 25 (per era)
├── analysis/era-tests/<suite>/filter_inventory.json        # step 25 (per era)
└── virtual-experiments/era-redpajama-v{024,030}/
    ├── indexes/                 # synthesized index slice
    ├── analysis/                # core-reports + experiment_summary
    └── reports/aggregate-summary/   # the per-era publication surface
```

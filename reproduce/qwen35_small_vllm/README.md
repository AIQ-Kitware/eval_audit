# Qwen3.5 small base family (0.8B / 2B / 4B) — unpinned VRAM-aware fan-out (extension, not reproduction)

The 0.8B / 2B / 4B **-Base** siblings of [`qwen35_vllm`](../qwen35_vllm/)
(the 9B runbook), run as **one combined 3-model COMPUTE preset**
(`qwen35_small_vllm`): no public HELM runs to replay, no `--from-spec`, no
official pairing — the same net-new extension shape as the 9B, same nlstrip
completions recipe, so 0.8→2→4→9B is a clean base-scaling column.

**The GPU story is the point of this runbook.** There is no
`INFER_STACK_ALLOWED_GPUS`, no `gpu_indices`, no per-host mapping anywhere.
Every catalog endpoint declares `placement.min_vram_gib` and infer-stack's
VRAM-aware planner (infer_stack `docs/planning/vram-aware-placement.md`,
Phases 0–3) places each per-run lease on whichever *eligible* GPU is free:

- All three smalls fit **both** yardrat cards (48 GiB RTX 8000 + 16 GiB
  RTX 5000), so this batch exercises the *everything-eligible ⇒ take any
  free GPU* path.
- Declared deployments best-fit onto the **smallest** eligible free card, so
  the 48 GiB card stays available — run the 9B runbook's full batch
  **concurrently** and its 24 GiB declaration lands it on the big card while
  the smalls flow around it. Neither runbook names a GPU index.
- If a best-guess declaration is too low, the acquire fails with a **guided
  OOM error** naming the exact `infer-stack measure <endpoint> --record`
  command that computes the real number; the weight-bytes floor clamps
  unsound guesses automatically once weights are in the HF cache.

**Run these scripts on the GPU host (yardrat), not the analysis VM.**

## Registration (no HELM edit)

All three ids (`qwen/qwen3.5-{0.8b,2b,4b}-base`) are net-new and ship as ONE
registry-sidecar pair —
[`configs/local_models/qwen35_small_vllm/`](../../configs/local_models/qwen35_small_vllm/)
`model_metadata.yaml` + `tokenizer_configs.yaml` (three entries each) —
declared once by the combined preset and merged by HELM's
`register_configs_from_directory` inside the runner container.

## Steps

```bash
../../docker/build.sh          # once: build eval-audit-helm-runner:dev
./00_check_env.sh              # eval-audit-check-env (+ leasing env resolution)
./05_check_registration.sh     # CPU-only: preset <-> sidecar consistency for all 3 profiles
./06_check_profiles.sh         # endpoints in the catalog + min_vram_gib declared on each
./07_check_container_image.sh  # docker + pinned image + stale-digest probe
./10_run_smoke.sh              # gc -> bootstrap -> export (compute) -> 6-entry smoke --lease
./15_run_full.sh               # 216-entry full grid (3 x 72, grouped by model)
./40_verify_artifacts.sh       # sweep: every run pairs its model with ITS nlstrip deployment
```

## Success criteria

1. `05`/`06`/`07` all print `OK` — 06 specifically proves every endpoint
   declares `placement.min_vram_gib` (the no-pinning contract).
2. `10` leases each endpoint at least once; all six smoke runs land
   `stats.json` + `per_instance_stats.json`; `infer-stack leases` during the
   run shows deployments placed by eligibility, not by index habit.
3. `40` confirms every run's `adapter_spec.model` ↔ `model_deployment`
   pairing (`vllm/qwen3.5-<size>-base-nlstrip-local` — the newline-tolerant
   completions client, the same declared substitution as the 9B).

## Knobs (env vars)

- `QWEN35S_CONTAINER_IMAGE` (default `eval-audit-helm-runner:dev`)
- `QWEN35S_TMUX_WORKERS` (default `2` — grouped-by-model entries mean both
  workers usually share one model block's deployment via ref-count
  coalescing; at block boundaries the second model spills onto the other
  free GPU)
- `QWEN35S_FORCE_RERUN` (default on for smoke, off for full)
- `INFER_STACK_CONFIG_DIR` / `INFER_STACK_DATA_DIR` — standard infer-stack
  knobs; the shipped config lives in [`config/infer_stack/`](config/infer_stack/).
  Deliberately **no** `INFER_STACK_ALLOWED_GPUS` guidance here: eligibility
  is declared in the catalog, placement is the planner's job.
- `AUDIT_STORE_ROOT` / `AUDIT_RESULTS_ROOT` (defaults under `/data`)

## The full grid (`15_run_full.sh`)

3 × the 9B preset's corrected **72-entry classic/Lite compute core** (boolq,
commonsense, gsm, 5×legalbench, med_qa, 57×mmlu in canonical *compute* form,
narrative_qa, 5×wmt_14; `math`/`natural_qa` dropped as data-access barriers —
see the 9B README for both stories), token-swapped per model, each entry
carrying its inline `model_deployment=` token (the per-run lease key).
Entries are **grouped by model, smallest first**: with `reclaim: stop` +
ref-count coalescing that is one vLLM cold start per block (3 total), not a
cold cycle per entry.

- **Interrupted? Just re-run `15_run_full.sh`** — completed runs are skipped.
- The post-run report is the **local-only** virtual experiment
  [`configs/virtual-experiments/qwen35-small-core.yaml`](../../configs/virtual-experiments/qwen35-small-core.yaml)
  (no official side exists, by design). For the family column next to the
  9B, the scoping regex in both virtual experiments matches all of Qwen3.5.

## What comes next (not this runbook)

The post-trained (chat) variants of all four sizes; the fable-coder-35B-A3B
LoRA arc (needs quantization); co-hosting several smalls on the 48 GiB card
(vram-aware-placement Phase 5 — capacity accounting is already in the
planner, awaiting the policy flip).

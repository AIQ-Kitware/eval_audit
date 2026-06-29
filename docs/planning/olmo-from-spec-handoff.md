# Handoff — continue the OLMo from-spec migration

You are picking up an in-progress migration of the six OLMo reproductions
(`reproduce/olmo_models/`) from **run-entry recipe reconstruction** to **faithful
from-spec replay** (replay the official `run_spec.json` verbatim instead of
re-deriving the recipe). This is the project's methodological core: reconstructing
the recipe lets recipe drift confound the reproducibility comparison, so the local
run must differ from the official only by *model execution*.

**Read first:** [`docs/planning/olmo-from-run-spec-migration-plan.md`](olmo-from-run-spec-migration-plan.md)
— the full plan with the per-model feasibility matrix (§4), the **measured**
discovery baseline (§4.1), the change list (§6), risks (§7), and sequencing (§8).
The template it mirrors is [`docs/planning/e2e-from-run-spec-migration-plan.md`](e2e-from-run-spec-migration-plan.md)
and [`docs/planning/from-spec-deployment-rewrite-plan.md`](from-spec-deployment-rewrite-plan.md)
(both IMPLEMENTED for the phi-2 e2e — the machinery already exists; this is wiring).

## Branch & state

- Branch: `impl/run-from-run-spec` (not main; commit here, do not fast-forward merge).
- Working tree clean. Done this far:
  - `b5c4cfe` fix(eee-convert) — converter registers the run's `prod_env` so the
    local `vllm/allenai-<model>` deployment resolves at HELM→EEE conversion. **This
    is a prerequisite for the whole migration and is already in.**
  - `5c31a05` **Change 4** — discovery dry-check CLI + runbook preflight.
  - `b2ebad7` **Change 1** — from-spec preset fields + run-entry reconciliation +
    the olmo-7b suite split.

## What is DONE (do not redo)

- **Change 4** — [`eval_audit/cli/check_precomputed_discovery.py`](../../eval_audit/cli/check_precomputed_discovery.py)
  (CPU-only; resolves each preset's run-entries against the corpus with the SAME
  matcher the replay uses; `--entry` overrides for ad-hoc keys; `--precomputed-root`
  defaults to the preset's own manifest root) and
  [`reproduce/olmo_models/08_check_discovery.sh`](../../reproduce/olmo_models/08_check_discovery.sh).
- **Change 1** — all 7 presets carry `precomputed_root`; the `bbq`
  `output_format_instructions=mcqa` token was dropped; **olmo-7b was SPLIT** into
  `allenai-olmo-7b-mmlu` (57 `eval_split=test` → `/data/crfm-helm-public/mmlu`) and
  `allenai-olmo-7b-lite` (19 → `/data/crfm-helm-public/lite`), both serving the one
  `allenai-olmo-7b-single` endpoint. `OLMO_TARGETS` (`reproduce/olmo_models/_lib.sh`)
  and `configs/virtual-experiments/olmo-models.yaml` updated.
  - **Verified:** all 7 presets, smoke + full → 149 entries, **0 NO_MATCH /
    0 AMBIGUOUS** (rc 0 under `--strict`). Re-confirm this after ANY preset edit.

Per-preset `precomputed_root` (the decided roots):
- `allenai-olmo-7b-mmlu` → `/data/crfm-helm-public/mmlu`
- `allenai-olmo-7b-lite` → `/data/crfm-helm-public/lite`
- `allenai-olmo-1-7-7b` → `/data/crfm-helm-public/mmlu`
- the four `*-instruct` / `olmoe` → `/data/crfm-helm-public` (parent; bbq lives under
  `safety`, the rest under `capabilities`, each benchmark in one suite → unambiguous)

## What REMAINS (in order)

1. **Change 2 — exporter threading (CPU; verify, likely no-op).** Confirm
   `_manifest_doc` in [`eval_audit/integrations/infer_stack/adapter.py`](../../eval_audit/integrations/infer_stack/adapter.py)
   threads `from_run_spec` + `precomputed_root` preset-agnostically (the e2e fix
   should already generalize). Acid test: run `export-benchmark-bundle --preset
   allenai-olmo-7b-mmlu --from-spec …` and assert the generated manifest carries
   `from_run_spec: true`, `precomputed_root: /data/crfm-helm-public/mmlu`, and
   `model_deployment: vllm/allenai-olmo-7b`. If `_manifest_doc` drops them, fix it
   exactly as e2e Change 2a (stop hardcoding `precomputed_root: None`, read both
   from the spec).
2. **Change 3 — grid wiring (CPU).** In
   [`reproduce/olmo_models/10_run_smoke_grid.sh`](../../reproduce/olmo_models/10_run_smoke_grid.sh)
   and `15_run_full_grid.sh`, append `--from-spec` to the `export-benchmark-bundle`
   call **unconditionally** (every OLMo preset is comparable — no e2e-style
   `incomparable` carve-out). The `eval-audit-run` line is unchanged (the bridge
   picks the pipeline from `manifest['from_run_spec']`).
3. **Change 6 — tests (CPU).** (a) Corpus-gated pytest wrapping the dry-check
   (mirror `tests/test_e2e_from_spec_bundle.py`; skip if `/data/crfm-helm-public`
   absent) asserting 0 NO_MATCH / 0 AMBIGUOUS for all 7 presets. (b) A comparability
   test: official `huggingface/olmo-2-…` (or `together/olmo-7b`) vs local
   `vllm/allenai-…` → `same_deployment=no` via `normalized/diff.py`.
4. **Change 7 — data_dir hardening (CPU).** Port `8d96a47` (the e2e
   `env > settings.yaml pin > /data default` resolution + NFS/autofs warning) into
   `reproduce/olmo_models/_lib.sh`, replacing the current hard
   `INFER_STACK_DATA_DIR=$HOME/.local/share/infer_stack`; pin `data_dir` in
   `reproduce/olmo_models/config/infer_stack/settings.yaml`. (Independent of
   from-spec; the file is open anyway.)
5. **Change 8 — docs (CPU).** Update `reproduce/olmo_models/README.md` (from-spec is
   now default; the olmo-7b split; how the deployment-rewrite works) and annotate
   `NOTES-bbq-instructions-drift.md` / `NOTES-dropped-run-expander-keys.md` as
   **resolved by from-spec** (keep as the historical "why").
6. **Change 5 — first GPU run + downstream verification (GPU, user's shell).** Smoke
   one preset end-to-end on `aiq-gpu`; confirm the produced run dir == official
   `run_spec.name`, the recorded `model_deployment` is `vllm/allenai-<model>`, and
   the per-scenario report shows `same_deployment=no`. Then full grid + compose +
   summary; confirm pairing + that `comparability_unknown:*` warnings clear.

## CRITICAL environment gotchas

- **The e2e venv interpreter is a DANGLING symlink for the `agent` user.**
  `dev/e2e-tests/.venv/bin/python` points into edward.wang's uv store, which isn't
  present for `agent`. To run Python AS the agent user, drive the **identical**
  CPython build from agent's home with an explicit path:
  ```bash
  REPO=/home/local/KHQ/edward.wang/code/eval_audit
  PY=/home/agent/.local/share/uv/python/cpython-3.14-linux-x86_64-gnu/bin/python3.14
  PP="$REPO:$REPO/submodules/aiq-magnet:$REPO/dev/e2e-tests/.venv/lib/python3.14/site-packages"
  env PYTHONPATH="$PP" "$PY" -m eval_audit.cli.check_precomputed_discovery --preset allenai-olmo-7b-mmlu --mode full --strict
  ```
  (PYTHONPATH does NOT process `.pth` editable installs, so add `$REPO` AND
  `submodules/aiq-magnet` explicitly — that's why both are on the path.) In the
  **user's own shell** (`edward.wang`), the venv works normally and
  `bash reproduce/olmo_models/08_check_discovery.sh` just runs.
- **venv dependency pin:** `transformers>=4.53,<5` + `huggingface_hub==0.36.2` — see
  [`dev/e2e-tests/NOTES.md`](../../dev/e2e-tests/NOTES.md). transformers 5.x imports
  `is_offline_mode` from huggingface_hub (absent in 0.36.2) and breaks the HELM→EEE
  rebuild. Do NOT "fix" by upgrading hub.
- **The deployment-rewrite is AUTOMATIC** once `--from-spec` is wired: the exporter
  sets the manifest's `model_deployment` to the bundle's native `vllm/allenai-<model>`,
  the magnet CLI rewrites `adapter_spec.model_deployment` to it, and `b5c4cfe` makes
  it resolve at conversion. You do NOT need an override yaml (that's the hf-only path).

## Verify after every preset edit (the safety net)

Run the dry-check (full + smoke, `--strict`) across all 7 presets and require
**0 NO_MATCH / 0 AMBIGUOUS** before moving on — either via
`bash reproduce/olmo_models/08_check_discovery.sh` (user shell) or the
`env PYTHONPATH=… $PY -m eval_audit.cli.check_precomputed_discovery …` loop (agent
shell). It enumerates each root once (~4–6 s) and matches in memory.

## Hard-won lessons — do NOT repeat these

- **VERIFY against the real corpus / public index — never trust a subagent's sample
  or assume.** During this work two "duplicate entry" claims were made and both were
  wrong (caught by the user). Ground every claim with the dry-check + `find
  /data/crfm-helm-public …` + `grep … /data/crfm-helm-audit-store/indexes/official_public_index.csv`.
- **The facts (verified):** the olmo-7b 5 HELM-Lite MMLU subjects appear **twice** in
  the public index because HELM ran olmo-7b under **two suites** (HELM-Lite v1.2.0 +
  full MMLU v1.1.0) — two genuine official runs, which is why olmo-7b is split.
  olmo-1.7-7b appears **once** per subject (never in Lite) and has 57 unique
  `eval_split=test` entries → 1:1, **no dupes**. Every run-entry across all 7 presets
  maps to exactly one official run.
- A no-`eval_split` mmlu entry is a *loose* token-subset key: what it resolves to
  depends on what official runs exist, so always check the matched dir, not the
  entry text.

## Commit discipline

Commit each Change as its own logical unit on `impl/run-from-run-spec`, with a body
explaining the *why*, ending with:
`Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
Do not auto-commit submodule gitlink bumps. Keep a `dev/journals/claude.md` entry.

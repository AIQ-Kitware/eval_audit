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

## What REMAINS

All CPU changes are now DONE (Changes 2, 3, 6, 7, 8 — committed on
`impl/run-from-run-spec`; see the plan's §6 status markers and the commit hashes
below). **Only Change 5 remains, and it needs a GPU + the user's own shell.**

- **Change 5 — first GPU run + downstream verification (GPU, `aiq-gpu`, user's
  shell).** Smoke one preset end-to-end (start with an instruct preset — single
  `capabilities`/`safety` root, 4 entries); confirm the produced run dir ==
  official `run_spec.name`, the recorded `model_deployment` is
  `vllm/allenai-<model>`, and the per-scenario report shows `same_deployment=no`.
  Then full grid → `20_index_local` → `30_compose` → `40_build_summary`; confirm
  pairing and that the `comparability_unknown:*` warnings clear for benchmarks with
  a public counterpart. Run `bash reproduce/olmo_models/08_check_discovery.sh`
  first as the preflight (must be 0 NO_MATCH / 0 AMBIGUOUS). Must run in
  edward.wang's shell — the agent's e2e venv interpreter is a dangling symlink (see
  the env gotchas below).
- **Change 6 parity diff (deferred sub-item of an otherwise-DONE change).** Once
  Change 5 produces a from-spec BBQ run dir, diff its `run_spec.json` / `stats.json`
  against the archived run-entry result to quantify the now-removed
  `output_format_instructions` drift — the methodology deliverable the NOTES
  describe. (The discovery + comparability tests are already committed in
  `tests/test_olmo_from_spec.py`.)

### Done this session (CPU changes)

| Change | Commit | What |
|---|---|---|
| 2 | (verify-only, no code) | `_manifest_doc` threading is preset-agnostic — drove `materialize_benchmark_bundle(from_run_spec=True)` for olmo-7b-mmlu/-lite/-olmoe and asserted `from_run_spec`/`precomputed_root`/`model_deployment: vllm/allenai-…`. |
| 3 | `99bdc0e` | `--from-spec` appended unconditionally to both grids' `export-benchmark-bundle`. |
| 6 | `037ba68` | `tests/test_olmo_from_spec.py` — corpus-gated discovery (14/14 blocks resolve 1:1) + pure comparability proof (`same_deployment=no`). |
| 7 | `1ad68a8` | data_dir resolved env > settings.yaml pin > /data default + NFS/autofs warn; `settings.yaml` pins `/data/service/infer-stack`. |
| 8 | `90d9581` | README from-spec default + olmo-7b split + Steps `08`; NOTES annotated resolved-by-from-spec; stale six→seven counts. |

> **No pytest for the `agent` user.** Validate by driving the test bodies'
> underlying helpers directly with the `env PYTHONPATH=… $PY …` recipe below
> (that's how this session verified Change 6); under CI / edward.wang's venv,
> `pytest tests/test_olmo_from_spec.py` runs normally.

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

# OLMo smoke-suite runner + grouped report

> **Migration note (2026-06-22):** the infer-stack serving recipe quoted below
> predates the catalog/leasing CLI rewrite. The verbs have changed —
> `switch --profile X --apply` + `wait-ready` → `acquire X --yes` + `wait`,
> `down` → `release --all --evict`, `list-profiles` → `catalog endpoint list`,
> and the `<preset>-single` profiles are now catalog **endpoints**. The runnable
> scripts under [`reproduce/olmo_models/`](../../../reproduce/olmo_models/) and their
> README are the current source of truth; see
> [`infer-stack-cli-api-migration.md`](infer-stack-cli-api-migration.md). The
> text below is kept as the original design narrative.

**Goal:** Write code that runs the **smoke** manifests for the six AllenAI OLMo
model presets and produces a **single grouped report** spanning all six, without
collapsing their per-model identities. Deliverable is a runnable script (plus one
virtual-experiment config), modeled on the single-experiment runners in
[`dev/e2e-tests/`](../../../dev/e2e-tests/).

**Why grouped, not shared `experiment_name`:** the six presets keep their own
`experiment_name`/`suite` (they run as six isolated jobs); grouping happens one
layer up via a *virtual experiment* that re-stamps their index rows into one
logical experiment. Sharing a literal `experiment_name` across presets would be
destructive (Stage 5 filters by exact `experiment_name`, so per-model reports
would no longer be addressable) and would conflate run identity with report
identity. See the rationale already established for this repo; the virtual-experiment
path is additive and reversible.

---

## The six targets

All six use `access_kind: vllm-direct` and a `*-single` infer-stack profile.
From `PRESET_CONFIGS` in
[`eval_audit/integrations/infer_stack/adapter.py`](../../../eval_audit/integrations/infer_stack/adapter.py):

| Preset | infer-stack profile | smoke `experiment_name` (== `suite`) | smoke entries |
|---|---|---|---|
| `allenai-olmo-7b` | `allenai-olmo-7b-single` | `audit-allenai-olmo-7b-smoke` | 1 |
| `allenai-olmo-1-7-7b` | `allenai-olmo-1-7-7b-single` | `audit-allenai-olmo-1-7-7b-smoke` | 4 |
| `allenai-olmo-2-0325-32b-instruct` | `allenai-olmo-2-0325-32b-instruct-single` | `audit-allenai-olmo-2-0325-32b-instruct-smoke` | 4 |
| `allenai-olmo-2-1124-13b-instruct` | `allenai-olmo-2-1124-13b-instruct-single` | `audit-allenai-olmo-2-1124-13b-instruct-smoke` | 4 |
| `allenai-olmo-2-1124-7b-instruct` | `allenai-olmo-2-1124-7b-instruct-single` | `audit-allenai-olmo-2-1124-7b-instruct-smoke` | 4 |
| `allenai-olmoe-1b-7b-0125-instruct` | `allenai-olmoe-1b-7b-0125-instruct-single` | `audit-allenai-olmoe-1b-7b-0125-instruct-smoke` | 4 |

The presets and their smoke `run_entries` already exist — **no `adapter.py`
change is required.**

---

## Execution model

The phi2 vLLM runner ([`e2e-phi_2-vllm-philosophy.sh`](../../dev/e2e-tests/e2e-phi_2-vllm-philosophy.sh))
is the template. Its shape, per experiment:

1. `infer-stack switch --profile <profile> --apply` then `infer-stack wait-ready` — bring the model up.
2. `export-benchmark-bundle --preset <preset> --bundle-root <dir> [...]` — materialize the bundle.
3. `eval-audit-run --run 1 <bundle>/{full,smoke}_manifest.yaml` — execute.
4. `eval-audit-index` → `eval-audit-analyze-experiment` → `eval-audit-build-summary`.

**Key differences for this task:**

- **Six models, one GPU box → loop, don't co-host.** The OLMo models (incl. a 32b
  and a 13b) won't all fit at once. The runner switches to one profile, runs its
  smoke manifest, then switches to the next. Treat each model as a sequential
  iteration of steps 1–3.
- **Smoke, not full.** Use `<bundle>/smoke_manifest.yaml` (the bundle emits both;
  the phi2 example happens to run `full_manifest.yaml`).
- **`vllm-direct`, not litellm/openai-compatible.** The phi2 vLLM script routes
  through the LiteLLM gateway and passes `--base-url`/`--api-key-value`. The OLMo
  presets are `vllm-direct`: `_select_access`/`_resolve_api_key` in
  [`adapter.py`](../../../eval_audit/integrations/infer_stack/adapter.py#L754) take the
  base URL from the **profile contract**, so `--base-url`/`--api-key-value` are
  *not* required on `export-benchmark-bundle`. The runner should rely on the
  profile contract; pass `--api-key-value` only if a deployment needs it.
- **Index once, group once, at the end.** Per-model `analyze`/`build-summary` are
  optional (useful for debugging a single model). The grouped report is the point,
  so run Stages 5–6 over the *virtual* experiment after all six have landed in the
  master index.

---

## Files to write

### 1. `dev/e2e-tests/e2e-olmo-smoke-grid.sh` (new)

A bash runner, `set -euo pipefail`, mirroring the e2e style. Structure:

```bash
#!/bin/bash
set -euo pipefail

export INFER_STACK_CONFIG_DIR="./config/infer_stack"
export EVAL_AUDIT_SKIP_LOCAL_REPEAT=1   # carried from e2e convention
export EVAL_AUDIT_GROUP_STRIP=1

STORE_ROOT="${AUDIT_STORE_ROOT:-/data/crfm-helm-audit-store}"
RESULTS_ROOT="${AUDIT_RESULTS_ROOT:-/data/crfm-helm-audit}"
VEXP_MANIFEST="${VEXP_MANIFEST:-configs/virtual-experiments/olmo-models-smoke.yaml}"

# preset  ->  infer-stack profile  (parallel arrays, run in this order)
PRESETS=(
  "allenai-olmo-7b:allenai-olmo-7b-single"
  "allenai-olmo-1-7-7b:allenai-olmo-1-7-7b-single"
  "allenai-olmo-2-1124-7b-instruct:allenai-olmo-2-1124-7b-instruct-single"
  "allenai-olmoe-1b-7b-0125-instruct:allenai-olmoe-1b-7b-0125-instruct-single"
  "allenai-olmo-2-1124-13b-instruct:allenai-olmo-2-1124-13b-instruct-single"
  "allenai-olmo-2-0325-32b-instruct:allenai-olmo-2-0325-32b-instruct-single"
)

eval-audit-check-env

for pair in "${PRESETS[@]}"; do
  preset="${pair%%:*}"; profile="${pair##*:}"
  bundle_root="./bundles/${preset}"

  infer-stack switch --profile "$profile" --apply
  infer-stack wait-ready

  python -m eval_audit.integrations.infer_stack export-benchmark-bundle \
    --preset "$preset" --bundle-root "$bundle_root"

  eval-audit-run --run 1 "$bundle_root/smoke_manifest.yaml"
done

# Index everything the six runs produced, once.
eval-audit-index --results-root "$RESULTS_ROOT" --report-dpath "$STORE_ROOT/indexes"

# Group: build the virtual experiment, which runs analyze + summary over the union.
eval-audit-build-virtual-experiment --manifest "$VEXP_MANIFEST"
```

Notes / decisions for the implementer:
- **Order smallest→largest** (1b/7b first, 32b last) so a smoke failure on a tiny
  model surfaces fast before the expensive load.
- **Keep going on a single-model failure?** Default to fail-fast (`set -e`). If the
  user wants "run all six, report what passed," wrap the per-model body in a
  function and collect non-zero exits into a summary at the end — call this out as
  an option, don't silently swallow failures.
- **`infer-stack switch` between models is the teardown.** If the backend needs an
  explicit `down` first, add `infer-stack switch --profile <next> --apply` semantics
  per how phi2 does it; verify against the live `infer-stack` CLI before finalizing.
- Per-model debug reports are intentionally omitted; add a `--per-model` flag that
  runs `eval-audit-analyze-experiment`/`eval-audit-build-summary` per
  `experiment_name` if needed.

### 2. `configs/virtual-experiments/olmo-models-smoke.yaml` (new)

Models the grouping. Mirrors
[`open-helm-models-reproducibility.yaml`](../../../configs/virtual-experiments/open-helm-models-reproducibility.yaml).

```yaml
schema_version: 1
name: olmo-models-smoke
description: >
  Smoke-suite reproductions for the six AllenAI OLMo presets, grouped into one
  report. Validates the run + grouping path end-to-end on the cheap smoke entries.

scope:
  models:
    - "regex:^allenai/olmo"   # matches olmo / olmo-1.7 / olmo-2 / olmoe variants

sources:
  - kind: audit_index
    fpath: /data/crfm-helm-audit-store/indexes/audit_results_index.csv
    include_experiments:
      - audit-allenai-olmo-7b-smoke
      - audit-allenai-olmo-1-7-7b-smoke
      - audit-allenai-olmo-2-0325-32b-instruct-smoke
      - audit-allenai-olmo-2-1124-13b-instruct-smoke
      - audit-allenai-olmo-2-1124-7b-instruct-smoke
      - audit-allenai-olmoe-1b-7b-0125-instruct-smoke

  # Optional: include the public-HELM side for comparison. For a pure pipeline
  # smoke check this can be omitted — the report will then be local-only and
  # surface comparability_unknown:* warnings, which is expected, not a bug.
  - kind: official_public_index
    fpath: /data/crfm-helm-audit-store/indexes/official_public_index.csv
    pre_filter:
      kind: helm_stage1
      inventory_fpath: /data/crfm-helm-audit-store/analysis/filter_inventory.json

output:
  root: /data/crfm-helm-audit-store/virtual-experiments/olmo-models-smoke
```

`eval-audit-build-virtual-experiment` re-stamps the six experiments' rows with
`name: olmo-models-smoke` and runs the existing analyze→summarize pipeline over
the synthesized index — one combined report under `output.root`, with per-model
provenance preserved in the underlying index. (See
[`eval_audit/cli/build_virtual_experiment.py`](../../../eval_audit/cli/build_virtual_experiment.py)
and [`eval_audit/virtual/compose.py`](../../../eval_audit/virtual/compose.py).)

---

## Verification checklist

- `bash -n dev/e2e-tests/e2e-olmo-smoke-grid.sh` (syntax) and
  `python -m py_compile` on any touched Python.
- Validate the YAML loads under the virtual-experiment manifest schema
  (`eval_audit/virtual/manifest.py`) — dry-run `eval-audit-build-virtual-experiment`
  if it supports one, else confirm the loader accepts it.
- After a run: confirm `audit_results_index.csv` has rows for all six
  `experiment_name`s, and that `…/virtual-experiments/olmo-models-smoke/` contains
  the combined report tree (sankeys + per-metric breakdown spanning all six models).
- Sanity: the combined report's model axis lists six OLMo variants; per-model
  experiments remain independently analyzable (grouping was additive).

---

## Risks / open questions for the implementer

1. **Backend teardown semantics.** Confirm `infer-stack switch --apply` fully
   releases the previous model's GPU before the next loads (esp. before the 32b).
   If not, add an explicit down/wait step.
2. **Large models on the smoke box.** The 32b/13b may need a multi-GPU resource
   profile (`gpu-tp2-*`). If the `*-single` profile can't place them, either fix
   the profile mapping or scope the smoke grid to the models that fit and `log`
   what was dropped (don't silently skip).
3. **`vllm-direct` base URL.** Assumed to come from the profile contract. Verify by
   inspecting the emitted `model_deployments.yaml` for one OLMo bundle before
   trusting the loop; add `--base-url`/`--api-key-value` only if the contract
   doesn't carry them.
4. **HF gating.** OLMo weights are open, but confirm `huggingface-cli login` is set
   if any tokenizer/model pull needs it — a gating failure here is a recipe/env
   filter, not a reproducibility failure.
5. **Public-side overlap may be empty for smoke entries.** That's fine; decide
   whether to keep the `official_public_index` source (comparison) or drop it
   (pure pipeline smoke). Default: keep it, expect `comparability_unknown` noise.

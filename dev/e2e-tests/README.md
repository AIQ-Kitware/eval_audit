# phi-2 e2e — smoke + full grids + grouped report

End-to-end exercises of the audit pipeline on Microsoft **phi-2**, restructured
into the same shape as [`reproduce/olmo_models/`](../../reproduce/olmo_models/)
(which was itself derived from these scripts). Runs the three phi-2 scenarios in
two passes — a cheap **smoke** preflight and the **full** batch — and folds the
**full** results into a **single grouped report** via a virtual experiment. The
smoke grid is a fast end-to-end exercise of the run path; the full grid is the
batch the downstream index → compose → summary steps operate on.

These are pipeline tests, not a reproducibility claim: phi-2 + MMLU-philosophy is
small and fast, the comparable/incomparable pair is a positive/negative control,
and the run is **local-only** (no public-HELM comparison side).

## The three scenarios

Each keeps its own `experiment_name`/`suite` and runs as an isolated job; the
virtual experiment re-stamps their index rows under one name (`e2e-phi2`) for
reporting. Ordered comparable baseline → incomparable control → HF-direct:

| scenario (`name`) | transport | full `experiment_name` | what it exercises |
|---|---|---|---|
| `e2e-phi_2-vllm-philosophy` | vllm | `e2e-phi_2-vllm-philosophy-full` | comparable baseline: phi-2 on vLLM via LiteLLM |
| `e2e-phi_2-vllm-philosophy-incomparable` | vllm | `e2e-phi_2-vllm-philosophy-incomparable-full` | negative control: same, but `temperature=1` (a deliberate recipe deviation the planner should flag) |
| `e2e-phi_2-huggingface-philosophy` | hf | `e2e-phi_2-huggingface-philosophy-full` | HELM loads `microsoft/phi-2` directly from HuggingFace (no infer-stack) |

The two vLLM presets and their smoke/full `run_entries` live in
[`eval_audit/integrations/infer_stack/adapter.py`](../../eval_audit/integrations/infer_stack/adapter.py);
the HF scenario is driven by the checked-in manifests under
[`manifests/`](manifests/). Each scenario has a `-smoke`
(`max_eval_instances=5`) and a `-full` (`max_eval_instances=1000`) experiment;
the grouped report uses `-full`.

## Transports

The grid branches per scenario on its `transport` (the second field of each
`E2E_TARGETS` row in [`_lib.sh`](_lib.sh)):

- **`vllm`** — `infer-stack switch` brings phi-2 up on vLLM and fronts it with
  the LiteLLM gateway; `export-benchmark-bundle` materializes the bundle from the
  preset. The phi-2 presets already declare `access_kind: openai-compatible`, so
  the export passes only the LiteLLM base-url + master key — **no** `--access-kind`
  override (unlike `reproduce/olmo_models`, whose presets declare `vllm-direct`).
- **`hf`** — no infer-stack: HELM's HuggingFace path loads `microsoft/phi-2`
  directly (the manifest's `enable_huggingface_models`), and the run is the
  checked-in `manifests/<experiment>.yaml`.

## Serving profile

The vLLM scenarios use the infer-stack profile `phi2-single`, shipped here:

- [`config/infer_stack/models.yaml`](config/infer_stack/models.yaml) — the `phi2`
  vLLM model + the `phi2-single` profile (single GPU, fronted by LiteLLM).
- [`config/infer_stack/config.yaml`](config/infer_stack/config.yaml) — points
  `user_models_file` at the models.yaml.

`_lib.sh` sets `INFER_STACK_CONFIG_DIR` to that dir by default.
`05_check_profiles.sh` validates the profile is present before the grid runs.

## Steps

```bash
./00_check_env.sh         # eval-audit-check-env
./05_check_profiles.sh    # verify the phi2-single profile is defined (vLLM scenarios)
./10_run_smoke_grid.sh    # preflight: per scenario -> (vllm: switch -> wait-ready -> export bundle) -> run smoke
./15_run_full_grid.sh     # per scenario: same, but run the FULL manifest (the batch)
./20_index_local.sh       # eval-audit-index -> audit_results_index.csv (verifies the -full run dirs)
./30_compose.sh           # build the virtual experiment from the -full runs (the grouping step)
./40_build_summary.sh     # aggregate publication surface across all three
```

The smoke preflight (`10`) is optional once you trust the path — `15` is the run
that feeds `20`/`30`/`40`. The grouping manifest is checked in at
[`configs/virtual-experiments/e2e-phi2.yaml`](../../configs/virtual-experiments/e2e-phi2.yaml)
(the `-smoke` variant,
[`e2e-phi2-smoke.yaml`](../../configs/virtual-experiments/e2e-phi2-smoke.yaml),
is kept for grouping the smoke preflight instead — point `VEXP_MANIFEST` at it).

## Knobs (env vars)

- `AUDIT_STORE_ROOT` (default `/data/crfm-helm-audit-store`)
- `AUDIT_RESULTS_ROOT` (default `/data/crfm-helm-audit`)
- `VEXP_MANIFEST` — override the grouping manifest path (e.g. the `-smoke` variant)
- `INFER_STACK_CONFIG_DIR` — infer-stack config providing the `phi2-single` profile
- `E2E_KEEP_GOING=1` — in `10_run_smoke_grid.sh` / `15_run_full_grid.sh`, attempt
  every scenario and report failures at the end instead of stopping on the first
  error (default: fail-fast)
- `E2E_FORCE_RERUN=1` — in `10_run_smoke_grid.sh` / `15_run_full_grid.sh`, clear
  each scenario's prior result dir (`$AUDIT_RESULTS_ROOT/<experiment>`) before
  running. `eval-audit-run` schedules with kwdagger `skip_existing=1`, so a
  scenario whose previous run already wrote its `DONE` sentinel is otherwise
  skipped on re-invocation. The **smoke** grid force-reruns by default (cheap
  preflight); the **full** grid keeps it opt-in.
- `EVAL_AUDIT_SKIP_LOCAL_REPEAT=1`, `EVAL_AUDIT_GROUP_STRIP=1` — set by `_lib.sh`,
  carried verbatim from the original e2e scripts (one local attempt per scenario,
  group prefix stripped)

## What this assumes / produces

- **Local-only by default.** The grouping manifest has no `official_public_index`
  source, so the report is the union of the three local full runs; comparability
  facts a public counterpart would supply collapse to `status=unknown` and
  surface as `comparability_unknown:*` warnings — expected for a local-only batch,
  not a bug. The incomparable scenario additionally deviates the recipe
  (`temperature=1`), which the planner flags per-packet. To compare against public
  HELM, uncomment the `official_public_index` source in the manifest.
- **LiteLLM / openai-compatible transport for the vLLM scenarios.**
  `10`/`15` resolve the LiteLLM endpoint + master key via `infer-stack env --key`
  and hand them to `export-benchmark-bundle`. HELM talks to the LiteLLM gateway,
  which routes to phi-2's vLLM backend.
- **No HuggingFace token required.** `microsoft/phi-2` and the MMLU dataset are
  public, so neither the HF-direct scenario nor the vLLM scenarios need a token
  (this is why there is no `06_check_hf_auth.sh`, unlike `reproduce/olmo_models`,
  whose gated `gpqa` runs do).

## Output layout

```
$AUDIT_STORE_ROOT/virtual-experiments/e2e-phi2/
├── manifest.yaml
├── indexes/                 # synthesized index slice (rows re-stamped e2e-phi2)
├── analysis/
│   ├── core-reports/<one per scenario packet>/
│   └── experiment_summary.{json,csv,txt}
└── reports/aggregate-summary/   # the grouped publication surface
```

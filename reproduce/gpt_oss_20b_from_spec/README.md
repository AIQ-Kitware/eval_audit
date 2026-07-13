# gpt-oss-20b — from-spec reproduction (bbq, ifeval, mmlu_pro, gpqa)

Faithful **from-spec** reproduction of the four public HELM `openai/gpt-oss-20b`
rows that need **neither a closed (GPT-4o) judge nor a co-hosted local judge**:
`bbq`, `ifeval`, `mmlu_pro`, `gpqa`. Each is replayed from its **official
`run_spec.json`** (exact-path freeze) against a local vLLM service, then compared
against the published HELM result by logical run key.

This is the single-model analogue of
[`reproduce/olmo_models_combined/`](../olmo_models_combined/): same from-spec
exporter (`export-benchmark-bundle --from-spec --freeze-rel-paths`), same
mandatory containerized HELM execution, same per-run leasing — just one model and
one serving endpoint instead of a multi-model fan-out. It deliberately does **not**
reuse the older `gpt_oss_20b_vllm` / `finish_qwen25_gptoss` presets, which predate
the from-spec/serving refactors and pin a run-entry (not from-spec) recipe.

## Why these four (and not the other seven)

The official gpt-oss-20b corpus has **11 rows**. The other seven are
recipe/environment-blocked, not reproducibility-blocked, so they are out of scope
here:

| public row | blocker | in scope? |
|---|---|---|
| `bbq` | metric-scored, public data | ✅ |
| `ifeval` | metric-scored, public data | ✅ |
| `mmlu_pro` | exact-match / CoT, public data | ✅ |
| `gpqa` | exact-match / CoT; **gated** `Idavidrein/gpqa` (needs HF token) | ✅ (with HF auth) |
| `air_bench_2024` | **closed judge** — GPT-4o only, no open fallback | ✗ |
| `omni_math`, `wildbench`, `anthropic_red_team`, `harm_bench`, `simple_safety_tests`, `xstest` | **closed judge** — GPT-4o + Llama-405B ensemble (official primary metric depends on GPT-4o) | ✗ |

The seven excluded rows still surface in the report as **unpaired official rows** —
that is the correct recipe/environment filtering story, not a reproducibility
failure. See [`docs/planning/judge-identity-inventory.md`](../../docs/planning/judge-identity-inventory.md).

## Design at a glance

- **Preset** — one single-model from-spec preset, `openai-gpt-oss-20b`
  (`eval_audit/integrations/infer_stack/preset_configs.yaml`), shaped exactly like
  the OLMo-2 / OLMoE instruct singles: top-level profile facts, `precomputed_root:
  /data/crfm-helm-public` inside each manifest block, run_entries with **no** inline
  `model_deployment=` token (the exporter injects `vllm/openai-gpt-oss-20b` as the
  from-spec rewrite target).
- **Protocol — chat/harmony, matching the official deployment.** The public rows
  used `together/gpt-oss-20b` (a `TogetherChatClient` that applies the harmony
  template), so a faithful replay serves **chat**, not the frozen `gpt_oss_20b_vllm`
  preset's completions workaround. This is the central design choice — see Caveats.
- **Export** — `--from-spec --freeze-rel-paths` (exact-path replay), routed through
  LiteLLM (`--access-kind openai-compatible`).
- **Serving** — one endpoint, `gpt-oss-20b-single` (shipped in
  [`config/infer_stack/catalog.yaml`](config/infer_stack/catalog.yaml)); mxfp4-native
  weights, ~40 GiB headroom on a single card.
- **Experiment** — one (`audit-openai-gpt-oss-20b-from-spec-full`), folded into the
  virtual experiment
  [`gpt-oss-20b-from-spec.yaml`](../../configs/virtual-experiments/gpt-oss-20b-from-spec.yaml).

## Setup (self-contained)

This runbook ships everything it needs — no dependency on a sibling runbook:

- [`config/infer_stack/`](config/infer_stack/) — the gpt-oss-20b model + the
  `gpt-oss-20b-single` endpoint (`catalog.yaml`) and durable leasing settings
  (`settings.yaml`). `_lib.sh` points `INFER_STACK_CONFIG_DIR` here.
- `_lib.sh` — resolves the repo root, the store/results roots, the
  docker-mountable `INFER_STACK_DATA_DIR`, `GPTOSS_CONTAINER_IMAGE`, HuggingFace-token
  resolution, and the `EVAL_AUDIT_*` group-strip conventions, then the
  gpt-oss-specific bits (vexp manifest, preset name, endpoint, fan-out width).
- `06_check_hf_auth.sh` / `07_check_container_image.sh` — self-contained preflights
  (the container image is built at the repo root via `./docker/build.sh`).

## Steps

```bash
../../docker/build.sh            # build eval-audit-helm-runner:dev (containerization is ON)
./00_check_env.sh                # eval-audit-check-env
./05_check_profiles.sh           # verify the gpt-oss-20b-single endpoint is defined
./06_check_hf_auth.sh            # verify a HuggingFace token (gated gpqa dataset needs it)
./07_check_container_image.sh    # verify docker + the pinned image (required)
./08_check_discovery.sh          # freeze the bundle (each entry resolves 1:1 or fails) + validate every frozen run_spec.json exists
./10_run_smoke.sh                # smoke: gc -> gateway bootstrap -> export --freeze-rel-paths -> run smoke (ifeval+bbq, 5 instances) --lease
./15_run_full.sh                 # full from-spec batch (bbq, ifeval, mmlu_pro, gpqa; max_eval_instances=1000)
./20_index_local.sh              # eval-audit-index -> audit_results_index.csv
./30_compose.sh                  # build the virtual experiment from the full run
./40_build_summary.sh            # aggregate publication surface
```

The smoke preflight (`10`) is optional once you trust the path — `15` is the run
that feeds `20`/`30`/`40`.

## Knobs (env vars)

Base setup (from `_lib.sh`): `GPTOSS_CONTAINER_IMAGE`, `AUDIT_STORE_ROOT`,
`AUDIT_RESULTS_ROOT`, `INFER_STACK_*`, `LITELLM_PORT`, `GPTOSS_FORCE_RERUN`, ….
gpt-oss-specific:

- `GPTOSS_TMUX_WORKERS` (default `2`) — concurrent HELM client runs. All four
  run_entries hit the SAME served model (they share one lease via ref-counting),
  so this just bounds how many run concurrently against the one vLLM endpoint.
- `GPTOSS_VEXP_MANIFEST` — override the grouping manifest path.
- `GPTOSS_FORCE_RERUN` — default **on** in `10_run_smoke.sh` (cheap preflight),
  **off** in `15_run_full.sh` (expensive); clears the prior result dir so
  kwdagger's `skip_existing` re-executes.
- `PRECOMPUTED_ROOT` (08 only) — override the corpus root the freeze resolves against.

## Status / caveats

- **Exporter + freeze + preset are wired here; the GPU end-to-end run is the
  remaining verification.** `08_check_discovery.sh` proves the freeze resolves 1:1
  against the corpus (CPU-only); the first `15_run_full.sh` on GPUs is what
  confirms serving + container + comparison.

- **Null-content chat crash (resolved on the faithful chat path).** gpt-oss is a
  reasoning model: when it spends its whole generation budget in the reasoning
  channel without emitting a final-channel answer (`finish_reason=length`), the
  local vLLM OpenAI-compat endpoint returns `message.content = null`, which
  un-patched HELM crashes on (`AttributeError: 'NoneType' object has no attribute
  'strip'`). The official `together/gpt-oss-20b` run returns `""` (empty string) for
  the *identical* event — verified on the public run dirs (`content is None` = 0;
  `content == ""` = 59/541 for ifeval, part of the published score). See
  [`docs/helm-null-completion-text-patch-proposal.md`](../../docs/helm-null-completion-text-patch-proposal.md)
  ("Confirmed root cause").

  **The from-spec chat path now normalizes `null → ""` faithfully**, via eval_audit's
  null-safe chat client (`eval_audit.integrations.helm_clients.NullSafeOpenAIChatClient`,
  selected by `_benchmark_client_class`) — HELM's own `client_spec.class_name` seam,
  no HELM-source edit. Local runs therefore emit exactly what Together emitted (empty
  prediction, scored through the normal metric path) instead of crashing. Note this is
  *not* window- or `max_tokens`-related: the from-spec freeze replays the official
  budgets (`bbq=10001`, others `14096`) and no official instance's `prompt+output`
  exceeds the local `max_model_len`. The old **completions fallback**
  (`litellm/gpt-oss-20b-local` / `OpenAILegacyCompletionsClient`) remains available for
  *liveness* only, but is no longer needed and is unfaithful (it drops the harmony
  chat/reasoning path the official run used).

- **gpqa is gated.** `Idavidrein/gpqa` requires an HF token whose account accepted
  the dataset terms (`06_check_hf_auth.sh` gates on this). To run only the three
  public benchmarks, drop the `gpqa:` entry from the preset's `full_manifest` and
  skip the HF gate.

- **Local corpus mirror may be partial.** The freeze needs the official gpt-oss
  run dirs present under `/data/crfm-helm-public` (capabilities/v1.12.0 for
  ifeval/mmlu_pro/gpqa; safety/v1.14.0 for bbq). If they are not mirrored locally,
  `08_check_discovery.sh` reports NO_MATCH (CPU-only, before any GPU work) — pull
  the missing run dirs from the canonical host (e.g. aiq-gpu) first.

- **Run_spec param strings.** The run_entries use the minimal token set that
  uniquely resolves each official gpt-oss row; the freeze replays the full official
  `run_spec.json` verbatim. Before a publish-grade run, confirm each row's params
  (especially any `num_output_tokens=`) against
  `/data/crfm-helm-audit-store/indexes/official_public_index.csv`.

- **Compared against public HELM.** The grouping manifest's `official_public_index`
  source pairs each local run with its official counterpart by logical run key.
  Comment that source out in
  [`gpt-oss-20b-from-spec.yaml`](../../configs/virtual-experiments/gpt-oss-20b-from-spec.yaml)
  to make the report local-only.

## Output layout

```
$AUDIT_STORE_ROOT/virtual-experiments/gpt-oss-20b-from-spec/
├── manifest.yaml
├── indexes/                 # synthesized index slice (rows re-stamped gpt-oss-20b-from-spec)
├── analysis/
│   ├── core-reports/<one per benchmark packet>/
│   └── experiment_summary.{json,csv,txt}
└── reports/aggregate-summary/   # the publication surface
```

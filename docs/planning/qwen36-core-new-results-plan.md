# Qwen 3.6 — new core results (produce, not reproduce) — implementation plan

**Status:** proposed (not started). **Author:** design session 2026-07-11.
**Prereq branch:** `impl/run-from-run-spec` (the serving/execution half — infer-stack
catalog + `eval-audit-run --lease` + containerized HELM — is the same machinery the
`qwen-combined` reproduction fan-out uses; this plan reuses it and *drops* the
from-spec/freeze/comparison half).

## 0. Goal

Produce **new** public-style HELM numbers for **Qwen 3.6** on the **classic/Lite
core** scenario set, by running HELM fresh against a **locally-served** Qwen 3.6, in
**two serving modes** (thinking vs non-thinking), and land a **standalone
(local-only)** report. There is no public HELM run for Qwen 3.6 to replay, so this
is a *compute* job, not a reproduction.

## 1. This is PRODUCE-NEW-RESULTS, not reproduction

The single most important framing. Everything the `qwen-combined` plan leaned on —
`--from-spec`, `--freeze-rel-paths`, `precomputed_root`, the
`official_public_index` pairing — is **absent here**, because there is nothing to
freeze against and no official counterpart to pair with.

| Axis | Reproduction (`qwen-combined`) | New results (this plan) |
|---|---|---|
| Source of run_entries | official run_spec.json (frozen from corpus) | **authored** (core scenario specs, model token swapped) |
| `precomputed_root` | `/data/crfm-helm-public` | **null** (compute mode) |
| Exporter flags | `--from-spec --freeze-rel-paths` | plain export (no from-spec) |
| Model known to HELM? | yes (already in `model_metadata.yaml`) | **no — must be registered** (§3A) |
| Comparison side | `official_public_index` pairing | **none** (local-only report) |
| What "success" means | local ≈ official (agreement curves) | valid HELM numbers land per scenario |

The reproducibility framing (agreement ratios, tolerance sweeps, recipe-clean
counts) **does not apply** — there is no second side. The report is a leaderboard-
style table of Qwen 3.6's core scores, per serving mode.

## 2. Scope

### 2.1 The core scenario set (9 groups)

The classic/Lite core the public Qwen models ran — reused verbatim so the numbers
are methodologically comparable to the public leaderboard (even though not *paired*):

`mmlu` (57 subjects) · `commonsense` · `gsm` · `math` · `legalbench` · `med_qa` ·
`narrative_qa` · `natural_qa` · `wmt_14` — **~85 run_entries per serving mode**
(the exact per-scenario specs are already in `official_public_index.csv` for any
`qwen/*` model; §5.2 lifts them and swaps the model token).

None of these nine gate a HuggingFace dataset (no `gpqa`), so — unlike the
reproduction fan-out — **HF auth is optional** here.

### 2.2 The model + two serving modes

Qwen 3.6 is served **twice**, as two distinct HELM model ids + deployments — the
same shape HELM already uses for Qwen3 (`qwen/qwen3-next-80b-a3b-instruct` **and**
`qwen/qwen3-next-80b-a3b-thinking` are separate ids, not a runtime flag):

| serving mode | HELM model id (proposed) | local deployment | notes |
|---|---|---|---|
| non-thinking | `qwen/qwen3.6-<size>`          | `vllm/qwen3-6-<size>-nothinking` | clean baseline for MC/exact-match core |
| thinking     | `qwen/qwen3.6-<size>-thinking` | `vllm/qwen3-6-<size>-thinking`   | reasoning traces; null-content risk (§3B) |

> **`<size>` + exact id/repo are unconfirmed (T1).** I could not verify a released
> "Qwen 3.6". Confirm the HF repo + the HELM id before execution (see §11). The plan
> is written model-size-agnostic; fill `<size>`/`tp` from the confirmed model.

## 3. The three tasks that differ from reproduction

### 3A. HELM model registration (the load-bearing new task)

`bundle_export._assert_helm_aliases_exist` (bundle_export.py:108) reads the
**vendored** HELM `model_metadata.yaml` + `tokenizer_configs.yaml` and **hard-fails**
if the model/tokenizer aren't registered. For a brand-new id this blocks the export.
And the runbook currently auto-stages only `model_deployments.yaml` into `prod_env`
(kwdagger_bridge.py:266) — **not** metadata/tokenizer.

First check: is the target id already in HELM? (`qwen/qwen3-*` variants already are;
`qwen3.6` almost certainly is not.) If present, skip this section. Otherwise pick one:

- **(A1, recommended — reusable) Local prod_env registration + relax the assert.**
  Teach the export/materialize path two new optional manifest fields —
  `model_metadata_fpath` / `tokenizer_configs_fpath` — that stage local
  `model_metadata.yaml` + `tokenizer_configs.yaml` into `prod_env` exactly as
  `model_deployments_fpath` already does, and make `_assert_helm_aliases_exist` also
  consult those local overrides (so a locally-declared model passes). HELM merges
  `prod_env/*.yaml` over its built-in config at runtime, so the in-container HELM
  then knows Qwen 3.6 with **no submodule edit and no image rebuild**. This is the
  general fix (any future new model benefits) and matches the historical brief in
  [`docs/historical/qwen35-helm-run.md`](../historical/qwen35-helm-run.md).
- **(A2, simpler one-off) Register in the vendored HELM submodule.** Add the two ids
  (instruct + thinking) to `submodules/helm/.../model_metadata.yaml` (tags:
  `TEXT_MODEL_TAG`, `INSTRUCTION_FOLLOWING_MODEL_TAG`) + `tokenizer_configs.yaml`
  (`HuggingFaceTokenizer`), then **rebuild the runner image** so the baked HELM sees
  them. Mirrors the existing `qwen3-next-80b-a3b-{instruct,thinking}` entries.
  Downsides: an intentional submodule change (gitlink bump — flag it, per CLAUDE.md)
  + an image rebuild.

Registration content is the same either way: `model_metadata` (two ids, correct
tags), `tokenizer_configs` (the Qwen3 tokenizer), and the deployment (the export
generates that). **Recommend A1.**

### 3B. Two serving modes (thinking / non-thinking)

Two infer-stack endpoints for the one model, differing only in the reasoning
toggle (Qwen3 exposes thinking via the chat template `enable_thinking` +, in vLLM, a
`--reasoning-parser`). Serve them as two endpoints rather than a per-request flag
(HELM's chat client does not reliably thread `chat_template_kwargs`):

- **`*-nothinking`** — chat template default `enable_thinking: false` (or the served
  model's non-thinking variant). Clean; `message.content` is the answer. This is the
  primary/first sweep.
- **`*-thinking`** — default Qwen3 template (thinking on) + the reasoning parser +
  a **larger** `max_model_len`/generation budget (traces are long). **Risk:** on
  short MC/exact-match core rows the model may return `message.content=null`
  (reasoning-only), which un-patched HELM crashes on — the same null-content path as
  gpt-oss. Mitigations: the null-content patch
  ([`docs/helm-null-completion-text-patch-proposal.md`](../helm-null-completion-text-patch-proposal.md))
  or a completions fallback. Watch `mmlu`/`commonsense` (max_tokens=1) closely.

Exact toggle mechanism is **T2** — confirm against vLLM's Qwen3 reasoning docs +
the served chat template before the run.

### 3C. Compute manifests + local-only report

- **Export without `--from-spec`.** `export_benchmark_bundle` with `from_run_spec=
  False` keeps `precomputed_root: None` and omits `from_run_spec` from the manifest
  (bundle_export.py:238) → HELM **computes** each run_entry fresh. No freeze, no
  discovery, no corpus.
- **Virtual experiment is local-only.** Drop the `official_public_index` source; keep
  only the `audit_index` source. `build_reports_summary` runs with
  `--no-filter-inventory` (the `40` script already has that fallback). Comparisons
  disable with `missing_official_component` — **correct here**, not a defect. (Note:
  the "keep public comparison active" convention is a *reproduction* rule; a net-new
  model has no public run, so local-only is the only honest shape.)

## 4. Artifacts to create

```
eval_audit/integrations/infer_stack/preset_configs.yaml   # 2 presets: qwen3-6-core-{nothinking,thinking}
                                                          #   (compute presets: precomputed_root null, authored run_entries)
eval_audit/integrations/infer_stack/bundle_export.py      # (A1) model_metadata_fpath/tokenizer_configs_fpath staging + relaxed assert
eval_audit/integrations/kwdagger_bridge.py                # (A1) forward the two new *_fpath overrides into the matrix
configs/local_models/qwen3_6/                             # (A1) model_metadata.yaml + tokenizer_configs.yaml to stage
configs/virtual-experiments/qwen3-6-core.yaml             # local-only grouping (both modes)
reproduce/qwen3_6_core/                                    # runbook (port of qwen_models_combined, freeze removed)
├── _lib.sh                                                # QWEN36_* names, 2 endpoints, registration paths
├── 00_check_env.sh
├── 05_check_profiles.sh                                   # verify the 2 <mode> endpoints
├── 06_check_model_registration.sh                         # NEW: assert qwen3.6 ids resolve in HELM (built-in or staged prod_env)
├── 07_check_container_image.sh
├── 10_run.sh                                               # gc -> gateway -> export (NO --from-spec) -> run --lease --tmux-workers 2
├── 20_index_local.sh
├── 30_compose.sh
├── 40_build_summary.sh                                    # --no-filter-inventory (local-only)
├── config/infer_stack/{catalog,settings}.yaml             # 2 endpoints (thinking/nothinking), reasoning parser
└── README.md
```

Note there is **no `08_check_discovery.sh`** (nothing to freeze); its slot is taken
by **`06_check_model_registration.sh`** — the new-model analogue (does HELM resolve
the id?).

## 5. Preset construction

### 5.1 Two compute presets

Two single-model presets (`qwen3-6-core-nothinking`, `qwen3-6-core-thinking`),
same top-level shape as the OLMo-2 members but **compute-mode**:

```yaml
'qwen3-6-core-nothinking':
  'profile': 'qwen3-6-<size>-nothinking-single'
  'bundle_name': 'qwen3-6-core-nothinking'
  'access_kind': 'vllm-direct'
  'model_deployment_name': 'vllm/qwen3-6-<size>-nothinking'
  'helm_model_name': 'qwen/qwen3.6-<size>'            # confirm/register (T1, §3A)
  'helm_tokenizer_name': 'qwen/qwen3.6-<size>'        # confirm/register
  'protocol_mode': 'chat'
  'helm_max_sequence_and_generated_tokens_length': 4064
  'full_manifest':
    'experiment_name': 'qwen3-6-core-nothinking'
    'run_entries': [ <~85 authored core keys, model=qwen/qwen3.6-<size>> ]
    'suite': 'qwen3-6-core-nothinking'
    'precomputed_root': null                          # <- COMPUTE, not reproduce
    'max_eval_instances': 1000
    'container_network': 'host'
    'hf_cache_dir': '~/.cache/eval-audit-hf'
    'container_gpus': 'none'
  'smoke_manifest': { … 1-2 entries (mmlu:subject=anatomy + gsm), max_eval_instances: 5 … }
```

The `*-thinking` preset is identical except `helm_model_name`/`helm_tokenizer_name`
= the `-thinking` id, `model_deployment_name` = `vllm/qwen3-6-<size>-thinking`, and
a larger token budget. **No combined preset needed** — two modes of one model fold
into one grouping via two suites (the olmo-7b extra-suites pattern), not a fan-out
bundle. (Fan-out width 2 is fine if you want them concurrent.)

### 5.2 Authoring the run_entries (the real work, minus the corpus)

Same generator idea as the fan-out plan, but **compute-mode** and **model-swapped**:

1. Take the 9-group core scenario specs from `official_public_index.csv` for **any**
   `qwen/*` model (they are identical across models — that is why the public runs are
   comparable), i.e. reuse the exact `run_spec_name`s the reproduction whitelist
   produced.
2. Substitute the model token → `model=qwen/qwen3.6-<size>` (and the `-thinking`
   variant for the second preset).
3. **Do NOT** discovery-validate against a corpus (there is none). Instead the
   correctness gate is: the id resolves in HELM (§3A / `06`) and a smoke run returns
   real per-instance stats.

## 6. infer-stack catalog

Two `<mode>-single` endpoints for the one model; `protocol: chat` both; the
thinking endpoint adds the reasoning parser + a larger context:

```yaml
models:
  qwen3-6-<size>: { source: hf://Qwen/Qwen3.6-<size>-Instruct }   # verify (T1)
endpoints:
  qwen3-6-<size>-nothinking-single:
    engine: vllm
    reclaim: stop
    model: qwen3-6-<size>
    runtime: { max_model_len: 4096, gpu_memory_utilization: 0.85, enable_prefix_caching: true }
    # non-thinking chat template (enable_thinking:false) — confirm mechanism (T2)
    extra_args: ['--chat-template-kwargs', '{"enable_thinking": false}']   # placeholder; verify vLLM flag
  qwen3-6-<size>-thinking-single:
    engine: vllm
    reclaim: stop
    model: qwen3-6-<size>
    runtime: { max_model_len: 16384, gpu_memory_utilization: 0.9, enable_prefix_caching: true }
    extra_args: ['--reasoning-parser', 'qwen3']    # placeholder; verify vLLM reasoning-parser name
```

`settings.yaml`: copy the qwen_models_combined one verbatim. Per-model `tp` from the
confirmed size.

## 7. Runbook (`reproduce/qwen3_6_core/`)

A trimmed port of `reproduce/qwen_models_combined/`:

- Drop `08_check_discovery.sh`. Add **`06_check_model_registration.sh`**: assert the
  two Qwen 3.6 ids resolve in the HELM the run will use — either built-in
  (`model_metadata.yaml`) or in the staged `prod_env` (A1). Fails loud with the
  registration instructions (§3A) otherwise.
- `10_run.sh` (replaces `10`+`15`): gc → gateway bootstrap → export **without**
  `--from-spec` (plain compute bundle, route through LiteLLM) → `eval-audit-run
  --lease --tmux-workers 2` over the full_manifest. Loop the two mode presets (or a
  2-profile combined preset if you prefer one schedule).
- `20`/`30`/`40`: same as the fan-out runbook; `40` uses `--no-filter-inventory`
  (local-only) — the fallback branch already present.
- `_lib.sh`: `QWEN36_*` names, the two endpoints, `QWEN36_TMUX_WORKERS` default 2,
  the registration-file paths (A1).

## 8. Virtual experiment (`configs/virtual-experiments/qwen3-6-core.yaml`)

Local-only grouping over both modes:

```yaml
schema_version: 1
name: qwen3-6-core
scope: { models: [ "regex:^qwen/qwen3\\.6" ] }
sources:
  - kind: audit_index
    fpath: /data/crfm-helm-audit-store/indexes/audit_results_index.csv
    include_experiments: [ qwen3-6-core-nothinking, qwen3-6-core-thinking ]
  # NO official_public_index source — there is no public Qwen 3.6 run to pair against.
output: { root: /data/crfm-helm-audit-store/virtual-experiments/qwen3-6-core }
```

## 9. Report shape

Standalone. The aggregate surface reports Qwen 3.6's per-scenario core scores for
each mode; the two modes sit side by side (thinking vs non-thinking is the
interesting axis, in place of the usual local-vs-official axis). No agreement
curves, no recipe-clean counts. If a thinking-vs-nothinking delta is the headline,
`30_compose.sh`'s per-packet core reports already give per-scenario numbers to diff.

## 10. Validation plan (in order)

| Gate | Command | Pass |
|---|---|---|
| V1 presets load | import `PRESET_CONFIGS["qwen3-6-core-nothinking"]` | present; compute shape (`precomputed_root` null; no from-spec) |
| V2 registration | `06_check_model_registration.sh` | both ids resolve in HELM (built-in or staged prod_env) |
| V3 yaml/bash | `yaml.safe_load` configs; `bash -n` scripts | clean |
| V4 serving | `infer-stack acquire qwen3-6-<size>-nothinking-single` + a curl chat | 200, non-null content |
| V5 smoke | `10_run.sh` on the smoke manifest | rows land; **thinking** smoke returns non-null content on `mmlu`/`gsm` (null-content canary) |
| V6 full | `10`→`20`→`30`→`40` | both experiments indexed, composed, summarized; local report renders |

V1/V3 run on the analysis host; V2 needs the target HELM config; V4–V6 need the
serving host.

## 11. Risks & open questions

1. **Model identity (T1)** — "Qwen 3.6" is unconfirmed. Need: exact HF repo(s),
   HELM id(s), size, `tp`. Wrong repo = silently benchmarking the wrong model.
2. **Thinking-mode serving (T2)** — the vLLM reasoning-parser name + the
   enable_thinking toggle mechanism must be confirmed against the served model; a
   wrong toggle means the "two modes" collapse to one.
3. **Null-content on the thinking endpoint** — concentrated on the short MC rows
   (`mmlu`, `commonsense`, max_tokens=1). Gate V5 on it; keep the completions
   fallback / null-content patch ready.
4. **Registration friction (§3A)** — A1 is a real (small) code change to the
   exporter + bridge; A2 touches the submodule + image. Decide before execution.
5. **No official baseline** — by design. If a comparison is later wanted, pair
   against Qwen's *published* numbers out-of-band (a manual reference table), not via
   the HELM pairing (which needs a public run that doesn't exist).
6. **Dataset availability** — the 9 core groups are ungated, but `wmt_14`/`mmlu`
   dataset-id resolution still depends on the runner image's pinned `huggingface_hub
   ==0.36.2` (the `07` probe already guards this).

## 12. Execution checklist

- [ ] T1 confirm Qwen 3.6 repo(s) + HELM id(s) + size/tp; T2 confirm thinking toggle.
- [ ] §3A: check if the ids are already in HELM; if not, implement A1 (recommended)
      or A2, and author `configs/local_models/qwen3_6/{model_metadata,tokenizer_configs}.yaml`.
- [ ] Add the 2 compute presets (`precomputed_root: null`, authored run_entries) to
      `preset_configs.yaml`; generate the ~85 core run_entries (model-swapped, §5.2).
- [ ] Author `config/infer_stack/{catalog,settings}.yaml` (2 endpoints, reasoning parser).
- [ ] Port `reproduce/qwen3_6_core/` (drop `08`, add `06_check_model_registration.sh`,
      `10_run.sh` exports WITHOUT `--from-spec`).
- [ ] Add `configs/virtual-experiments/qwen3-6-core.yaml` (local-only, both modes).
- [ ] V1/V3 on the analysis host; V2 once registration lands.
- [ ] V4 serving check, V5 smoke (null-content canary), V6 full on the serving host.

## 13. Acceptance criteria

1. Both presets load with valid **compute** shape (`precomputed_root` null, no
   from-spec).
2. `06_check_model_registration.sh` confirms both Qwen 3.6 ids resolve in the HELM
   the run uses.
3. A GPU smoke run lands core rows for both modes; the **thinking** endpoint returns
   non-null content on the short MC canary.
4. The local-only virtual experiment produces a standalone report with Qwen 3.6's
   per-scenario core scores for both serving modes.
5. No reproduction/comparison artifacts are emitted (no agreement curves) — the
   report is correctly a net-new-results surface.
```

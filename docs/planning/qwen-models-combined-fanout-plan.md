# Qwen text-family combined fan-out — implementation plan

**Status:** proposed (not started). **Author:** design session 2026-07-11.
**Prereq branch:** `impl/run-from-run-spec` (from-spec exporter + freeze; the
`allenai-olmo-combined` reference and the `openai-gpt-oss-20b` single-model
from-spec preset both live here).

## 0. Goal

Reproduce the public HELM **Qwen text models** locally as a **single
multi-deployment fan-out** — one `qwen-combined` preset whose members fan out
across GPUs under one `eval-audit-run --lease --tmux-workers N` schedule, folded
into one grouped virtual experiment — exactly the shape of
[`reproduce/olmo_models_combined/`](../../reproduce/olmo_models_combined/) and its
preset `allenai-olmo-combined`.

Every member is replayed **from its official `run_spec.json`** (exact-path
freeze), so the local runs inherit the official prompt/decoding/adapter recipe
verbatim. This is the direct fix for the one measured Qwen reproducibility gap
(see §9): `qwen2.5-7b-instruct-turbo` is 2/38 recipe-clean today purely because
the old run-entry audit didn't replicate the public prompt prefix; from-spec
replay carries it.

## 1. Scope — the 8 models

All are text models with public HELM runs and real overlap with the audit's
reproducible benchmark set (classic core + ungated-judge capabilities). VL/Omni
Qwen ids are out of scope (packaging-incompatible); `qwen/qwen3.5-9b` is out of
scope (no public run to replay — it is not in the corpus).

| # | HELM model id | gen | official protocol* | local HF weights (serve) | ~size | tp | runnable rows** |
|---|---|---|---|---|---|---|---|
| 1 | `qwen/qwen1.5-7b`               | 1.5 | confirm (base→completions?) | `Qwen/Qwen1.5-7B`          | 7B   | 1 | 85 |
| 2 | `qwen/qwen1.5-14b`             | 1.5 | confirm | `Qwen/Qwen1.5-14B`         | 14B  | 1 | 85 |
| 3 | `qwen/qwen1.5-32b`             | 1.5 | confirm | `Qwen/Qwen1.5-32B`         | 32B  | 1–2 | 85 |
| 4 | `qwen/qwen1.5-72b`             | 1.5 | confirm | `Qwen/Qwen1.5-72B`         | 72B  | 2 | 85 |
| 5 | `qwen/qwen1.5-110b-chat`       | 1.5 | chat | `Qwen/Qwen1.5-110B-Chat`   | 110B | 4 | 85 |
| 6 | `qwen/qwen2-72b-instruct`     | 2   | chat | `Qwen/Qwen2-72B-Instruct`  | 72B  | 2 | 86 (+369 unitxt/other, excluded) |
| 7 | `qwen/qwen2.5-7b-instruct-turbo`  | 2.5 | chat | `Qwen/Qwen2.5-7B-Instruct`  | 7B  | 1 | 132 |
| 8 | `qwen/qwen2.5-72b-instruct-turbo` | 2.5 | chat | `Qwen/Qwen2.5-72B-Instruct` | 72B | 2 | 132 |

\* **Protocol is per-model and MUST be confirmed** (Task T1). The freeze's
discovery printout shows the official `deploy(official)=…` string; cross-check
against HELM's `submodules/helm/src/helm/config/model_deployments.yaml` /
`model_metadata.yaml`. Base Qwen1.5 (non-chat) very likely ran completions;
`-chat` / `-instruct` / `-turbo` ran chat. The catalog `protocol:` and the
preset `protocol_mode:` must agree with this (the OLMo base-vs-instruct split is
the precedent).

\*\* "runnable rows" = public rows in the reproducible set (classic core +
`bbq`/`ifeval`/`mmlu_pro` + gated `gpqa`), from
`official_public_index.csv` (2026-05-20). `qwen2-72b`'s 350 `unitxt` + 19 other
rows are deliberately excluded from the reproduction manifest (§4.3).

## 2. Design — one combined bundle; the freeze decides splits

Mirror `allenai-olmo-combined` exactly:

- **One multi-deployment preset** `qwen-combined` with a `profiles:` list (one
  entry per model) and run_entries = the **union** of each member's from-spec
  run_entries, each suffixed with its inline `model_deployment=vllm/<model>`
  token (via the existing `_inline_local_deployment`).
- **Exported with `--from-spec --freeze-rel-paths`** — mandatory for a
  multi-deployment bundle (no single manifest-level rewrite target; each run
  needs its own).
- **A single shared `precomputed_root: /data/crfm-helm-public`**; the freeze
  resolves every entry to exactly one official dir and pins a per-run rel-path +
  rewrite target + lease endpoint.
- **One virtual experiment** `qwen-models-combined.yaml` groups the produced
  experiment; the core-report planner pairs each local run with its public
  counterpart by logical run key.

### 2.1 The ONE membership rule (the olmo-7b lesson)

**Model size is NOT a reason to exclude a member.** `allenai-olmo-combined`
already contains a `tensor_parallel_size: 2` model (the OLMo-2 32B) in the same
bundle as co-hostable 7B–13B models — the large one simply **serializes** while
the small ones co-host. Size affects *scheduling throughput*, never *bundle
membership*.

The **only** thing that forces a model to ride separately is **ambiguity under
the shared `precomputed_root`**: if any of a model's run_entries token-subset-
matches more than one official dir (olmo-7b's per-subject MMLU existed under both
`/mmlu` and `/lite`), the freeze cannot resolve it 1:1 and hard-fails. Such a
model rides as its **own single-model suite** against a narrow per-suite root,
folded into the **same** virtual experiment (the `OLMO_COMBINED_EXTRA_PRESETS`
pattern).

**Therefore: propose all 8 in the one combined bundle, and let the `08` freeze
preflight tell us — empirically, CPU-only, before any GPU work — which (if any)
must split out.** Do not pre-split by guessing. Expectation: few or none split
(the turbo set already resolved cleanly enough to be analyzed at 38 packets in
the reproducibility report), but candidates to watch are models whose `mmlu`
appears in more than one suite.

## 3. Artifacts to create

```
eval_audit/integrations/infer_stack/preset_configs.yaml   # 8 member single presets (+ from-spec fields)
eval_audit/integrations/infer_stack/presets.py            # compose qwen-combined (mirror the olmo block)
configs/virtual-experiments/qwen-models-combined.yaml      # grouping manifest
reproduce/qwen_models_combined/
├── _lib.sh                                                 # adapt olmo _lib.sh (QWEN_* names, endpoints, workers)
├── 00_check_env.sh
├── 05_check_profiles.sh                                    # verify the 8 <model>-single endpoints
├── 06_check_hf_auth.sh                                     # gated gpqa (turbo models) needs HF token
├── 07_check_container_image.sh
├── 08_check_discovery.sh                                   # freeze combined + validate; decides splits
├── 10_run_smoke.sh
├── 15_run_full.sh
├── 20_index_local.sh
├── 30_compose.sh
├── 40_build_summary.sh
├── config/infer_stack/
│   ├── catalog.yaml                                        # 8 models + 8 <model>-single endpoints (per-model tp)
│   └── settings.yaml
└── README.md
```

## 4. Preset construction

### 4.1 Member single-model from-spec presets

For each of the 8 models add a top-level preset to `preset_configs.yaml`,
cloning the `allenai-olmo-2-1124-7b-instruct` shape (top-level profile facts;
`precomputed_root` + `container_*` inside each `smoke_manifest`/`full_manifest`;
run_entries carry **no** inline `model_deployment=` token). Example
(`qwen-2-5-7b-instruct-turbo`):

```yaml
'qwen-2-5-7b-instruct-turbo':
  'profile': 'qwen-2-5-7b-instruct-turbo-single'
  'bundle_name': 'qwen-2-5-7b-instruct-turbo'
  'access_kind': 'vllm-direct'
  'model_deployment_name': 'vllm/qwen-2-5-7b-instruct-turbo'
  'helm_model_name': 'qwen/qwen2.5-7b-instruct-turbo'   # G1 alias, matches official run_spec model
  'helm_tokenizer_name': 'qwen/qwen2.5-7b-instruct-turbo'  # confirm vs HELM tokenizer_configs
  'protocol_mode': 'chat'                                # confirm per T1
  'helm_max_sequence_and_generated_tokens_length': 4064  # confirm vs official spec
  'smoke_manifest': { experiment_name: audit-qwen-2-5-7b-instruct-turbo-smoke,
                      run_entries: [ <1-2 discovery keys> ], suite: …,
                      precomputed_root: /data/crfm-helm-public, max_eval_instances: 5,
                      container_network: host, hf_cache_dir: ~/.cache/eval-audit-hf, container_gpus: none }
  'full_manifest':  { experiment_name: audit-qwen-2-5-7b-instruct-turbo-full,
                      run_entries: [ <full core discovery keys> ], suite: …,
                      precomputed_root: /data/crfm-helm-public, max_eval_instances: 1000,
                      container_network: host, hf_cache_dir: ~/.cache/eval-audit-hf, container_gpus: none }
```

> **Naming:** preset keys use hyphenated, dot-free forms (`qwen-1-5-7b`,
> `qwen-2-72b-instruct`, `qwen-2-5-72b-instruct-turbo`) — the OLMo convention —
> because preset keys double as `profile` / endpoint names, and dots are awkward
> in endpoint identifiers. The `helm_model_name` keeps the canonical dotted id
> (`qwen/qwen2.5-7b-instruct-turbo`).

Member presets are individually useful: a model that must split out (§2.1) rides
via its own preset as an extra suite, exactly like `allenai-olmo-7b-{mmlu,lite}`.

### 4.2 Compose `qwen-combined` in `presets.py`

Mirror `presets.py:70-173`. Two clean options:

- **(A) Copy-adapt:** add `_QWEN_COMBINED_PRESET_KEYS = (…8 keys…)` and a
  `PRESET_CONFIGS["qwen-combined"] = {…}` block that lists the 8 members'
  profiles and unions their run_entries via the existing
  `_olmo_combined_run_entries` logic.
- **(B) Refactor (preferred):** generalize the olmo helper into
  `_combined_run_entries(keys, mode)` and `_build_combined_preset(name, keys,
  smoke_meta, full_meta)`, then build both `allenai-olmo-combined` and
  `qwen-combined` through it. Keeps one code path; the olmo tests
  (`tests/test_olmo_from_spec.py`) guard the refactor.

`access_kind: vllm-direct` at the combined level; the runbook overrides with
`--access-kind openai-compatible` to route through LiteLLM (as olmo does).

### 4.3 Generating the run_entries (the real work)

Hand-writing ~775 entries is infeasible. Generate them from the corpus:

1. **Source of truth:** `official_public_index.csv` — filter rows by
   `model == qwen/<id>` AND `benchmark_group ∈ REPRODUCIBLE_SET` where
   `REPRODUCIBLE_SET = {classic core…} ∪ {bbq, ifeval, mmlu_pro, gpqa}` (the
   whitelist computed in the design session; excludes `unitxt`, `ewok`, `thai_exam`,
   `finance`, `medhelm`, closed-judge safety, and reasoning-only `aime/math500`).
2. **Discovery key:** for each row, take the official run-dir basename and strip
   to a token-subset key that keeps `model=<id>` + the disambiguating params, and
   normalize the model token to the dotted HELM id (`openai_gpt-oss-20b` →
   `openai/gpt-oss-20b` style). The safest key is the full run_spec name with the
   dir's `model=` normalized — the freeze does token-subset matching, so extra
   tokens are fine as long as they don't over-constrain.
3. **Validate every generated key** with
   `python -m eval_audit.cli.check_precomputed_discovery --preset <member>
   --precomputed-root /data/crfm-helm-public --mode full` — must report
   **N RESOLVED, 0 NO_MATCH, 0 AMBIGUOUS** per member. AMBIGUOUS ⇒ that member
   splits out (§2.1); NO_MATCH ⇒ fix the key or the corpus mirror.

Write a one-off generator (`dev/scripts/` or inline in the plan's execution) that
emits the YAML run_entries blocks; do not commit the generator's output blindly —
gate it on the discovery check.

Smoke manifests: 1–2 cheap entries per model (prefer a short classic MC scenario
like `mmlu:subject=anatomy` + one `ifeval`/`bbq` as the langdetect/[metrics]
container canary), `max_eval_instances: 5`.

## 5. infer-stack catalog (`config/infer_stack/catalog.yaml`)

One `models:` entry + one `<model>-single` endpoint per model, mirroring the OLMo
catalog. Per-model `tensor_parallel_size` and `protocol` are load-bearing:

```yaml
models:
  qwen-1-5-7b:               { source: hf://Qwen/Qwen1.5-7B }
  qwen-1-5-14b:              { source: hf://Qwen/Qwen1.5-14B }
  qwen-1-5-32b:              { source: hf://Qwen/Qwen1.5-32B }
  qwen-1-5-72b:              { source: hf://Qwen/Qwen1.5-72B }
  qwen-1-5-110b-chat:        { source: hf://Qwen/Qwen1.5-110B-Chat }
  qwen-2-72b-instruct:       { source: hf://Qwen/Qwen2-72B-Instruct }
  qwen-2-5-7b-instruct-turbo:  { source: hf://Qwen/Qwen2.5-7B-Instruct }
  qwen-2-5-72b-instruct-turbo: { source: hf://Qwen/Qwen2.5-72B-Instruct }

endpoints:
  qwen-1-5-7b-single:   { engine: vllm, reclaim: stop, model: qwen-1-5-7b,
                          protocol: completions,   # if base ran completions (confirm T1)
                          runtime: { max_model_len: 4096, gpu_memory_utilization: 0.85, … } }
  # … 14b/32b single-GPU …
  qwen-1-5-72b-single:  { engine: vllm, reclaim: stop, model: qwen-1-5-72b,
                          runtime: { tensor_parallel_size: 2, max_model_len: 4096, gpu_memory_utilization: 0.9, … } }
  qwen-1-5-110b-chat-single: { …, runtime: { tensor_parallel_size: 4, … } }
  qwen-2-72b-instruct-single: { …, runtime: { tensor_parallel_size: 2, … } }
  qwen-2-5-7b-instruct-turbo-single:  { …, runtime: { max_model_len: 4096, … } }   # single GPU
  qwen-2-5-72b-instruct-turbo-single: { …, runtime: { tensor_parallel_size: 2, … } }
```

`settings.yaml`: copy the OLMo one verbatim (`backend: compose`, `litellm: true`,
`ui: false`, `data_dir: /data/service/infer-stack`).

**Weight-id verification (Task T2):** confirm each `hf://` id on the HF Hub before
a real run (the OLMo catalog carries the same "verify" caveat). Qwen1.5 base vs
`-chat` and Qwen2.5 `-Instruct` (local) vs `-turbo` (Together-hosted) are the easy
places to pick the wrong repo.

## 6. Runbook (`reproduce/qwen_models_combined/`)

Direct port of `olmo_models_combined/`. Differences from that runbook:

- `_lib.sh`: `QWEN_*` names; `QWEN_COMBINED_PRESET="qwen-combined"`;
  `QWEN_COMBINED_ENDPOINTS=(…8…)`; `QWEN_TMUX_WORKERS` default `4`;
  `QWEN_COMBINED_EXTRA_PRESETS=()` initially (populated only if `08` finds a
  member that must split out); reuse the OLMo data-dir + HF-token resolution
  verbatim.
- `05_check_profiles.sh`: verify all 8 `<model>-single` endpoints.
- `06_check_hf_auth.sh`: keep — the turbo models' `gpqa` entry pulls gated
  `Idavidrein/gpqa`. Non-turbo classic-lite members don't gate, so if the run is
  scoped to those, the gate is optional.
- `08_check_discovery.sh`: freeze the combined bundle (`--from-spec
  --freeze-rel-paths`) + `check_precomputed_discovery --manifest` existence
  check. **This is the decision point for §2.1 splits.** Extend it to also
  freeze-check any `QWEN_COMBINED_EXTRA_PRESETS`.
- `10`/`15`: identical structure to the OLMo smoke/full — gc → gateway bootstrap
  (via `QWEN_COMBINED_ENDPOINTS[0]`) → export → `eval-audit-run --lease
  --tmux-workers N` → fold in any extra split-out suites.
- `30_compose.sh` / `40_build_summary.sh`: identical (point `VEXP_MANIFEST` at
  the qwen grouping config).

## 7. Virtual experiment (`configs/virtual-experiments/qwen-models-combined.yaml`)

Mirror `olmo-models-combined.yaml`:

```yaml
schema_version: 1
name: qwen-models-combined
scope:
  models: [ "regex:^qwen/" ]           # defensive; include_experiments is the hard bound
sources:
  - kind: audit_index
    fpath: /data/crfm-helm-audit-store/indexes/audit_results_index.csv
    include_experiments:
      - audit-qwen-combined-full
      # + any split-out extra suites, e.g. audit-qwen-1-5-72b-<suite>-full
  - kind: official_public_index          # comparison ON
    fpath: /data/crfm-helm-audit-store/indexes/official_public_index.csv
    pre_filter: { kind: helm_stage1, inventory_fpath: /data/crfm-helm-audit-store/analysis/filter_inventory.json }
output:
  root: /data/crfm-helm-audit-store/virtual-experiments/qwen-models-combined
```

## 8. Validation plan (do in order; each gates the next)

| Gate | Command | Pass criterion |
|---|---|---|
| V1 preset loads | import `PRESET_CONFIGS["qwen-combined"]` via the real loader | present; from-spec shape (per-member `precomputed_root`, no inline deploy tokens on member run_entries; combined has inline tokens) |
| V2 per-member discovery | `check_precomputed_discovery --preset <member> --precomputed-root /data/crfm-helm-public --mode full` ×8 | each: **0 NO_MATCH, 0 AMBIGUOUS**. AMBIGUOUS ⇒ split member to extra suite |
| V3 combined freeze | `08_check_discovery.sh` (CPU-only) | combined bundle freezes 1:1; every frozen `run_spec.json` exists |
| V4 YAML/bash | `yaml.safe_load` on 3 configs; `bash -n` on scripts | clean |
| V5 smoke | `10_run_smoke.sh` on a GPU host | all 8 endpoints serve; smoke rows land; ifeval canary passes (container has `[metrics]`) |
| V6 full | `15_run_full.sh` → `20`→`30`→`40` | full experiment indexed, composed, summarized; report pairs local vs public |

V1–V4 are runnable on the analysis host (no GPU): the corpus is present at
`/data/crfm-helm-public` and `check_precomputed_discovery` needs neither serving
nor the HELM submodule. V5–V6 need the serving host.

## 9. Headline benefit — the qwen2.5-7b prompt-prefix fix

The reproducibility report has `qwen/qwen2.5-7b-instruct-turbo` at **2/38
recipe-clean, mean agree@0 0.716, `execution_spec_drift`**
([REPRODUCIBILITY_REPORT.md:68](../../reproduce/open_helm_models_reproducibility/REPRODUCIBILITY_REPORT.md)).
The corrected diagnosis: the 36 drifted packets differ because the old audit
didn't replicate the public prompt prefix. **From-spec replay pulls the official
`run_spec.json` — prefix intact — so this fan-out is the mechanism that closes
that gap.** Expect qwen2.5-7b's recipe-clean count to jump once it reruns here.
This is the single most valuable outcome and argues for keeping qwen2.5-7b in the
first executed batch even if the run is otherwise scoped small.

## 10. GPU budget & scheduling

- `QWEN_TMUX_WORKERS` (default 4) = max concurrent leased HELM runs; each self-
  acquires its model's GPU lease; infer-stack co-hosts what fits on
  `INFER_STACK_ALLOWED_GPUS` and queues the rest.
- 7B–32B co-host; 72B (tp=2) and 110B (tp=4) serialize against the small ones.
- On a 2×80GB host: expect the small models to fan out 2–3 at a time while a 72B
  holds both cards; on 4×80GB the 110B (tp=4) blocks the whole node while it runs
  — schedule it first or last. No `INFER_STACK_ALLOWED_GPUS` pin by default
  (serve across all detected GPUs); pin per host.
- Membership is independent of all this (§2.1).

## 11. Risks & open questions

1. **Protocol per model (T1)** — base Qwen1.5 vs chat. Wrong protocol → the
   readiness probe hits the wrong endpoint and `acquire` hangs to TTL, or outputs
   diverge. Confirm from the freeze's `deploy(official)` + HELM model metadata.
2. **HF weight ids (T2)** — verify each `hf://` repo (base vs -chat; -Instruct vs
   -turbo). Wrong repo = silently reproducing a different model.
3. **Ambiguity splits (§2.1)** — unknown until V2/V3. Turbo models appear in both
   classic and capabilities suites; if the same benchmark+params exist under two
   suites for one model, it splits. The runbook must handle a non-empty
   `QWEN_COMBINED_EXTRA_PRESETS` (the olmo-7b path already models this).
4. **Scale** — ~775 full run_entries across 8 models is a large fan-out; first
   executed batch can scope to a subset (e.g. qwen2.5-7b + qwen1.5-7b/14b) to
   prove the path, then widen. `max_eval_instances` and the benchmark whitelist
   are the two size knobs.
5. **Corpus mirror** — confirmed present for Qwen at design time (all 50 ids'
   dirs exist under `/data/crfm-helm-public`), but re-verify with V2 before a run;
   the mirror can drift.
6. **Staleness** — public index 2026-05-20, audit index 2026-06-29; regenerate
   the run_entry whitelist from a fresh `official_public_index.csv` if the corpus
   has grown.
7. **num_output_tokens / adapter params** — the from-spec replay uses the official
   spec, so the discovery key only needs to locate the dir; do not hand-pin
   generation params in the run_entry.

## 12. Execution checklist

- [ ] T1 confirm per-model protocol; T2 verify HF weight ids.
- [ ] Add 8 member single-model from-spec presets to `preset_configs.yaml`.
- [ ] Generate + discovery-validate (V2) each member's run_entries from
      `official_public_index.csv` (reproducible whitelist).
- [ ] Compose `qwen-combined` in `presets.py` (prefer the refactor, §4.2B).
- [ ] Author `config/infer_stack/{catalog,settings}.yaml` (per-model tp/protocol).
- [ ] Port the `reproduce/qwen_models_combined/` runbook from the OLMo one.
- [ ] Add `configs/virtual-experiments/qwen-models-combined.yaml` + README rows.
- [ ] V1/V3/V4 on the analysis host; resolve any V2 AMBIGUOUS by splitting members
      into `QWEN_COMBINED_EXTRA_PRESETS`.
- [ ] V5 smoke, then V6 full on the serving host.
- [ ] Rebuild the reproducibility report; confirm qwen2.5-7b recipe-clean count
      rises (§9).

## 13. Acceptance criteria

1. `qwen-combined` loads through the real preset loader with valid from-spec
   shape.
2. `08_check_discovery.sh` freezes the combined bundle (plus any extra suites)
   1:1 against `/data/crfm-helm-public` with 0 NO_MATCH / 0 AMBIGUOUS.
3. A GPU smoke run lands rows for all 8 models under one fan-out schedule.
4. The grouped virtual experiment pairs each Qwen local run with its public
   counterpart and produces the aggregate publication surface.
5. `qwen2.5-7b-instruct-turbo`'s recipe-clean fraction improves versus the
   2/38 baseline once replayed from-spec.

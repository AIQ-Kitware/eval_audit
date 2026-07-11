# Qwen 3.6 — new core results via leased fan-out — implementation plan

**Status:** proposed (not started). **Author:** design session 2026-07-11
(supersedes the earlier manual-server draft of this file).
**Prereq branch:** `impl/run-from-run-spec` (reuses the `qwen-combined` serving /
leasing / fan-out machinery; drops the from-spec / freeze / comparison half).

## 0. Goal

Produce **new** classic/Lite-core HELM numbers for **Qwen 3.6**, served locally in
**two modes** (thinking + non-thinking), scheduled as a **leased fan-out** — one
`eval-audit-run --lease --tmux-workers 2` over a **compute** (non-from-spec) bundle,
so the two serving modes run **concurrently, each leasing its own GPU** — landing a
**standalone (local-only)** report.

This is the `qwen-combined` fan-out shape with exactly one axis changed: **compute
instead of reproduce**. No official run is replayed; no corpus is frozen against.

## 1. Compute + fan-out are compatible (the design pivot)

Fan-out is **not** tied to reproduction. It is the `--lease --tmux-workers`
scheduler + an infer-stack catalog + per-run `model_deployment=vllm/<endpoint>`
lease tokens. All three are independent of `--from-spec`. Verified against
`bundle_export.py`:

- the freeze is flag-gated (`if freeze_rel_paths:` — bundle_export.py:484); omit it
  and `run_spec_sources=None`, `precomputed_root` stays null ⇒ **compute mode**;
- `lease_facts` (the `deployment → endpoint` map the manifest needs for `--lease`)
  is built **unconditionally** (bundle_export.py:459);
- the inline `model_deployment=` tokens the combined builder appends survive into
  the compute run_entries, so `--lease` acquires the right endpoint per run.

So a **multi-deployment compute preset** exported *without* `--from-spec` yields a
manifest with leasing intact.

| Axis | `qwen-combined` (reproduce) | this plan (new results) |
|---|---|---|
| run_entries | frozen from official run_spec.json | **authored** (core specs, model-swapped) |
| exporter flags | `--from-spec --freeze-rel-paths` | **plain** (compute) |
| `precomputed_root` | `/data/crfm-helm-public` | **null** |
| comparison side | `official_public_index` pairing | **none** (local-only) |
| **fan-out** (`--lease --tmux-workers`) | ✅ | ✅ **kept** |
| infer-stack catalog + lease tokens | ✅ | ✅ **kept** |

## 2. Scope

- **Scenarios:** the 9-group classic/Lite core — `mmlu` (57 subjects), `commonsense`,
  `gsm`, `math`, `legalbench`, `med_qa`, `narrative_qa`, `natural_qa`, `wmt_14`
  (~85 run_entries **per mode**). None gate an HF dataset ⇒ HF auth optional.
- **Two serving modes = two deployments** (HELM's own Qwen3 convention —
  `qwen3-next-80b-a3b-instruct` and `-thinking` are separate ids):
  | mode | HELM model id | deployment / lease endpoint | tp |
  |---|---|---|---|
  | non-thinking | `qwen/qwen3.6-<size>`          | `vllm/qwen3-6-<size>-nothinking` ← `qwen3-6-<size>-nothinking-single` | `<tp>` |
  | thinking     | `qwen/qwen3.6-<size>-thinking`| `vllm/qwen3-6-<size>-thinking`   ← `qwen3-6-<size>-thinking-single`   | `<tp>` |
- **Report:** standalone local-only (no public Qwen 3.6 run exists to pair against).

> **T1 — model identity unconfirmed.** "Qwen 3.6" is not a model I can verify
> (HELM currently ships `qwen3-235b-*`, `qwen3-next-80b-a3b-{instruct,thinking}`).
> Confirm the HF repo + HELM id + `<size>`/`<tp>` before execution.

## 3. Design — one 2-profile compute preset, one leased schedule

Build a `qwen3-6-core` **combined-style** preset with **two profiles** (the two
modes of the one model) through the SAME `_build_combined_preset` helper that
produces `qwen-combined` — the two members' run_entries union, each suffixed with
its inline `model_deployment=vllm/qwen3-6-<size>-{thinking,nothinking}` token.

Then `10_run.sh` exports it **without** `--from-spec/--freeze-rel-paths` (compute)
and runs `eval-audit-run --lease --tmux-workers 2`. cmd_queue drives the two modes
concurrently; each self-acquires its endpoint's GPU lease; infer-stack co-hosts if
both fit or serializes if not. Within a mode, the ~85 run_entries share one
deployment (ref-counted) and fan across tmux workers against that mode's server.

### 3.1 One builder enhancement (small)

`_build_combined_preset` currently hardcodes `precomputed_root:
/data/crfm-helm-public` in both manifest blocks (reproduction-only). Add a
`precomputed_root` parameter (default the corpus; pass `None` for compute) plus a
`max_eval_instances` param, so both the reproduction combined preset and this
compute combined preset flow through **one** code path. This mirrors the §4.2B
generalization already done for the OLMo→Qwen combined builder — no new code path,
just a parameter. (Alternative: hand-author the 2-profile preset dict directly and
skip the builder; the param is cleaner and keeps one source of truth.)

## 4. Prerequisite — HELM must resolve the model id

The exporter asserts the model/tokenizer are registered
(`_assert_helm_aliases_exist`, bundle_export.py:108). This is a prerequisite for
*any* run of a new id, fan-out or not — it is **config, not a fan-out obstacle**:

1. **Check first.** If the target id is already in the HELM the run uses
   (`model_metadata.yaml`) — plausible if "Qwen 3.6" resolves to an existing Qwen3
   id — **nothing to do**.
2. **If new,** register it where BOTH the repo-side assert and the in-container HELM
   see it. Cleanest: add the two ids to the vendored HELM `model_metadata.yaml`
   (tags `TEXT_MODEL_TAG` + `INSTRUCTION_FOLLOWING_MODEL_TAG`) + `tokenizer_configs.yaml`
   (`HuggingFaceTokenizer`), mirroring the existing `qwen3-next-80b-a3b-{instruct,
   thinking}` entries, then rebuild the runner image so the baked HELM knows them
   (flag the submodule gitlink bump per CLAUDE.md). This is ~6 yaml lines + a
   rebuild, done once.

`06_check_model_registration.sh` gates this before any GPU work.

## 5. Artifacts to create

```
eval_audit/integrations/infer_stack/presets.py            # add precomputed_root/max_eval_instances params to _build_combined_preset; compose qwen3-6-core
eval_audit/integrations/infer_stack/preset_configs.yaml   # 2 member compute presets (precomputed_root null, inline no token)
submodules/helm/.../model_metadata.yaml + tokenizer_configs.yaml   # (only if id is new) register both ids — intentional submodule change
configs/virtual-experiments/qwen3-6-core.yaml             # local-only grouping (both modes)
reproduce/qwen3_6_core/
├── _lib.sh                                                # QWEN36_* names, 2 endpoints, QWEN36_TMUX_WORKERS=2
├── 00_check_env.sh
├── 05_check_profiles.sh                                   # verify the 2 <mode>-single endpoints
├── 06_check_model_registration.sh                         # NEW: assert both qwen3.6 ids resolve in HELM
├── 07_check_container_image.sh
├── 10_run.sh                                              # gc → gateway → export (NO --from-spec) → run --lease --tmux-workers 2
├── 20_index_local.sh
├── 30_compose.sh
├── 40_build_summary.sh                                    # --no-filter-inventory (local-only)
├── config/infer_stack/{catalog,settings}.yaml            # 2 endpoints (thinking/nothinking), reasoning parser, per-mode tp
└── README.md
```

No `08_check_discovery.sh` (nothing to freeze); its slot → `06_check_model_registration.sh`.

## 6. Preset construction

Two single-model **compute** member presets (`precomputed_root: null`, authored
run_entries with **no** inline token — the combined builder adds it), same shape as
the OLMo-2 members otherwise:

```yaml
'qwen3-6-core-nothinking':
  'profile': 'qwen3-6-<size>-nothinking-single'
  'model_deployment_name': 'vllm/qwen3-6-<size>-nothinking'
  'helm_model_name': 'qwen/qwen3.6-<size>'          # T1 / §4
  'helm_tokenizer_name': 'qwen/qwen3.6-<size>'
  'protocol_mode': 'chat'
  'helm_max_sequence_and_generated_tokens_length': 4064
  'full_manifest':
    'run_entries': [ <~85 authored core keys, model=qwen/qwen3.6-<size>> ]
    'suite': 'qwen3-6-core-nothinking'
    'precomputed_root': null                          # <- COMPUTE
    'max_eval_instances': 1000
    'container_network': 'host'; 'hf_cache_dir': '~/.cache/eval-audit-hf'; 'container_gpus': 'none'
  'smoke_manifest': { run_entries: [ mmlu:subject=anatomy…, gsm:model=… ], max_eval_instances: 5, precomputed_root: null, … }
# 'qwen3-6-core-thinking' identical except the -thinking id / deployment + a larger token budget.
```

Then in `presets.py`:

```python
PRESET_CONFIGS["qwen3-6-core"] = _build_combined_preset(
    "qwen3-6-core",
    ("qwen3-6-core-nothinking", "qwen3-6-core-thinking"),
    smoke_description=..., full_description=...,
    precomputed_root=None,           # <- the new param (compute)
    max_eval_instances=1000,
)
```

### 6.1 Authoring the run_entries (compute, model-swapped)

Lift the 9-group core scenario specs from `official_public_index.csv` for any
`qwen/*` model (identical across models — that is why the public runs are
comparable), swap the model token → `model=qwen/qwen3.6-<size>` (and the `-thinking`
id for the second member). **No corpus discovery** — the correctness gate is
registration (`06`) + a smoke run returning real per-instance stats.

## 7. infer-stack catalog

Two `<mode>-single` endpoints for the one model; `protocol: chat` both; the
thinking endpoint adds the reasoning parser + a larger context (verify T2 against
vLLM's Qwen3 docs):

```yaml
models:
  qwen3-6-<size>: { source: hf://Qwen/Qwen3.6-<size>-Instruct }     # verify (T1)
endpoints:
  qwen3-6-<size>-nothinking-single:
    engine: vllm; reclaim: stop; model: qwen3-6-<size>
    runtime: { max_model_len: 4096, gpu_memory_utilization: 0.85, enable_prefix_caching: true }
    extra_args: ['--chat-template-kwargs', '{"enable_thinking": false}']   # placeholder; verify (T2)
  qwen3-6-<size>-thinking-single:
    engine: vllm; reclaim: stop; model: qwen3-6-<size>
    runtime: { max_model_len: 16384, gpu_memory_utilization: 0.9, enable_prefix_caching: true }
    extra_args: ['--reasoning-parser', 'qwen3']                            # placeholder; verify (T2)
```

`settings.yaml`: copy `qwen_models_combined`'s verbatim. Per-mode `tp` from `<size>`.

## 8. Runbook (`reproduce/qwen3_6_core/`)

Port of `reproduce/qwen_models_combined/`, two changes:

- **`10_run.sh` exports COMPUTE**, not from-spec:
  ```bash
  python -m eval_audit.integrations.infer_stack export-benchmark-bundle \
    --preset qwen3-6-core --bundle-root "$BUNDLE_ROOT" \
    --access-kind openai-compatible --base-url "${LITELLM_BASE_URL}/v1" \
    --api-key-value "$LEASE_MASTER_KEY"        # NO --from-spec, NO --freeze-rel-paths
  eval-audit-run --run=1 "$BUNDLE_ROOT/full_manifest.yaml" \
    --container-image "$QWEN36_CONTAINER_IMAGE" --lease --tmux-workers "${QWEN36_TMUX_WORKERS:-2}"
  ```
- **`08` → `06_check_model_registration.sh`** (the new-model preflight).
- `_lib.sh`: `QWEN36_*` names, the two endpoints, `QWEN36_TMUX_WORKERS` default 2.
  `20`/`30`/`40` identical; `40` uses `--no-filter-inventory` (local-only).

## 9. Virtual experiment (`configs/virtual-experiments/qwen3-6-core.yaml`)

Local-only grouping over both modes:

```yaml
schema_version: 1
name: qwen3-6-core
scope: { models: [ "regex:^qwen/qwen3\\.6" ] }
sources:
  - kind: audit_index
    fpath: /data/crfm-helm-audit-store/indexes/audit_results_index.csv
    include_experiments: [ qwen3-6-core-nothinking, qwen3-6-core-thinking ]
  # NO official_public_index source — no public Qwen 3.6 run to pair against.
output: { root: /data/crfm-helm-audit-store/virtual-experiments/qwen3-6-core }
```

## 10. Report shape

Standalone. Per-scenario core scores for each mode; the interesting axis is
**thinking vs non-thinking** (in place of local-vs-official). No agreement curves /
recipe-clean counts (there is no second side). `30_compose.sh`'s per-packet core
reports give the per-scenario numbers to diff the two modes.

## 11. Fan-out behavior for this run

- `QWEN36_TMUX_WORKERS=2` → the two modes run concurrently; each `acquire --queue`s
  its endpoint's GPU lease. On ≥2 free GPUs both serve at once; on 1 GPU (or when a
  mode is large-tp) they serialize — same leasing semantics as `qwen-combined`, just
  2-way.
- **Honest scale note:** with one model in two modes the fan-out is **2-way** across
  models, plus within-mode client concurrency. It is a smaller win than the 8-model
  reproduction fan-out. To widen it, add more deployments to the same bundle (e.g.
  several Qwen 3.6 **sizes** × 2 modes) — each becomes another leased member and the
  fan-out spreads wider automatically.

## 12. Validation plan

| Gate | Command | Pass |
|---|---|---|
| V1 preset loads | import `PRESET_CONFIGS["qwen3-6-core"]` | 2 profiles; **compute** shape (`precomputed_root` null, no from-spec); inline tokens present; `lease`-ready |
| V2 registration | `06_check_model_registration.sh` | both ids resolve in the run's HELM |
| V3 yaml/bash | `yaml.safe_load` configs; `bash -n` scripts | clean |
| V4 serving | `infer-stack acquire qwen3-6-<size>-nothinking-single` + curl chat | 200, non-null content |
| V5 smoke (leased) | `10_run.sh` on the smoke manifest, `--lease --tmux-workers 2` | both modes lease + land rows; **thinking** returns non-null on `mmlu`/`gsm` (null-content canary) |
| V6 full | `10`→`20`→`30`→`40` | both experiments indexed, composed, summarized; local report renders |

V1/V3 on the analysis host; V2 once registration lands; V4–V6 on the serving host.

## 13. Risks & open questions

1. **Model identity (T1)** — exact Qwen 3.6 repo(s)/id(s)/size/tp unconfirmed.
2. **Thinking toggle (T2)** — vLLM reasoning-parser name + `enable_thinking`
   mechanism must be confirmed against the served model, else the two modes collapse.
3. **Null-content on the thinking endpoint** — short MC rows (`mmlu`/`commonsense`,
   max_tokens=1); gate V5 on it; keep the null-content patch / completions fallback.
4. **Registration** (§4) — only if the id is new; ~6 yaml lines + image rebuild.
5. **Multi-deployment compute export path** — verified supported by inspection
   (freeze is flag-gated, `lease_facts` unconditional); confirm empirically at V1 by
   exporting the bundle CPU-only and checking `lease_endpoints` + null
   `precomputed_root` in the produced manifest before any GPU run.
6. **No official baseline** — by design; compare to Qwen's *published* numbers
   out-of-band if wanted, not via HELM pairing.

## 14. Execution checklist

- [ ] T1 confirm repo/id/size/tp; T2 confirm thinking toggle.
- [ ] §4: check if the id is already in HELM; register both ids if new.
- [ ] Add `precomputed_root` + `max_eval_instances` params to `_build_combined_preset`.
- [ ] Add the 2 compute member presets (`precomputed_root: null`) + authored core
      run_entries (model-swapped); compose `qwen3-6-core`.
- [ ] Author `config/infer_stack/{catalog,settings}.yaml` (2 endpoints, reasoning parser).
- [ ] Port `reproduce/qwen3_6_core/` (10 exports compute; 06 replaces 08).
- [ ] Add `configs/virtual-experiments/qwen3-6-core.yaml` (local-only, both modes).
- [ ] V1 (incl. the compute-export manifest inspection) + V3 on the analysis host.
- [ ] V4 serving, V5 leased smoke (null-content canary), V6 full on the serving host.

## 15. Acceptance criteria

1. `qwen3-6-core` loads with valid **compute** multi-deployment shape (2 profiles,
   `precomputed_root` null, inline lease tokens, no from-spec).
2. `06_check_model_registration.sh` confirms both ids resolve.
3. A GPU smoke run **leases both modes concurrently** (`--lease --tmux-workers 2`)
   and lands core rows; the thinking endpoint returns non-null content on the MC canary.
4. The local-only virtual experiment produces a standalone report with Qwen 3.6's
   per-scenario core scores for both serving modes.
5. No reproduction/comparison artifacts (no agreement curves) — correctly a
   net-new-results surface, produced by the leased fan-out runbook.
```

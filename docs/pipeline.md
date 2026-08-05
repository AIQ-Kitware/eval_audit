# eval_audit pipeline

This document describes the **active** pipeline. It has two halves:

- **Execution** — replaying official runs locally from their frozen
  `run_spec.json`, inside a digest-pinned container, against a leased GPU
  endpoint. Exercised 2026-07-13 … 07-15 by the four
  [from-spec / era runbooks](#execution-from-spec-replay); this is what produced
  the current OLMo, Qwen, GPT-OSS, and RedPajama audit results.
- **Analysis** — Stages 1–4 below, read-only over audit results already on disk.
  Exercised continuously by the analysis-only runbooks
  ([`pythia_mmlu_stress`](../reproduce/pythia_mmlu_stress/),
  [`open_helm_models_reproducibility`](../reproduce/open_helm_models_reproducibility/)).

The **pre-EEE-refactor** pipeline — `eval-audit-make-manifest` for manifest
building, plus the KubeAI/LiteLLM serving variants — is preserved as
[`historical/pipeline-pre-eee-refactor.md`](historical/pipeline-pre-eee-refactor.md).
Its analysis stages are superseded by the four below; its manifest-building step
is superseded by `export-benchmark-bundle` (see
[Execution](#execution-from-spec-replay)). That older flow is **not** described
here.

## Mental model

```
Public HELM corpus              Local audit results
(/data/crfm-helm-public)        (/data/crfm-helm-audit/<exp>/...)
        │                                  ▲
        │                                  │  produced by the execution half
        │                                  │  (export-benchmark-bundle
        │                                  │   → eval-audit-run, see below)
        │                                  │
        ▼                                  ▼
        ┌──────────────────────────────────┐
        │  1. EEE conversion               │  every_eval_ever convert helm
        │     (per-run, on demand)         │  → eee_output/<dataset>/<dev>/<model>/<uuid>.json
        └──────────────────────────────────┘
                          │
                          ▼
        ┌──────────────────────────────────┐
        │  2. Virtual experiment compose   │  eval-audit-build-virtual-experiment
        │     (YAML-declared slice)        │  → coverage funnel, packet manifest
        └──────────────────────────────────┘
                          │
                          ▼
        ┌──────────────────────────────────┐
        │  3. Per-packet core analysis     │  eval-audit-analyze-experiment / -many
        │     (planner + core metrics)     │  → core_report/<packet>/...
        └──────────────────────────────────┘
                          │
                          ▼
        ┌──────────────────────────────────┐
        │  4. Aggregate / publication      │  eval-audit-build-summary
        │     (sankeys, prioritized,       │  → virtual-experiments/<name>/reports/...
        │      coverage matrix, README)    │
        └──────────────────────────────────┘
```

In Stages 1–4 no model is run and no benchmark is downloaded: they are read-only
over the audit results that already exist on disk. Producing those results is the
[execution half](#execution-from-spec-replay).

### Virtual experiments over EEE

If you want to *slice* across many EEE artifacts (multiple models
benchmarks, multiple sources), use a virtual-experiment manifest:

```yaml
sources:
  - kind: eee_root           # walk an official/+local/ EEE tree
    root: /path/to/tree
  - kind: external_eee       # cherry-pick individual artifacts
    components:
      - id: my-inspect-ai-run
        eee_artifact_path: /path/to/uuid.json
        run_entry: "mmlu:model=foo"
        side: local
```

Then `eval-audit-build-virtual-experiment --manifest <yaml>` composes
the slice (filtering by the manifest's `scope`), calls
`analyze_experiment` over the synthesized indexes, and produces the
same per-packet + aggregate-summary surface the HELM path produces.
EEE sources can be mixed with HELM-driven `audit_index` /
`official_public_index` sources in the same manifest. See
[`configs/virtual-experiments/eee-only-demo.yaml`](../configs/virtual-experiments/eee-only-demo.yaml)
for a worked example against the checked-in demo fixture.

### Tutorial path: `eval-audit-from-eee`

If you already have *both* sides of the comparison in EEE format (one
directory of "official" EEE artifacts and one directory of "local" EEE
artifacts that you'd like to compare against them), you can skip Stages 1
and 2 entirely and run

```
eval-audit-from-eee \
    --eee-root <root>/{official,local}/... \
    --out-dpath <out> \
    --build-aggregate-summary
```

This walks the artifact tree, synthesizes the same in-memory index rows
the official and local indexes would have produced, runs the planner +
core-metrics + aggregate summary, and writes per-packet reports +
cross-packet summary under `<out>/`. See
[`reproduce/eee_only_demo/README.md`](../reproduce/eee_only_demo/README.md)
for a worked tutorial against a checked-in 3×3 fixture, including the
expected agreement-bucket counts. Comparability facts that the
HELM-shaped pipeline derives from `run_spec.json` (scenario class,
deployment, instructions, max_eval_instances) collapse to `unknown` for
EEE-only inputs and surface as `comparability_unknown:*` warnings.

## Stage 1 — EEE conversion

The canonical comparison input is the EEE artifact format (`every_eval_ever`,
under [`submodules/every_eval_ever/`](../submodules/every_eval_ever/)). Both
public HELM runs and local audit runs get converted into the same shape.

**Conversion code:** [`eval_audit/normalized/eee_artifacts.py`](../eval_audit/normalized/eee_artifacts.py)
calls `every_eval_ever.converters.helm.adapter.HELMAdapter`.

**Output:**

```
$AUDIT_STORE_ROOT/eee/local/<experiment>/<helm_id>/<run-slug>/
├── eee_output/<dataset>/<developer>/<model>/<uuid>.json   # one per evaluation log
├── status.json
├── provenance.json
└── reproduce.sh
```

For public HELM runs, the equivalent tree lives under
`$AUDIT_STORE_ROOT/crfm-helm-public-eee-test/<suite>/<version>/<run>/eee_output/`.
That conversion is driven by the `eval-audit-prepare-eee` CLI
([`eval_audit/cli/prepare_eee.py`](../eval_audit/cli/prepare_eee.py), backed by
[`eval_audit/normalized/eee_artifacts.py`](../eval_audit/normalized/eee_artifacts.py))
— it converts the official runs in scope on demand (the full public corpus is
~36k runs; converting all of it is the slow upstream step).

EEE artifacts carry `source_organization_name=eval_audit_local` for local
runs (renamed from `helm_audit_local` on 2026-04-28).

## Stage 2 — Virtual experiment compose

A virtual experiment is a YAML-declared *slice* over the existing audit data.

**Manifest:** `configs/virtual-experiments/<name>.yaml`. Two checked-in
examples:

- [`pythia-mmlu-stress.yaml`](../configs/virtual-experiments/pythia-mmlu-stress.yaml)
- [`open-helm-models-reproducibility.yaml`](../configs/virtual-experiments/open-helm-models-reproducibility.yaml)

A manifest declares:

- `sources` — which official-public-index rows and local-audit-index rows are
  in scope (by model glob, benchmark glob, etc.). Sources can include a
  `pre_filter` block referencing the Stage-1 filter inventory so the Sankey
  shows the funnel from the universe of all HELM runs down to the manifest
  scope.
- `scope` — `MultiPattern` filters applied to those sources.
- Provenance metadata for the publication surface.

**CLI:** `eval-audit-build-virtual-experiment <manifest>`.

**What it does:** loads the official + local indexes, applies the manifest
scope, computes the three-level coverage funnel
([`eval_audit/virtual/coverage.py`](../eval_audit/virtual/coverage.py)):

| level | meaning |
|---|---|
| logical | same scenario + model + augmentation |
| recipe-canonical | + same scenario_spec, prompt, decoding, max_train_instances after schema-collapsing the run_spec |
| recipe-identical | byte-for-byte `run_spec_hash` match |

Why three levels: HELM's run_spec schema evolves between releases, so the
raw `run_spec_hash` produces 0 matches even when the recipe is semantically
identical. The canonical-recipe hash (in `_canonical_recipe_hash`) collapses
known schema-evolution fields (`chain_of_thought_prefix`, `global_suffix`,
`num_trials`, `model_deployment`, etc.) before hashing. See
[`docs/helm-gotchas.md`](helm-gotchas.md) §G1.

**Output:** writes a packet manifest plus coverage artifacts to
`$AUDIT_STORE_ROOT/virtual-experiments/<name>/`.

## Stage 3 — Per-packet core analysis

A "packet" is one local-run / public-row pairing being compared. The packet
planner ([`eval_audit/planning/core_report_planner.py`](../eval_audit/planning/core_report_planner.py))
turns the virtual-experiment compose output into individual analysis jobs.

**How local and official runs are paired.** The planner buckets components by
the **order-insensitive canonical logical key**
([`canonical_logical_key`](../eval_audit/run_entries.py)): parse the
`benchmark:k=v,...` run name, drop bookkeeping-only tokens
(`groups=`, `model_deployment=`), canonicalize values (model `/`<->`_`,
`mmlu_pro` `subject`->`subset`), then re-serialize with the kv pairs **sorted
by key**. Two runs that are the same token set in a different order — or differ
only by `groups=` — collapse to one key, while every semantic token
(`eval_split`, `method`, `subject`, `model`, ...) is preserved, so lite vs
full-sweep recipes and different subjects stay distinct. This is a *symmetric*
equivalence (the right tool for grouping), distinct from
`run_dir_matches_requested`'s asymmetric subset test (the right tool for "does
this candidate satisfy this request", used by `compare_batch`). It replaced an
order-sensitive, string-variant matcher that left runs like OLMo MMLU unpaired
purely because the official key listed the same tokens in a different order.
When canonicalization changes a key, the affected packet carries a
`keys_canonicalized:original_keys=...` warning. (`EVAL_AUDIT_GROUP_STRIP` is a
deprecated no-op — canonicalization is always on.)

**CLI:** `eval-audit-analyze-experiment` for a single experiment;
`eval-audit-analyze-many` to batch across experiments.

> **A packet may hold more than one local attempt.** When an experiment contains
> several local runs for the same official row (a pre-fix attempt and a rerun, a
> smoke and a full, two suites covering the same subject), the planner keeps all
> of them but enables **one** `official_vs_local` — against the canonical
> attempt, newest by `manifest_timestamp`. The others are emitted
> `enabled=False, disabled_reason="superseded_local_attempt"` and retyped as
> `local_repeat`, which is the question they actually answer. A packet therefore
> reports exactly one answer to "how well did this row reproduce?".
>
> Reductions over `pairs[]` must **select** an attempt regardless, never average
> — averaging a collapsed run against a working one halves the cell — and the
> reporting layer does this through one shared rule
> ([`eval_audit/reports/attempt_selection.py`](../eval_audit/reports/attempt_selection.py)),
> recording which rule it used beside the number. Stores built before
> 2026-08-05 still carry the old shape until re-rendered. Run
> `eval-audit-lint-store <store>` to find packets where the choice is worth
> something; it grades both shapes. See [`docs/helm-gotchas.md`](helm-gotchas.md)
> §G14 for the grading, the full-tree scan, and the three times this has bitten.

**What it does:** for each packet, loads both sides via the normalized
loader ([`eval_audit/normalized/loaders.py`](../eval_audit/normalized/loaders.py)),
runs the **unified comparison core**
([`eval_audit/normalized/diff.py`](../eval_audit/normalized/diff.py) —
`NormalizedDiff`, assembling
[`normalized/compare.py`](../eval_audit/normalized/compare.py) agreement rows,
curves, and the framework-free diagnosis in
[`normalized/diagnose.py`](../eval_audit/normalized/diagnose.py)),
and emits a per-packet core-metric report
([`eval_audit/reports/core_metrics.py`](../eval_audit/reports/core_metrics.py))
including per-instance ECDFs, agreement curves, comparability facts, and a
diagnosis (`deployment_drift`, `execution_spec_drift`,
`completion_content_drift`, `multiple_primary_reasons`, etc.). On the
HELM-driven path, `HelmRunDiff` additionally contributes the
run_spec.json *semantic* diff — how "same recipe" is proven rather than
asserted.

Two per-component provenance controls (Phase 3):

- `--instance-source {helm-preferred,eee-only}`: whether EEE-format
  components may enrich instance joins from their recorded HELM origin
  (`helm-preferred`, the default — degradations are recorded, never
  silent) or must stay EEE-pure (`eee-only`, what the EEE-only CLIs
  pass). The result is recorded per component as
  `pairs[].instance_sources` in the report. Subsumes the deprecated
  `EVAL_AUDIT_EEE_STRICT` env var.
- **Declared judge substitutions** (open-judge extension): runs admitted
  by `eval-audit-index-historic --allow-closed-judge-benchmarks` carry
  `judge_substitution_planned`, which the planner turns into
  `substitutions: ["judge"]` plus a scoped `same_judge` comparability
  fact (judge identities resolved through
  [`eval_audit/judge_registry.py`](../eval_audit/judge_registry.py)).
  The rendered pair re-labels the declared difference as
  `intended_substitution:judge` and attaches a `metric_class_split`
  separating deterministic metrics (the reproduction control) from
  judge-dependent metrics (the substitution measurement).

**Provenance emitted per packet.** `core_metric_report.json` records what each
side's number was computed *from* and what code computed it, so "this figure is
still what is on disk" becomes a check rather than a claim
([`eval_audit/normalized/digests.py`](../eval_audit/normalized/digests.py)):

| field | is |
|---|---|
| `component_digests[<id>].scores` | sha256 over `run_spec.json` + `stats.json` + `per_instance_stats.json` (EEE: the aggregate `.json` + `_samples.jsonl`) — what every reported metric is a function of |
| `component_digests[<id>].completions` | sha256 over `scenario_state.json` — what the diagnostics read. Separate, so a re-conversion touching completions cannot invalidate a score claim |
| `component_digests[<id>].status` | `ok` / `partial` / `missing` — a pruned run records the absence rather than failing the render |
| `pairs[].input_digest` | one digest over both sides plus the tolerance grid and the code identity |
| `code_identity` | git sha + `eval_audit` version |

The code identity is *inside* the comparison digest, not beside it: identical
artifacts through changed code give a different number, so a digest that
omitted it would certify a result it cannot reproduce. Only the named files are
hashed — hashing the run directory would churn on logs and absolute paths.
Measured cost ≈ 15.9 MB and 0.1 s per packet, so ≈ 2 min added to a
1000-packet render.

A digest proves *same inputs*, not *same answer*, and says a number is
attributable — not that it is correct.

**Checking it later.** `eval-audit-verify-provenance <store-root>` re-hashes each
component from the path its report recorded and compares:

| verdict | means | exit |
|---|---|---|
| `match` | re-hashes to what the report recorded | 0 |
| `drifted` | the path resolves, the content differs — the report describes something that is no longer there | **1** |
| `missing` | the recorded artifacts are gone | 1, or 0 with `--allow-missing` |
| `unhashed` | the report predates digests and records nothing to check | 0, or 1 with `--require-digests` |

`unhashed` passes rather than failing because every store rendered before
2026-08-05 is in that state; it is counted so the gap reads as a gap instead of
as "verified". Scope with `--store`, `--model`, `--benchmark`. Pairs with
`eval-audit-lint-store`: the lint says whether a packet's number depended on an
unrecorded choice, this says whether its inputs are still there.

**Output:**

```
# virtual-experiment scope (what the runbooks' 30_compose.sh produces):
$AUDIT_STORE_ROOT/virtual-experiments/<name>/analysis/core-reports/<packet-slug>/
# single-experiment scope (eval-audit-analyze-experiment --experiment-name X):
$AUDIT_STORE_ROOT/analysis/experiments/<experiment>/core-reports/<packet-slug>/

├── components_manifest.json
├── core_metric_management_summary.txt
├── core_metric_ecdfs.png         # per-metric agreement ECDF
├── *.json                         # comparability facts, etc.
└── .history/                             # stamped past runs
```

## Stage 4 — Aggregate / publication

**CLI:** `eval-audit-build-summary` (with `--analysis-root` and
`--no-filter-inventory` flags exposed for virtual-experiment scope).

**What it does** (see
[`eval_audit/workflows/build_reports_summary.py`](../eval_audit/workflows/build_reports_summary.py)):

1. **Sankey A — Universe → Scope:** how the 13k+ universe of discovered HELM
   runs narrows to the manifest's in-scope rows. Stages: structural gate,
   metadata gate, open-weight gate, tag gate, deployment gate, size gate,
   manifest scope.
2. **Sankey B — Scope → Reproduced → Analyzed:** how in-scope rows funnel
   to logical match → recipe-canonical match → analyzed packet → agreement
   bucket.
3. **Coverage funnel summary:** the three-level table from Stage 2,
   formatted as `coverage_funnel_summary.txt`.
4. **Prioritized examples:** quantile-bucketed example packets
   (`score_ge_95`, `best`, `mid`, `worst`, `score_lt_80`, `flagged`).
5. **Aggregate README:** narrative report combining the above.

**Output:**

```
$AUDIT_STORE_ROOT/virtual-experiments/<name>/
├── manifest.yaml
├── provenance.json
├── scoped_filter_inventory.json
├── reports/
│   ├── aggregate-summary/all-results/
│   │   ├── README.txt
│   │   ├── sankey_a_universe_to_scope.html
│   │   ├── sankey_b_scope_to_analyzed.html
│   │   └── prioritized_examples.latest/{score_ge_95,best,mid,worst,score_lt_80,flagged}/
│   └── scoped_funnel/
│       ├── coverage_funnel_summary.txt
│       └── missing_targets.csv
└── REPRODUCIBILITY_REPORT.md           # hand-written narrative
```

## Filesystem-as-interface

`*.<ext>` are symlinks to the most recent stamped run; the stamps
live under `.history/`. Many directories also carry a `reproduce.sh`
that re-runs the computation that produced that directory. ADRs 4 ("the
filesystem is part of the interface") and 5 ("every meaningful generated
output gets a reproduce script") in
[`docs/architecture.md`](architecture.md#appendix-architecture-decision-records)
describe the convention.

## Indexing (used by Stages 2–4)

Both Stage 2 and Stage 4 read from two indexes:

- **Local audit index:** `eval-audit-index` builds the audit-results index
  CSV/JSONL at `$AUDIT_STORE_ROOT/indexes/audit_results_index_<timestamp>.{csv,jsonl,txt}`.
  Re-run before composing if new audit runs have appeared on disk.
- **Official public index:** built by [`eval_audit/workflows/analyze_index_snapshot.py`](../eval_audit/workflows/analyze_index_snapshot.py)
  (`eval-audit-analyze-index-snapshot`; formerly named `analyze_official_index`)
  from the public HELM corpus mirror at `/data/crfm-helm-public/`. **UNSURE**:
  exact regeneration cadence; check `$AUDIT_STORE_ROOT/indexes/official_public_index*` modification times.

## Execution: from-spec replay

This is how the local side of every current audit result was produced. It does
**not** use `eval-audit-make-manifest`: the manifest producer is
`export-benchmark-bundle`, which freezes the official run specs and writes the
manifest in one step.

```
official run_spec.json (in /data/crfm-helm-public)
        │
        ▼
  export-benchmark-bundle          python -m eval_audit.integrations.infer_stack
  --preset <p> --from-spec         → <bundle-root>/{smoke,full}_manifest.yaml
  [--freeze-rel-paths] [--era K]     + era-schema model_deployments.yaml
        │
        ▼
  eval-audit-run <manifest>        kwdagger schedule → docker run (pinned digest)
  --lease --run=1                  → /data/crfm-helm-audit/<experiment>/...
  --container-image <ref>            + container_provenance.json
```

Two variants, differing only in which HELM builds the run:

| | modern | era-pinned |
|---|---|---|
| when | the official run is v0.5+ | the official run is pre-v0.5 (`v0.2.4`, `v0.3.0`) |
| image | modern runner (HELM 0.5.x) | per-era CPU-only image, HELM at that era's release commit |
| inner CLI | magnet's from-spec CLI | `helm_era_shim.replay` |
| export | `--from-spec`, or `--freeze-rel-paths` for exact-path replay | `--freeze-rel-paths --era <key>` (exact-path only) |
| runbooks | `olmo_models_combined`, `qwen_models_combined`, `gpt_oss_20b_from_spec` | `classic_together_combined` |

Containerization is **mandatory** — a manifest with no pinned image is refused at
schedule time. Details, including the era invariants and the digest-pinning
mechanism, are in
[`docs/container-execution.md`](container-execution.md).

**Rerunning into a live experiment.** kwdagger keys a job on the hash of its
algo params, so changing the recipe (a tokenizer flag, a dtype) correctly mints
a *new* job rather than overwriting the old one — and both then sit in the same
experiment, where downstream they become candidate attempts at the same run
entry. `eval-audit-run --run=1` reports that when it happens, naming the prior
and new job ids; `--strict-attempts` exits nonzero instead. It never blocks, and
**resuming a partly-finished sweep never triggers it** — a skipped entry creates
no new attempt. To keep attempts separate, re-run with a different
`--experiment-name`. See [`docs/helm-gotchas.md`](helm-gotchas.md) §G14.

### The runbook step ladder

The from-spec runbooks share a numbered shape; running them in order is the
end-to-end pipeline, execution half then analysis half:

```
00_check_env.sh              eval-audit-check-env
05_check_profiles.sh         the endpoints this preset needs are defined
06_check_hf_auth.sh          HuggingFace token resolves    (06_check_era_images.sh for eras)
07_check_container_image.sh  image present + labels match
08_check_discovery.sh        every official run_spec resolves BEFORE any GPU time
10_run_smoke.sh              export(freeze) → run a few instances --lease
15_run_full.sh               the batch
20_index_local.sh            local audit index (see Indexing above)
25_index_official_*.sh       official index for this scope (era runbooks)
30_compose.sh                Stage 2 → virtual experiment (Stage 1 EEE conversion
                             happens on demand underneath it, and Stage 3 runs per packet)
40_build_summary.sh          Stage 4 → publication surface
```

Step `08` is the load-bearing one: discovery failures are environment mismatches,
and catching them before `10` is what keeps a GPU batch from failing halfway.

## What this pipeline does *not* cover

- Standing up KubeAI / LiteLLM serving (the current path leases vLLM endpoints
  through infer-stack). Last-known-good runbooks: `small_models_kubeai/`,
  `gpt_oss_20b_vllm/`.
- The pre-container `eval-audit-make-manifest` → bare-venv flow. Runbooks
  `apples/`, `historic_grid/`, `smoke/`, `qwen2_72b_vllm/` still describe it;
  it can no longer run, because containerization is now required.
- Refreshing the public-HELM mirror at `/data/crfm-helm-public/`.
- Judge substitution, which has its own pipeline (response snapshots → judgment
  attempts → judge-variance report): see
  [`reproduce/open_judge_gpt_oss/`](../reproduce/open_judge_gpt_oss/) and
  [`docs/planning/open-judge-plan.md`](planning/open-judge-plan.md).

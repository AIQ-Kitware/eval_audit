# HELM Gotchas — undocumented or hard-to-see behaviors

A running ledger of HELM-specific behaviors we've hit while building the
audit pipeline. Each entry is short, names the symptom, the underlying
mechanism, and where we worked around it. Worth reading before the
NeurIPS EEE paper appendix is finalized; many of these are exactly the
kind of things a reviewer will ask about.

Append to this file as new gotchas surface; do not delete entries
(they're the institutional memory).

---

## G1. `run_spec.json` schema evolves silently between HELM releases

**Symptom.** Byte-for-byte hashing of `run_spec.json` produces 0/N
matches between local audit runs and public HELM rows that describe the
*same* recipe.

**Mechanism.** Newer HELM populates `adapter_spec` fields that older
HELM didn't write at all:

| field | new-HELM value | old-HELM behavior |
|---|---|---|
| `adapter_spec.chain_of_thought_prefix` | explicit `""` | absent (default `""`) |
| `adapter_spec.chain_of_thought_suffix` | explicit `"\n"` | absent (default `"\n"`) |
| `adapter_spec.global_suffix` | explicit `""` | absent (default `""`) |
| `adapter_spec.num_trials` | explicit `1` | absent (default `1`) |
| `adapter_spec.model_deployment` | explicit (e.g. `huggingface/eleutherai/pythia-6.9b`) | absent (defaulted to HF API) |
| top-level `metric_specs` | populated | absent or different shape |
| top-level `groups`, `annotators` | populated | absent |

A canonical-recipe hash that strips/defaults these fields produces
realistic match rates across releases.

**Workaround.** `eval_audit.virtual.coverage._canonical_recipe_hash`
computes a schema-collapsed hash; the coverage funnel reports both the
raw-hash match (= byte-for-byte identical) and the canonical-hash match
(= schema-collapsed). The gap between them is HELM-version churn; the
gap between canonical-hash and logical-key matches is real recipe drift.

---

## G2. The `suite` field on local audit runs is the experiment name, not a public-track version

**Symptom.** Versioned join (`logical_run_key + suite_version`) between
local and official rows produces 0 matches even when both refer to the
same logical run-spec.

**Mechanism.** HELM's `--suite` flag is whatever the operator passed at
run time. For public-track runs it's `v0.2.4` etc.; for local audits we
typically pass an experiment name like `audit-historic-grid`.

**Workaround.** The coverage funnel detects this (regex
`^v\d+\.\d+(\.\d+)?$` against the local-side `suite` field) and reports
`versioned_join_meaningful: False` so the summary shows `N/A` rather than
a misleading 0. Long-term fix: add a `target_suite_version` field to the
local audit index recording which public version the local run intended
to reproduce.

---

## G3. The `huggingface/<model>` deployment alias does not always exist in HELM

**Symptom.** Trying to rerun an open-weight HELM benchmark via
HuggingFace locally fails because HELM has no registered deployment
for the model.

**Mechanism.** Many public HELM rows were originally executed via the
HuggingFace API as `huggingface/<org>/<model>`. HELM's deployment
registry only ships YAMLs for a subset of these aliases. When the alias
is missing, attempting to run via that deployment errors out, and the
operator falls back to a different stack (vLLM, KubeAI, etc.) — which
introduces deployment-substitution drift.

**Workaround.** Register a custom deployment YAML pointing at a local
`LocalHuggingFaceClient` for the missing models (see G7 for the setup).
This lets the local rerun match the original deployment exactly.
Recommended for paper-quality reproducibility comparisons.

---

## G4. `adapter_spec.model_deployment` semantics differ across HELM versions

**Symptom.** Comparing `adapter_spec.model_deployment` between local
and official run_specs shows either `<MISSING> vs huggingface/<model>`
or `huggingface/<model> vs <MISSING>` for nearly every packet.

**Mechanism.** Older HELM didn't record the field (defaulted to "the HF
API"). Newer HELM does. So a local run with `huggingface/<model>`
written and an official run with the field absent are both "the HF
API" and semantically identical — but they hash differently.

**Workaround.** Treat as schema-evolution: drop the field from the
canonical recipe hash. Real serving-stack drift (e.g. `litellm/X` vs
`together/Y`) shows up in `adapter_spec.model_deployment` only when
both rows carry the field with different non-trivial values, which is
rare in our audit.

---

## G5. `adapter_spec.instructions` differs between HELM releases on identical scenarios

**Symptom.** Same scenario + same model + same data_augmentation, but
the prompt text differs.

**Mechanism.** Examples we've observed:

- MMLU: official prepends `Answer with only a single letter.\n\n` to
  the instructions; local doesn't.
- LegalBench: official prepends `Answer with only 'generic',
  'descriptive', 'suggestive', 'arbitrary' or 'fanciful'.\n\n`; local
  doesn't.
- Several legalbench scenarios show the same shape — official has a
  list-the-allowed-labels preamble, local doesn't.

This is a *real* recipe drift, not schema evolution. The model sees a
different prompt and produces measurably different output.

**Workaround.** None — this is genuine recipe disagreement. Surface it
in the per-packet `core_metric_management_summary.txt` (already
done via the `same_instructions` comparability fact); enumerate
affected scenarios in the report.

---

## G6. The `classic` HELM corpus moved bucket prefixes

**Symptom.** `gs://crfm-helm-public/benchmark_output/runs/v0.2.4/...`
returns no objects; `--list-versions classic` produces an empty list.

**Mechanism.** HELM reorganized the public bucket: `classic` runs that
used to live at the bucket root under `benchmark_output/runs/<ver>/...`
now live at `gs://crfm-helm-public/classic/benchmark_output/runs/<ver>/...`,
mirroring every other benchmark suite.

**Workaround.** The `_runs_root('classic')` quirk in
`submodules/aiq-magnet/.../download_helm_results.py` was removed; classic
now resolves like every other benchmark.

---

## G7. HuggingFace API determinism: same weights ≠ same output

**Symptom.** Replaying the same scenario+model+adapter via the original
HF API and via local vLLM produces different outputs on a small
fraction of instances.

**Mechanism.** Even with greedy decoding, the two stacks differ in:

- sampling implementation (HF transformers' `.generate()` vs vLLM's
  custom kernels)
- tokenizer batching/chunking
- stop-sequence handling
- defaults for sampling parameters that aren't explicitly set in the
  scenario adapter (e.g. `repetition_penalty`)
- handling of EOS/padding tokens

Net effect: 3-6% per-instance flip rate on multiple-choice scorers,
much higher on long-form generation (~30-40%).

**Mitigation paths.**

1. **Best**: register a `huggingface/<model>` deployment in HELM that
   uses `LocalHuggingFaceClient` for the model. This replicates the
   original serving stack on local hardware.
2. **Cheap**: pin `temperature=0`, `top_p=1.0`, identical
   `max_tokens`, identical `stop` sequences in both stacks. Doesn't
   close the gap entirely (kernel differences still flip a few
   instances) but removes sampling-policy drift.
3. **Document, don't fix**: report the gap as "deployment_drift" and
   distinguish it from genuine model-correctness drift. This is what
   the case study currently does; it's the publishable narrative.

---

## G8. `metric_specs` schema evolution

**Symptom.** Top-level `metric_specs` differs between local and
official run_spec.json on 85/125 packets even when no other recipe
change is intended.

**Mechanism.** Newer HELM may rename or restructure `metric_specs`
entries (e.g. add scoring sub-metrics for legalbench). The field is
schema-evolving and not necessarily a recipe difference.

**Workaround.** Excluded from the canonical-recipe hash. If you want
to actually compare metrics, use the run-level metric output, not
the run_spec's `metric_specs` declaration.

---

## G9. Local index `model_deployment` doubles up the model name

**Symptom.** Some local audit rows have `model_deployment` like
`kubeai/vicuna-7b-v1-3-no-chat-template-local` — a deployment that
doesn't appear in the public HELM run_spec at all.

**Mechanism.** Local audits use custom deployments registered in the
audit's `model_deployments.yaml`. These names are stable but
audit-specific; the public HELM corpus uses different deployment names.

**Workaround.** None — this is by design. The `logical_run_key` join
collapses across deployments (matches on
`benchmark + model + augmentation + method`); the comparison's
`comparability_facts.same_deployment` correctly reports `no` when the
deployments differ.

---

## G10. Public-track `suite_version` is *not* the same as HELM release version

**Symptom.** A public run from `gs://crfm-helm-public/classic/.../v0.2.4/`
and another from `.../v0.3.0/` may have *identical* run_spec.json. The
`v0.2.4`/`v0.3.0` is the suite-tracking version, not the HELM release
version.

**Mechanism.** Public HELM publishes versioned snapshots of the
benchmark corpus. A given run_spec might appear in v0.2.4 and v0.3.0
unchanged because the suite tracks new model evaluations, not
necessarily new recipes for old models.

**Workaround.** Use `run_spec_hash` to identify recipe-identical
public-track versions of the same logical run.

**Era-replay note.** The era-pinned reproduction containers
(`docs/planning/era-pinned-helm-containers-plan.md`,
`docker/eras.yaml`) key the *measurement instrument* on
`(public_track, suite_version)` — so `v0.2.4` and `v0.3.0` map to
distinct era images. That is a **suite**-era mapping, and for the
classic track it is the right granularity (the two suite dirs were
produced by the two era harnesses). It is NOT a claim that suite
version == release version in general; `run_spec_hash` remains the tool
for detecting recipe-identical duplicates *across* suites. Resolution
lives in `eval_audit/eras.py` (`resolve_era`), keyed on the same
path-derived signal the official public index records.

---

## G11. `per_instance_stats.json` corruption on giant runs

**Symptom.** `every_eval_ever convert helm` fails with
`json.decoder.JSONDecodeError: Unterminated string starting at: line
57094485 column 23 (char 3644456680)` on certain msmarco runs.

**Mechanism.** Public HELM's `cohere_small-20220720` msmarco runs were
originally ~3.5 GB on disk and got truncated mid-write during the
upload to GCS (apparent based on consistent-byte-offset failures
across the v0.2.2/v0.2.3/v0.2.4 mirrors). Recently re-uploaded
versions are ~44 MB and parse cleanly.

**Workaround.** The EEE converter (`eval-audit-prepare-eee`, backed by
`eval_audit/normalized/eee_artifacts.py`) surfaces these as
`JSONDecodeError` failures; redownload the affected paths via
`download_helm_results.py` (size mismatch triggers fresh fetch).

---

## G12. `run_spec_hash` is computed from canonicalized JSON, but canonicalization differs by release

**Symptom.** Two run_spec.json files with semantically identical
content can have different `run_spec_hash` values across HELM releases.

**Mechanism.** HELM canonicalizes the run_spec (key sort, drop
implementation-specific fields like absolute paths) before hashing,
but the canonicalization rules are HELM-version-dependent. New fields
added in newer releases mean the canonicalized form differs.

**Workaround.** Use the *coverage*-side canonical hash
(`_canonical_recipe_hash` in `eval_audit/virtual/coverage.py`), which
applies our own normalization on top of HELM's. The output is stable
across HELM releases for the same recipe.

---

## G13. Classic-corpus `class_name` paths are a migrated hybrid that resolves in *no* HELM version

**Symptom.** Era-replaying an original-paper classic run (GPT-J 6B /
GPT-NeoX 20B / OPT-66B) fails the era shim's class preflight:

```
Preflight failed: ... cannot resolve 1 class(es) referenced by the run_spec.json:
  - helm.benchmark.basic_metrics.BasicMetric: ModuleNotFoundError:
    No module named 'helm.benchmark.basic_metrics'
```

`BasicMetric` is in **every** classic run_spec (735/735 for these three
models; 9 distinct drifted metric classes total), so this blocks the
*entire* `reproduce/classic_together_combined` runbook — while
`dev/era-tests` (redpajama-3b) sails through.

**Mechanism.** The `class_name` string stored in the public classic
corpus is **not a faithful record of the producing code's import path**.
It is a once-migrated hybrid: at some point HELM bulk-rewrote archived
run_specs with a naive `benchmark.` → `helm.benchmark.` prefix
substitution (when the package moved to `src/helm/` at commit
`c2ee966d`, 2022-11-16 "Rename modules and commands"), **preserving
whatever module nesting existed at production time**. The metric classes
were *flat* under `benchmark/` at production but were later nested into
`benchmark/metrics/` — which the naive prefix rewrite did not account
for. The result, `helm.benchmark.basic_metrics.BasicMetric` (helm
prefix **+** flat), is a layout that **existed in no git commit**
(`git log -- src/helm/benchmark/basic_metrics.py` → 0 commits): flat
metrics only ever existed *without* the `helm.` prefix, and the `helm.`
prefix only ever appeared *after* metrics were nested.

Full rename-aware lineage of `basic_metrics.py` (from a blob-less clone
of `stanford-crfm/helm`):

| commit | date | path → import |
|---|---|---|
| `301ab631` | 2021-12-28 | `src/basic_metrics.py` → `basic_metrics` |
| `59b412c2` | 2021-12-28 | `src/benchmark/basic_metrics.py` → `benchmark.basic_metrics` (flat) |
| `37d8707a` | **2022-08-26** "Refactor metrics" | `src/benchmark/metrics/basic_metrics.py` → `benchmark.metrics.basic_metrics` (nested) |
| `c2ee966d` | 2022-11-16 "Rename modules and commands" | `src/helm/benchmark/metrics/basic_metrics.py` → `helm.benchmark.metrics.basic_metrics` |

Reversing the naive migration recovers the *producing-code* paths from
the stored run_spec: scenario `benchmark.scenarios.babi_qa_scenario`
(nested) + metric `benchmark.basic_metrics` (flat). Scenarios were
nested at `0c8738c8` (2022-07-31 "move scenarios to scenarios"); metrics
were nested at `37d8707a` (2022-08-26). The only window where scenarios
are already nested **and** metrics are still flat is
**2022-07-31 → 2022-08-26** — so these officials were produced by
**unreleased pre-v0.1.0 HELM from that ~4-week window** (v0.1.0 was
tagged 2022-11-17). No released/tagged HELM version matches them, and
building the true origin instrument is infeasible (untagged commit,
py3.8 + mid-2022 deps, and v0.1.0-era HELM predates the whole
`model_deployments` architecture the era shim relies on — see the
carry-forward memory / `docs/eee-vs-helm-metadata.md`).

Why redpajama-3b is immune: it debuted ~v0.2.3 (mid-2023), *after* the
metrics-subpackage refactor, so its stored run_specs already carry the
subpackage path `helm.benchmark.metrics.basic_metrics.BasicMetric`,
which resolves natively at the v0.2.4/v0.3.0 era builds. The **metric
class-path form is the origin fingerprint**: flat
`helm.benchmark.<X>_metrics` = pre-refactor (this migrated hybrid);
subpackage `helm.benchmark.metrics.<X>_metrics` = post-refactor.

**Workaround.** A self-verifying, declared class-path canonicalization in
the era shim: when a flat `helm.benchmark.*` class fails `get_class_by_name`
**and** its `helm.benchmark.metrics.*` relocation resolves (same leaf
class), remap it on the in-memory run_spec before preflight + scoring,
recorded as a declared substitution. It only fires for the known
relocation (a genuinely wrong era pin still fails loudly), covers all 9
drifted classes, and is a *spec* adaptation — not patching the era image
— consistent with the documented "later-era proxy instrument" framing.

**Implemented** in `docker/era_shim/helm_era_shim/replay.py`:
`_canonical_class_name` (single-path probe + relocation), `_remap_object_spec_tree`
(rebuilds the frozen ObjectSpec tree), and `_canonicalize_class_paths` (applies
it to `scenario_spec` + `metric_specs` — the same roots the preflight checks).
It runs as step "1b" right after the strict decode, so both the preflight and
scoring use the resolvable class, and the run dir's emitted `run_spec.json`
records the remapped path. Each substitution is recorded on the run manifest as
`class_path_substitutions` (a declared substitution) and logged to stdout. The
run-name/logical-key pairing and canonical-recipe hash (which excludes
`metric_specs`, see G8) are unaffected, so the local↔official comparison does not
regress. Tests: `tests/test_era_shim_hostside.py` (era-resolver-simulated).

---

## G14. A store can hold several local attempts per official row — the join must *select*, not average

**Symptom.** An aggregate reports roughly half the score the underlying runs
show. Concretely: `olmo-models-combined` MMLU for `allenai/olmo-7b` reads
public/local `0.295/0.144` under one reduction and `0.295/0.287` under another,
from *the same artifacts*.

**Mechanism.** An experiment legitimately accumulates more than one local run for
the same official row — a pre-fix attempt and a post-fix rerun, a smoke and a
full, two suites (`/mmlu` and `/lite`) covering the same subject. The planner
keeps all of them and emits `local_repeat` comparisons alongside the
`official_vs_local` one (`eval_audit/planning/core_report_planner.py`). A packet
therefore has *n* pairs, not one, and any reduction over `pairs[]` that treats
them as independent observations averages a collapsed run against a working one.

For `olmo-7b` specifically the second attempt is the **tokenizer collapse of
G-series (a)**: completions are the prompt-independent `"The …"` boilerplate and
`exact_match` is `0.000`, so averaging with the good run halves the cell exactly.

**How to check.** `eval-audit-lint-store` reports every packet with competing
attempts, and grades each by how much the unrecorded choice is worth (the spread
in zero-tolerance agreement across the attempts):

```bash
eval-audit-lint-store /data/crfm-helm-audit-store/virtual-experiments
eval-audit-lint-store <store> --json lint.json --strict
```

`MATERIAL` (attempts disagree by more than `--tol`) fails the lint; `BENIGN`
(several attempts that agree, so any selection gives the same answer) is reported
only. Nonzero exit on any `MATERIAL`, or on any ambiguity at all under `--strict`.

Full-tree scan 2026-08-04 — 1040 packets, 137 ambiguous:

| store | packets | ambiguous | max spread |
|---|--:|--:|--:|
| `qwen-models-combined` | 703 | — | — |
| `gpt-oss-20b-from-spec` | 4 | — | — |
| `era-redpajama-v024` / `-v030` | 2 / 2 | — | — |
| `e2e-phi2-{vllm,container,hf,incomparable}` | 1 each | — | — |
| `e2e-phi2` (first pass) | 1 | 1 MATERIAL | 0.222 |
| `olmo-models` | 175 | 38 MATERIAL + 27 BENIGN | 0.303 |
| `olmo-models-combined` | 149 | 71 MATERIAL | 0.623 |

All 71 in `olmo-models-combined` are `olmo-7b`. The `e2e-phi2` first-pass packet
holds **seven** attempts scoring 0.77–0.99, and the number that store reports is
its first — so that store's headline is a selection, not a measurement.

**How the code chooses.** Every reduction over `pairs[]` routes through
`eval_audit/reports/attempt_selection.py::select_official_vs_local`, which
returns the chosen pair *plus the rule that chose it*, in priority order:

| rule | when | provenance |
|---|---|---|
| `single_attempt` | one candidate | no choice was made |
| `latest_manifest_timestamp` | serialized `manifest_timestamp` on every candidate | strong |
| `latest_manifest_timestamp:attempt_fallback_key` | timestamp recovered by parsing `attempt_fallback_key` | weaker — packet predates the field |
| `pair_order` | no timestamps, or a tie | historical fallback |

Latest-wins matches the order the planner already sorts components by
(`_component_sort_key`), so the analysis layer and the planning layer name the
same run. It is a convention, not evidence — the newest attempt is not
necessarily the correct one — so a `MATERIAL` packet stays uncitable regardless
of which attempt the rule picks. `local_repeat` is never a candidate: a repeat is
an intentional noise measurement, not a rival answer.

Two behaviours changed when the guard landed (2026-08-05):

* the aggregate score-drift collector already selected, but by `ovl_pairs[0]`;
  it now selects by the shared rule and logs which one it used. On the existing
  olmo stores the two agree on all 162 cells, because the planner had already
  emitted newest-first — the guard makes that accidental agreement deliberate;
* the **instance-agreement** collectors (`_collect_cells`,
  `_collect_cells_per_metric`) were genuinely pooling `matched`/`count` across
  attempts. Selecting instead moves exactly the `olmo-7b` cells:

  | cell | pooled | selected |
  |---|--:|--:|
  | `olmo-7b` / `narrative_qa` | 0.637 | 0.948 |
  | `olmo-7b` / `mmlu` | 0.824 | 0.939 |
  | `olmo-7b` / `legalbench` | 0.794 | 0.967 |
  | `olmo-7b` / `med_qa` | 0.860 | 0.937 |
  | `olmo-7b` / `commonsense` | 0.852 | 0.926 |
  | `olmo-7b` / `gsm` | 0.977 | 0.990 |

  No other model in any store moves. Cells now also carry
  `n_attempts_dropped`, `n_ambiguous_packets` and `selection_rules`, so a cell
  built from a choice says so.

Rendered store artifacts predate this and still show the pooled values until
re-rendered.

**Rule.** A figure read from a multi-attempt store is meaningless without the
selection rule that produced it, and the rule belongs next to the figure. This
has now bitten three times: once in the aggregation code (fixed), once in an
ad-hoc analysis that re-derived the halved number, and once as a *false causal
reading* — two phi-2 reports differing by 0.003 were attributed to
containerization when they had simply paired against different same-config
attempts (four of five phi-2 runs are byte-identical, including the
containerized one; the outlier was the arm being called best).

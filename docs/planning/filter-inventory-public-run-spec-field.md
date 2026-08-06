# Plan: Add the materialized public run spec to `filter_inventory.json`

**Status:** PLAN — not yet implemented. 2026-06-18.
**Scope:** Stage 1 (`index_historic_helm_runs`) only. Additive field; no
behavior change to any existing field, downstream stage, or the runner.

## Objective

Add a field to every `filter_inventory.json` row that records the **accurate
public run spec** — the materialized recipe HELM actually executed — so the
inventory no longer represents a public run solely by the lossy `run_spec_name`
string.

## Root cause (what we're fixing)

`filter_inventory.json` rows are keyed on `run_spec_name`, which Stage 1
*reconstructs* from the materialized `run_spec.json` via
`reconstruct_run_entry_from_run_spec`
([run_entries.py:231](../../eval_audit/run_entries.py#L231)). That
reconstruction draws only from `scenario_spec.args`, `adapter_spec.method/model`,
and **display-name** tokens. It therefore *structurally cannot* recover the
**droppable run-expander keys** (`output_format_instructions`, `temperature`,
`chatml`, `process_output`, …) catalogued in
`reproduce/olmo_models/NOTES-dropped-run-expander-keys.md` (runbook since
renamed to `reproduce/olmo_models_combined/`; the NOTES file no longer exists
in the tree).

The run name is unreliable at **every** level — index `run_spec_name`, the run
directory name, and `run_spec.json["name"]` are the same expander-blind string.
The BBQ `output_format_instructions=mcqa` drift is the worked example: the
official `adapter_spec.instructions` carried a single-letter MCQA prefix that no
name level recorded. The inventory currently inherits that blind spot.

## What "accurate public run spec" means here

Per the NOTES trust hierarchy, the authoritative recipe is the **materialized
`run_spec.json`** (`adapter_spec` + `scenario_spec` + `metric_specs`, plus
`annotators`/`data_augmenter_spec`) — *after* all expanders applied. Every
droppable expander lands in those fields. This is the value we store. We do
**not** attempt to reconstruct a re-runnable entry string with the expander keys
(see Alternatives §A).

The materialized recipe is **already in hand**: `build_run_table` loads
`run_spec = run.json.run_spec()`
([historic_filtering.py:103](../../eval_audit/indexing/historic_filtering.py#L103))
and currently discards everything except four scalar fields. We carry it
forward instead of discarding it — so the common path adds **zero extra I/O**.

## New row fields

| field | value | source |
|---|---|---|
| `public_run_spec` | recipe-bearing subset of `run_spec.json`: `{adapter_spec, scenario_spec, metric_specs, annotators, data_augmenter_spec}` — the materialized recipe, verbatim. **Omits the misleading top-level `name`** (already captured, lossily, by `run_spec_name`). | already-loaded `run_spec` dict |
| `public_run_spec_fpath` | absolute path to `<run_dir>/run_spec.json` | `run_dir` (already on every row) |
| `public_run_spec_hash` | SHA-256 of the normalised spec — stable identity that the lossy name cannot provide | `compute_run_spec_hash` ([schema.py:157](../../eval_audit/indexing/schema.py#L157)) |
| `public_run_spec_status` | `present` \| `absent` \| `error` — honest about runs with no/unreadable spec | derived |

A `--inventory-embed-run-spec={recipe,full,pointer}` knob controls verbosity:
- `recipe` (default): the subset above — authoritative for comparability, bounded size.
- `full`: the entire `run_spec.json` dict verbatim (lossless, larger).
- `pointer`: only `public_run_spec_fpath` + `public_run_spec_hash` + `public_run_spec_status` (smallest; consumer reads the file).

Rationale for the default: `adapter_spec` is the field the NOTES tells you to
diff for comparability; embedding the recipe subset makes that diff possible
directly from the inventory while keeping per-row size at ~a few KB.

## Sites that change (Stage 1 only)

1. **`build_run_table`**
   ([historic_filtering.py:86-157](../../eval_audit/indexing/historic_filtering.py#L86-L157))
   — the `run_spec` dict is already loaded at
   [:103](../../eval_audit/indexing/historic_filtering.py#L103). Extend the
   appended row ([:141-154](../../eval_audit/indexing/historic_filtering.py#L141-L154))
   with the recipe subset (or full/pointer per the knob) and the hash. This is
   the *only* place that touches HELM artifacts on the common path — keep the
   extraction here so `build_filter_inventory_rows` stays a pure transform.

2. **`build_filter_inventory_rows`**
   ([historic_filtering.py:336-425](../../eval_audit/indexing/historic_filtering.py#L336-L425))
   — already spreads `**row` into the inventory row
   ([:399-422](../../eval_audit/indexing/historic_filtering.py#L399-L422)), so
   the new keys flow through to **complete** rows automatically once (1) is
   done. No change needed beyond confirming the new keys aren't shadowed by the
   `**info`/`**judge_fields` spreads.

3. **`build_incomplete_inventory_row`**
   ([historic_filtering.py:299-333](../../eval_audit/indexing/historic_filtering.py#L299-L333))
   — incomplete runs may lack a parseable `run_spec.json`. Best-effort: if
   `<run_dir>/run_spec.json` exists, read it; otherwise set
   `public_run_spec=None`, `public_run_spec_status='absent'`. Reuse
   `extract_run_spec_fields` ([schema.py:230](../../eval_audit/indexing/schema.py#L230))
   for the tolerant read + hash.

4. **CLI plumbing** in `index_historic_helm_runs`
   ([index_historic_helm_runs.py:447-465](../../eval_audit/cli/index_historic_helm_runs.py#L447-L465))
   — add the `--inventory-embed-run-spec` arg; pass it into
   `build_filter_inventory_rows`. The existing write site is unchanged
   (`kwutil.Json.ensure_serializable` already handles nested dicts).

No changes to Stage 2 (manifests), Stage 3 (runner), or Stages 4–6 (analysis).
Downstream consumers that don't know the field simply ignore it.

## Determinism & back-compat

- **Additive only.** Every existing field keeps its value and position. With the
  default knob, a consumer that ignores the new keys sees no change.
- **Deterministic serialization.** The embedded recipe preserves the source
  `run_spec.json` key order (deterministic per file). The hash is computed over
  the *normalised* spec via the existing `compute_run_spec_hash`, so it is
  stable regardless of cosmetic key ordering.
- **No new randomness / timestamps** — consistent with the pipeline's
  reproducibility guarantee.

## Edge cases

- **Missing / unreadable `run_spec.json`** → `public_run_spec_status='error'` or
  `'absent'`, `public_run_spec=None`. Never raise; the inventory must still build.
- **EEE-only inputs** (no HELM run dir) — out of this plan's path entirely;
  Stage 1 isn't used there. Documented as a non-applicable case.
- **File size** — `full` mode on a multi-thousand-run inventory produces an MB-scale
  file; `recipe` (default) keeps it bounded; `pointer` is minimal. The knob is
  the escape hatch.
- **Judge benchmarks** — `annotators` is included in the subset so closed-judge
  recipes are captured too (judge *identity* still lives in the annotator class,
  per [judge-identity-inventory.md](judge-identity-inventory.md), not the spec).

## Validation / testing

- Unit: extend `tests/test_filter_report_artifacts.py` —
  `build_filter_inventory_rows` populates `public_run_spec` /
  `public_run_spec_hash` for a row with a fixture `run_spec.json`, and sets
  `status='absent'` when the file is missing. Fixtures already exist under
  `submodules/every_eval_ever/tests/data/helm/*/run_spec.json`.
- Property: for a known dropped-expander run (BBQ), assert
  `public_run_spec['adapter_spec']['instructions']` carries the MCQA prefix even
  though `run_spec_name` does not — i.e. the field captures what the name drops.
- `python -m py_compile` on the two modified modules.

## Alternatives considered

**A. Store a recovered re-runnable entry string (with expander keys) instead.**
Rejected as the primary field. The original entry string lives only in HELM's
source `run_entries_*.conf` (not in the public data tree), and matching a run to
its `.conf` line is ambiguous *precisely on the dropped-expander runs that
matter*: e.g. safety BBQ (`mcqa`), palmyra (`mcqa_no_period`), and reasoning
(`mcqa,increase_max_tokens=10000`) all collapse to the same name-visible run.
Disambiguating requires expanding each candidate and diffing `adapter_spec`
against the official `run_spec.json` — i.e. it needs the materialized recipe as
its oracle, and is fragile across HELM versions with coverage gaps. Recovering
the entry string is therefore strictly more work than, and less authoritative
than, storing the recipe. (May be added later as a best-effort, confidence-flagged
*convenience* field — backfilled from the curated entry lists in
[`adapter.py`](../../eval_audit/integrations/infer_stack/adapter.py) where
available — but it is not the source of truth.)

**B. Non-invasive enrichment sidecar (no Stage 1 change).** A standalone
read-only tool reads the existing `filter_inventory.json`, opens each row's
`<run_dir>/run_spec.json`, and emits a derived enriched copy — leaving the
canonical Stage-1 artifact byte-identical. This is the better choice **if the
constraint is "do not modify the existing pipeline."** It produces the same
field set as this plan. Trade-off: the enriched recipe lives in a derived file
rather than the canonical inventory, and must be regenerated when the inventory
is rebuilt. Choose this over the in-pipeline change if Stage-1 determinism/
provenance must remain untouched.

## Out of scope (separate concern)

**Reproducing public runs *from* the materialized recipe.** It is technically
possible — `dacite.from_dict(RunSpec, run_spec.json)` → override
`adapter_spec.model_deployment` → `run_benchmarking([rs], …)` bypasses the
entry-string/expander path entirely (verified on real public specs). But that
requires an execution path the current eval-audit runner does not provide
(it shells `helm-run --run-entries`), so it belongs in a *standalone runner*
plan, not this annotation change. This field is the audit/comparability record;
it does not, by itself, change what gets executed.
```

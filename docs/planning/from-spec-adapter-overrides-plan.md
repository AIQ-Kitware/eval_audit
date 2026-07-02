# Plan: Override adapter_spec decode params for from-spec ablations

## Context

`eval_audit` reproduces official HELM runs "from-spec" by replaying the official
`run_spec.json` **verbatim**, with exactly two host-side substitutions —
`adapter_spec.model_deployment` (local engine) and `adapter_spec.max_eval_instances`
(cost cap). There is no way to deliberately vary other recipe fields, so ablations
like "how does temperature affect this run?" are impossible on the faithful-replay
path. Today the only temperature ablation
(`e2e-phi_2-vllm-philosophy-incomparable`) is forced onto the legacy **run-entry**
path (`...,temperature=1` token) and kept off from-spec — but that path is being
retired in favor of from-spec (all reproductions must be from-spec).

**Goal:** let an operator declare per-run-entry overrides of the safe decode params
on the from-spec exact-path replay, applied host-side, and have the resulting
deliberate deviation surface honestly in the comparison report.

**Why it works / why host-side:** the from-spec CLI deserializes `run_spec.json`
into a HELM `RunSpec` and hands it **directly** to `run_benchmarking(...)` — HELM does
*not* reconstruct `adapter_spec` from a run-entry string — so edits to `adapter_spec`
take effect. The exact-path pipeline already edits the JSON on the host
(`materialize_run_spec`) before the container ever runs, so extending it needs **no
runner-image rebuild / digest re-pin** (contrast: the in-container
`apply_adapter_substitutions` in the `aiq-magnet` submodule, which we deliberately
leave alone here).

## Scope decisions (confirmed with user)

- **Fields:** allowlist `{temperature, num_outputs, max_tokens, stop_sequences}` —
  a single constant so widening later is a one-line change. Unknown keys fail loud.
- **Driver (per-entry):** a `run_entry_overrides: {<run_entry string>: {...}}` map for
  per-run-entry overrides, plus an optional experiment-level `adapter_overrides` block
  as a broadcast default; per-entry values win per-key (`{**default, **per_entry}`).
  Keeps `run_entries` a pure `list[str]` (zero ripple to manifest/builder/filter code)
  and supports list-valued `stop_sequences`. Sweeps = define N experiments (or the
  existing kwdagger matrix); no new sweep harness in this cut.
- **Deviation label:** auto-detected drift via a new `same_decode_params`
  comparability fact → surfaces as `comparability_drift:same_decode_params` with the
  differing values. **No** declared-substitution re-labeling in this cut.

## Data flow (unchanged spine; new field rides existing rails)

```
preset block: adapter_overrides (default) + run_entry_overrides (per-entry map)
  → _freeze_run_spec_sources()     merges default+per-entry, attaches to each source dict
  → run_spec_sources: list[dict]   (un-schema'd → passes through ManifestSpec/YAML)
  → kwdagger bridge: coerce_sources → materialize_run_specs()
  → materialize_run_spec()         raw-JSON edit of adapter_spec.<key>, recorded in
                                   `substitutions` + materialization.json sidecar
  → from-spec CLI replays copy     produced run_spec.json carries the override
  → Stage 5/6 build_comparability_facts()  same_decode_params: official vs local
```

The `run_spec_sources` entry is a plain `dict` end-to-end (`ManifestSpec.run_spec_sources:
list[dict]` in `eval_audit/manifests/models.py`), so the new key needs **no** structural
change to the manifest model, builder, or bridge — only the code that *interprets* the
key changes.

## Changes

### 1. Materializer core — `eval_audit/manifests/run_spec_materializer.py`
- Add module constant `_DECODE_OVERRIDE_KEYS = frozenset({"temperature", "num_outputs",
  "max_tokens", "stop_sequences"})` — the single source of truth for the allowlist
  (imported by the producer and the fact helper below) — and a small
  `_validate_overrides(overrides, *, where)` that rejects any non-allowlist key
  (fail loud, naming the offending key + the location).
- `RunSpecSource`: add `adapter_overrides: dict[str, Any] = field(default_factory=dict)`.
  In `from_dict`, read `data.get("adapter_overrides")`, reject any key not in the
  allowlist, and coerce values (`temperature`→float, `num_outputs`/`max_tokens`→int,
  `stop_sequences`→list[str]).
- `materialize_run_spec()`: after the `max_eval_instances` block, iterate
  `source.adapter_overrides`; for each key compare `adapter_spec.get(key)` to the new
  value and, when different, set it and record `substitutions[key] = {"from": …, "to": …}`.
  This is byte-level identical to the existing `model_deployment` block — the change
  automatically flows into the `content_hash` (so a different temperature ⇒ new
  content address ⇒ clean kwdagger recompute) and into the `materialization.json` sidecar
  (provenance) with **no extra code**.
- `_run_id()`: fold a stable repr of `adapter_overrides` into the digest so two
  ablations of the same official run get distinct staging dirs (defensive; the
  content-hashed filename already prevents clobber).
- `source_to_dict()`: also drop an empty `adapter_overrides` dict so run-entry-path
  manifests stay byte-compatible.

### 2. Producer — `_freeze_run_spec_sources` (eval_audit/integrations/infer_stack/adapter.py)
- Resolve overrides **per entry** inside the existing loop (the `smoke_manifest`/
  `full_manifest` block is passed in as `spec`):
  - `default_ov = _validate_overrides(spec.get("adapter_overrides") or {}, where="adapter_overrides")`
  - `per_entry_map = spec.get("run_entry_overrides") or {}`; up front, assert its keys ⊆
    `spec["run_entries"]` and raise on any stray key (typo guard, fail loud at export).
  - For each `run_entry`: `overrides = _validate_overrides({**default_ov,
    **per_entry_map.get(run_entry, {})}, where=f"run_entry_overrides[{run_entry!r}]")`;
    attach `source["adapter_overrides"] = overrides` when non-empty — mirroring how
    `model_deployment`/`lease_endpoint` are attached today. `run_entries` stays `list[str]`,
    so `_manifest_doc`/builders are untouched.
- Preset authors declare an experiment default `adapter_overrides: {...}` and/or a
  per-entry `run_entry_overrides: {<entry>: {...}}` map in the manifest block. (The
  standalone `--run-spec-sources-fpath` file path in `eval_audit/manifests/builders.py`
  is per-entry for free, since each source round-trips through `RunSpecSource.from_dict`.)

Example preset block:
```python
"run_entries": [
    "mmlu:subject=philosophy,method=multiple_choice_joint,eval_split=test,model=microsoft/phi-2",
    "mmlu:subject=anatomy,method=multiple_choice_joint,eval_split=test,model=microsoft/phi-2",
],
"adapter_overrides": {"temperature": 0.0},          # optional experiment default
"run_entry_overrides": {                             # optional, wins per-key
    "mmlu:subject=philosophy,method=multiple_choice_joint,eval_split=test,model=microsoft/phi-2":
        {"temperature": 0.7, "stop_sequences": ["\n\n"]},
},
```

### 3. Docs-only touch-ups (no behavior change)
- Update the `run_spec_sources` shape comment in `eval_audit/manifests/models.py` and the
  `--run-spec-sources-fpath` help text in `eval_audit/manifests/builders.py` to mention
  `adapter_overrides?`.

### 4. Deviation fact — eval_audit/planning/core_report_planner.py
- Add `_component_decode_params(component)` next to `_component_instructions()`,
  reusing `_read_run_spec(component.run_spec_fpath)` → `adapter_spec`, returning a
  deterministic string of the allowlist keys present (e.g. sorted JSON), or `None`.
- In `build_comparability_facts()` add `"same_decode_params"` to both `facts` and
  `fact_inputs` (`[_component_decode_params(c) for c in components]`). The existing
  `_fact_status` loop + `_comparability_warning_lines` then emit
  `comparability_drift:same_decode_params` / `comparability_unknown:…` automatically.
- Mirror the key into the parallel producer `_comparability_summary()` in
  `eval_audit/reports/core_metric_curves.py` so the NormalizedDiff render path carries
  the fact too. **Verify during implementation** which producer feeds the from-spec
  report and update whichever are live (they are the planner-packet vs NormalizedDiff
  render paths).

## Non-goals (explicitly out of scope this cut)
- No declared/intentional-substitution re-labeling (`intended_substitution:*`) — the
  judge-substitution precedent in `diagnose.py`/`_comparison_payload` is the follow-on
  if desired.
- No in-container `apply_adapter_substitutions` / CLI-flag change in the `aiq-magnet`
  submodule (would force an image rebuild).
- No built-in temperature-sweep command; no override of non-decode adapter fields.

## Verification
- `python -m py_compile` on the four touched modules.
- Unit tests (mirror existing materializer/planner tests under `tests/`):
  - `materialize_run_spec` applies `adapter_overrides`, records `from→to` in
    `substitutions` and the sidecar, and yields a different `content_hash` than the
    un-ablated copy.
  - `RunSpecSource.from_dict` rejects an unknown override key and coerces value types.
  - `_freeze_run_spec_sources` merges `adapter_overrides` (default) with a
    `run_entry_overrides` entry (per-entry wins per-key), attaches only to the named
    entry, and raises when `run_entry_overrides` names an entry absent from `run_entries`.
  - `build_comparability_facts` returns `same_decode_params: {status: "no", values: […]}`
    for two components whose `adapter_spec.temperature` differ, and `"yes"` when equal.
- End-to-end smoke (phi-2 from-spec is the natural target): run the
  `e2e-phi_2-vllm-philosophy` smoke with `adapter_overrides: {temperature: 0.0}` and confirm
  (a) the materialized `run_spec.<hash>.json` has `temperature: 0.0`, (b) the
  `materialization.json` sidecar records `temperature: {from: 1.0, to: 0.0}`, (c) the
  produced run's `run_spec.json` carries it, (d) `core_metric_report.txt` shows
  `comparability_drift:same_decode_params`.

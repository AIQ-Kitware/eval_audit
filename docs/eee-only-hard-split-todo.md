# EEE-only hard split — status (SUPERSEDED)

**Status (2026-08-06): SUPERSEDED.** The named deliverable — a physically
isolated `eval_audit/eee_only/` namespace — was never built, and is no
longer planned. The problems this doc identified were solved a different
way by Phase 3 (see
[`docs/planning/phase3-comparison-core-unification.md`](planning/phase3-comparison-core-unification.md)).
This doc is kept as the historical record of the concern and now points
at what actually landed. **Do not follow the original instructions**
(several referenced flags and symbols no longer exist — most importantly
`EVAL_AUDIT_EEE_STRICT`, which is a silent no-op today).

---

## Why this existed

Case Study 3 of the paper claims:

> *"EEE's per-instance schema is sufficient for reproducibility analysis
> at multiple granularities."*

A reviewer will read that and ask: *did the analysis code actually use
only EEE? Or did it secretly fall back to HELM `run_spec.json` when
nobody was looking?* At the time this doc was written the answer was
"softly, via a shim" — the EEE loader also contained a **silent HELM
fallback**: when a HELM run dir was present next to an EEE artifact,
per-instance data was read from the HELM JSONs instead, so the same
artifact produced different numbers depending on what else was on disk.

## How each concern was actually resolved

| Original concern | What landed instead |
|---|---|
| Silent HELM fallback in `EeeArtifactLoader` (different numbers depending on what's on disk) | The fallback is now a **declared, recorded policy**: `--instance-source {helm-preferred,eee-only}` (`eval_audit/reports/core_metrics.py`), resolved via `instance_source_policy` on `ref.extra` (`eval_audit/normalized/loaders.py`) and recorded per pair in `pairs[].instance_sources`. EEE-only CLIs declare `eee-only`; the HELM-driven renderer declares `helm-preferred`. Nothing is silent. |
| Interim `EVAL_AUDIT_EEE_STRICT=1` env guard ("required for the paper run") | **Retired 2026-07-12** after its one-cycle deprecation window (plan item E5a). Setting it today does nothing. Use `--instance-source eee-only` for the published EEE-only numbers. |
| `eee_only/diagnose.py` re-implementing `_diagnose_repro` from a `recipe_facts` block | Landed as [`eval_audit/normalized/diagnose.py`](../eval_audit/normalized/diagnose.py) (Phase 3 sub-stage 4.2) — a framework-free diagnosis that is now the **single** implementation; `HelmRunDiff._diagnose_repro` delegates to it. |
| Extend the EEE schema with native comparability facts (work plan §2, option a) | Landed on the consumer side as [`eval_audit/normalized/recipe_facts.py`](../eval_audit/normalized/recipe_facts.py): native `recipe_facts` block under `source_metadata.additional_details`, then sidecar `run_spec.json`, then unknown. Upstream issue draft: [`docs/planning/upstream-eee-recipe-facts-issue.md`](planning/upstream-eee-recipe-facts-issue.md). |
| Unified comparison core so the EEE path stops importing HELM-shaped renderers | Both render paths now route through `NormalizedDiff` ([`eval_audit/normalized/diff.py`](../eval_audit/normalized/diff.py)); the retired `HelmRunDiff` batch surface (`summarize_instances` etc.) was deleted in R-2 (2026-07-06). |
| Hard import isolation (`grep eee_only/ for eval_audit.helm` → zero) | **Not built.** The EEE-only CLIs still transitively import `eval_audit.helm.*` for the legacy semantic-diff diagnosis path. Isolation is behavioral (declared instance-source policy + facts-grade diagnosis), not physical. If a reviewer demands the physical guarantee, the isolation tests sketched in the original work plan (assert `sys.modules` contains no `eval_audit.helm.*` after importing the EEE CLIs) are still the right shape. |

## What remains genuinely open

- The paper's methods section should describe the *declared-policy*
  guarantee (`--instance-source eee-only`, recorded in
  `pairs[].instance_sources`), not an import-isolation guarantee.
- The planner still derives comparability facts by calling
  `extract_run_spec_fields` directly; the unified resolver
  (`resolve_recipe_facts`) is exercised by tests and the native-block
  read path, but is not yet the planner's entry point.
- Upstream EEE has not yet adopted the `recipe_facts` schema slot; until
  it does, EEE-only runs without a sidecar get
  `comparability_unknown:*` diagnoses — the correct signal, not a bug.

## Pointers

- Field mapping + sidecar recommendations: [`docs/eee-vs-helm-metadata.md`](eee-vs-helm-metadata.md).
- The legacy bridge (still present, self-labelled):
  [`eval_audit/normalized/helm_compat.py`](../eval_audit/normalized/helm_compat.py).
- Instance-source policy behavior: `docs/pipeline.md` (Stage 3 notes)
  and `eval_audit/normalized/loaders.py` docstrings.

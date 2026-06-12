# DRAFT upstream issue — every_eval_ever: first-class `recipe_facts` block

**Status:** ready to file against `Erotemic/every_eval_ever` (Phase 3 sub-stage 4.7 of
[`phase3-comparison-core-unification.md`](phase3-comparison-core-unification.md)). Not yet filed —
posting is an outward-facing action; copy the body below when filing.

---

## Title

`EvaluationLog`: add a first-class `recipe_facts` slot for framework-neutral recipe metadata

## Body

### Context

`eval_audit` (HELM reproducibility auditing) uses EEE artifacts as its normalized comparison
currency. Comparing two runs requires scalar *recipe facts* — what produced the numbers — beyond
what `EvaluationLog` carries today:

| fact | HELM source (today) |
|---|---|
| `run_spec_name` | `run_spec.json:name` |
| `model` | `adapter_spec.model` |
| `model_deployment` | `adapter_spec.model_deployment` |
| `scenario_class` | `scenario_spec.class_name` |
| `instructions` | `adapter_spec.instructions` |
| `max_eval_instances` | `adapter_spec.max_eval_instances` |
| `benchmark_group` | derived from run name |
| `run_spec_hash` | canonical hash of run_spec.json |
| `judge_models` | annotator config (see note below) |

Without these, comparability facts collapse to `status='unknown'` — correct but uninformative.
Today we work around it by shipping the raw HELM `run_spec.json` as a sidecar next to the EEE
artifact, which keeps a HELM-shaped file in an otherwise framework-neutral pipeline.

### Interim convention we have already adopted (reader side)

`EvaluationLog` is `extra='forbid'`, but `source_metadata.additional_details: dict[str, str]` is
free-form, so our reader accepts a JSON-encoded block there:

```json
{
  "source_metadata": {
    "additional_details": {
      "recipe_facts": "{\"run_spec_name\": \"mmlu:subject=anatomy,model=org/m\", \"model\": \"org/m\", \"model_deployment\": \"huggingface/m\", \"scenario_class\": \"helm.benchmark.scenarios.MMLUScenario\", \"instructions\": \"...\", \"max_eval_instances\": \"100\", \"judge_models\": [\"openai/gpt-4o-2024-05-13\"]}"
    }
  }
}
```

Reader: `eval_audit.normalized.recipe_facts.resolve_recipe_facts` (resolution order: native block →
sidecar `run_spec.json` → unknown).

### Ask

1. A **first-class, typed `recipe_facts` slot** on `EvaluationLog` (or a dedicated sub-model),
   replacing the stringly-typed interim convention. Scalar string fields + `judge_models:
   list[str]`; all optional; producers fill what they know.
2. The **HELM converter populates it** at conversion time from `run_spec.json` (all fields above are
   cheap reads; `judge_models` from the `annotators` list — note that HELM run_specs carry annotator
   *class names* with empty args, the judge model being hard-coded per HELM version, so the
   converter can record the class basename and consumers map it; recording whatever is available is
   already valuable).
3. Other framework converters fill the same slot — that is the point: a framework-neutral recipe
   contract lets any framework's artifacts participate in reproducibility diagnosis without
   framework-specific sidecars.

### Why it matters

- Makes EEE artifacts **self-describing** for reproducibility comparison — no HELM sidecar needed,
  and the same contract works for non-HELM frameworks after HELM's eventual deprecation.
- The open-judge extension compares official closed-judge runs against open-judge re-runs;
  `judge_models` in the artifact is what makes the substitution machine-checkable.

### Compatibility

- Purely additive. Absent slot ⇒ consumers behave exactly as today (facts `unknown`).
- The interim `additional_details["recipe_facts"]` convention can be honored by readers
  indefinitely; the typed slot supersedes it for new conversions.

---

## After filing

- Link the issue URL here and in the design doc §4 (4.7 row).
- When the slot lands upstream: teach `resolve_recipe_facts` the typed slot (priority above the
  interim convention), regenerate fixture F5, and update
  [`docs/eee-vs-helm-metadata.md`](../eee-vs-helm-metadata.md).

# Open-weight judge reproduction plan

**Status:** PLANNED — implementation has not started.  
**Primary target:** reproduce selected HELM LLM-as-a-judge benchmark scores on
`aiq-gpu` while varying the judge among modern open-weight models.  
**Initial candidate source:** published or locally retained HELM outputs for
`openai/gpt-oss-20b`, beginning with XSTest and WildBench.  
**Initial judges:** Qwen3.5-27B and Qwen3.6-35B-A3B, served through infer-stack
and LiteLLM.  
**Last updated:** 2026-07-17.

This document is the implementation plan for turning the existing
`judge_substitution_planned` analysis seam into an executable, auditable
rejudging system. It is deliberately precise enough that an implementation
agent can work through it in small, reviewable commits without having to
rediscover the repository architecture.

The central design decision is:

> Candidate response generation and model-based judgment are separate stages.
> Candidate responses are frozen once, hashed, and then fanned out across
> independently attributable judge configurations and replicates.

This is not a plan to disguise Qwen as the original GPT-4o judge, nor to rerun
the candidate for every judge arm. The goal is to measure the effect of judge
substitution on exactly the same candidate responses.

---

## 1. Scientific question

The first experiment asks:

> For a HELM benchmark whose published score used closed-source model-based
> judging, how does the reported score change when the exact same candidate
> responses are judged by recent open-weight models?

The primary comparison should preserve all candidate-side facts and vary only
the judge. The first useful matrix is:

| Candidate responses | Judge result | Role |
|---|---|---|
| Frozen official/public responses | Original GPT-4o annotation | Existing closed-judge reference |
| Frozen official/public responses | Original Llama-3.1-405B annotation | Existing open-weight reference |
| Frozen official/public responses | Qwen3.5-27B | New judge arm |
| Frozen official/public responses | Qwen3.6-35B-A3B | New judge arm |

The official HELM ensemble already contains an open-weight Llama judge. That
provides a useful baseline for normal judge disagreement, even though that
model cannot necessarily be rerun locally.

A later two-factor experiment may add locally regenerated candidate responses:

| Candidate responses | Judge |
|---|---|
| Official/public responses | Existing official annotations |
| Official/public responses | Local Qwen judges |
| Locally reproduced responses | Local Qwen judges |

This separates:

1. **Judge substitution effect:** same responses, different judge.
2. **Candidate reproduction effect:** official versus locally generated
   responses, evaluated by the same local judge.

A full local rerun against a new judge must not be described as a pure judge
substitution experiment because it changes both factors.

---

## 2. Repository facts that constrain the implementation

### 2.1 The current open-judge work is an analysis-policy seam

The following files already encode part of the intended comparison semantics:

- `eval_audit/indexing/historic_filtering.py`
- `eval_audit/judge_registry.py`
- `eval_audit/indexing/schema.py`
- `eval_audit/metrics_taxonomy.py`
- `docs/planning/judge-identity-inventory.md`
- `docs/planning/phase3-comparison-core-unification.md`

They currently:

1. allow the six known closed-judge benchmark families through selected
   historic filters;
2. annotate those rows with `judge_substitution_planned=True`;
3. let the comparison layer declare `substitutions: ["judge"]`;
4. separate known judge-dependent metric prefixes from deterministic metrics.

They do **not** currently:

- configure a replacement judge;
- rerun a HELM annotator;
- reconstruct frozen candidate responses;
- emit open-judge annotation artifacts;
- support several judge arms or judge replicates for one candidate run.

The existing behavior should remain the analysis-policy layer. The new work is
an execution and artifact layer that feeds it honest judge facts.

### 2.2 The ordinary HELM runner cannot be reused directly

`Runner.run_one()` in
`submodules/helm/src/helm/benchmark/runner.py` executes the complete pipeline:

```text
scenario generation
-> preprocessing
-> adaptation
-> candidate inference
-> annotation
-> metrics
-> artifact writing
```

The faithful replay CLI under:

```text
submodules/aiq-magnet/magnet/backends/helm/cli/
    materialize_helm_run_from_spec.py
```

ultimately calls the normal HELM benchmarking path. It does not expose an
annotation-only mode.

Likewise, `eval_audit/manifests/run_spec_materializer.py` has a deliberately
narrow invariant: it rewrites only candidate deployment and instance limit.
Do not turn that module into a general arbitrary `RunSpec` mutation mechanism.
Rejudging requires a separate runner and manifest type.

### 2.3 HELM writes `scenario_state.json` after annotation

When a local run contains `scenario_state.json`, it is already post-annotation.
It is not a pristine candidate response cache.

A local-state import path must therefore:

- preserve every candidate `RequestState.result` exactly;
- detach and separately archive existing annotations;
- replace the annotator specifications;
- verify that a response-set hash remains unchanged before and after judging;
- never modify the source run directory in place.

### 2.4 Published HELM artifacts are usually compact display artifacts

The public CRFM mirror generally exposes:

- `run_spec.json`
- `instances.json`
- `display_requests.json`
- `display_predictions.json`
- `stats.json`
- `per_instance_stats.json`

but generally not `scenario_state.json`.

The display types in
`submodules/helm/src/helm/benchmark/presentation/run_display.py` preserve the
facts needed for the targeted generation-based judge benchmarks:

`DisplayRequest` preserves:

- `instance_id`;
- perturbation;
- `train_trial_index`;
- the exact HELM `Request`.

`DisplayPrediction` preserves:

- the same identifying key;
- candidate output text;
- candidate thinking text;
- reference index;
- original annotations;
- selected display statistics.

They do not preserve every execution detail needed by arbitrary metrics,
including all generated tokens, log probabilities, request timings, output
mappings, calibration requests, and conditioning-token facts.

Therefore a reconstructed public response state is **annotation-only**. The
new runner may rerun judge-dependent metrics, but it must not claim to have
recomputed every original HELM metric.

### 2.5 Judge identity is per metric, not merely per run

The official judge ensemble is currently:

- `openai/gpt-4o-2024-05-13`;
- `meta/llama-3.1-405b-instruct-turbo`.

The benchmark implementations expose these members differently.

Safety benchmarks already produce separate fields such as:

```text
safety_gpt_score
safety_llama_score
safety_gpt_annotator_success
safety_llama_annotator_success
safety_score
```

WildBench stores separate annotation fields:

```text
gpt_score
llama_score
```

but its metric scans keys ending in `_score` and emits aggregate names:

```text
wildbench_score
wildbench_score_rescaled
```

Omni-MATH stores separate judgments:

```text
gpt_equivalence_judgement
llama_equivalence_judgement
```

but emits only:

```text
omni_math_accuracy
```

The new implementation must preserve actual judge identity in annotation and
metric names. It must not replace one opaque aggregate run-level judge label.

### 2.6 HELM annotators hard-code the official models

The following HELM annotators hard-code their judge ensemble:

- `WildBenchAnnotator`
- `OmniMATHAnnotator`
- `HarmBenchAnnotator`
- `AnthropicRedTeamAnnotator`
- `SimpleSafetyTestsAnnotator`
- `XSTestAnnotator`

The shared safety helper in `model_as_judge.py` even contains a TODO to make the
judges configurable.

HELM also has `LLMAsJuryAnnotator`, but it implements a generic protocol. It is
not benchmark-faithful and must not replace benchmark-specific WildBench,
Omni-MATH, or safety prompts and parsers.

### 2.7 The repository already has a suitable HELM plugin seam

The project already registers HELM extensions through:

- `eval_audit/integrations/helm_plugins.py`
- `eval_audit/integrations/helm_clients.py`
- the HELM entry point in `pyproject.toml`.

The evaluation container installs `eval_audit`, so dotted classes under
`eval_audit.*` are available to HELM. The first implementation should use this
plugin seam instead of patching the vendored HELM submodule.

### 2.8 The built-in Qwen3.6 profile does not itself provide one balanced name

The existing Qwen3.6 serving recipe exposes two TP2 replicas under different
served model names. A single public alias backed by several replicas is only
available when infer-stack uses opt-in dynamic routing with Postgres-backed
LiteLLM route registration.

Dynamic routing is therefore an experiment requirement, not an incidental
serving preference. Acquisition, release, reconciliation, and garbage
collection must all agree on the dynamic-routing setting.

---

## 3. Target architecture

```text
Public or local HELM source run
    |
    |-- run_spec.json
    |-- instances.json
    |-- display_requests.json
    |-- display_predictions.json
    |-- stats.json
    |-- per_instance_stats.json
    |-- optional scenario_state.json
    |
    v
Immutable response snapshot
    |
    |-- exact candidate requests
    |-- exact candidate outputs
    |-- detached original annotations
    |-- source artifact hashes
    |-- response-set hash
    |
    |------ Qwen3.5-27B / replicate 0
    |------ Qwen3.5-27B / replicate 1
    |------ Qwen3.5-27B / replicate 2
    |------ Qwen3.6-35B / replicate 0
    |------ Qwen3.6-35B / replicate 1
    `------ Qwen3.6-35B / replicate 2
             |
             v
Judge-only artifacts
             |
             |-- raw judge outputs
             |-- parsed judgments
             |-- request failures
             |-- parser failures
             |-- judge-attributed metrics
             `-- exact judge provenance
             |
             v
Judge comparison report
```

The candidate response set is content-addressed once. Every judge attempt must
refer to the same `response_set_hash`.

---

## 4. New package and CLI layout

Add the domain model and artifact logic under:

```text
eval_audit/judging/
    __init__.py
    specs.py
    source_audit.py
    response_snapshot.py
    metric_replay.py
    rejudge.py
    indexing.py
    analysis.py
```

Add HELM-specific judging integrations under:

```text
eval_audit/integrations/helm_judging/
    __init__.py
    common.py
    safety.py
    wildbench.py
    omni_math.py
    metrics.py
```

Add thin CLI modules:

```text
eval_audit/cli/audit_judge_sources.py
eval_audit/cli/build_response_snapshot.py
eval_audit/cli/rejudge_helm_run.py
eval_audit/cli/analyze_judge_variance.py
```

Add the execution pipeline and bridge:

```text
eval_audit/pipelines/helm_rejudge_pipeline.py
eval_audit/integrations/open_judge_kwdagger_bridge.py
```

Expose scripts in `pyproject.toml`:

```toml
eval-audit-audit-judge-sources = "eval_audit.cli.audit_judge_sources:main"
eval-audit-build-response-snapshot = "eval_audit.cli.build_response_snapshot:main"
eval-audit-rejudge-helm = "eval_audit.cli.rejudge_helm_run:main"
eval-audit-analyze-judges = "eval_audit.cli.analyze_judge_variance:main"
eval-audit-run-open-judge = "eval_audit.integrations.open_judge_kwdagger_bridge:main"
```

Do not add all files in one commit. Follow the staged implementation sequence
below.

---

## 5. Phase 0 — establish a trustworthy development baseline

The source archive alone is not an installed environment. Missing imports such
as `ubelt` or `every_eval_ever` during collection are environment failures, not
code-test failures.

On the development machine:

```bash
cd ~/code/helm_audit

git submodule update --init --recursive

uv venv --python 3.13 .venv
source .venv/bin/activate

uv pip install -e .
uv pip install -e submodules/aiq-magnet
uv pip install -e submodules/infer_stack
```

Record repository revisions before implementation:

```bash
git rev-parse HEAD
git -C submodules/helm rev-parse HEAD
git -C submodules/aiq-magnet rev-parse HEAD
git -C submodules/infer_stack rev-parse HEAD
```

Run the tests that cover the current judge policy:

```bash
python -m pytest -q \
    tests/test_closed_judge_relax.py \
    tests/test_judge_registry.py \
    tests/test_phase3_judge_substitution.py \
    tests/test_metrics_taxonomy.py \
    tests/test_run_spec_materializer.py
```

**Stop gate:** all selected tests collect and pass before implementation begins.

---

## 6. Phase 1 — audit actual source artifacts

Create:

```text
eval_audit/judging/source_audit.py
eval_audit/cli/audit_judge_sources.py
tests/test_judge_source_audit.py
```

The CLI should accept at least:

```text
--public-root
--model
--benchmarks
--output
```

Begin with the excluded `openai/gpt-oss-20b` runs listed in
`reproduce/gpt_oss_20b_from_spec/README.md` and inspect:

- `wildbench`;
- `xstest`;
- `simple_safety_tests`;
- `harm_bench`;
- `anthropic_red_team`;
- `omni_math`.

Leave `air_bench_2024` out of version 1. It uses a different GPT-only judging
path and is not covered by the current six-benchmark registry.

### 6.1 Stable display key

Use one normalized key for joining display artifacts:

```python
(
    instance_id,
    serialized_perturbation,
    train_trial_index,
)
```

Centralize serialization of the perturbation. Do not duplicate ad hoc key
construction in the snapshot builder and analysis code.

### 6.2 Audit record

For every candidate source run, emit a record containing at least:

```json
{
  "run_path": "...",
  "run_spec_name": "...",
  "benchmark": "wildbench",
  "adapter_method": "...",
  "annotator_classes": ["..."],
  "metric_classes": ["..."],
  "files": {
    "run_spec": true,
    "instances": true,
    "display_requests": true,
    "display_predictions": true,
    "stats": true,
    "per_instance_stats": true,
    "scenario_state": false
  },
  "num_instances": 0,
  "num_requests": 0,
  "num_predictions": 0,
  "num_original_annotations": 0,
  "duplicate_request_keys": [],
  "duplicate_prediction_keys": [],
  "missing_request_keys": [],
  "missing_prediction_keys": [],
  "annotation_outer_keys": [],
  "annotation_inner_keys": [],
  "metric_names": [],
  "supported_for_rejudging": true,
  "unsupported_reasons": []
}
```

### 6.3 Required validations

The audit must verify:

1. Every displayed request refers to an instance in `instances.json`.
2. Request and prediction key sets are identical.
3. There is exactly one request and one prediction per display key.
4. A prediction represents at most one candidate completion in the supported
   reconstruction path.
5. The adapter shape is supported by the response reconstruction code.
6. Original annotations contain the expected official judge fields.
7. The source judge metric exists in both `stats.json` and
   `per_instance_stats.json`.
8. The selected judge can be reconstructed without token-level candidate
   information absent from display artifacts.

Do not mark a run supported merely because its benchmark name appears in a
registry. Inspect the actual artifact shape.

Example:

```bash
eval-audit-audit-judge-sources \
    /data/crfm-helm-public \
    --model openai/gpt-oss-20b \
    --benchmarks xstest wildbench omni_math \
    --output /data/crfm-helm-audit-store/open-judge/source-audit.json
```

**Tests:** cover duplicate keys, missing counterpart keys, missing instances,
unexpected annotation shapes, and unsupported multiple-completion artifacts.

**Stop gate:** at least one XSTest source and one WildBench source pass with no
missing or duplicate display keys.

---

## 7. Phase 2 — build immutable response snapshots

Create:

```text
eval_audit/judging/response_snapshot.py
eval_audit/cli/build_response_snapshot.py
tests/test_response_snapshot.py
tests/test_published_response_reconstruction.py
```

### 7.1 Snapshot layout

Use a content-addressed directory:

```text
response-snapshots/<response_set_hash>/
    response_manifest.json
    source_run_spec.json
    instances.json
    display_requests.json
    display_predictions.json
    response_scenario_state.json
    official_annotations.jsonl
    DONE
```

Copy normalized source content into the snapshot. The snapshot must not depend
on a mutable public-corpus path continuing to exist.

Write `DONE` only after every file has been atomically written and validated.
A partially constructed directory must not be considered a cache hit.

### 7.2 Response-set hash

Hash an ordered normalized sequence containing only judging-relevant facts:

- stable display key;
- complete serialized `Instance`;
- exact candidate `Request`;
- candidate output text;
- candidate thinking text;
- reference index.

Do not include:

- source filesystem path;
- original annotations;
- aggregate source statistics;
- synthetic reconstruction defaults;
- creation timestamps.

This ensures that identical response sets copied to different directories have
the same identity.

Sort by the normalized display key before hashing. Use the repository's
canonical JSON serialization conventions rather than relying on Python object
`repr` output.

### 7.3 Reconstruct a judging-only `ScenarioState`

Deserialize HELM dataclasses with HELM's codec:

```python
from helm.common.codec import from_json
```

Do not manually reinterpret serialized `Request`, `Instance`, or perturbation
objects when a HELM codec exists.

For every display key, construct one `RequestState` with:

- the complete `Instance` from `instances.json`;
- the exact `Request` from `display_requests.json`;
- `reference_index` from `display_predictions.json`;
- one successful candidate completion from `predicted_text`;
- optional candidate reasoning from `thinking_text`;
- `annotations=None`.

Construct the synthetic request result as:

```python
RequestResult(
    success=True,
    embedding=[],
    completions=[
        GeneratedOutput(
            text=prediction.predicted_text,
            logprob=0.0,
            tokens=[],
            thinking=(
                Thinking(text=prediction.thinking_text)
                if prediction.thinking_text is not None
                else None
            ),
        )
    ],
    cached=True,
    request_time=None,
    request_datetime=None,
)
```

Use these reconstruction defaults only for benchmark families proven not to
consume them during annotation:

```python
request_mode = None
output_mapping = None
num_train_instances = 0
prompt_truncated = False
num_conditioning_tokens = 0
```

The source audit must reject a run if its annotator requires omitted fields.

### 7.4 Detach official annotations

Write one record per display key to `official_annotations.jsonl`:

```json
{
  "key": {
    "instance_id": "...",
    "perturbation": null,
    "train_trial_index": 0
  },
  "annotations": {}
}
```

`response_scenario_state.json` must be judge-neutral. Do not leave official
annotations attached and then mutate them in place.

### 7.5 Manifest contract

At minimum:

```json
{
  "artifact_type": "helm_response_snapshot",
  "schema_version": 1,
  "reconstruction_scope": "annotation_only",
  "candidate_inference_reused": true,
  "response_set_hash": "...",
  "source_run": "...",
  "source_artifact_hashes": {},
  "num_request_states": 0,
  "supported_benchmark": "wildbench"
}
```

The manifest must explicitly state that the state is sufficient for annotation
replay, not a complete token-level reconstruction of the original HELM run.

### 7.6 Tests

Prove:

- two builds from the same source produce the same response-set hash;
- normalized request-state content is byte-equivalent;
- relocating the source corpus does not change the hash;
- modifying one candidate character changes the hash;
- modifying only original annotations does not change the hash;
- `DONE` is absent after an injected mid-write failure.

**Stop gate:** response snapshots are stable, immutable, and independently
verifiable.

---

## 8. Phase 3 — replay official annotations before contacting a new judge

Create:

```text
eval_audit/judging/metric_replay.py
tests/test_official_annotation_identity_replay.py
```

This is the primary correctness gate.

For each supported benchmark:

1. Load the judge-neutral reconstructed state.
2. Reattach the original annotations by stable display key.
3. Instantiate only the original judge-dependent metric.
4. Evaluate it.
5. Compare aggregate results with source `stats.json`.
6. Compare per-instance results with source `per_instance_stats.json`.

Do not evaluate every metric in the original `RunSpec`; the reconstructed state
lacks token-level information needed by several non-judge metrics.

Expected judge metrics:

```text
WildBench:
    wildbench_score
    wildbench_score_rescaled

Omni-MATH:
    omni_math_accuracy

Safety:
    safety_score
    safety_gpt_score
    safety_llama_score
    safety_gpt_annotator_success
    safety_llama_annotator_success
```

Handle perturbation metadata and per-instance ordering exactly as HELM writes
them. Compare values with a tight tolerance, such as `1e-12`, and report all
missing or extra rows.

Emit a replay report:

```json
{
  "aggregate_match": true,
  "per_instance_match": true,
  "max_absolute_error": 0.0,
  "num_missing_source_rows": 0,
  "num_extra_replayed_rows": 0
}
```

**Stop gate:** do not send any request to a Qwen judge until the original
annotations reproduce the published judge metrics exactly.

---

## 9. Phase 4 — define explicit judge specifications

Create `eval_audit/judging/specs.py`.

Use separate immutable concepts for model configuration and an execution
attempt:

```python
@dataclass(frozen=True)
class JudgeSpec:
    id: str
    model: str
    model_deployment: str
    lease_endpoint: str
    temperature: float
    max_tokens: int
    parser_version: str
    prompt_version: str
    thinking_mode: str
    client_class: str
    model_revision: str | None = None
    quantization: str | None = None


@dataclass(frozen=True)
class JudgmentAttemptSpec:
    response_set_hash: str
    benchmark: str
    judge: JudgeSpec
    replicate: int
    request_random: str
```

The judge-spec hash must include every inference-affecting field but not:

- local output directory;
- timestamp;
- hostname;
- replicate number.

The attempt hash additionally includes:

- response-set hash;
- benchmark;
- replicate;
- `request_random`.

Reject an incomplete judge spec. At minimum require model identity, deployment
identity, parser version, prompt version, client class, and an explicit thinking
mode.

### 9.1 Keep model identity visible in `AnnotatorSpec`

The existing `extract_judge_models()` recognizes top-level string arguments
whose key contains `model`. Generate annotator specs with explicit flat fields:

```json
{
  "class_name": "eval_audit.integrations.helm_judging.wildbench.ConfigurableWildBenchAnnotator",
  "args": {
    "judge_id": "qwen3_5_27b",
    "judge_model": "qwen/qwen3.5-27b",
    "judge_model_deployment": "litellm/qwen3.5-27b-judge",
    "temperature": 0.0,
    "max_tokens": 2000,
    "request_random": "experiment:arm:r0",
    "thinking_mode": "disabled"
  }
}
```

Do not initially hide the model in a nested opaque dictionary. It should be
recoverable from the normal HELM artifact without requiring a sidecar.

Upgrade `extract_judge_models()` to support future recursive
`judge_models` structures, while preserving the flat v1 artifact contract and
existing official-class fallbacks.

---

## 10. Phase 5 — implement benchmark-faithful configurable annotators

Create:

```text
eval_audit/integrations/helm_judging/common.py
eval_audit/integrations/helm_judging/safety.py
eval_audit/integrations/helm_judging/wildbench.py
eval_audit/integrations/helm_judging/omni_math.py
```

### 10.1 Shared judge request execution

`common.py` should provide a small helper that:

1. constructs the judge `Request`;
2. calls `AutoClient.make_request()`;
3. records request success and cache status;
4. captures final content and reasoning content separately;
5. captures timing and finish reason;
6. converts request and parse failures into structured results;
7. never aborts the entire annotation batch for one malformed judge response.

Use:

```python
random=request_random
```

on every judge request. This produces distinct HELM cache identities for
replicates without changing benchmark prompt bytes.

Never append cache-busting text to the benchmark prompt.

### 10.2 Common annotation provenance

Every configurable judge annotation must preserve:

```text
judge_id
judge_model
judge_model_deployment
judge_spec_hash
prompt_text
prompt_hash
raw_response
raw_thinking
parse_status
parse_error
request_success
request_cached
request_time
finish_reason
```

Use a controlled `parse_status` vocabulary:

```text
ok
empty_candidate_output
request_error
empty_judge_output
malformed
out_of_range
```

Do not rely on logs as the only record of malformed judge output. Disagreement
and parser failure analysis requires the raw response.

### 10.3 WildBench implementation

Reuse the exact official template:

```text
helm.benchmark.annotation.wildbench/eval_template.score.v2.md
```

Preserve all official substitutions:

- conversation history;
- current user query;
- candidate output;
- checklist.

Preserve official empty-output behavior:

```text
empty candidate output -> score 1.0
```

Parse the official sections:

- strengths;
- weaknesses;
- score.

Validate the score range explicitly. Do not silently accept an arbitrary float.
Return one actual-judge field, such as:

```text
qwen3_5_27b_score
```

Do not instantiate an internal two-model ensemble.

### 10.4 Omni-MATH implementation

Reuse the exact official selected prompt template and preserve:

- problem text;
- reference answer;
- student solution.

Parse:

- student final answer;
- justification;
- equivalence judgment.

Preserve official empty-output behavior:

```text
empty candidate output -> equivalence false
```

Use an explicit judge-specific equivalence key rather than `gpt_*` or
`llama_*` aliases.

### 10.5 Safety implementation

Do not substitute `LLMAsJuryAnnotator`.

Factor a configurable single-judge replacement for
`score_with_reasoning_with_gpt_and_llama()` while preserving each benchmark's
existing prompt construction and result interpretation.

Implement in this order:

1. XSTest;
2. SimpleSafetyTests;
3. HarmBench;
4. AnthropicRedTeam.

XSTest is the smallest end-to-end live-serving smoke and should land first.

### 10.6 Prompt-parity tests

For every configurable annotator:

1. instantiate the official annotator with a fake `AutoClient`;
2. instantiate the configurable annotator with a fake judge matching one
   official judge;
3. feed both the same synthetic `RequestState`;
4. capture the outgoing prompt;
5. assert prompt bytes are identical;
6. assert relevant temperature and token budgets are identical;
7. assert the custom parser accepts a known official-format response.

Add at least:

```text
tests/test_configurable_xstest_annotator.py
tests/test_configurable_wildbench_annotator.py
tests/test_configurable_omni_math_annotator.py
tests/test_judge_parse_failures.py
```

**Stop gate:** prompts and parsing behavior match the official benchmark logic
before a model is substituted.

---

## 11. Phase 6 — add judge-attributed metrics

Create:

```text
eval_audit/integrations/helm_judging/metrics.py
tests/test_judge_attributed_metrics.py
```

Do not use the original WildBench and Omni-MATH metric implementations for new
judge arms. They scan matching suffixes and may silently average several judge
fields.

Provide explicit metrics such as:

```python
SingleJudgeWildBenchMetric(judge_id="qwen3_5_27b")
SingleJudgeOmniMathMetric(judge_id="qwen3_5_27b")
SingleJudgeSafetyMetric(judge_id="qwen3_5_27b")
```

Emit judge-attributed names:

```text
wildbench_score:judge=qwen3_5_27b
wildbench_score_rescaled:judge=qwen3_5_27b

omni_math_accuracy:judge=qwen3_5_27b

safety_score:judge=qwen3_5_27b
safety_annotator_success:judge=qwen3_5_27b
```

This retains compatibility with prefix-based taxonomy while making the actual
judge visible.

Only emit an ensemble result when an experiment explicitly declares an
ensemble, for example:

```text
wildbench_score:ensemble=qwen35_qwen36
```

Do not emit canonical `wildbench_score` or `omni_math_accuracy` for one
replacement judge. Those names imply the original benchmark aggregation.

The metric must read one explicit judge field. It must not scan every key
ending in `_score`.

Update metric taxonomy tests so that the new score names are judge-dependent.
Classify `*_annotator_success:*` as diagnostic/bookkeeping rather than model
quality.

**Stop gate:** adding an unrelated annotation field ending in `_score` does not
change a single-judge metric.

---

## 12. Phase 7 — implement the annotation-only runner

Create:

```text
eval_audit/judging/rejudge.py
eval_audit/cli/rejudge_helm_run.py
tests/test_rejudge_runner.py
tests/test_rejudge_cache.py
```

This must be a standalone runner. Do not subclass the normal HELM `Runner`, and
do not call `run_benchmarking()`.

### 12.1 Execution algorithm

The runner should:

1. Load and validate the response snapshot.
2. Recompute and verify `response_set_hash`.
3. Prepare HELM's local configuration directory.
4. Register built-in HELM configs.
5. Register local model, deployment, and tokenizer sidecars.
6. Load HELM entry-point plugins.
7. Deserialize `response_scenario_state.json`.
8. Assert all request states already have one successful candidate result.
9. Assert all annotations are absent.
10. Construct the benchmark-specific configurable `AnnotatorSpec`.
11. Replace `scenario_state.annotator_specs`.
12. Run `AnnotationExecutor.execute()` only.
13. Evaluate only the judge-attributed metric.
14. Recompute the candidate response-set hash and prove it is unchanged.
15. Write the judge artifact atomically.
16. Write `DONE` last.

It is acceptable to construct a HELM executor or local context solely to obtain
services required by metrics. Do not call candidate `Executor.execute()`.

### 12.2 Artifact layout

Use a distinct artifact format rather than pretending this is a complete
ordinary HELM run:

```text
open-judge-results/<attempt_hash>/
    run_spec.json
    scenario_state.json
    stats.json
    per_instance_stats.json
    judgments.jsonl
    response_manifest.json
    judge_manifest.json
    rejudge_manifest.json
    process_context.json
    cmd_stdout.txt
    cmd_stderr.txt
    DONE
```

The rejudge manifest must include:

```json
{
  "artifact_format": "helm_rejudge_v1",
  "execution_kind": "rejudge",
  "candidate_inference_reused": true,
  "response_set_hash": "...",
  "judge_spec_hash": "...",
  "attempt_hash": "...",
  "replicate": 0
}
```

`run_spec.json` is a derived rejudge spec containing:

- original scenario and candidate adapter facts;
- the configurable judge annotator;
- only the judge-attributed metric;
- source run and response-snapshot references.

Do not fabricate a source `scenario.json` if the public corpus did not contain
one.

### 12.3 Cache and replicate isolation

Use a dedicated SQLite cache per response set, judge spec, and replicate:

```text
cache/<response_set_hash>/<judge_spec_hash>/replicate-0/
cache/<response_set_hash>/<judge_spec_hash>/replicate-1/
```

Each replicate also receives a distinct `Request.random` value.

This guarantees:

- restarting one replicate reuses its completed judge requests;
- different replicates cannot reuse one another's responses;
- benchmark prompt bytes remain unchanged.

### 12.4 Required tests

- Monkeypatch the candidate execution path to raise; rejudging still completes.
- Assert every `AutoClient` request targets the declared judge deployment.
- Assert no request targets the candidate deployment.
- Inject a mid-run failure, restart, and verify completed judge requests are
  cached.
- Verify a different replicate does not hit the first replicate's cache.
- Verify response-set hash equality before and after annotation.
- Verify one malformed response yields a structured failure record and does
  not abort other instances.

**Stop gate:** a fixture can be rejudged successfully with the candidate server
completely unavailable.

---

## 13. Phase 8 — make Qwen thinking behavior explicit

The existing `NullSafeOpenAIChatClient` already handles `content=null`, and
HELM's OpenAI client can capture vLLM `reasoning_content` into
`GeneratedOutput.thinking`.

However, generic HELM `Request` objects do not expose Qwen-specific chat-template
arguments. Add a specialized client only if live smoke tests prove that an
explicit thinking switch is needed:

```text
eval_audit/integrations/helm_clients.py
tests/test_qwen_judge_client.py
```

Possible shape:

```python
class QwenJudgeOpenAIChatClient(NullSafeOpenAIChatClient):
    def __init__(self, *args, enable_thinking=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.enable_thinking = enable_thinking

    def _make_chat_raw_request(self, request):
        raw = super()._make_chat_raw_request(request)
        if self.enable_thinking is not None:
            raw["extra_body"] = {
                "chat_template_kwargs": {
                    "enable_thinking": self.enable_thinking,
                }
            }
        return raw
```

The exact request key must be verified against the deployed vLLM and Qwen
versions. Do not assume unsupported extra-body arguments are applied.

Record one explicit mode in every judge manifest:

```text
disabled
enabled
server_default
```

Both Qwen arms should use the same thinking policy for the primary comparison
when technically possible. Otherwise the experiment varies model plus inference
mode.

### 13.1 Structured-output smoke

Before any full run, submit at least one known prompt for each benchmark and
verify:

- final `content` is nonempty;
- reasoning is captured separately when present;
- final content matches the benchmark parser format;
- `finish_reason` is not `length`;
- parser status is `ok`;
- raw content and raw reasoning are preserved.

**Stop gate:** no full benchmark run until each judge passes the parser smoke.

---

## 14. Phase 9 — add judge-specific infer-stack serving bundles

Do not reuse the interactive Qwen3.6 262k-context profile unchanged. It
allocates a much larger context than judge prompts probably require and exposes
two distinct endpoint names.

Create dedicated configuration under:

```text
reproduce/open_judge_gpt_oss/
    config/infer_stack/catalog.yaml
    config/infer_stack/settings.yaml
```

Use one public alias per judge:

```text
qwen3.5-27b-judge
qwen3.6-35b-a3b-judge
```

### 14.1 Qwen3.5-27B topology

The repository's model suggestions treat this as a one-GPU model on the 96 GiB
GPUs. Request four dedicated replicas:

```text
GPU 0 -> replica 0
GPU 1 -> replica 1
GPU 2 -> replica 2
GPU 3 -> replica 3
```

All replicas register the same public LiteLLM model alias:

```text
qwen3.5-27b-judge
```

### 14.2 Qwen3.6-35B-A3B topology

Use two tensor-parallel replicas:

```text
GPUs 0,1 -> replica 0
GPUs 2,3 -> replica 1
```

Both register:

```text
qwen3.6-35b-a3b-judge
```

### 14.3 Choose judge-sized context windows

Add an offline prompt-length preflight that renders every selected judge prompt
and records:

- maximum characters;
- tokenizer-estimated maximum tokens;
- p50, p95, and p99 prompt tokens;
- requested judge output budget.

Set:

```text
max_model_len >= max_prompt_tokens + max_tokens + safety_margin
```

A 32k context may be sufficient for WildBench and safety tasks, but the
preflight must establish that from actual source responses. Do not retain a
262k context merely because an interactive profile uses it.

### 14.4 Add replica-count acquisition

Add an infer-stack command shape such as:

```bash
infer-stack acquire qwen3.5-27b-judge \
    --dedicated \
    --replicas 4
```

and:

```bash
infer-stack acquire qwen3.6-35b-a3b-judge \
    --dedicated \
    --replicas 2
```

Implement `--replicas` in:

```text
submodules/infer_stack/infer_stack/cli/commands_leasing.py
```

Requirements:

- default is `1`;
- values greater than one require `--dedicated`;
- version 1 accepts one endpoint name only;
- one lease records all N dedicated deployment requests;
- one env file describes the aggregate lease;
- release tears down all replicas in the lease;
- dynamic routing is mandatory;
- static routing rejects the request instead of silently collapsing replicas.

Add tests under:

```text
submodules/infer_stack/tests/test_cli_leasing.py
submodules/infer_stack/tests/test_leasing_compose.py
```

Tests must prove:

1. four identical dedicated requests create four deployment IDs;
2. each vLLM service has a unique service name;
3. every route uses the same public LiteLLM alias;
4. one release removes all four deployments;
5. static routing rejects this topology.

Persist dynamic routing:

```bash
infer-stack config set dynamic_routing true
```

Do not depend on a one-off flag used only by acquisition. Apply, release, GC,
and reconciliation must resolve the same setting.

### 14.5 Live route acceptance test

After acquisition:

1. verify `/v1/models` contains exactly one public judge alias;
2. send at least 32 concurrent requests;
3. inspect LiteLLM or vLLM telemetry and prove more than one backing replica
   served traffic;
4. release the lease;
5. verify the alias has no live routes;
6. verify route changes did not restart the gateway container.

**Stop gate:** do not describe the setup as load-balanced until this test proves
requests reached several replicas.

---

## 15. Phase 10 — export HELM judge deployment sidecars

Extend the serving exporter under either:

```text
eval_audit/integrations/infer_stack/bundle_export.py
```

or a focused neighboring module:

```text
eval_audit/integrations/infer_stack/judge_bundle_export.py
```

Emit:

```text
judge_model_deployments.yaml
judge_model_metadata.yaml
judge_tokenizer_configs.yaml
judge_bundle_manifest.json
```

Register explicit models:

```text
qwen/qwen3.5-27b
qwen/qwen3.6-35b-a3b
```

and explicit deployments:

```text
litellm/qwen3.5-27b-judge
litellm/qwen3.6-35b-a3b-judge
```

Use the specialized Qwen client only when explicit thinking control is enabled;
otherwise use the existing null-safe OpenAI-compatible client.

The bundle manifest must record:

- infer-stack endpoint;
- LiteLLM base URL;
- public served alias;
- HELM model name;
- HELM deployment name;
- client class;
- model metadata source;
- tokenizer source;
- judge-spec hash;
- catalog hash;
- infer-stack revision.

Never map a Qwen judge onto an official GPT-4o or Llama deployment name.

---

## 16. Phase 11 — add a dedicated kwdagger rejudge pipeline

Create:

```text
eval_audit/pipelines/helm_rejudge_pipeline.py
tests/test_helm_rejudge_pipeline.py
```

Do not inherit from `MaterializeHelmRunFromSpecDockerNode`. Its identity,
command, and lease resolution describe candidate inference runs.

Reuse only generic infrastructure patterns:

- Docker command rendering;
- container provenance capture;
- `LeaseBracketMixin` lifecycle concepts;
- sentinel handling;
- read-only input mounts.

### 16.1 Node parameters

Algorithm parameters:

```text
response_snapshot
response_set_hash
judge_spec
judge_spec_hash
replicate
benchmark
model_deployments_fpath
model_metadata_fpath
tokenizer_configs_fpath
container_image
```

Performance parameters:

```text
parallelism
sqlite_cache_dir
container_network
container_mounts
```

Output parameters:

```text
out_dpath
done_fname
manifest_fname
```

The rejudge container should run with:

```text
--network host
--gpus none
```

The model lives in infer-stack on the host. The annotation client container
must not reserve a GPU.

Mount:

- response snapshot read-only;
- judge sidecars read-only;
- output directory read-write;
- judge cache directory read-write.

### 16.2 Serving lifetime and scheduling

Do not acquire and unload a large model for every source run. Group all work by
judge arm:

```text
acquire Qwen3.5 replica set
    run all source x replicate jobs for Qwen3.5
release Qwen3.5 replica set

acquire Qwen3.6 replica set
    run all source x replicate jobs for Qwen3.6
release Qwen3.6 replica set
```

The workflow creates the Cartesian product:

```text
response sources x judge arms x replicates
```

but schedules jobs grouped by `judge_spec_hash`.

**Stop gate:** a two-job fixture shares one serving session while writing
independent output and cache artifacts.

---

## 17. Phase 12 — index rejudge artifacts without confusing them with reproduction

Add explicit indexed fields for `helm_rejudge_v1` artifacts:

```text
execution_kind
response_source_kind
response_source_path
response_set_hash
candidate_inference_reused
judge_arm_id
judge_model
judge_model_deployment
judge_spec_hash
judge_replicate
judge_prompt_version
judge_parser_version
judge_thinking_mode
judge_substitution_planned
```

A rejudge artifact is not a normal locally reproduced candidate run. The
candidate identity remains the source run's logical identity; judge identity
is an orthogonal dimension.

The existing comparison planner generally assumes one local counterpart for a
logical run key. Feeding every judge replicate into one ordinary virtual
experiment may create duplicate-candidate ambiguity.

For version 1, use one of these constrained approaches:

1. aggregate replicates and create one virtual experiment per judge arm; or
2. use a dedicated judge-analysis table keyed by `response_set_hash`.

Prefer the dedicated analysis path first. Do not generalize the ordinary
candidate-reproduction planner until this workflow is proven.

Update `extract_judge_models()` to recognize the explicit flat judge arguments
while retaining current official-class fallback behavior.

---

## 18. Phase 13 — implement judge comparison analysis

Create:

```text
eval_audit/judging/analysis.py
eval_audit/cli/analyze_judge_variance.py
tests/test_judge_analysis.py
```

Join records by:

```text
response_set_hash
stable display key
```

Never join by row position alone.

### 18.1 WildBench reporting

Compare each open judge with:

- official GPT-4o sub-score;
- official Llama-405B sub-score;
- official GPT/Llama ensemble mean;
- the other open judge.

Aggregate statistics:

- mean score;
- mean signed difference;
- mean absolute difference;
- median difference;
- bootstrap 95% confidence interval;
- parser success rate;
- empty-output rate;
- request-failure rate.

Instance-level statistics:

- Pearson correlation;
- Spearman correlation;
- exact-score agreement;
- agreement within one WildBench point;
- largest positive disagreements;
- largest negative disagreements.

Replicate statistics:

- within-judge standard deviation;
- percentage of instances whose score changes across replicates;
- maximum replicate range;
- variance attributable to judge model versus replicate.

Always report official GPT-4o versus official Llama-405B disagreement. This is
the canonical-ensemble baseline against which Qwen disagreement should be
interpreted.

### 18.2 Safety reporting

Report:

- label agreement;
- confusion matrix;
- flip rate;
- false-safe and false-unsafe rates relative to each official judge;
- Cohen's kappa;
- parser and request failure rates.

### 18.3 Omni-MATH reporting

Report:

- equivalence-label agreement;
- disagreement categories;
- parse failure rate;
- examples where final-answer extraction differs;
- raw justification comparisons for selected disagreements.

### 18.4 Ranking stability

When several candidate systems exist for the same benchmark and response
population, add:

- model-ranking Spearman correlation;
- Kendall tau;
- top-k stability;
- pairwise ordering flips.

Do not claim ranking stability from a first experiment with only one candidate
system.

---

## 19. Experiment manifest

Use a dedicated open-judge manifest rather than the existing candidate
`ManifestSpec`:

```yaml
schema_version: 1
experiment_name: gpt-oss-20b-open-judge-v1

source_root: /data/crfm-helm-public
snapshot_root: /data/crfm-helm-audit-store/open-judge/response-snapshots
output_root: /data/crfm-helm-audit-store/open-judge/results

sources:
  - id: gptoss20b-xstest
    rel_path: <resolved exact public run path>
    source_kind: public_display

  - id: gptoss20b-wildbench
    rel_path: <resolved exact public run path>
    source_kind: public_display

judge_arms:
  - id: qwen3_5_27b
    model: qwen/qwen3.5-27b
    model_deployment: litellm/qwen3.5-27b-judge
    lease_endpoint: qwen3.5-27b-judge
    replicas: 4
    tensor_parallel_size: 1
    temperature: 0.0
    thinking_mode: disabled
    replicates: 3

  - id: qwen3_6_35b_a3b
    model: qwen/qwen3.6-35b-a3b
    model_deployment: litellm/qwen3.6-35b-a3b-judge
    lease_endpoint: qwen3.6-35b-a3b-judge
    replicas: 2
    tensor_parallel_size: 2
    temperature: 0.0
    thinking_mode: disabled
    replicates: 3

execution:
  container_image: eval-audit-helm-runner:dev
  network: host
  annotation_parallelism: 16
  sqlite_cache: true
  dynamic_routing: true
```

`thinking_mode: disabled` remains valid only after a live client smoke confirms
that the deployed server honors that request. Otherwise record
`server_default`; never silently fall back.

---

## 20. Experimental progression

### Milestone A — fixture-only validation

No GPU is required.

Complete:

- source audit fixtures;
- response reconstruction;
- official annotation identity replay;
- prompt-parity tests;
- fake judge execution;
- cache restart test;
- no-candidate-call test.

### Milestone B — XSTest 20-instance smoke

Use:

- Qwen3.5 only;
- one replica;
- one replicate;
- 20 instances.

Validate actual serving, output shape, parser behavior, raw response retention,
metrics, and artifact writing.

### Milestone C — WildBench 20-instance smoke

Use:

- Qwen3.5 only;
- one replica;
- one replicate;
- 20 instances.

Manually inspect every parser failure and a representative set of successful
raw responses.

### Milestone D — 100-instance replicated pilot

Run:

```text
100 candidate responses
x 2 judge models
x 3 replicates
```

Evaluate:

- parser success;
- request success;
- replicate stability;
- throughput;
- output-token exhaustion;
- judge disagreement.

Do not proceed automatically when:

- parser failures exceed approximately 1%;
- `finish_reason=length` occurs repeatedly;
- final content is frequently empty while reasoning is nonempty;
- replicate variation is large enough to obscure model differences.

These are investigation triggers, not observations to silently filter out.

### Milestone E — full WildBench source run

Run all selected responses with:

- Qwen3.5-27B, three replicates;
- Qwen3.6-35B-A3B, three replicates.

Build the primary judge-substitution report.

### Milestone F — remaining safety benchmarks

Apply the common safety path to:

- SimpleSafetyTests;
- HarmBench;
- AnthropicRedTeam.

### Milestone G — Omni-MATH

Implement and run this after structured output and reasoning behavior are
stable. Its long judge outputs and multi-section parser make it a poor first
infrastructure smoke test.

---

## 21. Reproduction runbook layout

Create:

```text
reproduce/open_judge_gpt_oss/
    README.md
    _lib.sh
    experiment.yaml

    00_check_env.sh
    03_check_dynamic_routing.sh
    05_audit_source_artifacts.sh
    06_check_hf_auth.sh
    07_check_container_image.sh
    08_build_response_snapshots.sh
    09_verify_official_identity_replay.sh

    10_smoke_xstest_qwen35.sh
    11_inspect_xstest_smoke.sh
    12_smoke_wildbench_qwen35.sh
    13_inspect_wildbench_smoke.sh

    15_run_100_instance_pilot.sh
    16_analyze_pilot.sh

    20_run_qwen35_full.sh
    21_run_qwen36_full.sh

    30_index_rejudge_artifacts.sh
    40_analyze_judges.sh
    45_build_report.sh
    50_rsync_from_aiq_gpu.sh

    config/infer_stack/catalog.yaml
    config/infer_stack/settings.yaml
```

Every script must:

- use `set -euo pipefail`;
- source `_lib.sh`;
- print resolved source and output paths;
- fail on missing prerequisites;
- be idempotent;
- write under experiment-specific or content-addressed directories;
- never modify `/data/crfm-helm-public`;
- write success sentinels only after output validation.

---

## 22. Commit-sized implementation sequence

A weak implementation agent should follow these slices in order. Each slice
should be independently reviewable and should not leave the repository in a
state where an apparently supported path is silently incorrect.

### Commit 1 — source-artifact audit

- Add source audit domain objects and CLI.
- Add fixtures covering valid and invalid public artifact sets.
- Do not add judging code.

### Commit 2 — immutable response snapshots

- Add display-artifact reconstruction.
- Add stable response hashing.
- Add detached official annotation storage.
- Add atomic `DONE` semantics.

### Commit 3 — official annotation identity replay

- Reattach official annotations.
- Reproduce published judge metrics exactly.
- Block unsupported metric replay.

### Commit 4 — judge specification model

- Add `JudgeSpec` and `JudgmentAttemptSpec`.
- Add canonical hashing and validation.
- Add explicit annotator arguments.

### Commit 5 — configurable XSTest annotator

- Preserve official prompt and parser behavior.
- Add prompt-parity tests.
- Add structured raw result records.

### Commit 6 — annotation-only runner

- Use a fake judge deployment.
- Add no-candidate-call test.
- Add cache restart and replicate isolation tests.

### Commit 7 — judge-attributed safety metric

- Produce the first complete `helm_rejudge_v1` fixture artifact.
- Update metric taxonomy.

### Commit 8 — configurable WildBench annotator and metric

- Preserve official prompt and empty-output semantics.
- Validate score range.
- Preserve raw strengths and weaknesses output.

### Commit 9 — judge deployment sidecars and Qwen client control

- Export explicit HELM model/deployment/tokenizer sidecars.
- Add optional explicit thinking-mode client support.
- Add structured-output smoke command.

### Commit 10 — infer-stack multi-replica leasing

- Add `--replicas` for dedicated dynamic-routing leases.
- Add static-mode rejection.
- Add route registration and release tests.
- Update the parent repository's infer-stack gitlink only after submodule tests
  pass.

### Commit 11 — kwdagger rejudge pipeline

- Add Docker node and bridge.
- Reuse one serving session across several rejudge jobs.
- Add fake-endpoint pipeline tests.

### Commit 12 — indexing and judge analysis

- Index `execution_kind=rejudge` distinctly.
- Join by response-set hash and display key.
- Produce aggregate, instance, and replicate reports.

### Commit 13 — aiq-gpu runbook

- Add dedicated infer-stack catalog.
- Add smoke, pilot, and full-run scripts.
- Document live gate checks.

### Commit 14 — remaining benchmark wrappers

- Add SimpleSafetyTests, HarmBench, AnthropicRedTeam, and Omni-MATH after the
  primary path is proven.

---

## 23. Anti-goals and prohibited shortcuts

Implementation agents must not:

- rewrite `openai/gpt-4o-*` to point to Qwen;
- label Qwen output as `gpt_score` or `llama_score`;
- rerun candidate inference for every judge arm;
- call the complete HELM runner for rejudging;
- turn `run_spec_materializer.py` into arbitrary mutation infrastructure;
- recompute token, timing, probability, or efficiency metrics from incomplete
  display artifacts;
- silently average several judge fields;
- emit canonical official metric names for a substitute single judge;
- assume public runs contain `scenario_state.json`;
- assume the built-in Qwen3.6 profile is load-balanced;
- reuse one HELM judge cache key across replicates;
- discard malformed raw judge output;
- turn parser failure into score zero;
- merge every judge replicate into the ordinary one-counterpart comparison
  planner;
- claim that a local candidate rerun with a local judge isolates the judge
  substitution effect;
- modify source public corpus files in place.

---

## 24. Definition of done

The infrastructure is complete when all of the following are demonstrated:

1. A public display artifact can be converted into a stable immutable response
   snapshot.
2. Reattaching official annotations reproduces the published judge-dependent
   metric exactly.
3. Rejudging performs no candidate inference.
4. Every judge arm points to the same response-set hash.
5. Judge model, deployment, parser, prompt, thinking mode, revision, and
   replicate are explicit.
6. Raw judge content, reasoning, request failures, and parser failures are
   retained.
7. Interrupted runs resume from their own SQLite cache.
8. Distinct replicates do not share cached judge responses.
9. WildBench and Omni-MATH metric names identify the actual judge.
10. Dynamic routing demonstrably spreads work across the requested replicas.
11. Rejudge artifacts are indexed as `execution_kind=rejudge`, not candidate
    reproductions.
12. The final report compares each Qwen judge with each official judge, the
    official ensemble, and the other Qwen judge.
13. The report separates model disagreement, replicate variation, request
    failure, and parser failure.
14. The source public corpus remains byte-for-byte unchanged.

The central new abstraction is therefore not "a HELM run with a rewritten
judge deployment." It is an immutable **response snapshot** followed by one or
more independently attributable **judgment attempts**.

# HELM Patch Proposal: Normalize Null Completion Text

## Problem

Local `gpt-oss-20b` reproduction runs against an OpenAI-compatible LiteLLM/vLLM endpoint exposed a robustness gap in HELM's completion handling:

- some successful responses arrive with `message.content = null` on the chat path
- HELM later assumes completion text is always a string
- metric code then crashes with:

```text
AttributeError: 'NoneType' object has no attribute 'strip'
```

This showed up in in-scope runs such as:

- `ifeval:model=openai/gpt-oss-20b`
- `bbq:subject=all,method=multiple_choice_joint,max_train_instances=0,model=openai/gpt-oss-20b`

The practical effect is that HELM aborts the run instead of recording an empty or malformed completion and continuing through the normal metric/failure path.

## Evidence

Observed OpenAI-compatible chat response:

```json
{
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": null,
        "reasoning_content": "The user says: \""
      },
      "finish_reason": "length"
    }
  ]
}
```

Observed completions-path response for the same backend:

```json
{
  "choices": [
    {
      "text": " **",
      "logprobs": {
        "tokens": [" **"]
      }
    }
  ]
}
```

This suggests two things:

1. The provider/backend is unusual on the chat path.
2. HELM still should not crash when completion text is `None`.

## Confirmed root cause and faithfulness (verified 2026-07-13)

A CPU-only trace of the `openai-gpt-oss-20b` from-spec freeze plus an audit of the
official public run dirs pinned the mechanism precisely and ruled out two plausible
alternates:

- **Not a bad freeze / not `max_tokens`.** The from-spec freeze resolves all four
  rows 1:1 to their official dirs and replays the official `adapter_spec` verbatim
  (the exporter rewrites only `model_deployment`, never `max_tokens`). Frozen budgets
  are `bbq=10001`, `ifeval/mmlu_pro/gpqa=14096` — never `1`. The `max_tokens=1`
  signature belongs to the older run-entry runbooks (`gpt_oss_20b_vllm`,
  `gpt_oss_20b_core_grid`), not this from-spec path.
- **Not window truncation.** Official prompts are tiny (bbq median 90, gpqa max 2853
  tokens) and **no official instance's `prompt + output` exceeds 16384** (the local
  `max_model_len`). Nothing truncates at the local window; a window bump is a red
  herring for this crash.
- **The actual event:** gpt-oss sometimes spends its whole generation budget in the
  reasoning channel and emits **no final-channel answer** (official ifeval: 59/541
  instances, all at the 14096 cap). The two serving stacks *represent the same event
  differently*:
  - Official `together/gpt-oss-20b` (`TogetherChatClient`) → `message.content = ""`
    (empty string). `"".strip()` succeeds; HELM scores an empty/wrong answer and
    continues. **These 59 empties are part of the published ifeval score.**
  - Local vLLM OpenAI-compat endpoint → `message.content = null`. `None.strip()`
    crashes.
  Verified counts on the official run dirs: `content is None` = **0** across all four
  rows; `content == ""` = **59** for ifeval (0 for bbq/mmlu_pro/gpqa).

**Faithfulness consequence.** Normalizing local `content: null → ""` is therefore the
*faithful* reproduction, not a workaround: it makes the local client emit exactly what
Together's client already emitted for these instances (empty string, reasoning kept in
`reasoning_content`/`thinking`). Reproducing the published numbers *requires* it,
because the official result itself contains empty predictions. The **completions
fallback is both unfaithful** (it drops the harmony chat/reasoning path the official
run actually used) **and unnecessary**; a **window bump is unnecessary** (nothing
truncates at 16384).

Residual signal to keep honest: if the *local* serving fails to answer on different
instances than Together did (or more of them), that surplus is real reproducibility
disagreement and should surface through the normal metric path — the normalization
must not suppress it, only prevent the hard crash.

**Keeping HELM untouched.** Because the fix is a pure `null → ""` representation
change that HELM already tolerates from Together, it can equally be applied *below*
HELM — at the LiteLLM gateway the runbook already routes through — so HELM's pristine
`OpenAIClient` sees `""` exactly as it did from Together. See the gateway-normalization
option in the runbook README / the proposal's short-term workaround section.

## Why This Belongs In HELM

Even if the provider is quirky, HELM's current behavior is too brittle:

- a successful HTTP response can still produce a non-string payload
- downstream metric code assumes `completion.text` is always a string
- the run fails with an uncaught attribute error rather than a benchmark-level failure or empty prediction

For an evaluation framework, the safer default is:

- normalize missing completion text to `""`, or
- surface a structured client failure

but do not let a raw `NoneType.strip()` exception crash the run.

## Likely Crash Sites

The trimmed `gpt-oss` reproductions hit `.strip()` assumptions in at least these HELM metric paths:

- `helm/src/helm/benchmark/metrics/evaluate_reference_metrics.py`
  - `preds: List[str] = [completion.text.strip() for completion in sorted_completions]`
- `helm/src/helm/benchmark/metrics/ifeval_metrics.py`
  - `response_text = request_state.result.completions[0].text.strip()`

There are many similar `.text.strip()` sites across HELM, so the most maintainable fix is probably not to patch each metric independently.

## Proposed Fix

Normalize null completion text at the client/result-construction layer so every `GeneratedOutput.text` is always a string.

### Preferred behavior

- if the provider returns `text = null` or `message.content = null`, store `GeneratedOutput.text = ""`
- if the provider returns structured content parts, flatten the text-bearing parts conservatively
- if no text can be recovered, keep `""` and preserve the raw response in metadata if available

## Why Client-Layer Normalization Is Better

- fixes the issue once instead of chasing metric-level `.strip()` calls
- preserves existing metric semantics
- keeps malformed-but-successful provider payloads from crashing unrelated scenarios
- still allows logging/debugging of the original response shape

## Minimal Acceptance Criteria

1. A completion with null text/content no longer crashes HELM metrics.
2. The run records an empty prediction or equivalent benign fallback.
3. Existing string-valued completion behavior remains unchanged.
4. A regression test covers both:
   - chat response with `message.content = null`
   - legacy/completions response with `text = null`

## Implemented Local Resolution (HELM untouched)

Independent of the upstream proposal above, the local reproduction resolves this
without editing the vendored HELM submodule, by extending HELM through its own
`client_spec.class_name` seam. `eval_audit/integrations/helm_clients.py` defines
`NullSafeOpenAIChatClient` / `NullSafeVLLMChatClient` — thin subclasses whose
`make_request` normalizes any `GeneratedOutput.text is None` to `""` (matching what
`TogetherChatClient` already emitted). The exporter's `_benchmark_client_class`
(`eval_audit/integrations/infer_stack/serving_facts.py`) selects these for every
**chat** deployment; completions deployments are unchanged. Because the frozen
`model_deployments.yaml` carries the class by name and HELM imports it at run time,
the vendored HELM source stays pristine. The override fires only on null text, so it
is a strict no-op on ordinary responses and preserves `thinking`/`reasoning_content`.
This is strictly preferable to the completions fallback below, which is retained only
as a liveness escape hatch.

## Short-Term Local Workaround

For the current `gpt-oss-20b` local reproduction, the safer configuration workaround is to use:

- `helm.clients.openai_client.OpenAILegacyCompletionsClient`

and explicitly pin:

- `model_deployment=litellm/gpt-oss-20b-local`

for the in-scope runs that can use the completions path.

That workaround is useful operationally, but it should not be treated as a full substitute for making HELM robust to null completion text.

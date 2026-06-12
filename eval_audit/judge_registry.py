"""Curated judge-model registry for official HELM runs.

The judge-identity inventory
(docs/planning/judge-identity-inventory.md) established that official
HELM run_specs do **not** record judge model identity: the
``annotators`` entries carry empty args, and the models are hard-coded
in the HELM annotator classes per HELM version. So for official runs
the extractable identity is the annotator *class basename* (what
``eval_audit.indexing.schema.extract_judge_models`` returns), and this
registry maps it to the judge models that class used.

Local re-runs record their judges explicitly (we control the recipe),
so they never need this map. Resolution therefore makes official and
local identities comparable for the ``same_judge`` fact
(Phase 3 / 4.9, design doc §3.5).

Maintenance: keyed by annotator class basename. The values below were
read out of the vendored HELM sources (submodules/helm,
``model_as_judge.score_with_reasoning_with_gpt_and_llama`` and the
per-benchmark annotators) at inventory time. If a HELM upgrade changes
a judge ensemble, add the suite-version-qualified entry rather than
editing in place — entries are evidence about what produced existing
official artifacts. Unmapped identifiers resolve to themselves, which
keeps the fact honest: an unmapped class basename never accidentally
equals a model id.
"""

from __future__ import annotations

from typing import Iterable

#: The GPT+Llama ensemble shared by the four safety benchmarks (via
#: ``score_with_reasoning_with_gpt_and_llama``) and configured
#: identically by the WildBench / Omni-MATH annotators.
_GPT_LLAMA_ENSEMBLE: tuple[str, ...] = (
    "meta/llama-3.1-405b-instruct-turbo",
    "openai/gpt-4o-2024-05-13",
)

#: annotator class basename -> judge models that class invokes.
OFFICIAL_JUDGE_MODELS_BY_ANNOTATOR: dict[str, tuple[str, ...]] = {
    "WildBenchAnnotator": _GPT_LLAMA_ENSEMBLE,
    "OmniMATHAnnotator": _GPT_LLAMA_ENSEMBLE,
    "HarmBenchAnnotator": _GPT_LLAMA_ENSEMBLE,
    "AnthropicRedTeamAnnotator": _GPT_LLAMA_ENSEMBLE,
    "SimpleSafetyTestsAnnotator": _GPT_LLAMA_ENSEMBLE,
    "XSTestAnnotator": _GPT_LLAMA_ENSEMBLE,
}

#: Judge models in the official ensembles that are open-weight — the
#: per-metric sub-scores they produce (e.g. ``safety_llama_score``) are
#: reproducible same-judge controls rather than substitution targets.
OPEN_WEIGHT_JUDGES: frozenset[str] = frozenset({
    "meta/llama-3.1-405b-instruct-turbo",
})


def resolve_judge_models(identifiers: Iterable[str] | None) -> tuple[str, ...] | None:
    """Map extracted judge identifiers to concrete judge-model ids.

    ``identifiers`` is what the run_spec extractor produced: model ids
    (recorded explicitly — local re-runs) and/or annotator class
    basenames (officials, where the model is hard-coded in HELM).
    Class basenames found in the registry expand to their model
    ensemble; anything unmapped passes through unchanged so unknown
    identities stay visibly unknown instead of colliding.

    ``None`` stays ``None`` (judge identity unknown); an empty input
    stays ``()`` (explicitly judge-free).
    """
    if identifiers is None:
        return None
    resolved: set[str] = set()
    for identifier in identifiers:
        text = str(identifier).strip()
        if not text:
            continue
        mapped = OFFICIAL_JUDGE_MODELS_BY_ANNOTATOR.get(text)
        if mapped is not None:
            resolved.update(mapped)
        else:
            resolved.add(text)
    return tuple(sorted(resolved))


__all__ = [
    "OFFICIAL_JUDGE_MODELS_BY_ANNOTATOR",
    "OPEN_WEIGHT_JUDGES",
    "resolve_judge_models",
]

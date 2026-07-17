"""Benchmark-faithful configurable judge annotators and metrics.

HELM integrations for the open-judge experiment
(``docs/planning/open-judge-plan.md`` Phases 5-6): drop-in annotators
that preserve each benchmark's official prompt construction and parsing
byte-for-byte (test-pinned against the installed HELM annotators) while
making the judge model explicit and single, plus judge-attributed
metrics that never emit canonical official metric names for a
substitute judge.

Loaded through the existing HELM plugin seam (classes referenced by
dotted name from ``AnnotatorSpec``/``MetricSpec``); the vendored HELM
submodule is never modified.
"""

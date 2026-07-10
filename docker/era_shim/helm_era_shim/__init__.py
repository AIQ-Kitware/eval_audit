"""helm_era_shim — standalone pre-v0.5 HELM verbatim-replay shim.

Two pieces, both installed *only* inside the era Docker image (never on the
host, never importing magnet or eval_audit):

- :mod:`helm_era_shim.replay` — a from-spec replay CLI that is flag-compatible
  with magnet's ``materialize_helm_run_from_spec`` (so the eval_audit docker
  node contract is unchanged), but decodes the run_spec.json into the *era*
  ``helm.benchmark.runner.RunSpec`` and drives era ``run_benchmarking``.
- :mod:`helm_era_shim.openai_compat_client` — a backported OpenAI-compatible
  completions client (no such client exists pre-v0.5) that routes the era
  harness to a local vLLM ``/v1/completions`` endpoint, constructing era result
  types.

See docs/planning/era-pinned-helm-containers-plan.md.
"""

__version__ = "0.1.0"

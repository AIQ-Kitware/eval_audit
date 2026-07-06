"""Deployment-match search — find the local serving recipe that best reproduces
a public HELM run.

General, model-agnostic tool that generalizes the OLMo-7B deploy-matrix MWE
(the since-retired ``olmo7b_deploy_matrix`` debug harness). Given one public HELM
run it (1) extracts a small instance sample + the official completions as the oracle,
(2) generates a grid of local serving recipes for that model, (3) runs each on
the sample, and (4) ranks them by agreement with the official outputs.

See ``docs/planning/deployment-match-search-plan.md`` for the design.

Modules
-------
* ``oracle``   — read a public HELM run: recipe facts + sampled instances.
* ``registry`` — resolve HELM model -> local HF source / protocol + official
  deployment facts (tokenizer, max_sequence_length).
* ``grid``     — two-tier grid generator (serve-recipes x request-variants ->
  cells + an infer-stack catalog).
* ``probe``    — OpenAI-compatible client with request-time knobs
  (add_special_tokens / echo / logprobs).
* ``score``    — candidate-vs-official scorer + composite ranking.
* ``report``   — ranking table + best_deployment.yaml.
* ``cli``      — command-line entry point.

The stdlib-only core (oracle read, grid shape, scoring) runs on CPU with no
serving; ``eval_audit`` / ``infer_stack`` imports are optional enrichment.
"""

__all__ = ["oracle", "registry", "grid", "probe", "score", "report"]

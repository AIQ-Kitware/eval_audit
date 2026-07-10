# helm_era_shim

Standalone **pre-v0.5 (era) HELM verbatim-replay shim**, installed *only* inside
the era Docker image (`docker/helm-runner-era.dockerfile`). It **never** imports
`magnet` or `eval_audit`, and depends only on the stdlib + era `crfm-helm[all]` +
`dacite` (an era dependency) + `requests` (a helm dependency).

| Module | Purpose |
|---|---|
| `helm_era_shim/replay.py` | The inner executable the era docker node runs (`python -m helm_era_shim.replay`). Flag-compatible with magnet's `materialize_helm_run_from_spec`; strict-decodes the run_spec.json into the era `helm.benchmark.runner.RunSpec` (dacite `strict=True` = drift detector), preflights class resolution, prepares `prod_env` (era `model_deployments.yaml` + synthesized `credentials.conf`), and drives era `run_benchmarking` in-process. Exact-path only — requires `--run_spec_json`. |
| `helm_era_shim/openai_compat_client.py` | Backported OpenAI-legacy-completions client (none exists pre-v0.5). A `requests`-based port that POSTs `/v1/completions` to a local vLLM endpoint and constructs era `Sequence`/`Token`/`RequestResult`. Registered by-name via the era deployment registry. |

Replay is **verbatim**: a pre-v0.5 `adapter_spec` has no `model_deployment`
field, so nothing is rewritten — routing to vLLM is purely by-name (a deployment
registered under the exact official model name). The only opt-in mutation is
`adapter_spec.max_eval_instances` (truncation).

The package is validated by `python -m py_compile` on the host; its era imports
resolve only inside the era image (the image's final-stage assertion imports both
modules). See `docs/planning/era-pinned-helm-containers-plan.md`.

# Era-Pinned HELM Reproduction Containers (pre- and post-v0.5)

> **Status (branch `impl/era-pinned-helm-containers`).** All six commits are
> implemented: era registry + resolver (`eval_audit/eras.py`, `docker/eras.yaml`);
> era image build (`docker/build.sh` `ERA=` mode, `docker/helm-runner-era.dockerfile`);
> the `helm_era_shim` package (`docker/era_shim/`); host-side era yaml +
> materializer guard; manifest/pipeline/bridge threading with the era↔image
> label guard; and the runbook (now `dev/era-tests/`, restructured from the
> original `reproduce/classic_era_replay/` to mirror `dev/e2e-tests/` — see
> `docs/planning/era-tests-dev-runbook-plan.md`) + docs. The 2026-07-10 code
> review's ten findings are all fixed (`docs/planning/era-pinned-review-findings-2026-07-10.md`).
> Unit
> tests: `tests/test_eras*.py`. What remains is **empirical validation on a GPU
> host** — the validation ladder below (build the era images, freeze the
> constraints, run instrument-fidelity + end-to-end) has not been executed; the
> "Open questions" are settled during that pass.

## Context

The audit corpus (configs/run_details.yaml, 270 runs) is **59% pre-v0.5**: 159 classic-track runs (85 × v0.2.4, 74 × v0.3.0) vs 111 modern-track runs. Today only the modern era is runnable: the single `helm-runner` image pins HELM 0.5.14 + Python 3.11 + modern deps, and magnet's from-spec CLI imports v0.5+ module paths (`helm.common.codec`, `helm.benchmark.run_spec`) that don't exist pre-v0.5.

The goal: run **from-spec, verbatim replays** of official runs inside a Docker image whose harness matches the era that produced the official artifacts — HELM source at the era's release commit, era Python, era dep pins (pandas/numpy govern instance selection; the tech report proved pandas 2.0.x vs 2.2+ flips instance identity). Model inference stays out-of-process on modern vLLM (infer_stack), so the era container is a **CPU-only harness**. This holds the measurement instrument fixed at the era and isolates deployment as the only variable — the cleanest form of the audit's research question.

Facts established by exploration (verified against git history of submodules/helm):

- Era release commits exist in the submodule history: v0.2.4=`626d8609`, v0.3.0=`8ea285f7`.
- The **model-deployment registry exists at both eras** (`--model-deployment-paths` → `register_model_deployments_from_path`; `AutoClient` consults `get_model_deployment(model)` *before* the hardcoded org→Together dispatch). So local routing needs no HELM patch — just a registered deployment named exactly like the official model.
- **No OpenAI-compatible client exists pre-v0.5** (era `OpenAIClient` hardcodes `api.openai.com`; `HTTPModelClient` speaks the neurips `/process` protocol). A small backported client is required.
- `run_benchmarking(...)` has the **identical signature at v0.2.4 and v0.3.0**; era `RunSpec` lives at `helm.benchmark.runner.RunSpec` with exactly the six keys official classic run_spec.json carries.
- Python 3.10 satisfies both eras (`>=3.8,<3.11` at v0.3.0; `~=3.8` at v0.2.4; pyext needs <3.11). Tech-report-validated v0.3.0 pins: `pandas==2.0.3`, `numpy==1.23.5`.
- Pre-v0.5 `adapter_spec` has **no `model_deployment` field** → era replay must be truly verbatim; no deployment rewrite.
- No entry-point plugin loading pre-v0.5 → eval_audit's plugin mechanism can't apply; era overrides go via config files / the shim package.
- Era signal = `(public_track, suite_version)`, path-derived, already captured in the official index (`eval_audit/indexing/schema.py:52-76`).
- Plumbing: `container_image` is manifest-global, broadcast into the kwdagger matrix (`kwdagger_bridge.py:271`), and already part of algo identity (`helm_docker_pipeline.py:112-117`). The from-spec docker node's inner `executable` is a class attribute (`helm_docker_pipeline.py:298`).

## Design decisions

1. **Dispatch = per-era manifests**, not per-run images. One manifest = one era = one image = one measurement instrument (the audit's provenance unit). Avoids touching the kwdagger broadcast/submatrix contract; digest-in-algo-identity gives correct caching for free. Mixed-era `run_spec_sources` is a hard error at make-manifest time.
2. **Separate era dockerfile**, not a parameterized modern one. Every load-bearing modern layer is era-hostile (magnet install, eval_audit plugin + entry-point assertion, `huggingface_hub==0.36.2` pin, olmo assertion, CUDA). Era image = `ubuntu:22.04` (no CUDA — CPU harness) + uv-managed CPython 3.10 + era HELM + a tiny shim package.
3. **A standalone shim package** (`helm-era-shim`, never installed on the host, never imports magnet/eval_audit) provides (a) a from-spec replay CLI flag-compatible with magnet's from-spec CLI so the docker node contract is unchanged, and (b) the backported OpenAI-compatible completions client registered via the era's own deployment registry.
4. **Verbatim replay for era runs**: no spec rewriting (there is no `model_deployment` field to rewrite). Routing to vLLM happens purely by registering a deployment under the exact official model name. Era window services keyed on the official model name reproduce official tokenization/windowing untouched.
5. Era registry is declarative (`docker/eras.yaml`), read by both `build.sh` and a new `eval_audit/eras.py` resolver keyed on `(public_track, suite_version)`. Absence of a match = modern era = existing image + magnet CLI, unchanged.

## Implementation (dependency-ordered commits)

### 1. Era registry
- **New `docker/eras.yaml`**: per era key (`helm-v0.2.4`, `helm-v0.3.0`): `helm_git_ref`, `python_version: "3.10"`, `constraints` path, `helm_extras: all`, `capability: era-shim-from-spec`, `image_name`, and `matches: [{public_track, suite_version}]` predicates.
- **New `eval_audit/eras.py`**: frozen `EraSpec` dataclass; `load_era_registry()`, `resolve_era(public_track, suite_version)`, `era_for_run_dir(path)` (same path convention as `official_public_index.py` / `compare_batch.py:43-55`), `resolve_era_for_sources(precomputed_root, sources)` (raises on mixed eras). Unit tests.
- **New seed constraints** `docker/eras/constraints-helm-v0.3.0.txt` (start: `pandas==2.0.3`, `numpy==1.23.5`) and `-v0.2.4.txt`; superseded by the empirical freeze (below).

### 2. Era image build
- **`docker/build.sh`**: add `ERA=<key>` mode — reads `docker/eras.yaml`; stages HELM via `git archive <helm_git_ref>` (new `stage_committed_ref`); skips magnet + eval_audit staging; stages `docker/era_shim/`; selects the era dockerfile; tags `<image_name>:<eval-audit-short-sha>`. No `ERA` ⇒ byte-identical behavior to today. Reject `ERA` + `BUILD_FROM=worktree` for the helm tree.
- **New `docker/helm-runner-era.dockerfile`**: two-stage uv pattern reused; `ubuntu:22.04` base; `uv pip install -e '/opt/src/helm[all]' --constraint constraints.txt` with CPU-only torch; `uv pip install -e /opt/src/era-shim --no-deps`; reuse `entrypoint.sh` verbatim (pure shell). Final-stage assertions: era API imports (`register_model_deployments_from_path`, `helm.benchmark.runner.RunSpec`, shim client), Python 3.10, pandas/numpy pin spot-check. Labels: existing set + `org.aiq.era=<key>`.
- **Constraints freeze workflow** (document in `docker/README.md`): build with seeds → `pip freeze` in-container → commit as the era constraints file → rebuild. Satisfies the frozen-at-build-time policy.

### 3. The shim package — `docker/era_shim/` (`helm_era_shim`, requires-python >=3.8)
- **`replay.py`** (CLI, `python -m helm_era_shim.replay`): accepts the exact underscore flag set `render_magnet_command` emits (`--run_spec_json`, `--suite`, `--out_dpath`, `--model_deployments_fpath`, `--num_threads`, `--local_path`, …; hard error on `--model_deployment` with the message "pre-v0.5 adapter_spec has no model_deployment; era replay is verbatim"). Flow:
  1. Strict-decode run_spec.json → era `helm.benchmark.runner.RunSpec` via dacite (already an era dep); strict mode is the era-drift detector.
  2. Preflight: resolve every reachable `ObjectSpec.class_name` via the era's `get_class_by_name`; loud failure mirroring magnet's preflight message.
  3. `local_config.prepare()`: prod_env with the era deployments yaml + a synthesized `credentials.conf` (`deployments {<model_name>: <key>}` from env `EVAL_AUDIT_ERA_API_KEY`, default `"EMPTY"`) — required because v0.2.4 eagerly demands a deployments credential before client construction.
  4. In-process `run_benchmarking(run_specs=[spec], suite=…, output_path=<out>/benchmark_output, …)` (signature verified identical at both eras).
  5. Locate run dir deterministically (`benchmark_output/runs/<suite>/<sanitized run_spec.name>`); write the magnet-compatible output contract: `adapter_manifest.json` (with `replay: {era, helm_git_ref, computed_run_dir}`), `process_context.json` (argv, versions, pip-freeze), `DONE` last.
- **`openai_compat_client.py`**: `requests`-based port of modern `VLLMClient`/`OpenAILegacyCompletionsClient` request/response logic constructing **era** result types (`Sequence`/`Token` with `top_logprobs`). Constructor `(cache_config=None, api_key=None, base_url=None, openai_model_name=None, **_ignored)` — tolerates both eras' injection styles (v0.2.4 `additional_args` override; v0.3.0 provider bindings). POST `/v1/completions` with `echo`/`logprobs`/`stop`/`n` fidelity; wrap in the era cache the way era `TogetherClient` does. Implement `echo_prompt` + `max_tokens=0` even though the pinned 159-run set is generation + multiple_choice_joint (cheap, future-proof).

### 4. Host-side era yaml generation + materializer guard
- **`eval_audit/integrations/infer_stack/bundle_export.py`**: `_model_deployment_entry_era(...)` emitting era-schema entries — deployment `name` = exact official model name; `client_spec.class_name = helm_era_shim.openai_compat_client.OpenAICompatCompletionsClient` with `args: {base_url, openai_model_name}`; for v0.2.4 emit all five required keys (`model_name`, `tokenizer_name: null`, `max_sequence_length: null` — cattrs no-defaults). No api_key in args (credentials.conf owns it). Skip `_assert_helm_aliases_exist` on the era path (it validates against the modern submodule — wrong universe; the shim preflight is the loud check). Thread `era` from preset config into `_manifest_doc`.
- **`eval_audit/integrations/infer_stack/freeze.py`**: era mode — sources omit `model_deployment`; `lease_endpoint` from the manifest scalar.
- **`eval_audit/manifests/run_spec_materializer.py`**: guard — if a `model_deployment` rewrite is requested but the official `adapter_spec` lacks the key, fail loud (never insert a novel field into a pre-v0.5 spec). `max_eval_instances` edits remain valid at all eras.

### 5. Manifest/pipeline threading
- **`eval_audit/manifests/models.py`**: add `era: str | None = None`.
- **`eval_audit/manifests/builders.py`**: `--era {auto,<key>,modern}` (default auto → `resolve_era_for_sources`; hard error on mixed eras). Validate era manifests: `from_run_spec=True` + non-empty `run_spec_sources` (exact-path only — the shim has no discovery mode), no deployment rewrite targets.
- **`eval_audit/pipelines/helm_docker_pipeline.py`**: `class MaterializeHelmRunFromSpecEraDockerNode(MaterializeHelmRunFromSpecDockerNode): executable = "python -m helm_era_shim.replay"` + factory `helm_single_run_from_spec_era_docker_pipeline()`. Forward `-e EVAL_AUDIT_ERA_API_KEY` in the docker command (same pattern as `HF_TOKEN`).
- **`eval_audit/integrations/kwdagger_bridge.py`**: select the era pipeline on the exact-path branch when the manifest's era has `capability == "era-shim-from-spec"`; reject era + discovery/run-entry modes; **era↔image guard** — after `resolve_image_digest`, inspect the image's `org.aiq.era` label and require it to match the manifest era (absence required for modern), failing at schedule time rather than GPU time. Record era in schedule provenance.

### 6. Analysis surfacing + runbook + docs
- Era + era-image digest into local-side manifest/job provenance (rides the existing manifest-recording path into the index extras / `recipe_facts.extra`). Official side already derivable from `public_track` + `suite_version`. `same_deployment` for era pairs correctly resolves `unknown` (both sides lack the field) — no Stage 5/6 changes.
- **Runbook `dev/era-tests/`** (restructured from `reproduce/classic_era_replay/` to mirror `dev/e2e-tests/`; validation-ladder gates live in `07_run_gate.sh`, the end-to-end path is a turnkey grid — see `docs/planning/era-tests-dev-runbook-plan.md`), docs updates: `docs/container-execution.md` era section; `docs/helm-gotchas.md` cross-ref to G10 (era keyed on suite_version is a *suite*-era, `run_spec_hash` detects recipe-identical duplicates).

## Verification (the validation ladder)

1. **Image sanity**: build both era images; `python -m helm_era_shim.replay --help` in-container; dry-run replay of one spec (scenario construction + adaptation, no requests). Freeze + commit constraints; rebuild; re-verify.
2. **Instrument fidelity (no model)**: replay up to scenario-state construction for the pandas-sensitive `entity_matching` (Abt_Buy) plus one `math` and one `raft` run; diff instance identity against official artifacts (must be byte-for-byte, as the tech report demonstrated with era pins).
3. **End-to-end single run**: `synthetic_reasoning_natural` × `pythia-6.9b` through bundle-export (era yaml) → make-manifest `--era auto` → eval-audit-run with a vLLM-served pythia. Expected: recovers ~20% vs the official 0% (known Together deployment artifact) — the audit's flagship demonstration.
4. **One full packet per era** through Stages 3–6 (pairing, `same_deployment=unknown`, era in provenance).
5. **HF-fetch audit**: dry-run scenario construction for each of the ~25 classic scenario families under the era image with warmed `/hf-cache`; pre-warm or mount-vendor any that no longer fetch cleanly from the 2026 Hub (never patch the image at run time).

Also: `python -m py_compile` on touched files; unit tests for `eras.py` resolution and mixed-era rejection; existing test suites for builders/bridge (`tests/test_make_manifest_sources.py`, `tests/test_kwdagger_submatrix_contract.py`) extended for the era paths.

## Open questions to settle empirically during implementation

1. vLLM `/v1/completions` fidelity vs Together-era responses: `echo=True` + `max_tokens=0`, server-side `--max-logprobs` ceiling vs `top_k_per_token`, stop-sequence inclusion in returned text — validate against a handful of official per-instance requests.
2. `pyext~=0.7` build under uv on Python 3.10 inside the image (runtime validated by the tech report; build path unconfirmed).
3. Era `Client` base-class variance (v0.3.0 tokenizer-aware base) — one shim client with `**_ignored` + feature-detect, or two tiny subclasses.
4. v0.3.0 `inject_object_spec_args` behavior when the constructor param has a default (drop the default if injection requires it).
5. datasets 2.5.2 / era hub client vs 2026 HF Hub, per scenario family (ladder step 5).
6. Torch-CPU pinning style under `uv pip install --constraint` (`+cpu` pin vs extra index).
7. Stage-4 indexer expectations on `adapter_manifest.json` keys (mirror only what it consumes).
8. v0.2.4 constraints may need different pandas/numpy pins than the validated v0.3.0 set (entity_matching determinism check per era).

## Risks

- **HF Hub drift** is the main empirical risk (old `datasets` against today's Hub); mitigated by cache pre-warming + read-only mounts, surfaced per-scenario by ladder step 5.
- **Logprob fidelity of the shim client** is the main correctness risk for multiple-choice scoring; ladder step 3 + per-instance request diffs cover it.
- **G10 caveat**: era is keyed on suite_version, which is a suite-tracking version. For the classic track this maps cleanly (v0.2.4/v0.3.0 dirs ↔ era harnesses); `run_spec_hash` remains the tool for detecting recipe-identical cross-suite duplicates. Documented, not blocking.

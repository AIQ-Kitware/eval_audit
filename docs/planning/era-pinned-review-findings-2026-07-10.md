# Era-Pinned HELM Containers — Code-Review Findings (2026-07-10)

> **Resolution (2026-07-11).** All ten findings are fixed on
> `impl/era-pinned-helm-containers`, with host-importable tests for every one
> that could be exercised without an era image (Findings 3/8 verify at build
> time; 6 at build + run time):
>
> - `0c58ee9` — Findings 1, 2 (shim), 3, 7, 8 (`docker/era_shim/`)
> - `befd490` — Findings 2 (bundle), 5, 10 + era `protocol_mode` assert
> - `f549943` — Finding 4 (digest-pinned era<->image guard)
> - `9bf2228` — Finding 6 (dockerfile ENV)
> - `f09051b` — Finding 9 (fail loud on track-less era-suite paths)
> - `0486bb7` — below-the-cap cleanups: process-context timing, requests.Session
>   reuse, `setdefault`
>
> **Deferred below-the-cap cleanups** (not exercisable in the sandbox; each is a
> pure refactor with no correctness impact): the shared `benchmark_output` path
> parser extraction (`eras.py` vs `compare_batch.py`), `docker/read_eras.py`
> default dedup, and the `build.sh` era/modern build-invocation dedup.

**Scope.** The six era commits on `impl/era-pinned-helm-containers`
(`25b00db..a520062`, i.e. `git diff 6092144..HEAD`), implementing
`docs/planning/era-pinned-helm-containers-plan.md`.

**Method.** Multi-angle review (line-by-line, removed-behavior, cross-file
tracing, reuse/simplification/efficiency/altitude/conventions), with every
era-API claim verified **directly against the era HELM source** via
`git -C submodules/helm show 626d8609:<path>` (v0.2.4) and
`git -C submodules/helm show 8ea285f7:<path>` (v0.3.0), plus one empirical
pyhocon test run in the repo venv.

**Verdict in one line.** The host-side plumbing (registry, resolver, manifest
threading, bridge selection, guards, tests) is sound; the shim's era contract
was effectively written against **v0.3.0 semantics and never validated against
v0.2.4's older API**. Findings 1–3 break v0.2.4 outright (one of them silently);
finding 2 also breaks v0.3.0 for the flagship model. None of this is catchable
by the host test suite (nothing imports era `helm.*` outside the image) — which
confirms the plan's validation ladder must run before any results are trusted.

**How to use this file.** Fix in the order given (1–6 are the required set;
each has a "Fix" and a "Verify" block). Findings 7–10 are smaller correctness
items; the "Below the cap" section lists worthwhile cleanups. Suggested commit
grouping at the end.

---

## Finding 1 — CRITICAL: v0.2.4 never registers the deployments yaml → silent Together routing

**Where.** `docker/era_shim/helm_era_shim/replay.py` — `main()` step 3 comment
(~line 194) and `_prepare_local_helm_config` (~line 348).

**Status.** CONFIRMED against era source.

**What happens.** The shim copies `model_deployments.yaml` into `prod_env` and
relies on the comment's claim that "ServerService auto-registers deployments
from this base path at run_benchmarking time". That auto-registration
(`maybe_register_model_deployments_from_base_path`, called from
`ServerService.__init__`) **exists only at v0.3.0**
(`8ea285f7:src/helm/proxy/services/server_service.py:52`). At v0.2.4:

- `maybe_register_model_deployments_from_base_path` does not exist at all
  (`626d8609:src/helm/benchmark/model_deployment_registry.py` has only
  `register_model_deployments_from_path` + `get_model_deployment`);
- the **only** caller of `register_model_deployments_from_path` is
  `run.py main()`'s `--model-deployment-paths` flag handling
  (`626d8609:src/helm/benchmark/run.py:275`) — which the shim bypasses by
  calling `run_benchmarking()` in-process.

So at v0.2.4 the deployment registry stays empty, `get_model_deployment(model)`
returns `None`, and `AutoClient._get_client` falls through to the hardcoded org
dispatch, where `eleutherai` (also `lmsys`, `meta`, `mosaicml`, `stabilityai`,
`tiiuae`, `together`, `databricks`, `stanford`) maps to **`TogetherClient`**
(`626d8609:src/helm/proxy/clients/auto_client.py`, the `elif organization in
[...]` branch). Every request silently routes to `api.together.xyz` — the exact
deployment artifact the audit exists to eliminate. With no `togetherApiKey` the
requests fail per-instance (recorded as failed RequestResults, potentially
completing the run with garbage scores that *mimic the official 0% Together
artifact the flagship demo is supposed to correct*). Models with orgs outside
the elif chain instead raise `ValueError("Could not find client for model")`.

**Fix.** In `replay.py`, after `_prepare_local_helm_config` (and before the
preflight), explicitly register the deployments file at the era API — it exists
identically at both eras:

```python
deployments_yaml = prepared_local_path / "model_deployments.yaml"
if deployments_yaml.exists():
    from helm.benchmark.model_deployment_registry import (
        register_model_deployments_from_path,
    )
    register_model_deployments_from_path(os.fspath(deployments_yaml))
```

This is idempotent at v0.3.0 (the ServerService re-registration overwrites the
same entries). Update the step-3 comment to say the shim registers explicitly
because v0.2.4 has no base-path auto-registration.

**Verify.** Validation-ladder step 1 in the v0.2.4 image: after a dry
replay setup, assert `get_model_deployment("<official model>")` is non-None.
Ladder step 3 end-to-end covers the routing.

---

## Finding 2 — CRITICAL: pyhocon dot-splitting breaks the credentials lookup for dotted model names (both eras; includes `pythia-6.9b`)

**Where.** `docker/era_shim/helm_era_shim/replay.py` —
`_prepare_local_helm_config` credentials.conf write (~line 375).

**Status.** CONFIRMED **empirically** (run in the repo `.venv`):

```python
from pyhocon import ConfigFactory
raw = 'deployments: {\n  "eleutherai/pythia-6.9b": "EMPTY"\n}\n'
deps = ConfigFactory.parse_string(raw)["deployments"]
"eleutherai/pythia-6.9b" in deps        # -> False
deps["eleutherai/pythia-6.9b"]          # -> ConfigMissingException:
                                        #    'No configuration setting found
                                        #     for key eleutherai/pythia-6'
```

pyhocon's `ConfigTree` **path-splits lookup keys on `.`** even when the key was
written quoted. HELM's `get_credentials` parses credentials.conf with pyhocon,
and both eras' `AutoClient` look up the credential with the raw model string:

- **v0.2.4** (`626d8609:.../auto_client.py`): eager —
  `if model not in deployment_api_keys: raise AuthenticationError(...)` before
  client construction. Any dotted model name dies here (once Finding 1 is
  fixed and the deployment branch is actually reached).
- **v0.3.0** (`8ea285f7:.../auto_client.py`): `provide_api_key` is wired as a
  `provider_binding` for the client's `api_key` parameter; because the era
  client_spec args do NOT contain `api_key` (Finding 5 / by design), the
  provider **fires during `inject_object_spec_args`** and raises the same
  `AuthenticationError`.

The flagship demo model `eleutherai/pythia-6.9b` has a dot; so do
`pythia-2.8b-v0`, `pythia-1.4b-v0`, etc. Most of the classic-track set is
affected.

**Fix (two parts, both needed).**

1. **v0.3.0 side — don't trigger the provider.** Add `api_key` to the era
   client_spec args in `_model_deployment_entry_era`
   (`eval_audit/integrations/infer_stack/bundle_export.py`). With `api_key`
   present in args, `inject_object_spec_args` skips the `provide_api_key`
   provider entirely and the credentials lookup never runs at v0.3.0. Use the
   literal `"EMPTY"` (the shim client already treats `"EMPTY"` as unset), or
   thread a real key when the endpoint needs one. NOTE: this partially
   reverses the current "no api_key in args (credentials.conf owns it)"
   comment — rewrite that comment: credentials.conf cannot own it, because
   pyhocon cannot address dotted model names.
2. **v0.2.4 side — the lookup is mandatory**, so credentials.conf must be
   written so that pyhocon's *path* lookup finds the key. Write the entry
   nested along the dot-split path, e.g. for `eleutherai/pythia-6.9b`:

   ```hocon
   deployments {
     "eleutherai/pythia-6" { "9b": "EMPTY" }
   }
   ```

   Generalize: `model.split(".")` → nested quoted segments (a model with no
   dot stays flat). Write a small helper `_hocon_nested_deployment_key(model,
   key)` with a unit-testable pure-string output, and test it against pyhocon
   directly in the host test suite (pyhocon IS importable on the host — add
   `tests/test_era_shim_hostside.py` exercising the written text with
   `ConfigFactory.parse_string` + a simulated `deps[model]` lookup).

**Verify.** Host-side unit test as above (this one is fully testable without
the image); then ladder step 3 with pythia-6.9b at v0.2.4.

---

## Finding 3 — CRITICAL: `wrap_request_time` import fails at v0.2.4 → the v0.2.4 image cannot build

**Where.** `docker/era_shim/helm_era_shim/openai_compat_client.py` line ~46
(module-level import block).

**Status.** CONFIRMED against era source.

**What happens.** The shim client does
`from helm.common.request import (..., wrap_request_time)`. At v0.2.4,
`wrap_request_time` lives in **`helm.proxy.clients.client`**
(`626d8609:src/helm/proxy/clients/client.py:43`); it only moved to
`helm.common.request` at v0.3.0 (`8ea285f7:src/helm/common/request.py:219`).
So importing the module raises `ImportError` at v0.2.4. The dockerfile's
final-stage assertion imports the client → the **ERA=helm-v0.2.4 build fails**
(loud, but the deliverable covering 85 of the 159 classic runs is unusable).

**Fix.** Version-tolerant import in the client module:

```python
from helm.common.request import Request, RequestResult, Sequence, Token
try:  # v0.3.0+: helm.common.request
    from helm.common.request import wrap_request_time
except ImportError:  # v0.2.4: helm.proxy.clients.client
    from helm.proxy.clients.client import wrap_request_time
```

(`Request/RequestResult/Sequence/Token` and `helm.common.cache.Cache/CacheConfig`
and `helm.proxy.clients.client.Client/truncate_sequence` were all verified to
exist at both eras — only `wrap_request_time` moved.)

**Verify.** The existing final-stage image assertion covers it once the
v0.2.4 image builds (ladder step 1 for BOTH eras, not just v0.3.0).

---

## Finding 4 — era↔image guard false-fails for digest-pinned era images not present locally

**Where.** `eval_audit/integrations/kwdagger_bridge.py`
`_prepare_container_execution` (~line 620, the `image_label(...)` call);
`eval_audit/integrations/docker_provenance.py` `resolve_image_digest` (the
`already_pinned` short-circuit) and `image_label`.

**Status.** CONFIRMED code-path.

**What happens.** `resolve_image_digest` short-circuits on a `@sha256:` ref
**without pulling**. `image_label` then runs `docker image inspect` on a ref
that may not exist locally → returncode != 0 → returns `None`. For an era
manifest, `None != manifest_era` raises
`era<->image mismatch ... carries org.aiq.era=None` with a misleading
"rebuild the era image" instruction — blocking a legitimate run. This bites
exactly the RECOMMENDED cross-machine form (digest-pinned); a mutable tag
works because the non-pinned branch pulls first.

Secondary issues in the same hunk:

- The label is read from `container_image` (the requested ref), not
  `resolved_image.run_ref` — a mutable tag retagged between resolve and
  inspect is a (small) TOCTOU window; `run_ref` is the immutable choice and is
  what provenance records.
- The inspect now runs on the **modern** path too: one wasted subprocess per
  schedule, and a behavior change — for a digest-pinned modern manifest on a
  host with no docker on PATH, `image_label → _runtime_bin` raises
  `RuntimeError` where the old code's `already_pinned` short-circuit never
  touched the runtime.

**Fix.**

1. In `image_label`, distinguish "label absent" from "image not inspectable":
   return a sentinel / raise on inspect failure, or have the caller attempt
   `docker pull` (best-effort, like `resolve_image_digest` does) before
   inspecting when the first inspect fails.
2. Read the label from `resolved_image.run_ref`.
3. Skip the label read entirely when `manifest.get("era")` is falsy AND the
   requested ref is digest-pinned (preserves the old no-runtime-needed
   behavior for pinned modern manifests). For unpinned modern refs the image
   is local post-resolve, so the absent-label check is cheap and still guards
   against pinning an era image to a modern manifest.

**Verify.** Extend `tests/test_eras_pipeline.py`: monkeypatch `image_label` to
simulate "inspect failed" vs "label absent" and assert the era path attempts
the pull/re-inspect (or errors with an actionable "image not present locally"
message, not "mismatch"), and that a pinned modern manifest performs no label
read.

---

## Finding 5 — era deployment defaults `base_url` to the auth-protected LiteLLM gateway with no usable credential

**Where.** `eval_audit/integrations/infer_stack/bundle_export.py`
`_model_deployment_entry_era` (~line 138:
`resolved_base_url = base_url or _default_gateway_base_url()`).

**Status.** CONFIRMED.

**What happens.** An era bundle exported without `--base-url` points the
deployment at the LiteLLM gateway. The shim client's `api_key` resolves from
`EVAL_AUDIT_ERA_API_KEY` defaulting to `"EMPTY"`, which the client treats as
unset → no `Authorization` header → every `/v1/completions` request 401s.
The modern `_model_deployment_entry` **explicitly forbids** this default for
the analogous unauthenticated client (`vllm-direct` "requires an explicit
--base-url ... must not default to the LiteLLM gateway").

**Fix.** Mirror the vllm-direct guard: era entries require an explicit
`base_url`; raise a ValueError naming the flag when it is absent. Delete the
`_default_gateway_base_url()` fallback from the era builder. (If gateway use
is ever wanted, it needs a real key threaded — do not silently default.)

**Verify.** Add a case to `tests/test_eras_hostside.py`:
`_model_deployment_entry_era(..., base_url=None)` raises.

---

## Finding 6 — era provenance env vars are read but never set → `replay.era` / `helm_git_ref` always null

**Where.** `docker/helm-runner-era.dockerfile` (~line 146: `ARG ERA_KEY` /
`ARG ERA_HELM_REF` exist for the LABEL only); reader:
`docker/era_shim/helm_era_shim/replay.py:149-150` + process-context env block.

**Status.** CONFIRMED.

**What happens.** `replay.py` reads `EVAL_AUDIT_ERA_KEY` /
`EVAL_AUDIT_ERA_HELM_REF` from the environment for the manifest's
`replay: {era, helm_git_ref}` block (a plan requirement). Nothing sets them:
the dockerfile declares ARG+LABEL but no ENV, and the docker node forwards only
`EVAL_AUDIT_ERA_API_KEY`. Every era run's `adapter_manifest.json` records
`replay.era: null, replay.helm_git_ref: null` — era identity survives only via
the image label and experiment-level provenance, not the per-run artifact.

**Fix.** In the final stage of `helm-runner-era.dockerfile`, after the ARG
declarations:

```dockerfile
ENV EVAL_AUDIT_ERA_KEY=$ERA_KEY \
    EVAL_AUDIT_ERA_HELM_REF=$ERA_HELM_REF
```

(ENV persists into `docker run`; ARG alone does not.)

**Verify.** Ladder step 1: `docker run --rm <era-image> python -c
'import os; assert os.environ["EVAL_AUDIT_ERA_KEY"]'`; step 3: check the
produced adapter_manifest.json `replay` block.

---

## Finding 7 — `_locate_run_dir` can return an arbitrary wrong directory; contains dead code

**Where.** `docker/era_shim/helm_era_shim/replay.py` `_locate_run_dir`
(~line 486).

**Status.** CONFIRMED logic.

**What happens.** After the exact-name check fails, the fallback scans the
suite dir. The rescan loop `for d in candidates: if d.name == sanitized:
return d` is dead (such a candidate would have matched the exact check). The
trailing `return candidates[0] if candidates else None` picks the first
*sorted* run dir — if the suite dir ever holds more than one valid run dir
(reused/mounted output_path), the manifest records `computed_run_dir` /
`computed_run_name` pointing at an **unrelated run**, corrupting provenance
silently.

**Fix.** Drop the dead loop. Auto-pick only when `len(candidates) == 1`;
otherwise return `None` (the caller already raises loudly).

**Verify.** The function is import-safe on the host (pure pathlib) — add a
tmp-path unit test to `tests/test_era_shim_hostside.py` (load the module via
`importlib.util.spec_from_file_location`, as the existing smoke check does).

---

## Finding 8 — `helm.benchmark.huggingface_registration` does not exist at v0.2.4 (conditional crash)

**Where.** `docker/era_shim/helm_era_shim/replay.py`
`_register_optional_hf_models` (~line 389).

**Status.** CONFIRMED (conditional — fires only when the flags are non-empty).

**What happens.** At v0.2.4, HF registration lives in
`helm.proxy.clients.huggingface_model_registry` with different names
(`register_huggingface_hub_model_config` / `register_huggingface_local_model_config`;
see `626d8609:src/helm/benchmark/run.py:268-271`). A v0.2.4 replay whose
manifest carries non-empty `enable_huggingface_models` /
`enable_local_huggingface_models` raises `ModuleNotFoundError` mid-run. Empty
flags (the standard era path) never reach the import.

**Fix.** Version-dispatch inside the `if hub:` / `if local:` blocks:

```python
try:    # v0.3.0+
    from helm.benchmark.huggingface_registration import (
        register_huggingface_hub_model_from_flag_value as _reg_hub)
except ImportError:  # v0.2.4
    from helm.proxy.clients.huggingface_model_registry import (
        register_huggingface_hub_model_config as _reg_hub)
```

(same for the local variant), or hard-error with a clear "not supported at
v0.2.4" message. Either is acceptable; silent divergence is not.

---

## Finding 9 — `parse_public_signal_from_run_dir` silently resolves to modern for track-rooted mirrors (PLAUSIBLE)

**Where.** `eval_audit/eras.py` `parse_public_signal_from_run_dir` (~line 186:
`public_track = parts[idx - 1] if idx >= 1 else None`).

**What happens.** If `benchmark_output` is the FIRST component of the joined
path (a mirror whose `--precomputed-root` IS the track dir, rel_paths starting
`benchmark_output/runs/v0.2.4/...`), `idx == 0` → `public_track=None` → the
classic match predicate fails → `resolve_era` returns None → `--era auto`
builds a **modern** manifest for a v0.2.4 run. The era↔image guard does not
trip (manifest era is None); the wrong instrument surfaces only when magnet's
v0.5 codec chokes on (or silently field-drifts) the pre-v0.5 spec.
Silent-wrong-instrument is the exact failure class this feature exists to
prevent. The explicit `--era <key>` pin is an escape hatch, but `auto` is the
documented default.

**Fix options** (pick one):
- When `suite_version` parses but `public_track` is None, and the
  suite_version alone would match exactly one era, fail loud ("cannot derive
  public_track from <path>; pass --era <key> explicitly") instead of silently
  returning modern.
- Or treat a missing track as wildcard-compatible when the suite_version is
  unambiguous across the registry.

The first (fail loud) fits the repo's design language better.

---

## Finding 10 — multi-endpoint era bundles lose their lease endpoints (PLAUSIBLE)

**Where.** `eval_audit/integrations/infer_stack/freeze.py` (~line 93:
`endpoint = lease_scalar if omit_model_deployment else (...)`).

**What happens.** `_lease_facts` emits a scalar `lease_endpoint` only for
single-endpoint bundles; multi-endpoint bundles get the `lease_endpoints`
map. Era mode ignores the map, so a multi-entry era bundle freezes NO
`lease_endpoint` into any source → the bridge emits no `helm.lease_endpoint`
→ the lease bracket never renders → the vLLM endpoint is never acquired for
those runs.

**Fix.** In era mode, key the map on the era entry's deployment name — which
IS the official `helm_model_name` (`entry["name"]`) — instead of dropping it:
`lease_map.get(<matching era entry name for this run's model>)`. The run-entry
→ model mapping is derivable from the run-entry token (`model=...`) or by
single-entry fallback. If that mapping is not worth building now, at minimum
raise on (era + multi-endpoint + lease_endpoints-map) instead of silently
freezing nothing.

---

## Below the cap (worthwhile cleanups, not blocking)

- **`protocol_mode` required-but-unused for era presets**
  (`bundle_export.py` — the required-check sits before the era fork; the era
  builder never reads it). Either skip the check when `resolved_era` is set,
  or validate it is `completions` (the only mode the era client implements) —
  the latter turns dead config into a real assertion.
- **Era rows get null `process_stop_timestamp`/`process_duration`** —
  the shim's `_capture_process_context` runs once before the replay (no
  stop/duration), while the modern CLI's kwutil ProcessContext records both;
  `index_results.py` reads them. Cheap fix: re-write process_context.json
  after the replay with `stop_timestamp`/`duration` filled. (The dropped
  `nvidia_smi` block is a non-issue: the era image has no nvidia-smi and no
  GPU work in-container.)
- **Duplicated `benchmark_output` path-convention parser** —
  `eras.py:parse_public_signal_from_run_dir` vs
  `workflows/compare_batch.py:parse_helm_run_dir` (they even disagree on
  fallbacks). Extract one shared parser; era resolution silently picking the
  instrument makes parser drift dangerous (see Finding 9).
- **`docker/read_eras.py` duplicates registry defaults** (`helm_extras='all'`,
  `capability='era-shim-from-spec'`) that `eras.py:_parse_era_spec` also
  hardcodes. Cheapest fix: make both fields explicit-required in `eras.yaml`
  (they are already written there) and drop the code defaults from both
  readers.
- **No `requests.Session` reuse in the shim client** — a 1000-instance run
  opens a fresh TCP (and TLS, if https) connection per completion. Create
  `self._session = requests.Session()` in `__init__` and post through it.
- **Duplicated `docker build` invocation in `build.sh`** (era vs modern arms
  share everything except `--file` and the build-arg set) — fold into one
  invocation with a `BUILD_ARGS` array to prevent drift.
- **Minor simplifications:** `resolve_era_for_sources`'s `seen` dict dance
  (`seen[None] = seen.get(None, None)` is just `setdefault`), the nested
  `rewrite_deployment` ternary in `bundle_export.py`, `_coerce_str_list`'s
  redundant json-then-literal_eval double loader, and
  `_completions_endpoint`'s unreachable `/completions` branch.

## Explicitly cleared during review (do not "fix")

- `PANDAS_PIN`/`NUMPY_PIN` ARGs DO reach `os.environ` inside the quoted
  `<<'PY'` heredoc (BuildKit exports build ARGs into the RUN environment).
- The era dockerfile's `COPY constraints.txt` before the heavy install is
  required (the install consumes it), not cache thrash.
- The registry double-load (builders + bridge) hits the `lru_cache` — no
  repeated parsing.
- `uuid4()`/`time.time()` in the shim's process context mirrors the magnet
  CLI (provenance artifact, not pipeline output) — intentional parity, not a
  determinism violation.
- v0.2.4 `ModelDeployment` requires exactly the five keys the era entry emits
  (`name`, `model_name`, `client_spec`, `max_sequence_length`,
  `tokenizer_name`) — the cattrs shape is correct as written.

## Suggested fix order / commit grouping

1. **Shim era-compat fixes (one commit):** Findings 1, 2 (shim half), 3, 7, 8
   — all inside `docker/era_shim/`, plus the new host-side
   `tests/test_era_shim_hostside.py` (pyhocon nested-key helper test,
   `_locate_run_dir` test — both host-importable).
2. **Host-side export fixes (one commit):** Finding 2 (bundle_export half:
   `api_key` in era client_spec args + comment rewrite), Finding 5 (require
   explicit base_url), Finding 10 (lease map or loud error) + test updates in
   `tests/test_eras_hostside.py`.
3. **Bridge/guard fixes (one commit):** Finding 4 (pull-or-distinguish, read
   label from `run_ref`, skip for pinned-modern) + `tests/test_eras_pipeline.py`
   extensions.
4. **Dockerfile ENV (one commit, or fold into 1):** Finding 6.
5. **Resolver hardening (one commit):** Finding 9 (fail loud on
   track-less era-suite paths) + `tests/test_eras.py` case.
6. Cleanups from "Below the cap" as a separate pass, optional.

After fixes: re-run `tests/test_eras*.py` + the touched-area suites
(`test_run_spec_materializer.py`, `test_exporter_freeze.py`,
`test_from_spec_materialized_schedule.py`, `test_make_manifest_sources.py`,
`test_kwdagger_submatrix_contract.py`), then walk the validation ladder for
BOTH eras (step 1 alone would have caught Findings 3 and 6; step 3 catches
1, 2, 5).

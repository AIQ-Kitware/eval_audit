# Running HELM from `run_spec.json` (faithful replay) plan

**Status (2026-08-06): IMPLEMENTED — this is the production execution path.**
Landed as `--from-spec` / `--freeze-rel-paths` in
`eval_audit/integrations/infer_stack/__main__.py` and the `from_run_spec`
exact-path replay in `eval_audit/integrations/kwdagger_bridge.py`; every audit
result the paper reports was produced this way (see the top-level README).
The plan below is kept as the design record.
**Decision:** Placement **Option A (aiq-magnet)**; substitution **by-name only**.
**Context:** Public HELM runs do not record the CLI run-entry string (nor the
crfm-helm library version) they were produced with. They *do* ship a fully
resolved `run_spec.json`. This plan adds a second execution path that replays
that `run_spec.json` directly via HELM's `from_json` + `Runner`, instead of
reconstructing a run-entry string and re-parsing it through `helm-run`.
**Method:** read the vendored HELM source under `submodules/helm/`, the magnet
materializer, the eval_audit container pipeline + bridge + manifest, and real
public artifacts under `/data/crfm-helm-public`. 2026-06-24.

---

## 1. Why this path (the central insight)

A run-entry string (`mmlu:subject=...,model=X`) is the *pre-expansion* input:
`helm-run` feeds it through `construct_run_specs` + run-expanders, which
**derive** `metric_specs`, fill `adapter_spec` defaults, and apply `model=`
expansion using whatever the *currently installed* crfm-helm version's defaults
are. `run_spec.json` is written **after** all of that
(`submodules/helm/src/helm/benchmark/runner.py:337`, via `asdict_without_nones`)
— it is the **fully resolved** recipe: exact `scenario_spec`, `adapter_spec`,
`metric_specs`, `annotators`, `data_augmenter_spec`.

So reconstructing a string and re-parsing it (today's
`reconstruct_run_entry_from_run_spec` in `eval_audit/run_entries.py`)
re-derives the recipe under today's library defaults — a silent drift surface.
Replaying the `run_spec.json` directly replays the official recipe verbatim. For
a paper whose claim is "same recipe → same metrics," replaying the resolved spec
is the defensible choice.

**Key elegance of the integration:** in discovery mode the run-entry string's
*only* remaining job is to **locate** the official run dir (with an explicit
`--run-spec-json` path you skip even that). The authoritative `run_spec.json`
then drives execution. We stop feeding reconstructed strings to HELM's parser
entirely. Directory-name matching (what `find_best_precomputed_run` already does)
is robust; it is the *parser* round-trip that was fragile.

### Deserialization is already supported

HELM ships a symmetric cattrs codec, `helm.common.codec.from_json`
(`submodules/helm/src/helm/common/codec.py`). Its structure hooks specifically
re-insert the `None`s that `asdict_without_nones` drops on write
(`codec.py:85-95`), so `from_json(open(run_spec_path).read(), RunSpec)`
round-trips a real public `run_spec.json` against a current-shape crfm-helm.

### The failure modes: class drift is loud, field drift is silent

Neither the crfm-helm **library version** nor the dataset version is recorded in
any run artifact (confirmed against `/data/crfm-helm-public` — the only version
token is the per-leaderboard `runs/<suite>/` directory name, a weak proxy). There
are two distinct drift surfaces, and they fail very differently:

- **Class availability — loud.** A renamed or not-yet-existing
  scenario/metric/annotator class surfaces as a clean `ImportError` naming the
  exact missing class (`get_class_by_name` is pure `importlib` —
  `submodules/helm/src/helm/common/object_spec.py:30`). We convert this into an
  explicit preflight (see §4).

- **Field/shape drift — silent.** The codec is a plain `cattrs.Converter()`
  (`codec.py:67`); cattrs by default **ignores unknown keys and fills missing
  optionals with their defaults** rather than erroring. So a public spec written
  by an older crfm-helm that carries a since-renamed/removed `adapter_spec` (or
  metric/annotator) field deserializes *successfully* — the old field is dropped
  and the new field silently takes its current default. That is a quiet recipe
  change, precisely the drift this path exists to remove. `from_json` round-trips
  the *current* shape faithfully; it cannot detect that the *source* shape
  differed. The §9 round-trip test must therefore assert that **no key in the raw
  JSON is silently dropped** (compare parsed-and-re-serialized keys against the
  raw keys), not merely that parsing succeeds.

---

## 2. Constraints discovered (what shapes the design)

1. **The pipeline is keyed on run-entry strings end to end.**
   `run_specs.yaml` → `eval_audit/manifests/builders.py` → `manifest["run_entries"]`
   → bridge matrix `helm.run_entry`
   (`eval_audit/integrations/kwdagger_bridge.py:161`) → docker node renders
   `--run_entry=<string>` → magnet materializer runs
   `helm-run --run-entries <string>`.

2. **The runtime image `eval-audit-helm-runner` installs only `helm[heim]` +
   `aiq-magnet` — not `eval_audit`** (`docker/helm-runner.dockerfile:79-88`;
   default CMD `python -m magnet.backends.helm.cli.materialize_helm_run`). Any
   new in-container CLI must be importable from `magnet` or `helm`, or the image
   must gain `eval_audit` (rejected — see §3).

3. **Deployment substitution already works by name.**
   `configs/debug/repro_model_overrides.yaml` re-registers the *same* deployment
   name (e.g. `together/qwen2.5-7b-instruct-turbo`) pointed at a local
   `HuggingFaceClient`. `helm-run` loads it via
   `register_configs_from_directory(local_path)` (`run.py:285`). **→ the replay
   path needs no `adapter_spec` model rewrite — registering the override file is
   enough.**

4. **The official `run_spec.json` is already delivered into the container.** The
   docker node bind-mounts `precomputed_root` read-only
   (`eval_audit/pipelines/helm_docker_pipeline.py:179-182`), and
   `find_best_precomputed_run`
   (`submodules/aiq-magnet/.../materialize_helm_run.py:1129`) already locates the
   matching official run dir by directory-name token-subset matching.

5. **HELM's own `run_benchmarking` does the execution wiring.** The replay driver
   mirrors the `helm_run(args)` preamble (`run.py:281-320`) and calls
   `run_benchmarking([run_spec], ...)` (`run.py:153`) — leaning on HELM public
   functions, minimal new surface. That preamble is the source of truth for
   *which* registration steps must run and in what order (§4) — notably
   `load_entry_point_plugins()` (`run.py:287`), which the replay must not skip.

---

## 3. Decision: placement = Option A (aiq-magnet)

New file: `submodules/aiq-magnet/magnet/backends/helm/cli/materialize_helm_run_from_spec.py`
— a sibling of `materialize_helm_run.py`.

**Why A over putting it in eval_audit.** The CLI must run inside the pinned
container (containerization is mandatory). The question is whether to add a
module to a package already in the image (magnet) or pull a new package into the
image (eval_audit).

- **Option A (magnet) — chosen.** Zero image-environment change: the image
  already `uv pip install -e`'s magnet, so the new module is automatically
  importable and the image stays byte-identical except for the new file →
  lowest risk of perturbing the deliberately-pinned `helm[heim]` env. Direct
  helper reuse via plain import. Execution code is pure-helm (`from_json` /
  `run_benchmarking` / `Runner`) → no new dependency anywhere. Symmetric with the
  existing run-entry variant.
  - Cost: a submodule commit + a deliberate gitlink bump in the superproject
    (do **not** auto-commit the gitlink). Wider blast radius if other projects
    vendor magnet.

- **Option B (eval_audit) — rejected.** Single-repo change and a tidier
  conceptual home, but it forces installing eval_audit into the runner image
  (Dockerfile `COPY` + `pip install`), dragging in a heavy closure (pandas,
  matplotlib/plotting, kwdagger, the reporting stack). That bloats the image,
  slows worker pulls, and risks shifting the pinned helm env — an open-ended risk
  against the one environment this project is most careful about. Avoiding it
  would require carving a slim extras split that does not exist today.

**The reframe that removes A's main objection:** only the *mechanical replay*
("deserialize this `run_spec.json`, register the override, run it") lives in the
magnet CLI — that is generic helm/magnet work, the same kind as the existing
run-entry replay. All *audit semantics* stay in eval_audit: which spec to replay
(bridge/manifest) and how the substitution + recipe-grade facts are recorded
(`recipe_facts.py`, the comparability layer, `adapter_manifest.json` consumed by
Stage 4 indexing). Magnet placement moves no audit concern into magnet.

---

## 4. Change 1 — new CLI `materialize_helm_run_from_spec.py` (magnet)

A near-clone of the original's *scaffolding* that imports its helpers and swaps
only the compute step:

```python
from magnet.backends.helm.cli.materialize_helm_run import (
    find_best_precomputed_run, find_run_in_out_dpath,
    prepare_local_helm_config, _capture_process_context,   # reuse verbatim
)
```

**Config** `MaterializeHelmRunFromSpecConfig` — same fields as today
(`run_entry`, `suite`, `out_dpath`, `precomputed_root`, `max_eval_instances`,
`local_path`, `model_deployments_fpath`, `enable_local_huggingface_models`,
`enable_huggingface_models`, `num_threads`, `mode`, `require_per_instance_stats`,
`done_fname`, `manifest_fname`), **plus a new `run_spec_json` input** (explicit
path to a `run_spec.json`). **No** `--model-deployment` / `--model` args —
substitution is by-name only (§5).

**Two input modes (dual-input).** The CLI accepts the recipe either way; the
explicit path wins when both are given:

- **Explicit:** `--run-spec-json <path>` → read that file directly, skip matching
  entirely. Works on *any* spec file — a modified/hand-authored spec, a
  non-public spec, an EEE sidecar `run_spec.json`, or one outside a
  `benchmark_output/` tree. This is the most faithful form ("you hand me the
  source of truth").
- **Discovery:** `--run-entry <str>` + `--precomputed-root <root>` → locate via
  `find_best_precomputed_run` and read the matched dir's `run_spec.json`. This is
  what the eval_audit pipeline uses (it is keyed on run-entry strings and the
  manifest carries no per-entry paths), so the dual-input is **additive** —
  Changes 2–5 are unchanged and keep using discovery.

Require exactly one mode; if neither resolves a spec → hard error (cannot replay
without the recipe; in discovery mode `precomputed_root` is the recipe source,
not just a reuse cache).

**Flow** (differs from the original only at the compute step). The registration
preamble must run **before** the preflight so the preflight resolves classes in
exactly the environment the run will use (entry-point plugins contribute
scenarios / run-spec functions / model metadata; skipping them both breaks the
run *and* could make the preflight false-positive on a plugin-provided class):

1. **Resolve the recipe path:** if `run_spec_json` is set, use it; else
   `match = find_best_precomputed_run(precomputed_root, run_entry, ...)` and use
   `match.run_dir / "run_spec.json"`.
2. **Deserialize:** `run_spec = from_json(text, RunSpec)`.
3. **Prepare local config:** `prepare_local_helm_config(...)` copies the override
   yaml into `<local_path>/model_deployments.yaml`.
4. **Register the full environment (mirror the `helm_run` preamble verbatim,
   `run.py:284-301`):** `register_builtin_configs_from_helm_package()` →
   `register_configs_from_directory(local_path)` → **`load_entry_point_plugins()`**
   (`run.py:287` — do not skip) → `import_user_plugins(...)` if any →
   enable-HF-models registration (`run.py:292-301`). Same calls, same order as
   `helm_run`, so the replay environment is identical to a normal `helm-run`.
5. **Preflight (version-drift guard):** now that everything is registered, resolve
   every `class_name` reachable from the spec via `get_class_by_name` —
   `scenario_spec`, `metric_specs[]`, `annotators[]`, **recursing into nested
   `ObjectSpec` values inside each spec's `args`** (annotators reference judge
   model specs; some metric specs nest sub-specs — a top-level-only scan misses
   them). On any `ImportError`, fail fast listing the exact unresolved classes —
   turns a mid-run crash into an actionable "this crfm-helm build lacks class X"
   message.
6. **Substitute (minimal):** if `max_eval_instances` is set,
   `adapter_spec = dataclasses.replace(adapter_spec, max_eval_instances=N)` and
   `run_spec = dataclasses.replace(run_spec, adapter_spec=adapter_spec)`. No
   model/deployment rewrite (§5).
7. **Run:** `set_benchmark_output_path(<out>/benchmark_output)`, then
   `run_benchmarking([run_spec], auth=Authentication(""), url=None,
   local_path=str(local_path), num_threads=..., output_path=<out>/benchmark_output,
   suite=..., dry_run=False, skip_instances=False, cache_instances=False,
   cache_instances_only=False, skip_completed_runs=False, exit_on_error=True,
   runner_class_name=None)`.
8. **Locate + finalize:** `find_run_in_out_dpath(...)` to find the produced dir;
   write `adapter_manifest.json` (status, located official recipe path, applied
   `max_eval_instances`) + `DONE` last.

**Forensics parity (and its limit):** the original runs `helm-run` as a
subprocess and persists `cmd_stdout.txt` / `cmd_stderr.txt`. This path is
**in-process**, so wrap step 7 in try/except and write the traceback to
`cmd_stderr.txt` so the existing failure classifier
(`eval_audit/cli/summarize_experiment_failures.py`) still has content. **Limit:**
a Python exception is caught and recorded, but a *hard* crash (CUDA OOM-kill,
segfault) takes down the in-process CLI before the except block runs, so
`cmd_stderr.txt` may be empty where the subprocess path would have captured the
child's stderr. Failure is still detected — the CLI exits non-zero, the container
exits non-zero, and kwdagger records the run as failed (no `DONE`) — but the
classifier has thinner content on hard crashes. Acceptable for v1 given these are
GPU runs where OOM is the common hard-crash and the missing-`DONE` signal already
flags it; revisit only if the thin forensics prove to obscure real triage.

---

## 5. Change — substitution is by-name only (decided)

> **AMENDED (2026-06-25) by
> [`from-spec-deployment-rewrite-plan.md`](../historical/planning/from-spec-deployment-rewrite-plan.md).**
> The CLI now also accepts an **optional** `--model-deployment <local-name>` that,
> after deserialization, rewrites `adapter_spec.model_deployment` to that local
> name (the by-name override registers the local name; the rewrite points the spec
> at it). `adapter_spec.model` is still never touched. The **default stays pure
> by-name** (no rewrite) — so the general path below is unchanged — but the e2e
> (and any audit that substitutes a local engine) opts in so the produced run
> records the local deployment and the comparison reports `same_deployment=no`
> instead of masking the substitution behind the official name. The judge/annotator
> "sharp edge" below is unaffected (judge deployments are still by-name; the rewrite
> targets only the primary `model_deployment`).

The CLI never rewrites `adapter_spec.model` / `model_deployment`. The official
`run_spec.json` keeps its deployment name (e.g.
`together/qwen2.5-7b-instruct-turbo`); the locally-registered override
(`model_deployments.yaml`) binds that name to a local `HuggingFaceClient`. A
future model needing substitution is handled by **adding an entry to the override
yaml**, not by CLI surface.

**By-name applies to judge/annotator deployments too — and this is the sharp
edge.** `run_spec.json` carries `annotators[]`, and for judge-dependent metrics
those annotators reference a *judge model deployment* (e.g. an OpenAI judge),
which is a **separate** deployment name from the primary model. By-name
substitution rebinds only the names present in the override yaml, so a verbatim
annotator spec will, unless its judge deployment is *also* overridden, attempt to
call the original (closed) judge API — needing credentials, or silently scoring
with a *different* judge than the local open-judge recipe intends. This is the
crux of the open-judge story (`eval_audit/judge_registry.py`,
`docs/planning/judge-identity-inventory.md`): faithful local replay of a
judge-dependent run requires override entries for the judge deployment(s) as well,
exactly as for the primary model. v1 does nothing special here (by-name is
uniform); the requirement is documented as an open item (§10) and is a recipe-fact
the comparability layer already surfaces via the `same_judge` scope (§7).

Consequence: the produced run dir name == official `run_spec.name`, so indexing,
`logical_run_key`, and planner pairing are untouched (§7).

---

## 6. Changes 2-5 — eval_audit pipeline wiring

**Change 2 — docker node + factory** (`eval_audit/pipelines/helm_docker_pipeline.py`):

```python
class MaterializeHelmRunFromSpecDockerNode(MaterializeHelmRunDockerNode):
    executable = 'python -m magnet.backends.helm.cli.materialize_helm_run_from_spec'
def helm_single_run_from_spec_docker_pipeline(): ...
```

Inherits mounts, `out_paths`, `primary_out_key='done_fname'`, and identity
unchanged. (Do **not** add `model` to `algo_params` — the model identity always
replays verbatim. **Amended:** the deployment-rewrite plan *does* add
`model_deployment` to the from-spec node's `algo_params` — the optional
rewrite target, default `None`/by-name; see
[`from-spec-deployment-rewrite-plan.md`](../historical/planning/from-spec-deployment-rewrite-plan.md)
Change 3.) The `precomputed_root` `:ro` mount already delivers the official
`run_spec.json` into the container.

**Change 3 — bridge** (`eval_audit/integrations/kwdagger_bridge.py`): add
`_DOCKER_FROM_SPEC_PIPELINE`. `build_schedule_params` already **requires a
container image** — it raises when `resolved_image is None`
(`kwdagger_bridge.py:182-187`; the bare host-venv pipeline was removed by commit
`4158bea`) and then returns `_DOCKER_PIPELINE`. So the from-spec selection is just
a variant of that final return: pick `_DOCKER_FROM_SPEC_PIPELINE` vs
`_DOCKER_PIPELINE` based on `manifest.get("from_run_spec")`. Because the selection
sits **after** the `resolved_image is None` raise, `from_run_spec: true` with no
image is rejected by the *existing* guard — **no new containerization guard is
needed** (the replay path inherits mandatory containerization for free, which is
also what keeps the engine pinned/attested; see §8). The pipeline always uses
**discovery** (it is keyed on run-entry strings; the explicit `--run-spec-json`
input from §4 is for standalone/ad-hoc use), so still **require `precomputed_root`**
when `from_run_spec` is set (it is the recipe source) and raise a clear error if
absent.

**Change 4 — manifest** (`eval_audit/manifests/models.py`): add
`from_run_spec: bool = False`, mirroring how `container_image` gates
containerization.

**Change 5 — make-manifest** (`eval_audit/manifests/builders.py`): add
`--from-run-spec` and **`--precomputed-root`**, plumbed into `_build_manifest`.
Note the plumbing **downstream of the manifest already exists**: `precomputed_root`
is an existing `ManifestSpec` field (`models.py:20`), already flows into the bridge
matrix as `helm.precomputed_root` (`kwdagger_bridge.py:163`), and is already
`:ro`-mounted by the docker node (`helm_docker_pipeline.py:177-180`). Today
builders.py simply never *populates* it (it relies on the magnet node default
`/data/crfm-helm-public`). So Change 5 is purely the two new argparse flags +
`_build_manifest` wiring; only `from_run_spec` needs a new field (Change 4) —
`precomputed_root` does not.

---

## 7. Identity / comparison (what deliberately does NOT change)

The local run dir name stays the official `run_spec.name` (no `model` rewrite),
so Stages 4-6 (`index`, `logical_run_key`, planner pairing) are untouched — the
run-entry string remains the logical key. The replay run can advertise
*grounded* `same_recipe` facts (read from the real official `run_spec.json`)
rather than reconstructed ones — a strict upgrade to the comparability story
(see `eval_audit/normalized/recipe_facts.py` and
[`phase3-comparison-core-unification.md`](phase3-comparison-core-unification.md)),
not a disruption.

Optional enhancement (not in v1): hash the official `run_spec.json` into the
node's algo identity so a changed official recipe forces recompute.

---

## 8. Change 6 — image rebuild & re-pin

Because the new module is in magnet, the only image action is a rebuild:

1. Commit `materialize_helm_run_from_spec.py` in the `aiq-magnet` submodule
   (`docker/build.sh` `git-archive`s magnet HEAD, so the file must be committed —
   or use the worktree-copy build path).
2. `./docker/build.sh` → capture the new `@sha256:` digest.
3. Re-pin via `eval-audit-run --container-image` / the preset `container_image`.
   `container_image` is part of algo identity, so this naturally forces recompute.
4. Deliberately bump the magnet gitlink in the superproject (separate, explicit
   commit — not auto).

---

## 9. Change 7 — tests

- **Schema-drift guard (unit):** `from_json(run_spec.json, RunSpec)` round-trips
  across a sample of real public specs (mmlu, mmlu_pro, narrative_qa, the
  call-center annotator spec) under `/data/crfm-helm-public`. Beyond "parsing
  succeeds," assert **no raw key is silently dropped**: re-serialize the parsed
  spec and diff its key set against the raw JSON's key set (recursively), so the
  silent field-drift mode from §1 is caught rather than masked by cattrs' default
  unknown-key tolerance. Plus a unit test for the class-path preflight (§4 step
  5), including a spec with a **nested** annotator/judge `ObjectSpec` to confirm
  the recursion resolves nested `class_name`s (and fails loudly on a bogus one).
- **Integration (CPU):** tiny fixture `run_spec.json` + `openai/gpt2`,
  `max_eval_instances=2`; assert a complete run dir + `DONE`.
- **Parity artifact:** run both paths (string vs from-spec) on one run_entry and
  diff the produced `run_spec.json` / `stats.json` — quantifies the recipe drift
  the replay path removes (a result for the methodology section).
- **e2e:** add a from-spec variant to `dev/e2e-tests/` behind a flag, mirroring
  the existing container example.

---

## 10. Open items / risks

- **Version coupling.** Pin the helm submodule to the era of the runs being
  replayed; the preflight (§4 step 5) makes class mismatches explicit but cannot
  manufacture a missing class — and cannot catch silent *field* drift (§1), which
  is why the §9 round-trip test asserts no raw key is dropped. Document in the
  methodology that full byte-faithful pinning always requires external
  cross-reference (no self-describing version stamp exists in the artifacts).
- **Primary `model_deployment` rewrite — DONE (2026-06-25).** The optional
  `--model-deployment` rewrite landed (see the §5 amendment and
  [`from-spec-deployment-rewrite-plan.md`](../historical/planning/from-spec-deployment-rewrite-plan.md)):
  the produced run now records the **local** deployment, so the comparison reports
  `same_deployment=no` instead of masking the engine substitution. Default stays
  pure by-name; the e2e opts in.
- **Judge/annotator deployment substitution (§5).** A judge-dependent
  `run_spec.json` names a judge model deployment distinct from the primary model.
  By-name substitution rebinds it only if the override yaml has an entry for that
  judge deployment; otherwise replay hits the original (closed) judge or scores
  with the wrong judge. v1 ships the uniform by-name mechanism and documents the
  requirement; a curated official-judge → local-judge override set (sourced from
  `judge_registry.py`) is the natural follow-up so judge-dependent runs replay
  faithfully end to end. The primary-model rewrite above is the template — a
  judge-deployment rewrite is the analogous next step (rewrite-plan §7).
- **In-process hard-crash forensics (§4).** A Python exception is captured to
  `cmd_stderr.txt`, but an OOM-kill/segfault bypasses the except block, leaving
  thin forensics (failure is still flagged by non-zero exit + missing `DONE`).
  Acceptable for v1; revisit only if triage proves blind.
- **`max_eval_instances` policy.** When set, it truncates (replace
  `adapter_spec.max_eval_instances`), matching the string path's
  `--max-eval-instances` semantics. When unset, replay verbatim. A local cap
  below the official count compares on HELM's deterministic instance prefix — same
  as today.
- **Internal reuse.** v1 delegates "already computed locally" to kwdagger
  `skip_existing` (the `DONE` sentinel); `precomputed_root` is repurposed as the
  recipe source. A separate local reuse root can be added later if needed.

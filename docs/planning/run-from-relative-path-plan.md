# Addressing reproductions by `(public_root, relative_path)` — plan

**Status:** PLAN (not implemented).
**Goal:** stop using the **run name** (the HELM run-entry string) as the
identifier that *locates* the official run to reproduce. Instead, **before
kwdagger runs**, the eval_audit tools resolve each run to a **real
`run_spec.json` on disk** — `(public_root, relative_path)` → an absolute path —
and hand the scheduler a per-run **list of absolute spec paths** to replay
(zipped with each run's lease endpoint via a kwdagger **submatrix**). Field
substitutions (`model_deployment`, `max_eval_instances`) apply as today. The run
name is a drifty *locator*; an exact path resolved up-front is not.
**Method:** read the from-spec replay CLI, the bridge/manifest wiring, the lease
bracket, kwdagger's grid expander (`util_param_grid.extended_github_action_matrix`),
and the live corpus layout under `/data/crfm-helm-public`. 2026-06-30.

**Builds on / relates to:**
- [`run-from-run-spec-json-plan.md`](run-from-run-spec-json-plan.md) — the
  from-spec replay path. Its explicit `--run-spec-json <path>` mode is **already
  in the pinned runner image**; this plan drives that mode from the host, so **no
  magnet change and no image rebuild are required**.
- [`from-spec-deployment-rewrite-plan.md`](from-spec-deployment-rewrite-plan.md)
  — the in-container `--model-deployment` rewrite is reused **unchanged** (now
  passed per-run via a submatrix instead of a single manifest value).
- [`olmo-from-run-spec-migration-plan.md`](olmo-from-run-spec-migration-plan.md)
  — the single-model from-spec migration; relative-path addressing is the next
  evolution of its discovery step.
- **Supersedes (for the fan-out case)**
  [`olmo-multi-model-from-spec-plan.md`](olmo-multi-model-from-spec-plan.md):
  that plan exists *only* to make run-entry **token-subset discovery** survive
  multi-model manifests (the `local_deployment_names` strip rule, its dry-check
  mirror, the negative guard). Resolving the exact path up-front removes
  discovery entirely, so there is nothing left to strip — see §6.

---

## 1. Why this path (the central insight)

A HELM run-entry string (`mmlu:subject=philosophy,model=openai/gpt2,...`) plays
**three** distinct roles today. Only one is fragile:

| role | mechanism | drift-prone? |
|---|---|---|
| **Locator** — find the official run dir whose `run_spec.json` we replay | `find_best_precomputed_run` → `run_dir_matches_requested` *token-subset match* over the whole corpus, **in-container at run time** | **YES — this is the drift** |
| **Lease key** — which local server each run uses | `_parse_model_deployment` reads `model_deployment=` off the same string (`lease_bracket.py:90`) | no — exact token parse |
| **Logical / pairing key** — group local↔official, name the produced run dir | produced dir name == `run_spec.name`; Stage 4–6 pairing keys off it | no — derived from the real spec |

Token-subset matching is fragile in three documented ways: order/variant
sensitivity (the canonical-logical-key work exists because of it),
`model_deployment=` collisions (the entire multi-model plan exists because of
it), and library-version normalization of the official dir name. **All three live
in the *locator* role.**

**The fix has two moves, one per user instruction:**

1. **Resolve *and substitute* before kwdagger, on the host.** The official corpus
   is laid out as
   `<public_root>/<suite>/benchmark_output/runs/<version>/<run_name>/run_spec.json`
   (e.g. `lite/benchmark_output/runs/v1.0.0/wmt_14:.../run_spec.json`). The tuple
   `(public_root, relative_path)` names that file **exactly**. The eval_audit
   tools — where there is full Python + filesystem access — read it, apply the
   declared field substitutions as **raw-JSON edits** (set
   `adapter_spec.model_deployment` to the local name, `adapter_spec.max_eval_instances`
   to the cap), and write a **materialized substituted copy** to a staging dir on
   disk. A bad address fails **loud, early, on the host**, not mid-run inside a
   container. Discovery stops being a run-time, in-container token scan and becomes
   a schedule-time, host-side resolve-and-materialize.

2. **Hand kwdagger a list of materialized specs.** The scheduler matrix's one
   per-run axis becomes the **absolute path to the run's materialized
   `run_spec.json`** — no run-entry, no token relation, no version-relative
   normalization, and already fully substituted. The existing `--run-spec-json
   <path>` replay mode consumes it verbatim (no in-container rewrite needed).

`public_root` is the per-machine mount (it already varies across
yardrat/namek/aiq-gpu); `relative_path` is the stable, drift-free identifier,
and its `<version>/` token pins the leaderboard release on purpose. We keep the
run-entry string only as an optional **label/provenance** value — its locator and
lease roles are both retired (§4).

---

## 2. Constraints discovered (what shapes the design)

1. **kwdagger cross-products plain axes, but `submatrices` zip.** The grid
   expander (`extended_github_action_matrix`) takes the Cartesian product of
   `matrix:` axes — two length-N axes ⇒ **N×N jobs**. So per-run fields that must
   travel *together* (a run's spec path *and* its lease endpoint *and* its
   deployment) cannot be parallel plain axes. kwdagger provides the zip primitive:
   **`submatrices`** is a *list of dicts, one per job*; a submatrix dict whose
   shared original-matrix keys agree with the base item merges into it and yields
   one job (`util_param_grid.py:731` `submatrix_variants`). A `submatrices` list of
   N complete per-run dicts over a singleton base ⇒ **exactly N jobs**, each =
   broadcast singletons + that run's fields. This is the carriage (§4.2).

2. **`--run-spec-json` and scalar `lease_endpoint` are already shipping.** The
   from-spec CLI's explicit-path mode and the lease bracket's scalar
   `lease_endpoint` path (`_resolve_lease_endpoint` returns `cfg["lease_endpoint"]`
   when no map is set, `lease_bracket.py:130`) are **already in the pinned image**.
   Driving them per-run from the host needs **no in-container code** ⇒ no magnet
   change, no gitlink bump, no rebuild. Every change below is host-side eval_audit.

3. **Materialized copies relocate the recipe source from the corpus to a tiny
   staging dir.** Because substitution happens on the host, the container reads the
   run's recipe from the **materialized copy**, not the public corpus. So the
   runner box no longer needs the (large) `/data/crfm-helm-public` corpus mounted
   at all — only the small staging dir of materialized specs, bind-mounted `:ro` at
   an identical host=container path (the same mechanism the docker node already uses
   for `precomputed_root`, `helm_docker_pipeline.py:177`). This both adds the
   staging mount and lets the from-spec path **drop the `precomputed_root` mount**.

3b. **Raw-JSON substitution needs no HELM on the host and cannot drift.** The
   materializer edits only the two scalar fields and re-dumps; it does **not**
   round-trip through HELM's cattrs codec, so it preserves every other key
   byte-for-byte (no silent field-drop — the failure mode `run-from-run-spec-json-plan.md`
   §1 warns about). The pinned in-container HELM still deserializes the copy at run
   time, so the image remains the source of *execution* truth; the host only does
   scalar edits. A consequence: the in-container `--model-deployment` /
   `--max-eval-instances` rewrite is **not exercised** on this path (the copy is
   pre-substituted) — it stays available for other callers, unchanged.

4. **Identity / pairing is independent of the locator.** `adapter_spec.model` is
   never rewritten, so the produced run dir name stays `run_spec.name`. Stages 4–6
   (`index`, `logical_run_key`, planner pairing) are untouched regardless of how
   the spec was located. This plan changes *addressing*, not *comparison*.

5. **The from-spec docker node does not yet declare `run_spec_json`.** Its
   `algo_params` are the base run-entry params plus `model_deployment`
   (`helm_docker_pipeline.py:245`). Threading a per-run spec path needs
   `run_spec_json` added there (eval_audit-side; the magnet CLI already accepts the
   flag — constraint 2).

---

## 3. Decision: host-side resolve-and-materialize + submatrix zip (Option C)

**On the host, at schedule time**, resolve each run's `(public_root, rel_path)` to
the official `run_spec.json`, apply the field substitutions as raw-JSON edits, and
write a **materialized substituted copy** to a staging dir. Then emit the per-run
tuple `(run_spec_json=<materialized copy>, lease_endpoint, run_entry-label)` as one
**submatrix dict per run**. Substitution is baked into the copy, so no
`model_deployment` rewrite travels in-container.

This supersedes the two designs considered earlier in this plan's history:
- **Broadcast-map (prior revision):** carry `{run_entry: rel_path}` and resolve
  in-container. Worked around cross-products *before* submatrices were on the
  table; needed an in-container resolver (⇒ magnet change + rebuild). Submatrices
  zip directly, so the indirection — and the rebuild — are unnecessary.
- **Option B (replace run_entry wholesale, structured records):** the right *end
  state*, and submatrices essentially deliver it — the per-run axis is now the
  spec path, with run_entry demoted to a label — but achieved additively and with
  zero in-container change.

The single per-run axis moves from `helm.run_entry` to the submatrix tuple; the
token-subset locator is removed from every path that used it.

---

## 4. Changes (all host-side eval_audit)

### 4.1 Schedule-time resolver + materializer (new)
A host-side step (in `manifests/builders.py` or a small `materialize_run_specs`
helper) that, for each in-scope run, takes `(precomputed_root, relative_path)` and:
- joins to the absolute official `run_spec.json` path (appending `run_spec.json`
  if the rel-path names a directory) and asserts it **exists**;
- `json.load`s it and applies the substitutions as **raw scalar edits**: set
  `adapter_spec.model_deployment` to the local name (when a rewrite is declared)
  and `adapter_spec.max_eval_instances` to the cap (when set) — every other key is
  left exactly as the official file had it;
- `json.dump`s the result to a per-run staging path
  (`<staging>/<run-id>/run_spec.json`) and writes a sidecar
  `materialization.json` recording the official source path, rel-path,
  `precomputed_root`, and each field's `from→to` (the diffable provenance record);
- emits a per-run record `{run_spec_json: <staging copy>, lease_endpoint:
  <resolved|None>, run_entry: <label>}`.

Resolution failure is a **hard, host-side error naming the path tried** — the
whole value of exact addressing is that "not found" is precise, not a silent
best-effort match. The rel-paths are resolved **once, against the corpus snapshot
the operator is looking at** (the exporter can freeze them at export time, §4.5),
turning a run-time token scan into a pinned, inspectable address. The staging dir
lives under the experiment result dir; copies are a few KB each and double as the
exact recipe each run replayed.

### 4.2 Bridge — emit a submatrix (`kwdagger_bridge.py`)
In the `from_run_spec` branch of `build_schedule_params`, replace the
`helm.run_entry` axis with `matrix["submatrices"]` — one dict per run:

```yaml
matrix:
  helm.suite: [<suite>]
  helm.container_image: ["<digest>"]
  helm.model_deployments_fpath: ["<override.yaml>"]   # still registers the local deployment(s)
  helm.staging_root: ["<exp>/materialized_run_specs"] # bind-mounted :ro (replaces the corpus mount)
  submatrices:
    - helm.run_spec_json: "<exp>/materialized_run_specs/<run-id>/run_spec.json"  # materialized copy
      helm.lease_endpoint: "<catalog endpoint for this run>"
      helm.run_entry: "<original run-entry string>"    # label / provenance only
    - helm.run_spec_json: "<exp>/materialized_run_specs/<run-id>/run_spec.json"
      ...
```

Each submatrix dict shares no original-matrix key with the singleton base, so it
matches and yields exactly one merged job (§2.1) — N runs ⇒ N jobs, no
cross-product. The per-run fields travel together (the zip we need). The copy is
pre-substituted, so **no `helm.model_deployment` / `helm.max_eval_instances`
rewrite is emitted** — the inner CLI replays the copy verbatim. `precomputed_root`
is **not** in the matrix (no corpus mount; the recipe source is the staging copy).

### 4.3 From-spec docker node — declare `run_spec_json` (`helm_docker_pipeline.py`)
Add `run_spec_json` to `MaterializeHelmRunFromSpecDockerNode.algo_params`
(alongside the existing `model_deployment`) so the bridge can thread the per-run
path into the inner `--run-spec-json=...`. It is `algo` identity: a different spec
path is a different run (a changed official recipe forces recompute — closes the
`run-from-run-spec-json-plan.md` §7 "hash the spec into identity" gap for free,
since the path *is* the version-pinned address). No magnet change — the CLI flag
exists.

### 4.4 Leasing — per-run scalar endpoint (`lease_bracket.py`, bridge)
Each run's `lease_endpoint` is resolved **on the host** at schedule time (the
exporter knows each run's deployment→endpoint) and placed in its submatrix dict.
The lease bracket then takes its existing **scalar** `lease_endpoint` path
unchanged. This **retires** `_parse_model_deployment(run_entry)` and the
`lease_endpoints` map for the from-spec path: leasing no longer parses the run
name, it reads a resolved endpoint. (Keep the map path for the legacy run-entry
pipeline.)

### 4.5 Make-manifest / exporter — freeze the rel-paths once
`builders.py`: accept `--run-spec-rel-path RUN_ENTRY=REL_PATH` (repeatable)
and/or a `--rel-path-map <json>`, plumbed into the resolver (§4.1). The
infer-stack exporter (`integrations/infer_stack/adapter.py`) already knows each
entry's matched official dir (the dry-check resolves it); at export it records
`relpath_from_root(matched_dir)` per entry, so discovery happens **once**, against
a known corpus, and is then **pinned** into the manifest. Inline
`model_deployment=<local>` stays available as the rewrite target + label.

### 4.6 Docker node — mount the staging dir, drop the corpus mount (`helm_docker_pipeline.py`)
The materialized copies live under the staging dir, so the from-spec docker node
bind-mounts `staging_root` `:ro` at an identical host=container path (the same
host-side `-v {p}:{p}:ro` rendering it already does for `precomputed_root`,
`helm_docker_pipeline.py:177` — command rendering, **not** an image change) and
**stops mounting `precomputed_root`**. Net operational win: the runner box no
longer needs the large `/data/crfm-helm-public` corpus present at run time — only
the tiny staging dir — and the from-spec bridge branch no longer needs to
*require* `precomputed_root` as a container input (it is now a host-only resolver
input, §4.1). `_prepare_container_execution` resolves `staging_root` to an absolute
path and `mkdir`s it as the host user, exactly as it does for the HF cache /
`precomputed_root` today.

### 4.7 Dry-check becomes an existence check (`check_precomputed_discovery.py`)
With frozen rel-paths the dry-check stops re-running the token matcher: for each
`(run → rel_path)` it asserts `precomputed_root/rel_path/run_spec.json` exists and
deserializes, and reports its `adapter_spec.model_deployment`. NO_MATCH /
AMBIGUOUS cannot occur by construction; the only failure is "frozen path
missing/unreadable" (corpus moved / snapshot changed) — exactly the drift we want
surfaced loudly. Keep the old token-subset mode for *producing* the initial map.

### 4.8 Tests
- **Resolver + materializer:** rel-path→abs join (dir vs file); loud error on
  missing file / missing `precomputed_root`; the materialized copy changes **only**
  `adapter_spec.{model_deployment, max_eval_instances}` and every other key is
  byte-identical to the official (the no-drift guarantee); the `materialization.json`
  sidecar records the correct `from→to`.
- **Bridge submatrix:** N records ⇒ N grid items, no cross-product (assert against
  `extended_github_action_matrix`); broadcast singletons present on each; no
  `model_deployment`/`max_eval_instances` rewrite key emitted (substitution baked).
- **Leasing:** per-run scalar `lease_endpoint` reaches the bracket; run-entry parse
  no longer consulted on the from-spec path.
- **Mounts:** the docker node renders the `staging_root` `:ro` mount and **no**
  `precomputed_root` mount on the from-spec path.
- **Parity:** the rel-path frozen by the exporter == the dir token-subset discovery
  would have located; and replaying the materialized copy yields a run dir
  byte-identical (modulo the substituted fields) to the in-container-rewrite path —
  proving host materialization is recipe-neutral.
- **e2e:** flip the existing from-spec e2e to materialized-path addressing.

---

## 5. What deliberately does NOT change

- **Comparison / pairing / index.** Produced dir name stays `run_spec.name`;
  Stages 4–6 untouched (§2.4). Locator-only change.
- **Substitution semantics.** The produced run still records the local deployment
  (⇒ `same_deployment=no`); `adapter_spec.model` is never touched; the field set
  stays `model_deployment` + `max_eval_instances` ("replacing the fields as
  specified" maps to exactly these two). Materializing moves *where* they apply
  (host raw-JSON edit instead of in-container `dataclasses.replace`) without
  changing *what* they do — the resulting recipe is identical (§4.8 parity).
- **The runner image.** No rebuild — the explicit-path replay and scalar lease are
  already in the pinned image (§2.2); the staging mount is host-side command
  rendering (§4.6).

---

## 6. How this integrates with fanning out multiple models on GPUs

This is the second half of the request, and it is where the design pays off most:
**submatrices make fan-out the default shape, and exact addressing dissolves the
problem the multi-model plan was built to solve.**

[`olmo-multi-model-from-spec-plan.md`](olmo-multi-model-from-spec-plan.md) needed
a per-model `model_deployment` for leasing *and* the rewrite, but that token also
had to survive **token-subset discovery**, which it cannot (a local name is not a
subset of the official dir; even the official name breaks the match). Its whole
apparatus — the matcher's `local_deployment_names` strip (§4.1), the dry-check
mirror (§4.3), the "local-only" negative guard (§5) — exists *solely* to teach
**discovery** to ignore that token. **With the path resolved up-front there is no
discovery to teach, and nothing to strip.**

Fan-out then becomes pure scheduler wiring, with **no scheduler-internals change
and no in-container change**:

1. **One submatrix, one entry per run** (§4.2): each entry carries its own
   materialized `run_spec_json` (already substituted, including the per-run local
   deployment) and `lease_endpoint` (resolved host-side). These are zipped by
   construction — exactly the per-run tuple fan-out needs, with the per-model
   deployment difference already baked into each copy.
2. **Shared narrow roots collapse; no corpus on the runner.** Models that needed
   *separate* narrow `precomputed_root`s purely to keep token discovery unambiguous
   (the olmo-7b `/mmlu` vs `/lite` split, multi-model plan §4.4) can now share
   **one** host-side root — the absolute path disambiguates by construction. And
   since each run replays its materialized copy, the GPU box needs no corpus mount
   at all (§4.6) — only the tiny staging dir.
3. **Leasing is per-run and explicit** (§4.4) — co-host/serialize across
   `INFER_STACK_ALLOWED_GPUS` is driven by each entry's resolved endpoint; no run
   name parsing.
4. **Schedule with `tmux_workers=N` and `--devices`** (multi-model plan §4.7):
   cmd_queue issues N concurrent jobs from the N submatrix entries; infer-stack
   leasing co-hosts what fits and serializes the rest.

Net: the GPU fan-out plan **shrinks to wiring**. The sequencing inverts — instead
of "land single-model from-spec, then build discovery-strip machinery for
multi-model," it becomes "land host-side path resolution + submatrix emission, and
multi-model fan-out falls out with bundle/grid wiring only" (no matcher change, no
negative guard, no per-model root split, no rebuild).

---

## 7. Risks / open items

- **Snapshot coupling (by design, made explicit).** A frozen rel-path embeds the
  `<version>/` token — that is the point (it pins the release). If the mirror is
  re-laid-out, the existence dry-check (§4.7) fails loud naming the path. Re-export
  to refresh — strictly better than today, where a corpus change silently shifts
  which dir the token match picks.
- **Coverage.** A run with no resolved rel-path must be a schedule-time hard error,
  never a silent fall-through to discovery (which would reintroduce the drift for
  that one run).
- **`submatrices` semantics.** Per-run tuples must go in `submatrices` (zip), never
  parallel plain axes (→ N×N). One place — the bridge — emits them; tested in §4.8.
- **Host materialization faithfulness.** The materializer does **only** raw-JSON
  scalar edits — it never re-serializes through a host HELM codec, so it cannot
  drop/rename keys (no field-drift; §2.3b). The pinned in-container HELM still
  deserializes the copy at run time, so the image remains the source of *execution*
  truth. The §4.8 no-drift test guards this.
- **Staging-dir lifecycle.** Copies are written at schedule time as the host user
  and mounted `:ro`; they must outlive the run (they are the replayed recipe + the
  provenance record). Treat the staging dir as part of the experiment result tree,
  not a temp dir.
- **Judge/annotator deployments (inherited).** By-name judge substitution remains
  an open item from `run-from-run-spec-json-plan.md` §10 — orthogonal to addressing
  (a judge rewrite would be another raw-JSON edit in the materializer).

## 8. Sequencing

1. **Resolver + materializer** (§4.1) + unit tests (incl. the no-drift guarantee) —
   host-side, no scheduler needed.
2. **Bridge submatrix emission** (§4.2) + **node `run_spec_json` param** (§4.3) +
   **staging mount / drop corpus mount** (§4.6) + **per-run lease** (§4.4) + tests
   (§4.8). All host-side; **no rebuild**.
3. **Exporter freeze + dry-check existence mode** (§4.5, §4.7).
4. **Flip the e2e** to materialized-path addressing; confirm the replayed run is
   recipe-identical to the in-container-rewrite path (parity).
5. **Multi-model fan-out** (§6): combined bundle + submatrix + `tmux_workers=N`;
   GPU smoke that N models co-host/serialize under leasing and `same_deployment=no`
   holds. Wiring only.

## 9. Why this over the alternatives

| | run-entry token discovery (today) | broadcast `{run_entry: rel_path}` map (prior revision) | **host materialize + submatrix (this plan)** |
|---|---|---|---|
| Where resolution + substitution happens | in-container, run time | in-container, run time | **host, schedule time (raw-JSON edit)** |
| Locator | token-subset match | exact map lookup | **exact path → materialized copy** |
| Per-run carriage | `helm.run_entry` axis | broadcast map + node resolver | **submatrix tuple (zip)** |
| Magnet change / rebuild | — | **yes** (in-container resolver) | **none** (existing `--run-spec-json`) |
| Recipe source in container | corpus `:ro` mount | corpus `:ro` mount | **tiny staging dir (no corpus)** |
| Leasing | parse `model_deployment=` off run name | same | **resolved scalar per run** |
| When "not found" | best-effort / ambiguous | run-time KeyError | **host-side, loud, names the path** |
| Multi-model cost | matcher strip + guard + root split | broadcast map + rebuild | **submatrix entries only** |

Host materialization removes the locator's drift at the source, surfaces a bad
address early and loudly, and yields a diffable substituted-recipe artifact per
run; submatrices carry the per-run tuple without a rebuild or a broadcast-map
indirection; the runner no longer needs the public corpus; and the multi-model GPU
fan-out drops from a matcher project to bundle-and-grid wiring.

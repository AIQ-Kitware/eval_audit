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

1. **Resolve before kwdagger, on the host.** The official corpus is laid out as
   `<public_root>/<suite>/benchmark_output/runs/<version>/<run_name>/run_spec.json`
   (e.g. `lite/benchmark_output/runs/v1.0.0/wmt_14:.../run_spec.json`). The tuple
   `(public_root, relative_path)` names that file **exactly**. The eval_audit
   tools resolve it to an **absolute path to a real `run_spec.json` on disk** at
   schedule time — where there is full Python + filesystem access — and validate
   it exists and deserializes. A bad address fails **loud, early, on the host**,
   not mid-run inside a container. Discovery stops being a run-time, in-container
   token scan and becomes a schedule-time, host-side path resolution.

2. **Hand kwdagger a list of absolute specs.** With each run resolved to a path,
   the scheduler matrix's one per-run axis becomes the **absolute `run_spec.json`
   path** — no run-entry, no token relation, no version-relative normalization.
   The existing `--run-spec-json <path>` replay mode consumes it directly.

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

3. **`precomputed_root` is already mounted `:ro` at an identical path.** The
   from-spec docker node bind-mounts it (`helm_docker_pipeline.py:177`). The
   absolute path the host resolves (`<root>/<rel_path>/run_spec.json`) therefore
   resolves to the *same* path inside the container — so passing it as
   `--run-spec-json` Just Works with no new mount. (The optional materialized-copy
   variant in §4.6 is the only thing that would add a mount.)

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

## 3. Decision: host-side resolution + submatrix zip (Option C)

Resolve each run's `run_spec.json` to an absolute path **on the host at schedule
time**, and emit the per-run tuple `(run_spec_json, lease_endpoint,
model_deployment, run_entry-label)` as one **submatrix dict per run**.

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

### 4.1 Schedule-time resolver (new)
A host-side step (in `manifests/builders.py` or a small `resolve_run_specs`
helper) that, for each in-scope run, takes `(precomputed_root, relative_path)` and:
- joins to the absolute `run_spec.json` path (appending `run_spec.json` if the
  rel-path names a directory);
- asserts it **exists** and **deserializes** (`json.loads`; optionally
  `from_json(..., RunSpec)` if HELM is importable on the host — not required);
- reads `adapter_spec.model_deployment` (the rewrite "from") for the report;
- emits a per-run record `{run_spec_json: <abs>, model_deployment: <local|None>,
  lease_endpoint: <resolved|None>, run_entry: <label>}`.

Resolution failure is a **hard, host-side error naming the path tried** — the
whole value of exact addressing is that "not found" is precise, not a silent
best-effort match. The rel-paths are resolved **once, against the corpus snapshot
the operator is looking at** (the exporter can freeze them at export time, §4.5),
turning a run-time token scan into a pinned, inspectable address.

### 4.2 Bridge — emit a submatrix (`kwdagger_bridge.py`)
In the `from_run_spec` branch of `build_schedule_params`, replace the
`helm.run_entry` axis with `matrix["submatrices"]` — one dict per run:

```yaml
matrix:
  helm.suite: [<suite>]
  helm.container_image: ["<digest>"]
  helm.model_deployments_fpath: ["<override.yaml>"]
  helm.precomputed_root: ["/data/crfm-helm-public"]   # still mounted :ro
  helm.max_eval_instances: [<N>]                       # broadcast (or per-run in submatrix)
  submatrices:
    - helm.run_spec_json: "/data/crfm-helm-public/lite/benchmark_output/runs/v1.0.0/<run>/run_spec.json"
      helm.model_deployment: "vllm/allenai-olmo-7b"    # rewrite target (in-container)
      helm.lease_endpoint: "<catalog endpoint for this run>"
      helm.run_entry: "<original run-entry string>"    # label / provenance only
    - helm.run_spec_json: ".../run_spec.json"
      ...
```

Each submatrix dict shares no original-matrix key with the singleton base, so it
matches and yields exactly one merged job (§2.1) — N runs ⇒ N jobs, no
cross-product. The per-run fields travel together (the zip we need).

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

### 4.6 Optional — materialize substituted copies (provenance enhancement)
Primary design passes the **official** spec path and lets the in-container CLI
apply `--model-deployment` / `--max-eval-instances` (unchanged, already tested).
*Optionally*, the resolver can instead write a **substituted copy** to a staging
dir via **raw-JSON field edits** (`json.load` → set `adapter_spec.model_deployment`
+ `adapter_spec.max_eval_instances` → `json.dump`). Raw editing needs **no HELM on
the host** and preserves every other key byte-for-byte (no codec round-trip ⇒ no
silent field-drift), yielding an artifact you can `diff` against the official spec
to show exactly the 1–2 changed fields. Cost: the staging dir must be bind-mounted
`:ro` into the container (a small docker-node addition). Recommend shipping the
primary (official-path) form first; add materialized copies if the methodology
section wants the diffable artifact.

### 4.7 Dry-check becomes an existence check (`check_precomputed_discovery.py`)
With frozen rel-paths the dry-check stops re-running the token matcher: for each
`(run → rel_path)` it asserts `precomputed_root/rel_path/run_spec.json` exists and
deserializes, and reports its `adapter_spec.model_deployment`. NO_MATCH /
AMBIGUOUS cannot occur by construction; the only failure is "frozen path
missing/unreadable" (corpus moved / snapshot changed) — exactly the drift we want
surfaced loudly. Keep the old token-subset mode for *producing* the initial map.

### 4.8 Tests
- **Resolver:** rel-path→abs join (dir vs file); loud error on missing file /
  missing `precomputed_root`; reads the official deployment.
- **Bridge submatrix:** N records ⇒ N grid items, no cross-product (assert against
  `extended_github_action_matrix`); broadcast singletons present on each.
- **Leasing:** per-run scalar `lease_endpoint` reaches the bracket; run-entry parse
  no longer consulted on the from-spec path.
- **Parity:** the path frozen by the exporter == the dir token-subset discovery
  would have located, and the replayed `run_spec.json` is byte-identical between
  the two locators (addressing change is recipe-neutral).
- **Raw-JSON substitution (if §4.6):** only `adapter_spec.{model_deployment,
  max_eval_instances}` change; all other keys byte-identical to the official.
- **e2e:** flip the existing from-spec e2e to path addressing.

---

## 5. What deliberately does NOT change

- **Comparison / pairing / index.** Produced dir name stays `run_spec.name`;
  Stages 4–6 untouched (§2.4). Locator-only change.
- **Substitution semantics.** `--model-deployment` still yields
  `same_deployment=no`; `adapter_spec.model` is never touched; field set stays
  `model_deployment` + `max_eval_instances`. ("Replacing the fields as specified"
  maps to exactly these two.) The §4.6 variant moves *where* they apply (host raw
  edit) without changing *what* they do.
- **The runner image.** No rebuild — the explicit-path replay and scalar lease are
  already in the pinned image (§2.2).

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
   `run_spec_json` (resolved path), `lease_endpoint` (resolved host-side), and
   `model_deployment` (rewrite target). These are zipped by construction —
   exactly the per-run tuple fan-out needs.
2. **Shared narrow roots collapse.** Models that needed *separate* narrow
   `precomputed_root`s purely to keep token discovery unambiguous (the olmo-7b
   `/mmlu` vs `/lite` split, multi-model plan §4.4) can now share **one** root —
   the absolute path disambiguates by construction, so the AMBIGUOUS hazard that
   forced the split is gone.
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
- **Host vs in-container faithfulness.** Primary design does **no** schema-touching
  work on the host (it passes the official path; substitution stays in the pinned
  HELM). The §4.6 copy variant only ever does raw-JSON scalar edits, so it never
  re-serializes through a host HELM (no field-drift). Either way the pinned image
  remains the source of recipe truth.
- **Judge/annotator deployments (inherited).** By-name judge substitution remains
  an open item from `run-from-run-spec-json-plan.md` §10 — orthogonal to addressing.

## 8. Sequencing

1. **Resolver** (§4.1) + unit tests — host-side, no scheduler needed.
2. **Bridge submatrix emission** (§4.2) + **node `run_spec_json` param** (§4.3) +
   **per-run lease** (§4.4) + tests (§4.8). All host-side; **no rebuild**.
3. **Exporter freeze + dry-check existence mode** (§4.5, §4.7).
4. **Flip the e2e** to path addressing; confirm byte-identical replayed spec vs the
   discovery locator (parity).
5. **Multi-model fan-out** (§6): combined bundle + submatrix + `tmux_workers=N`;
   GPU smoke that N models co-host/serialize under leasing and `same_deployment=no`
   holds. Wiring only.
6. *(Optional)* materialized-copy variant (§4.6) if the methodology wants diffable
   substituted specs.

## 9. Why this over the alternatives

| | run-entry token discovery (today) | broadcast `{run_entry: rel_path}` map (prior revision) | **host resolve + submatrix (this plan)** |
|---|---|---|---|
| Where resolution happens | in-container, run time | in-container, run time | **host, schedule time** |
| Locator | token-subset match | exact map lookup | **exact path, resolved up-front** |
| Per-run carriage | `helm.run_entry` axis | broadcast map + node resolver | **submatrix tuple (zip)** |
| Magnet change / rebuild | — | **yes** (in-container resolver) | **none** (existing `--run-spec-json`) |
| Leasing | parse `model_deployment=` off run name | same | **resolved scalar per run** |
| When "not found" | best-effort / ambiguous | run-time KeyError | **host-side, loud, names the path** |
| Multi-model cost | matcher strip + guard + root split | broadcast map + rebuild | **submatrix entries only** |

Host-side resolution removes the locator's drift at the source and surfaces a bad
address early and loudly; submatrices carry the per-run tuple without a rebuild or
a broadcast-map indirection; and the multi-model GPU fan-out drops from a matcher
project to bundle-and-grid wiring.

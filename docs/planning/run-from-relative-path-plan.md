# Addressing reproductions by `(public_root, relative_path)` — plan

**Status:** PLAN (not implemented).
**Goal:** stop using the **run name** (the HELM run-entry string) as the
identifier that *locates* the official run to reproduce. Instead specify the
**HELM public root directory** plus the **relative path** to the official run
dir, and replay its `run_spec.json` with the declared field substitutions. The
run name is a drifty *locator*; an exact relative path is not.
**Method:** read the from-spec replay CLI (`materialize_helm_run_from_spec.py`),
the bridge/manifest wiring (`kwdagger_bridge.py`, `manifests/`), the lease
bracket, the discovery dry-check, and the live corpus layout under
`/data/crfm-helm-public`. 2026-06-30.

**Builds on / relates to:**
- [`run-from-run-spec-json-plan.md`](run-from-run-spec-json-plan.md) — the
  from-spec replay path this extends (its explicit `--run-spec-json` mode is the
  machinery we reuse).
- [`from-spec-deployment-rewrite-plan.md`](from-spec-deployment-rewrite-plan.md)
  — the `--model-deployment` rewrite is **unaffected** and still applies (§7).
- [`olmo-from-run-spec-migration-plan.md`](olmo-from-run-spec-migration-plan.md)
  — the single-model from-spec migration; relative-path addressing is the next
  evolution of its discovery step.
- **Supersedes (for the fan-out case)**
  [`olmo-multi-model-from-spec-plan.md`](olmo-multi-model-from-spec-plan.md):
  that plan exists *only* to make run-entry **token-subset discovery** survive
  multi-model manifests (the `local_deployment_names` strip rule, its dry-check
  mirror, the negative guard). Exact relative-path addressing removes the
  token-subset match entirely, so there is nothing left to strip — see §6.

---

## 1. Why this path (the central insight)

A HELM run-entry string (`mmlu:subject=philosophy,model=openai/gpt2,...`) plays
**three** distinct roles in the pipeline today. Only one of them is fragile:

| role | mechanism | drift-prone? |
|---|---|---|
| **Locator** — find the official run dir whose `run_spec.json` we replay | `find_best_precomputed_run` → `run_dir_matches_requested` *token-subset match* over the whole corpus | **YES** — this is the drift |
| **Lease key** — which local server each run uses | `_parse_model_deployment` reads the `model_deployment=` token off the *same* string (`lease_bracket.py:90`) | no — exact token parse |
| **Logical / pairing key** — group local↔official, name the produced run dir | produced dir name == `run_spec.name`; Stage 4–6 pairing keys off it | no — derived from the real spec |

The **locator** role is the problem the user names: the run name "is not a
consistent identifier and is highly susceptible to drift." Token-subset matching
is fragile in three documented ways:

1. **Order/variant sensitivity** — the canonical-logical-key work
   ([`core-report-planner-robust-matching-plan.md`](core-report-planner-robust-matching-plan.md))
   exists because the same token set in a different order failed to match.
2. **`model_deployment=` collisions** — the entire multi-model plan exists
   because a local `model_deployment=` token is not a subset of the official dir
   name, while a `stanfordhealthcare`-style official token *must* stay to
   discriminate. Token matching forces a per-token "is this local?" rule.
3. **Library-version normalization** — `helm-run`'s own defaults (method
   tokens, `data_augmentation`) shift what tokens the official dir name carries
   across releases, so the subset relation is version-relative.

**The fix: address the run exactly.** The official corpus is laid out as

```
<public_root>/<suite>/benchmark_output/runs/<version>/<run_name>/run_spec.json
e.g.  lite/benchmark_output/runs/v1.0.0/wmt_14:language_pair=fr-en,model=anthropic_claude-instant-1.2/run_spec.json
```

The tuple `(public_root, relative_path)` names the run dir **exactly** — no
scan, no token relation, no version-relative normalization. `public_root` is the
per-machine mount (it already varies: `/data/crfm-helm-public` here, elsewhere on
yardrat/namek/aiq-gpu); `relative_path` is the stable, drift-free identifier of
*which* run, including the `<version>/` token that pins the leaderboard release.

**Crucially, this reuses machinery that already exists.** The from-spec CLI
*already* has an explicit-path mode (`--run-spec-json <abs path>`,
`run-from-run-spec-json-plan.md` §4) that replays an exact spec with **no
discovery**. `(public_root, relative_path)` is just the **machine-portable
decomposition** of that absolute path: the root is a separate, already-mounted
manifest field; the relative path is what we freeze and ship. We are not adding a
new replay engine — we are changing how the existing replay engine is *addressed*.

**We keep the run-entry string for its two non-fragile roles** (lease key,
logical/pairing key). It is reliable there: leasing parses one exact token, and
pairing derives from the produced `run_spec.name`, never from a token-subset
match. So this is an *additive* change to the **locator** only.

---

## 2. Constraints discovered (what shapes the design)

1. **`precomputed_root` is already a manifest field, already mounted.** It flows
   manifest → `helm.precomputed_root` matrix value → docker node `:ro` bind-mount
   at the *identical* absolute path inside the container
   (`kwdagger_bridge.py:176,376`; `helm_docker_pipeline.py`). So
   `<root>/<relative_path>/run_spec.json` resolves to the same path host-side and
   in-container. No new mount.

2. **The matrix has exactly one per-run axis: `helm.run_entry`.** Everything else
   (`precomputed_root`, `model_deployments_fpath`, `lease_endpoints`,
   `model_deployment`, container knobs) is a **single broadcast value**
   (`kwdagger_bridge.py:173–217`). Per-run data that varies must therefore either
   ride *on* the run_entry axis or be a **broadcast map the node resolves against
   its own run_entry** — which is exactly how `lease_endpoints` already works
   (`lease_bracket.py:106–131`: one `{model_deployment: endpoint}` map, each job
   resolves its row by parsing its own run_entry). We mirror that precedent (§4).

3. **The from-spec replay is in-container (magnet).** Per-run resolution that
   needs the job's own run_entry must happen in the magnet node, not the bridge
   (the bridge never sees the per-run expansion). Any node change → magnet gitlink
   bump + image rebuild + re-pin (the discipline the from-spec work already
   follows; [[container-env-frozen-at-build-time]]).

4. **Identity / pairing is independent of the locator.** Because
   `adapter_spec.model` is never rewritten (substitution is by-name; the optional
   `--model-deployment` touches only `model_deployment`), the produced run dir
   name stays `run_spec.name`. Stages 4–6 (`index`, `logical_run_key`, planner
   pairing) are untouched whether we located the official spec by token match or
   by exact path. This plan changes *addressing*, not *comparison*.

5. **The official spec is the recipe source, not a reuse cache.** From-spec always
   recomputes; `precomputed_root` exists to supply `run_spec.json`. Exact
   addressing makes that explicit: we read one named file, never scan.

---

## 3. Decision: additive relative-path **locator**, keyed off run_entry (Option A)

**Option A (chosen).** Add a per-run *locator* — the relative path — and resolve
it at the from-spec node against the job's own run_entry, via a broadcast
`{run_entry: relative_path}` map (mirroring `lease_endpoints`). The run_entry
string remains the lease key and the logical/pairing key. Smallest blast radius;
reuses the explicit-path replay and the lease-map pattern verbatim; kills the
token-subset locator everywhere it is used.

**Option B (rejected).** Replace the run-entry string outright with structured
per-run records `{relative_path, model_deployment, max_eval_instances}`. Tidier in
the abstract, but the pipeline is "keyed on run-entry strings end to end"
(`run-from-run-spec-json-plan.md` §2): leasing parses run_entry, the matrix's one
per-run axis is run_entry, the index and planner pairing key off the run name. B
forces touching all of those at once for no locator benefit beyond A. A reaches
the same end state incrementally and reversibly; B can be a later cleanup if the
run-entry string ever loses its other two roles.

---

## 4. Changes

### Change 1 — from-spec CLI: relative-path resolution (magnet)
`materialize_helm_run_from_spec.py` gains a third recipe-resolution input,
sitting between discovery and explicit-absolute:

- New config field `run_spec_rel_path` (`type=str`, `algo_param`). When set,
  resolve the recipe path as `precomputed_root[0] / run_spec_rel_path`, appending
  `run_spec.json` if the path names a directory. This is the **machine-portable
  explicit path** — it routes straight into the existing explicit-path branch
  (`_resolve_run_spec_path`), so the replay/preflight/substitute/run flow is
  unchanged.
- **Resolution precedence:** `run_spec_json` (absolute) > `run_spec_rel_path`
  (root-relative) > discovery (`run_entry` + token match). Record which in the
  `adapter_manifest.json` `recipe_source` field (extend the existing enum).
- Hard error if `run_spec_rel_path` is set but `precomputed_root` is empty, or the
  joined path has no `run_spec.json` — fail loud before any instances run, naming
  the path tried (the locator's whole value is that "not found" is exact, not a
  silent best-effort match).
- The `run_entry` is still accepted (it is the lease key + label); in rel-path
  mode it is **not** used to locate anything.

### Change 2 — manifest model (`manifests/models.py`)
Add `run_spec_rel_paths: dict[str, str] = field(default_factory=dict)` — the
broadcast `{run_entry: relative_path}` map, exactly parallel to how
`lease_endpoints` is carried. Empty ⇒ current behavior (discovery). Document that
when non-empty it must cover every `run_entries` element (validated in Change 5).

### Change 3 — bridge (`kwdagger_bridge.py`)
In the `from_run_spec` branch of `build_schedule_params`, when
`manifest["run_spec_rel_paths"]` is non-empty, emit it as a single broadcast
matrix value `helm.run_spec_rel_paths` (JSON-encoded map, like
`lease_endpoints`). The docker-from-spec node passes the map through; the node
resolves its own row by `map[run_entry]` (Change 1 + the node's per-run
run_entry). A broadcast map — not a parallel axis — is **required**, not merely
preferred: **kwdagger does not zip; it always cross-products its matrix axes**
(confirmed). So a parallel `helm.run_spec_rel_path` (or `helm.run_spec_json`)
axis of length N alongside `helm.run_entry` of length N would expand to **N×N
jobs**, pairing every run_entry with every rel-path — silently wrong. The single
per-run axis stays `helm.run_entry`; everything else that varies per run must be
a broadcast value the node resolves against its own run_entry, exactly as
`lease_endpoints` already does. This also means the magnet node change (Change 1
+ 4) is **unavoidable** — there is no zero-rebuild shortcut via a parallel axis.

### Change 4 — from-spec docker node (`helm_docker_pipeline.py`)
Thread `run_spec_rel_paths` through `MaterializeHelmRunFromSpecDockerNode` as a
node param the magnet CLI receives (alongside the existing `precomputed_root`,
`model_deployment`). It is `algo_param` identity: a different relative path is a
different run (a changed official recipe path forces recompute — closes the
`run-from-run-spec-json-plan.md` §7 "optional: hash the spec into identity" gap
for free, since the path *is* the version-pinned address).

### Change 5 — make-manifest / exporter: resolve the rel-path **once, at export**
This is the quiet win. The relative path is resolved against a **known corpus
snapshot at export time**, frozen into the manifest, and never re-discovered at
run time.

- `manifests/builders.py`: accept `--run-spec-rel-path RUN_ENTRY=REL_PATH`
  (repeatable) and/or a `--rel-path-map <json>`, plumbed into `_build_manifest`
  as `run_spec_rel_paths`. Validate the map covers every in-scope run_entry.
- The infer-stack exporter (`integrations/infer_stack/adapter.py`) already knows
  the matched official dir for each entry (the dry-check resolves it). At export
  it records `relpath_from_root(matched_dir)` per entry into
  `run_spec_rel_paths`, so discovery happens **once**, against the corpus the
  operator is looking at, and is then **pinned**. Inline `model_deployment=<local>`
  tokens stay on the run-entries (lease key + rewrite target — §6).

### Change 6 — dry-check becomes an *existence* check (`check_precomputed_discovery.py`)
Today the dry-check re-runs the token-subset matcher to baseline NO_MATCH /
AMBIGUOUS. With frozen rel-paths it becomes far stronger and simpler: for each
`(run_entry → rel_path)`, assert `precomputed_root / rel_path / run_spec.json`
exists and deserializes (`from_json(..., RunSpec)`), and report its
`adapter_spec.model_deployment` (the rewrite "from"). NO_MATCH/AMBIGUOUS cannot
occur by construction — the only failure is "frozen path missing/unreadable"
(corpus moved or snapshot changed), which is exactly the drift we want surfaced
loudly. Keep the old token-subset mode available for *producing* the initial
rel-path map (it is how the exporter discovers the dir the first time).

### Change 7 — image rebuild & re-pin
Changes 1 + 4 are in-container (magnet). Commit the magnet change, `./docker/build.sh`,
capture the digest, re-pin (`07_check_container_image.sh`), and bump the magnet
gitlink in a **separate, explicit** commit (never auto-commit the gitlink —
[[commit-logical-units]]).

### Change 8 — tests
- **Unit (magnet):** `run_spec_rel_path` resolution precedence; dir-vs-file path
  handling; loud error on missing `precomputed_root` / missing file.
- **Unit (eval_audit):** bridge emits the broadcast map only in the from-spec
  branch; `run_spec_rel_paths` validation rejects a map missing a run_entry.
- **Dry-check:** frozen map → all-exist / one-missing → nonzero exit naming the path.
- **Parity:** for one entry, the dir located by token-subset discovery == the dir
  named by the frozen rel-path (proves the freeze is faithful), and the produced
  `run_spec.json` is byte-identical between the two locators (addressing change is
  recipe-neutral).
- **e2e:** flip the existing from-spec e2e to rel-path addressing behind a flag.

---

## 5. What deliberately does NOT change

- **Comparison / pairing / index.** Produced dir name stays `run_spec.name`;
  Stages 4–6 untouched (§2.4). This plan is locator-only.
- **The deployment rewrite.** `--model-deployment` still rewrites
  `adapter_spec.model_deployment` to the local name so the audit reports
  `same_deployment=no`; `adapter_spec.model` is still never touched
  (`from-spec-deployment-rewrite-plan.md`). Field substitution = exactly today's
  set: `model_deployment` (rewrite) + `max_eval_instances` (truncate). "Replacing
  the fields as specified" in the user's request maps to these two; nothing new
  is needed for them.
- **Leasing.** `lease_endpoints` resolution is unchanged — it still reads
  `model_deployment=` off run_entry (§6).

---

## 6. How this integrates with fanning out multiple models on GPUs

This is the second half of the request, and it is where exact addressing pays off
most: **it dissolves the problem the multi-model plan was built to solve.**

[`olmo-multi-model-from-spec-plan.md`](olmo-multi-model-from-spec-plan.md) needed
a per-model `model_deployment` for two purposes — **leasing** (which GPU server)
and the **deployment rewrite** — but the `model_deployment=` token also had to
survive **token-subset discovery**, which it cannot (a local name is not a subset
of the official dir; even the official name breaks the match). Its entire
apparatus — the matcher's `local_deployment_names` strip parameter (§4.1), the
dry-check mirror (§4.3), the "local-only" negative guard (§5) — is there *solely*
to teach **discovery** to ignore that token while keeping genuine
`stanfordhealthcare`-style discriminators.

**With relative-path addressing there is no discovery to teach.** The locator is
the frozen rel-path; the `model_deployment=` token is never matched against
anything. So:

- **`local_deployment_names` strip rule (multi-model plan §4.1): not needed.**
- **Dry-check strip + negative guard (§4.3, §5): not needed** — the dry-check is
  now an existence check (Change 6) and cannot go AMBIGUOUS.
- **The `model_deployment=` token keeps doing its two real jobs, unchanged:**
  lease resolution (`_resolve_lease_endpoint`, untouched) and the rewrite target
  (the node reads the inline token as today, or honors a single
  `--model-deployment` for single-model back-compat).

The fan-out recipe then becomes purely mechanical, with **no scheduler change**:

1. **One combined multi-model bundle.** Run-entries for all N models in one
   manifest, each carrying inline `model_deployment=vllm/allenai-<model>` (lease
   key + rewrite target — the existing run-entry multi-model convention).
2. **One frozen `run_spec_rel_paths` map** covering every entry (Change 5),
   resolved once at export. Models that needed *separate narrow roots* purely to
   keep token discovery unambiguous (the olmo-7b `/mmlu` vs `/lite` split in the
   multi-model plan §4.4) can now share **one** `precomputed_root` — the rel-path
   disambiguates by construction, so the AMBIGUOUS hazard that forced the split is
   gone. Verify per bundle, but the split is no longer *required* by the locator.
3. **`lease_endpoints` map** keyed by the inline deployments (multi-model plan
   §4.5 — unchanged).
4. **Schedule with `tmux_workers=N` and `--devices`** (multi-model plan §4.7):
   cmd_queue issues N concurrent jobs, each resolves its own rel-path
   (`map[run_entry]`) and its own lease (`map[model_deployment]`) from the two
   broadcast maps, and infer-stack leasing co-hosts what fits / serializes the
   rest across `INFER_STACK_ALLOWED_GPUS`. The two broadcast maps are the only
   per-run state, both resolved against the job's own run_entry — symmetric and
   scheduler-semantics-free.

Net: the GPU fan-out plan **shrinks** under relative-path addressing. The
sequencing dependency inverts — instead of "land single-model from-spec, then add
the discovery-strip machinery for multi-model," it becomes "land relative-path
addressing, and multi-model fan-out falls out with only bundle/grid wiring (no
matcher change, no negative guard, possibly no per-model root split)."

---

## 7. Risks / open items

- **Snapshot coupling (by design, made explicit).** A frozen rel-path embeds the
  `<version>/` leaderboard token — that is the point (it pins the exact release).
  If the corpus mirror is re-laid-out or a run dir is renamed, the existence
  dry-check (Change 6) fails loud naming the path. Re-export to refresh. This is
  strictly better than today, where a corpus change silently shifts which dir the
  token match selects.
- **Map coverage.** A `run_spec_rel_paths` map missing an in-scope run_entry must
  be a schedule-time hard error (Change 5 validation), not a fall-through to
  discovery — falling through would reintroduce the drift for that one entry
  silently.
- **kwdagger cross-products (confirmed) ⇒ the broadcast map is mandatory.**
  kwdagger always takes the Cartesian product of matrix axes, so per-run data
  cannot ride a parallel axis (it would fan out N×N). The `{run_entry: rel_path}`
  broadcast map resolved at the node is the only correct carriage; there is no
  zero-rebuild parallel-axis shortcut. (This is why Change 1 + 4 — the in-container
  magnet change — are unavoidable.)
- **Container rebuild discipline.** Changes 1+4 are in-container; gitlink bump +
  rebuild + re-pin before any grid uses the new node ([[container-env-frozen-at-build-time]]).
- **Judge/annotator deployments (inherited, unchanged).** By-name judge
  substitution is still an open item from `run-from-run-spec-json-plan.md` §10 —
  orthogonal to addressing.

---

## 8. Sequencing

1. **magnet:** Change 1 (`run_spec_rel_path` resolution) + unit tests. *Do not
   bump the gitlink yet.*
2. **eval_audit:** Changes 2–6 (manifest field, bridge broadcast map, node thread,
   make-manifest/exporter freeze, dry-check existence mode) + unit/parity tests —
   all host-side, testable before the rebuild.
3. **Rebuild + re-pin + gitlink bump** (Change 7), separate explicit commits.
4. **Flip the e2e** to rel-path addressing (Change 8); confirm byte-identical
   produced spec vs the discovery locator (parity).
5. **Multi-model fan-out** (§6): combined bundle + frozen map + `tmux_workers=N`;
   GPU smoke that N models co-host/serialize under leasing and `same_deployment=no`
   holds. This is now *wiring only* — no matcher/guard work.

## 9. Why this over the alternatives

| | run-entry token discovery (today) | multi-model discovery-strip (`olmo-multi-model-from-spec-plan.md`) | **`(root, rel_path)` addressing (this plan)** |
|---|---|---|---|
| Locator | token-subset match over corpus | token-subset match, minus local-name tokens | **exact path, no match** |
| Drift surface | order, `model_deployment=`, version normalization | narrowed, but still a match | **none — exact file** |
| Multi-model cost | n/a | matcher param + dry-check strip + negative guard + maybe per-model root split | **bundle + frozen map + tmux_workers** |
| When "not found" | best-effort/ambiguous, silent-ish | best-effort, guarded | **loud, names the path** |
| Reuses existing code | — | matcher internals | **explicit-path replay + lease-map pattern** |
| Scheduler-semantics risk | none | none | **none (broadcast map)** |

Relative-path addressing removes the locator's drift at the source rather than
teaching the matcher to tolerate more of it, reuses the from-spec explicit-path
replay and the `lease_endpoints` broadcast-map pattern verbatim, and turns the
multi-model GPU fan-out from a matcher project into bundle-and-grid wiring.

# LiteLLM Route-Registry Plan (infer_stack)

**Status:** planned, not started — v2: Opus review (§13) resolved in place;
each concern's decision is folded into the body sections below
**Target:** `submodules/infer_stack` (leasing compose backend)
**Motivating incident:** olmo-7b vLLM healthy, gateway 400 "Invalid model
name" — another runbook's converge re-rendered the shared gateway from *its*
catalog and stripped the olmo routes. Also the likely cause of historical
"failed to acquire lease" events: a cross-catalog converge during the
minutes-long readiness window removes the alias mid-wait, and the probe
(`probe.py` "not advertised by the gateway yet") polls until lease timeout.

## 1. Problem

Multiple runbooks (olmo, qwen, gpt-oss, classic_together) share one standing
stack (`data_dir: /data/service/infer-stack`) but each ships a **disjoint**
catalog. In the default static-superset mode the gateway's route table is
rendered from **the invoking catalog only**
(`render_compose` → `_litellm_model_list_from_catalog`, `compose.py:907-908`).
Consequences:

1. **Stale routes:** any converge under catalog B strips the routes of
   catalog A's still-live deployments (the deployments themselves survive —
   their specs are persisted lease state — so the container is healthy while
   the front door 400s).
2. **Blips:** every cross-catalog converge changes the rendered config bytes
   → `CONFIG_HASH_LABEL` changes → `docker compose up` recreates the gateway
   container → a few seconds of downtime that severs in-flight requests.

## 2. Design

Persist an **append-only route registry** in the shared state dir. Converge
merges the invoking catalog (plus live non-catalog deployments) into the
registry, then renders the gateway `model_list` from **the whole registry**
instead of the invoking catalog.

**Invariant:** the rendered gateway config is a function of accumulated
shared state, not of which runbook invoked the converge. Once every catalog
has been merged once, all converges render byte-identical configs → the hash
never moves → the gateway is never recreated, and every live deployment stays
routable regardless of interleaving.

Residual (justified) recreations: first-ever appearance of a new endpoint,
or a genuinely changed endpoint definition.

Registry stores **semantic inputs** (served name, engine, host), not rendered
LiteLLM entries — render derives entries through the existing helpers, so
future renderer changes propagate to old registry entries automatically (this
is what makes the registry cheaper than the compose-roundtrip alternative:
the rendered config never becomes a parse-compatibility surface).

**Activation (resolves review concern A):** the registry path is
unconditional in `ComposeBackend` whenever `litellm and not dynamic_routing`
— **regardless of whether a catalog is discoverable**. A catalog-less
converge (bare `infer-stack release`/`gc` without `INFER_STACK_CONFIG_DIR`)
merges only its live deployments and still renders from the accumulated
registry, so it can no longer strip routes or blip. This retires the legacy
per-deployment branch (`_litellm_model_list`) from the backend path entirely
— it survives in `render_compose` only for direct callers/tests. The one
behavioral delta vs. legacy: the gateway service no longer carries per-model
`depends_on` (which churned the spec on every model change anyway); the
static-superset `router_settings` self-healing covers warmup, as it already
does today. Per repo policy (**no flags to preserve bugs**): no opt-out
flag. Dynamic-routing mode is untouched.

**What the registry uniquely adds (review concern B):** `desired` already
spans *all* runbooks — the ledger is shared
(`data_root()/leasing/ledger.db`), so every converge sees every live
deployment, and the deployment-derived incoming alone would keep *live*
cross-runbook models routable. The registry's distinct contributions are
(1) routes that **persist past release** (a released olmo endpoint stays
routable/testable), and (2) **byte-stable renders** — without persistence,
the rendered set would oscillate with the live set and recreate the gateway
on every cross-catalog acquire/release.

## 3. Registry file

- **Path:** `<state_dir>/litellm_registry.json` (next to
  `litellm_config.yaml`, `.env`, `.converge.lock`), new constant
  `LITELLM_REGISTRY_FILENAME` in `compose.py`.
- **Schema (versioned):**

```json
{
  "version": 1,
  "entries": {
    "allenai-olmo-7b-single": {
      "engine": "vllm",
      "served": "allenai-olmo-7b-single"
    },
    "some-ollama-endpoint": {
      "engine": "ollama",
      "model": "llama3:8b",
      "host": "gpu-host"
    }
  }
}
```

- vLLM entries need only `served`: the upstream is derived at render time via
  `vllm_service_name_for(served)` — exactly what
  `_litellm_model_list_from_catalog` does today.
- Ollama entries need `model` (tag) + `host` (input to
  `ollama_service_name_for`).
- **No `service`/api_base override field** (resolves review concern D): every
  entry re-derives its upstream through the live naming helpers, so renderer
  evolution always propagates. An earlier draft had a seeding-only `service`
  override to carry non-invertible dns-slugged Ollama hostnames; dropped —
  Ollama rows are skipped at seed time instead of half-inverted (§6).
- **Canonical serialization:** `json.dumps(..., sort_keys=True, indent=2)`
  + trailing newline. Byte-stable output is load-bearing — a nondeterministic
  dump would manufacture phantom hash changes.

## 4. Merge semantics

New pure function in `compose.py`:

```
_merge_route_registry(existing: dict, incoming: dict[str, dict])
    -> tuple[dict, list[str]]   # (merged, warnings)
```

- Idempotent: merging already-present identical entries is a no-op (dict
  equality on the entry).
- Additive: never removes entries.
- Conflict (same `model_name`, different entry): **incoming wins**, emit a
  warning naming both definitions. A changed definition changes the rendered
  bytes → one recreate, which is correct (the config genuinely changed).

Incoming entries per converge (static-superset branch only):

1. Every resolvable endpoint of the invoking catalog, **when a catalog is
   discoverable** (same iteration as `_litellm_model_list_from_catalog`;
   unresolvable endpoints skipped, as today). Row key = catalog endpoint
   name; `served = ep.served_name or ep.name` (mirrors
   `catalog._resolve_vllm`).
2. Every **placed deployment in the full `desired` set** (`desired` +
   `plan.assignments`) — which spans all runbooks via the shared ledger,
   not just the endpoint being acquired. Rules (resolves review concern E):
   - Engine filter: only `vllm` / `ollama`. Skip `RESERVED_ENGINE`
     (reservations render no service) and unknown engines, exactly as
     `render_compose`'s service loop does.
   - **One row per key of `deployment.served`** (a coalesced deployment can
     back multiple endpoint aliases; `_litellm_model_list` iterates
     `sorted(deployment.served)` for the same reason). For vLLM each row's
     `served = deployment.spec['served_model_name']` with the
     `vllm_service_name` fallback chain (`or sorted(served)[0] or id`,
     `compose.py:142-144`); for Ollama, `model = payload.get('model',
     endpoint)` and `host = deployment.spec.get('host') or deployment.id`.
   - For a catalog-listed endpoint acquired live, both sources reduce to the
     identical entry dict (same key, same `served`), so live-vs-released
     status never changes the bytes.

Non-catalog/dedicated acquires become and stay routable via item 2 — this
also fixes the documented static-superset gap ("cannot route non-catalog
acquires").

## 5. Code changes

### `infer_stack/leasing/compose.py`

- `LITELLM_REGISTRY_FILENAME = 'litellm_registry.json'`.
- Extract the per-endpoint entry-building bodies of
  `_litellm_model_list_from_catalog` (compose.py:376) and
  `_litellm_model_list` (compose.py:338) into small pure helpers
  (`_vllm_route_entry(model_name, served, api_base)`,
  `_ollama_route_entry(model_name, tag, api_base)`), so the registry render
  cannot drift from the existing renders.
- New `_registry_incoming_from_catalog(catalog) -> dict[str, dict]` and
  `_registry_incoming_from_deployments(deployments, assignments) -> dict`.
- New `_litellm_model_list_from_registry(registry) -> list[dict]`: iterate
  `sorted(entries)`, derive `api_base` per engine (honoring the `service`
  override), build entries via the shared helpers.
- New `_merge_route_registry` (§4).
- `render_compose`: new kwarg `route_registry: dict | None = None`. In the
  gateway branch (compose.py:903-912) the branch order becomes
  `dynamic_routing` → `route_registry` → `catalog` → legacy:

  ```python
  elif route_registry is not None:
      entries = _litellm_model_list_from_registry(route_registry)
      litellm_depends = []
  elif catalog is not None:            # unreachable from ComposeBackend once
      entries = _litellm_model_list_from_catalog(catalog)   # wired; kept for
      litellm_depends = []                                  # direct callers
  ```

  `render_compose` stays pure (no file I/O): the backend loads/merges/writes
  the registry and passes the merged dict in. **Verified (review concern
  G):** inside `render_compose`, `catalog` feeds *only* this `entries`
  computation — no other consumer (Open WebUI wiring uses the gateway URL,
  not the catalog) — so the registry render is a drop-in for the catalog
  arm.

### `infer_stack/leasing/compose.py` — `ComposeBackend`

- Property `_registry_file = self.state_dir / LITELLM_REGISTRY_FILENAME`.
- `_load_route_registry() -> dict`: tolerant read — missing file → seed
  (§6); unparseable/wrong-version → log a warning and fall back to seeding
  (fail-open; a broken registry must never block converge).
- `_update_route_registry(desired, assignments) -> dict`: load, merge
  incoming (§4), log added/updated names (visibility for the one justified
  recreate), `_atomic_write` iff changed, return merged dict.
- `merge_route_registry(incoming: dict[str, dict]) -> dict` (resolves review
  concern C): public method for out-of-converge registry writes — takes the
  converge flock, read-merge-writes, returns the merged dict. This is the
  write path `routes seed` needs, since `converge` only ever merges the
  invoking process's own catalog. (The flock is taken and released here,
  then again by the subsequent `reconcile` converge — sequential
  acquisitions, not nested, so no reentrancy concern.)
- `converge()` (compose.py:1208), inside `self._converge_lock()` — the
  existing per-state-dir flock already serializes concurrent converges from
  different runbooks, so read-merge-write is race-safe with **no new
  locking**:

  ```python
  route_registry = None
  if self.litellm and not self.dynamic_routing:
      # Unconditional (review concern A): catalog may be None (bare
      # release/gc without a discoverable config dir) — incoming is then
      # deployments-only, and the render still comes from the accumulated
      # registry, so a catalog-less converge cannot strip routes or blip.
      route_registry = self._update_route_registry(desired, plan.assignments)
  rendered = render_compose(..., route_registry=route_registry, ...)
  ```

- **Apply-path interleaving is safe as-is** (resolves review concern H):
  `apply()` re-reads the **on-disk** compose file last written under the
  render lock — it does not apply the in-memory render (`compose.py:1318`
  docstring). If converge A writes registry+config v1 and converge B writes
  v2 before A's apply runs, A applies B's v2 file. Because the registry is
  append-only, v2 ⊇ v1 — last-writer-wins always applies the largest union,
  so the worst case is one recreate total (when the union genuinely grew),
  never an extra flip-flop.

### Not touched

- Dynamic-routing mode (`_litellm_routes`, `_reconcile_routes`, Postgres):
  already deployment-driven and catalog-independent; registry not created or
  consulted there.
- Legacy no-catalog branch (`_litellm_model_list`): unchanged.
- `stack down`: does **not** delete the registry (it is durable state, like
  leases; explicit forgetting is `routes prune`, §7).

## 6. Seeding (upgrade migration)

Without seeding, the first post-upgrade converge would know only its own
catalog — one last route-strip event. Avoid it with a one-shot,
single-format import:

- In `_load_route_registry`, when `_registry_file` is absent but
  `<state_dir>/litellm_config.yaml` exists: parse its `model_list` (our own
  rendered output) and import **vLLM rows only** — `openai/<served>` recovers
  `served` verbatim, so `{engine: vllm, served}` is exact. **Ollama rows are
  skipped with a warning** (resolves review concern D): the host survives
  only as a non-invertible `dns_slug` inside `api_base`, and a half-inverted
  row would route to a recomputed, possibly wrong hostname. A skipped Ollama
  endpoint re-enters the registry at the next converge that has it in its
  catalog or live set (one recreate then — the pre-fix status quo, only for
  Ollama, only once). The fleet motivating this plan is vLLM-only. Anything
  else unparseable is likewise skipped with a warning; write the seeded
  registry and log what was imported.
- This parser reads only the current live file once, at migration time — it
  is not a general roundtrip and carries no cross-version compatibility
  promise.
- **Version tolerance** (resolves review concern F): an *unknown*
  `version` whose `entries` still parses as a name→dict map is **preserved
  as-is** (render what's understood, warn, do not rewrite the file) rather
  than reseeded — reseeding from the currently rendered config would discard
  the accumulated union on a binary rollback. Only a structurally unusable
  file (not a map, garbage JSON) falls back to seeding. Known limitation to
  document: after a rollback, rows written by a newer schema may carry
  fields the old renderer ignores; that is acceptable — ignoring unknown
  fields is the forward-compat contract.

## 7. CLI: `infer-stack routes`

New `RoutesModalCLI(scfg.ModalCLI)` group (`__command__ = 'routes'`) with
nested command classes, following the `ConfigModalCLI` precedent
(`commands_meta.py:584` — this is how `config set/get/show` nests, so
`routes list/prune/seed` needs no ad-hoc positional-action dispatch):

- `routes list` — print the registry (name, engine, served/model, upstream);
  note which entries are also live right now.
- `routes prune` — rewrite the registry to *invoking catalog ∪ live
  deployments*, then converge (one accepted recreate). This is the explicit
  "forget stale endpoints" verb; automatic pruning is deliberately excluded
  because any catalog-keyed pruning rule reintroduces the alternation churn.
  **Requires confirmation** (reuse the `_ApprovalMixin` flow): print exactly
  which entries will be dropped first — a prune run from the wrong
  `INFER_STACK_CONFIG_DIR` would silently nuke every other runbook's routes,
  so the operator must see the drop list before it happens.
- `routes seed <catalog.yaml> [...]` — merge extra catalog files into the
  registry, then converge. Mechanism (resolves review concern C):
  `Catalog.load(path)` per file (exists, `catalog.py:230`) →
  `_registry_incoming_from_catalog(cat)` → `backend.merge_route_registry(...)`
  (§5) → `controller.reconcile(apply=True)` to render+apply once. Requires
  the compose backend (error out under `--backend null`); works before
  `stack up` — the reconcile brings the standing gateway up with the full
  route table, which is exactly what pre-seeding is for. Operational key for
  blip-free concurrency: seed all runbooks' catalogs once while idle, then
  no converge from any of them ever recreates the gateway. Catalog file
  arguments use `nargs='+', position=1` (precedent:
  `commands_catalog.py:471`).

Runbook guidance (docs, and optionally each `reproduce/*/_lib.sh` preflight
later, in eval_audit): run `infer-stack routes seed <sibling catalogs>` — or
simply rely on first-converge merging — before launching overlapping grids.

## 8. Determinism requirements

- Registry serialization canonical (§3).
- `_litellm_model_list_from_registry` iterates `sorted(entries)`.
- Entry dicts contain only stable fields (no timestamps, no ids that vary
  per acquire). Deployment-derived entries must reduce to the same semantic
  fields a catalog merge would produce for the same endpoint, so a
  catalog-listed endpoint acquired live does not oscillate the bytes.

## 9. Tests

New `tests/test_leasing_route_registry.py` (+ touch-ups where existing
`test_leasing_compose.py` asserts catalog-only `model_list`):

1. **Merge idempotence:** merging the same catalog twice → byte-identical
   registry.
2. **Hash stability across alternation (the headline property):** converge
   catalog A, then B, then A again (fake docker seam, shared state_dir) —
   renders 2 and 3 produce byte-identical `litellm_config.yaml` **and an
   equal rendered litellm service dict (assert on `CONFIG_HASH_LABEL` and
   the full service mapping, not just config bytes)** — the label is what
   actually drives recreation.
3. **Union correctness:** after A then B, the rendered `model_list` contains
   both catalogs' aliases, sorted.
4. **Live non-catalog deployment:** a deployment absent from the invoking
   catalog is merged from `desired` and remains routed on a later converge
   under a different catalog.
5. **Conflict:** redefine an endpoint's served name → incoming wins, warning
   emitted, bytes change (exactly one recreate).
6. **Seeding:** state_dir with a pre-existing `litellm_config.yaml` (both
   `openai/` and `ollama/` entries) and no registry → first converge under a
   disjoint catalog still routes the old aliases.
7. **Corrupt registry:** garbage JSON / non-map `entries` → warning,
   converge still succeeds, registry rebuilt from seed+catalog.
8. **Dynamic-routing isolation:** with `dynamic_routing=True` no registry
   file is created and `litellm_routes.json` behavior is unchanged.
9. **CLI:** `routes list` / `prune` / `seed` happy paths (seed then converge
   under another catalog → no config change).
10. **Concurrency smoke:** two backends on one state_dir converging
    different catalogs under the flock → final registry contains both (no
    lost update).
11. **Catalog-less converge (review concern A):** seed a registry via a
    catalog-full converge, then converge with `catalog=None` (as a bare
    release/gc does) → rendered config still contains the full registry,
    bytes unchanged, no fall-through to the legacy branch.
12. **Engine filter:** a `RESERVED_ENGINE` deployment in `desired`
    contributes no registry row (mirrors `render_compose` skipping it).
13. **Multi-alias deployment (review concern E):** a deployment whose
    `served` map carries two endpoint keys yields two registry rows sharing
    one `served` upstream; the rendered entries match what
    `_litellm_model_list` produces for the same deployment.
14. **Unknown-version tolerance (review concern F):** registry with
    `version: 99` and a valid `entries` map → rendered as-is with a warning,
    file NOT rewritten/reseeded.

Run with the repo-root `.venv` python and `-o addopts=""` (no xdoctest
plugin in that venv).

## 10. Acceptance (GPU host, manual)

1. Stack up with the olmo runbook config; `curl :14042/v1/models` → olmo
   aliases.
2. Converge under the qwen runbook config. Assert: `/v1/models` lists
   **both** catalogs; `docker ps -q --filter name=litellm` container ID
   **unchanged** (no recreate); an olmo completion through the gateway still
   succeeds.
3. Alternate converges A/B a few times: `litellm_config.yaml` mtime may
   update but bytes/hash stable; container ID stable throughout.
4. Regression of the incident: with an olmo lease live, run a qwen acquire;
   during the qwen model-load window, an olmo `/v1/completions` through the
   gateway keeps working, and the olmo readiness probe (if re-run) still
   sees its alias listed.

## 11. Rollout / commit plan

- **Repo:** `submodules/infer_stack`, branched off its current HEAD. Note
  the submodule already has unpushed work (e5fba7b, reserve-gpus); push
  ordering: submodule commits first, then any eval_audit gitlink bump —
  and per repo policy the gitlink bump is never auto-committed.
- **Commit 1:** registry core — constants, pure functions, `render_compose`
  kwarg, `ComposeBackend` wiring incl. `merge_route_registry`, seeding,
  tests 1–8 and 11–14.
- **Commit 2:** `routes` CLI group + docs (`docs/litellm-gateway-routing.md`
  gains a static-mode section; module docstrings in `compose.py` updated),
  tests 9–10.
- **Companion (separate, optional):** surface HTTP error bodies in the TUI
  API tab (`tui.py:2296` logs only `str(ex)`) and keep `infer-stack test`'s
  existing body-printing — the missing body is what made this incident hard
  to diagnose.

## 12. Out of scope

- Dynamic-routing changes, blue-green gateway swaps behind the reverse
  proxy, LiteLLM config hot-reload.
- eval_audit runbook `_lib.sh` seeding hooks (follow-up once the CLI verb
  exists).

---

## 13. Review record (Opus review → Fable resolutions)

Opus reviewed v1 against the code; every concern is resolved in the body
sections above (the resolution list below says where). First, the
load-bearing assumptions the review **verified hold**, so they are not
risks:

- **Shared state.** `state_dir = data_root() / 'leasing' / 'compose'`
  (`commands_leasing.py:233`) and `data_root()` honors the pinned
  `data_dir` setting (`paths.py:139-151`). All four runbooks pin
  `/data/service/infer-stack`, so they share one registry file and one
  `.converge.lock`. The concurrency story is sound.
- **Service-name parity.** In static mode (`unique=False`),
  `vllm_service_name(deployment) == vllm_service_name_for(served)`
  (`compose.py:142-147`), so a registry entry storing only `served` derives
  the same upstream whether the row came from a catalog or a live
  deployment. No oscillation from this axis.
- **Converge-without-acquire entry.** `Controller.reconcile(apply=True)` →
  `_render()` → `backend.converge(desired, apply=False)` (`controller.py:407`).
  This is what `routes prune`/`seed` call; it flows through
  `_update_route_registry`.
- **Path-based catalog load** (`Catalog.load(path)`, `catalog.py:230`) and
  **scfg variadic sub-actions** (`position=1` action + `nargs='*', position=2`
  files; precedent `commands_catalog.py:720`) both exist — so the `routes`
  verb shape is expressible.

### Resolutions (Fable, v2 — all concerns folded into the body above)

- **A. Trigger condition** — *accepted, stronger form.* The registry path is
  now unconditional for `litellm and not dynamic_routing`; `catalog is None`
  just means deployments-only incoming. This goes further than the suggested
  "registry-exists OR catalog" guard: the legacy branch is retired from the
  backend path entirely (first catalog-less converge on a fresh state_dir
  simply creates the registry from live deployments — same routes as legacy,
  minus the `depends_on` churn). See §2 (Activation), §5 (converge snippet),
  test 11.
- **B. `desired` spans all runbooks** — *accepted.* §4 item 2 now says
  "full `desired` set"; §2 states the registry's unique value explicitly
  (persistence past release + byte-stability), so the machinery is justified
  on its actual merits. Ledger sharing verified:
  `data_root()/leasing/ledger.db`.
- **C. Standalone registry write for seed** — *accepted.* New
  `ComposeBackend.merge_route_registry(incoming)` specified in §5; `routes
  seed` flow spelled out in §7 (Catalog.load → incoming → merge → reconcile).
  Flock is taken sequentially (merge, then converge), never nested. Seed
  requires the compose backend; pre-`stack up` seeding is supported and
  documented.
- **D. Ollama seeding lossiness** — *accepted, pragmatic option.* Seed
  imports vLLM rows exactly and skips Ollama rows with a warning; the
  `service` override is deleted from the schema (§3, §6). A skipped Ollama
  endpoint self-heals at its next catalog/live converge (one recreate,
  once).
- **E. Multi-alias deployments + served-name rule** — *accepted.* §4 item 2
  now gives the exact keying rule (one row per `deployment.served` key,
  `served` from `spec['served_model_name']` with the `compose.py:142`
  fallback chain; catalog rows `served_name or name`), plus the
  `RESERVED_ENGINE`/unknown-engine filter Opus's review didn't cover but the
  same audit surfaced. Tests 12–13.
- **F. Version downgrade** — *accepted (the suggested softening).* Unknown
  version + parseable `entries` map → preserve as-is, warn, don't rewrite;
  only structurally unusable files reseed. §6, test 14.
- **G. Other `catalog` consumers in `render_compose`** — *verified closed.*
  `catalog` feeds only the `entries` computation (grep over the full
  function body); noted inline in §5.
- **H. Apply/render lock interleaving** — *verified benign, reasoning now in
  §5.* `apply()` re-reads the on-disk file, and append-only ⇒ later render
  ⊇ earlier render, so last-writer-wins applies the largest union; worst
  case is the one justified recreate, never a flip-flop.
- **Smaller notes** — `routes` is a `scfg.ModalCLI` group per the
  `ConfigModalCLI` precedent (true nested subcommands exist; no positional
  dispatch needed) — §7. `routes prune` now requires an `_ApprovalMixin`
  confirm showing the exact drop list — §7. Test 2 now asserts on the
  rendered litellm service dict / `CONFIG_HASH_LABEL`, not just config
  bytes — §9.

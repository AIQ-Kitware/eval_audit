# Multi-model from-spec via local-deployment-scoped discovery strip — plan

**Status:** PLAN (not implemented).
**Goal:** enable a *single multi-model* from-spec manifest (so the OLMo grid can
fan out across GPUs under one schedule) by letting from-spec run-entries carry
their **local** `model_deployment` inline, and teaching discovery to ignore that
token **only when it names a local deployment**.
**Scope:** `aiq-magnet` discovery matcher + from-spec node;
`eval_audit/cli/check_precomputed_discovery.py` (+ its test); the OLMo exporter
preset; the grid. No change to leasing.
**Depends on / sequencing:** lands *after* the single-model from-spec migration is
GPU-verified (the migration plan's Change 5). See
[`olmo-from-run-spec-migration-plan.md`](../../planning/olmo-from-run-spec-migration-plan.md) and
[`from-spec-deployment-rewrite-plan.md`](from-spec-deployment-rewrite-plan.md).
**Supersedes:** the two earlier multi-model options explored in session
(matrix-`include:` pairing; node-side rewrite map). This is the cleaner third
option — keep the deployment inline (the existing run-entry multi-model
convention) and *narrow discovery* instead of reattaching the deployment after the
fact. 2026-06-29.

---

## 1. Motivation

Today the OLMo grid runs **serially**: the smoke/full scripts iterate presets in a
sequential bash loop, and each preset's manifest schedules with cmd_queue
`tmux_workers=1` (the OLMo presets don't override `_manifest_doc`'s default). So at
most one HELM run executes at a time, against one served model. infer-stack's
leasing *handles* concurrency (per-node `acquire --queue`, ref-count coalescing,
admission-queue serialization), but it is **demand-driven** — it only multiplexes
lease requests that are actually issued concurrently, and nothing upstream issues
them. The number of concurrent `acquire`s equals `tmux_workers`, which is 1.

To use multiple GPUs you need concurrent lease requests for *different* models —
i.e. one **multi-model manifest** scheduled with `tmux_workers > 1`, so leasing
co-hosts what fits and serializes the rest.

**The blocker.** A multi-model manifest needs a per-run `model_deployment` (which
local server each run uses). On the **run-entry** path that already works — the
existing `small_models_kubeai_overnight` preset puts `model_deployment=kubeai/…`
inline in each run-entry, and leasing reads it per run. But **from-spec** run
entries are bare discovery keys: the run-entry doubles as a *discovery query* that
must token-subset-match the official run directory, and the official run used a
*different* (official) deployment — so a `model_deployment=vllm/…` token in the
query is not a subset of the official dir name and the match fails. Proven against
the live corpus:

```
RESOLVED   (no model_deployment token)              # bare key → the official dir
NO_MATCH   model_deployment=vllm/allenai-olmo-7b    # our local name
NO_MATCH   model_deployment=together/olmo-7b        # even the official name
```

(The official OLMo dir name encodes `model=allenai_olmo-7b` but **no**
`model_deployment=` token at all, so *any* deployment token breaks the subset
match.)

## 2. The idea (and why "local-only")

Carry the **local** `model_deployment=vllm/allenai-<model>` inline in each
from-spec run-entry — exactly the run-entry multi-model convention — and make the
discovery matcher **ignore that token, but only when it names a local
deployment**. Official deployment tokens stay in the query so discovery can still
discriminate genuine multi-deployment models.

Why not "always ignore the token"? Because same-model-multiple-deployment is a
**real phenomenon** in public HELM, even though it never happens for OLMo:

- **OLMo (measured, 182 runs):** every model maps to exactly one deployment
  (`allenai/olmo-7b → together/olmo-7b`; the rest → `huggingface/<model>`), and
  **0** `(model, recipe-key)` pairs map to more than one deployment. The token
  carries zero discriminating information here, so stripping it is a true no-op —
  consistent with the bare keys already resolving at 0 AMBIGUOUS.
- **Corpus at large (measured):** **1,800 / 84,966** run dirs *do* carry a
  `model_deployment=` token in the name — dominated by the MedHELM
  `stanfordhealthcare_*` deployments (gpt-4o, claude, gemini, llama, deepseek,
  o3-mini, …) served privately *in addition to* their public deployments. HELM
  writes `model_deployment=` into the dir name precisely to disambiguate these.

A **global** "ignore `model_deployment`" would make a `stanfordhealthcare`-style
model AMBIGUOUS (the bare key would match both the public and the private run).
**Local-only** stripping avoids that: a local name can never appear in an official
dir, so dropping it is safe; an official name is kept and continues to
discriminate.

## 3. The classification rule — registry membership (prefix-agnostic)

A `model_deployment=<name>` token is **LOCAL iff `<name>` is registered in the
bundle's `model_deployments.yaml`** — the local registry the replay copies into the
run's `prod_env`/`local_path`. This is the set of deployments *we* created for
local serving. It is **not** a name-prefix heuristic, because prefixes are
ambiguous (`huggingface/` names both official `huggingface/olmo-1.7-7b` and local
`huggingface/phi-2-local`).

Truth table:

| Inline token | In bundle `model_deployments.yaml`? | In official dir name? | Discovery action | Outcome |
|---|---|---|---|---|
| `vllm/allenai-olmo-7b` | **yes (local)** | no | **strip** | bare-key match (✓, what we ship today) |
| `huggingface/phi-2-local` | **yes (local)** | no | **strip** | bare-key match — membership handles the `huggingface/` prefix correctly |
| `together/olmo-7b` | no (official) | no | keep | NO_MATCH → *don't inline official names* (bare key is right for these) |
| `stanfordhealthcare_gpt-4o-…` | no (official) | **yes** | keep | matches and **discriminates** (✓ preserved) |

So the only valid inline token is the **local rewrite target**; it gets stripped
for matching and reused as the rewrite target (§4.2). Official tokens are never
stripped — and inlining one is a no-op-to-harmful, so the rule "inline LOCAL only"
is documented and test-guarded.

## 4. Changes

### 4.1 `aiq-magnet` matcher — optional local-names parameter (the single rule)
`run_dir_matches_requested(...)` / `find_best_precomputed_run(...)` gain an
optional `local_deployment_names: frozenset[str] = frozenset()`. When matching, a
requested `model_deployment=<name>` with `name ∈ local_deployment_names` is dropped
from the requested-token set (not required to appear in the candidate). Default
empty ⇒ **current behavior unchanged** (back-compat). This is the *single source of
truth* for the strip rule; both the node (§4.2) and the dry-check (§4.3) pass the
set in. **Submodule change → gitlink bump + container rebuild.**

### 4.2 `aiq-magnet` from-spec node — inline token is the rewrite target
`materialize_helm_run_from_spec`:
1. Read the bundle's `model_deployments.yaml` (already copied in via
   `--model-deployments-fpath`) → the set of local deployment names.
2. Parse the inline `model_deployment=<local>` from the run-entry; pass the local
   set to discovery (§4.1) so the local token is ignored for matching.
3. Use the inline token as the **deployment-rewrite target** (rewrite
   `adapter_spec.model_deployment` to it after deserializing the official spec) —
   superseding the need for a manifest-level `--model-deployment` for the
   multi-model case. The `--model-deployment` flag is still honored when no inline
   token is present (single-model back-compat). Same registration check as today
   (target must be in `model_deployments.yaml`).

### 4.3 `check_precomputed_discovery.py` + test — mirror the node
The dry-check must apply the **same** local-strip so the preflight stays faithful
to the node. Build the local-name set from the preset
(`model_deployment_name` / `profiles[].model_deployment_name`) and thread it into
`_classify` → the matcher (§4.1). Update `tests/test_olmo_from_spec.py` to assert
**0 NO_MATCH / 0 AMBIGUOUS** with the inline local tokens present, plus a
**negative guard** (§5).

### 4.4 Exporter / preset — inline local tokens + a combined preset
- From-spec multi-model run-entries carry `model_deployment=<local>` inline (the
  run-entry multi-model convention); other Change-1 reductions stay (the bbq
  `output_format_instructions` drop, `eval_split` disambiguation). The bundle's
  `model_deployments.yaml` already registers all locals for a multi-model bundle.
- Add a **combined preset** (`profiles:` = the five parent-root models —
  `allenai-olmo-1-7-7b` + the four instruct; one `precomputed_root:
  /data/crfm-helm-public`). **olmo-7b-mmlu / -lite stay as separate single-model
  manifests** (they need the narrow `/mmlu` vs `/lite` roots; the parent root would
  reintroduce AMBIGUOUS for their shared MMLU subjects). Verified: olmo-1-7-7b
  resolves 57/57 with 0 AMBIGUOUS under the parent root, so the five share one root
  cleanly and **no per-entry `precomputed_root` is needed**.

### 4.5 Leasing — no change
`_resolve_lease_endpoint` already reads `model_deployment=` off the run-entry and
resolves it against the `lease_endpoints` map. Inline local tokens ⇒ multi-model
leasing works untouched.

### 4.6 Bridge — drop the single rewrite value for multi-model
The from-spec branch currently threads one manifest-level `model_deployment` as a
single `helm.model_deployment` matrix value. With the inline-token approach the
rewrite target comes from the entry, so stop emitting it for multi-model (keep it
for single-model back-compat).

### 4.7 Grid — combined bundle + `tmux_workers`
Export the combined bundle and run `eval-audit-run … --tmux_workers N` (and
`--devices`), so cmd_queue issues N concurrent leases and infer-stack co-hosts /
serializes across `INFER_STACK_ALLOWED_GPUS`. Keep the olmo-7b pair as separate
manifests (optionally run concurrently).

## 5. Tests

- **Discovery stays clean.** Dry-check = **0 NO_MATCH / 0 AMBIGUOUS** for every
  preset with inline local tokens (the local-strip makes matching identical to the
  bare keys verified today).
- **Negative guard (proves "local-only").** A synthetic entry whose
  `model_deployment=` names a *non-local* deployment (a `stanfordhealthcare`-style
  name, or `together/olmo-7b`) is **not** stripped → it stays in the query and
  behaves as a genuine discriminator / NO_MATCH. This is the test that would catch
  a regression to "always ignore".
- **Multi-model exporter test.** The combined bundle registers all five locals in
  `model_deployments.yaml`, the run-entries carry the matching inline locals, and
  the `lease_endpoints` map is present and keyed correctly.
- **Comparability unchanged.** Produced runs record `vllm/allenai-<model>` ⇒
  `same_deployment=no` (the existing `tests/test_olmo_from_spec.py` proof).
- **Container.** Rebuild the runner image and repin
  (`07_check_container_image.sh`) — the matcher + node changes are in-container.

## 6. Risks / edge cases

- **Inline an official token by mistake → NO_MATCH.** By design (it isn't local, so
  it's kept, and it isn't in the OLMo dir names). Document "inline LOCAL only"; the
  dry-check catches it as a hard stop.
- **Shared strip rule.** The node (in-container) and the dry-check (host) MUST use
  the *same* rule, or one passes while the other fails. Mitigation: the rule lives
  in one place — the magnet matcher param (§4.1) — and both callers pass the local
  set; nothing re-implements it.
- **stanfordhealthcare-style models stay non-reproducible-by-bare-key.** You cannot
  disambiguate two *official* deployments of one model via a from-spec entry
  (you'd need the official token in the query, which conflicts with the local
  rewrite). Out of scope; the 0-AMBIGUOUS test is the guard that flags it loudly if
  such a model ever enters a preset.
- **Container rebuild + gitlink discipline** (do not auto-commit the submodule
  bump; rebuild + repin before the grid uses the new node).

## 7. Sequencing

1. `aiq-magnet`: matcher `local_deployment_names` param (§4.1) + from-spec node
   inline-token handling (§4.2), with magnet unit tests. Gitlink bump → rebuild
   image → repin.
2. `eval_audit`: thread the local-name set through `check_precomputed_discovery`
   and `tests/test_olmo_from_spec.py` (§4.3), incl. the negative guard.
3. Exporter: combined preset + inline local tokens (§4.4); olmo-7b stays split.
4. Bridge tweak (§4.6) + grid wiring + `tmux_workers` (§4.7).
5. GPU smoke of the combined manifest; confirm multiple models co-host /
   serialize under leasing and `same_deployment=no` holds.

## 8. Why this over the earlier two options

| | matrix `include:` pairing | node-side rewrite map | **inline + local-strip (this plan)** |
|---|---|---|---|
| Deployment lives… | per-run in the matrix side-channel | one manifest-level map | inline in the run-entry (existing convention) |
| Lease change | bridge re-plumb | `model=` fallback in `lease_bracket` | **none** (reads the inline token today) |
| New manifest machinery | per-run `include:` rows | a rewrite map field | **none** |
| Container rebuild | no (host-only) | yes | yes |
| Main risk | subtle GHA `include` merge semantics | map threading | the discovery semantics change (narrow, test-guarded) |
| Consistency w/ existing multi-model | medium | low | **highest** (identical to the run-entry bundle) |

This plan reuses the existing run-entry multi-model convention end-to-end (inline
deployment + untouched lease resolution) and concentrates the change in one place
(the discovery matcher, scoped to local names). It costs the same container rebuild
as the node-map option but is simpler — no map threading, no lease change, no
matrix pairing.

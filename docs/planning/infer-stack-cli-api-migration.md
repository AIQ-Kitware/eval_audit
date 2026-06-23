# infer-stack CLI/API migration plan

**Status:** proposed · **Date:** 2026-06-22

The `submodules/infer_stack` pointer was bumped from `045a31f` →
`0344636`. That range is a hard, breaking rewrite: the
**profile / smoke / setup** world was replaced by a
**catalog (models + endpoints) + leasing** world. This document
inventories every location in `eval_audit` (superproject) that depends
on the old surface and the intended fix for each.

## 0. Decisions & assumptions (locked 2026-06-22)

These narrow the plan to the work that's actually wanted. Sections
below are kept as full reference, but **only the in-scope items get
fixed/validated**.

- **Scope = `dev/e2e-tests/` (phi-2) + `reproduce/olmo_models/` only.**
  Everything else is **archival / frozen** (left broken with a note):
  `reproduce/{gpt_oss_20b_core_grid,gpt_oss_20b_vllm,qwen2_72b_vllm,finish_qwen25_gptoss,small_models_kubeai}`.
  → drops §2.E (`manage.py` service starters), §2.F bundle-writers in
  those dirs, and §5.G4 (`active_profile`) out of the critical path.
- **No kubeai.** `small_models_kubeai` is frozen → **§5.G6 closes**
  (no `--namespace`/`deploy` verification needed).
- **G3 is effectively moot for the in-scope work — confirmed by
  inspection.** Both in-scope flows already run through the LiteLLM
  gateway: phi-2 presets declare `access_kind: openai-compatible`
  natively, and `reproduce/olmo_models/10_run_smoke_grid.sh:65-67`
  passes `--access-kind openai-compatible --base-url <gateway>/v1
  --api-key-value <key>`, **overriding** the olmo presets' declared
  `vllm-direct`. So default-B *matches today's behavior* — no
  vllm-direct port discovery, resolver stays pure-static, and the
  callers even supply `base_url`/`api_key` as flags. The logprob/`echo`
  fidelity concern is the **pre-existing status quo**, not introduced by
  this migration. (The vllm-direct A/C machinery in §5.G3 is retained
  only as a footnote for the archival presets.)
- **G1 source of truth is known.** The olmo HELM model/tokenizer
  aliases (case-sensitive, non-obvious — e.g. `allenai/OLMo-1.7-7B-hf`)
  currently live in `reproduce/olmo_models/config/infer_stack/models.yaml`
  (`tokenizer_name:` fields). The new `catalog.yaml` has no tokenizer
  field, so on reschema (§2.B) these move into the eval_audit olmo
  `PRESET_CONFIGS` as `helm_tokenizer_name` / `helm_model_name`.
- **Still load-bearing and in-scope:** §2.A (adapter — both flows call
  `export-benchmark-bundle`), §2.B/C/D for the two in-scope dirs,
  §5.G1, §5.G2 (olmo has base=completions + instruct=chat; phi-2=completions),
  and the two **semantic** hazards **C-1** (`acquire` accumulates — the
  olmo loop `switch`es one model at a time with no release;
  `10_run_smoke_grid.sh:57`) and **C-2** (`config_root` vs `data_root`
  split — scripts set only `INFER_STACK_CONFIG_DIR`).
- **Open (not blocking):** Q1 (pin infer_stack at a tag before §2.A),
  Q2 (HELM metadata stays in eval_audit presets — assumed yes),
  Q7 (keep `materialize_benchmark_bundle` output shape stable — assumed
  yes).

## 1. What actually changed

### Deleted Python modules (the deep break)
- `infer_stack/contracts.py` — **deleted**. Provided
  `load_profile_contract(profile, backend=, simulate_hardware_spec=)`
  returning the `serving-profile-contract` dict
  (`services[].model.logical_model_name`, `.tokenizer_name`,
  `services[].runtime.max_model_len`,
  `services[].access.default.{kind,base_url,request_model_name,auth_env_name,...}`,
  `services[].protocol.mode`). Our entire bundle-export adapter is built
  on this.
- `infer_stack/resolver.py`, `validator.py`, `verification.py` — deleted.
- `infer_stack/templates/default-profiles.yaml` and the `profiles:`
  concept — deleted. There are no "profiles" anymore, only catalog
  **models** + **endpoints**.

### CLI verb changes
`manage.py` is now just `from infer_stack.cli import main` — identical
to the `infer-stack` entry point — so `python manage.py <verb>` breaks
exactly like `infer-stack <verb>`.

| Old (gone/changed)                              | New equivalent | Notes |
|---|---|---|
| `infer-stack list-profiles`                     | `infer-stack catalog endpoint list` | profiles → endpoints |
| `infer-stack switch --profile X --apply --yes`  | `infer-stack acquire X --yes` | `acquire` (no `--ttl`) = standing lease; render+apply+wait |
| `infer-stack wait-ready`                         | `infer-stack wait` | same semantics |
| `infer-stack env --key NAME`                     | `infer-stack env NAME` | positional; bare `env` prints the `.env` path |
| `infer-stack down`                              | `infer-stack release --all --evict` | leasing-correct "free the GPUs"; `stack down` is the raw compose escape hatch |
| `manage.py setup --backend compose --profile X` | `infer-stack config init --backend compose` | one-time; no per-profile setup |
| `manage.py render`                              | `infer-stack render` | semantics changed: renders the **whole desired** compose project, not one profile |
| `manage.py up -d`                               | `infer-stack apply` | (or just `acquire`, which does render+apply+wait) |
| `manage.py status [--format json].active_profile` | `infer-stack leases --json` / `infer-stack status` | **`active_profile` no longer exists**; state is lease/deployment-based |
| `manage.py validate`                            | `infer-stack catalog validate` | |
| `manage.py deploy` / `switch … --namespace` (kubeai) | `infer-stack acquire … --backend kubeai` | ⚠️ kubeai `--namespace` surface needs on-machine verification |

### Config-directory schema changes
The `INFER_STACK_CONFIG_DIR` env var still works (`infer_stack/paths.py`),
but the *contents* changed:
- Old: `config.yaml` (with `active_profile`, `user_models_file`) +
  `models.yaml` (with `vllm_models:` and `profiles:` blocks).
- New: `settings.yaml` (durable settings) + `catalog.yaml` (`models:`,
  `endpoints:`, `runtime_hosts:`, `bundles:`).

### Managed env-file location changed
Old scripts read `submodules/infer_stack/generated/.env`. The new
managed env-file is at `data_root()/leasing/compose/.env`
(default `~/.local/share/infer_stack/leasing/compose/.env`). Discover it
with bare `infer-stack env`; read keys with `infer-stack env KEY`.
`LITELLM_MASTER_KEY` still exists; the **port key name**
(`INFER_STACK_LITELLM_PORT`) and default gateway port (now `14042`)
must be re-verified against the new env-file.

## 2. Affected locations & intended fixes

### A. Python integration layer — `eval_audit/integrations/infer_stack/` (highest priority)

**A1. `adapter.py`** — `load_profile_contract()` (lines ~807-826) calls
`infer_stack.contracts.load_profile_contract(...)`, which no longer
exists → `ModuleNotFoundError`. Everything downstream
(`export_benchmark_bundle`, `materialize_benchmark_bundle`,
`_select_service/_select_access/_model_deployment_entry`) consumes the
old contract dict shape.

The adapter needs, per model: HELM logical model name, tokenizer,
`max_model_len`, base_url, served/request model name, access kind
(`vllm-direct` vs `openai-compatible`), and protocol mode
(chat vs completions). **Most HELM-side facts already live in
`PRESET_CONFIGS`** (`helm_model_name`, `helm_tokenizer_name`,
`access_kind`, `model_deployment_name`,
`helm_max_sequence_and_generated_tokens_length`). What the contract
uniquely supplied: `base_url`, served/request model name,
`max_model_len` fallback, and the **chat-vs-completions** distinction
(which used to be encoded in profile *names* like
`gpt-oss-20b-completions` vs `gpt-oss-20b-chat`).

→ **Fix:** see the strategic decision in §3. Recommended: replace
`load_profile_contract` with a new resolver
(`resolve_serving_facts`) that reads the new catalog via
`infer_stack.leasing.Catalog.load(<config>/catalog.yaml).resolve_endpoint(name)`
(→ `served['served_model_name']`, `served['hf_model_id']`,
`capacity['max_model_len']`) and derives `base_url` from config/`env`,
while moving the protocol-mode (chat/completions) and access-kind facts
explicitly into the preset/profile spec. Keep
`materialize_benchmark_bundle`'s output shape unchanged so the rest of
the pipeline (Stage 2/3 bundle consumers) is untouched.

**A2. `__main__.py`** — `describe-contract` and `export-benchmark-bundle`
subcommands import `load_profile_contract` (line 7) and forward
`--simulate-hardware` / `--vllm-root`. → **Fix:** keep the CLI shape; if
the resolver no longer simulates hardware/contracts, either drop
`describe-contract` or repoint it at the catalog resolver. `--vllm-root`
becomes "path to the infer_stack config dir / catalog".

**A3. `tests/test_infer_stack_integration.py`** — imports
`load_profile_contract`; builds a fixture infer_stack root via
`infer_stack.config.initial_config()` + `save_yaml`, writing
`models.yaml` = `{"models": {}, "profiles": {}}` (old schema). Asserts on
the old contract dict (`contract["kind"] == "serving-profile-contract"`,
`contract["services"][0]...`). Profile names used
(`qwen2-72b-instruct-tp2-balanced`, `gpt-oss-20b-completions`,
`gpt-oss-20b-chat`) no longer exist. → **Fix:** rewrite the fixture to
emit the new `settings.yaml` + `catalog.yaml` (models + endpoints) and
re-assert against the new resolver output. `save_yaml` is still
importable from `infer_stack.config`; `initial_config()`'s shape
changed — verify its new top-level keys.

### B. Shipped infer-stack config dirs (reschema)

**B1. `dev/e2e-tests/config/infer_stack/{config.yaml,models.yaml}`** —
`config.yaml` has `active_profile: phi2-single`, `user_models_file`;
`models.yaml` has `vllm_models:` + `profiles:` (`phi2-single`).
*(`config.yaml` is currently modified in the working tree.)*
→ **Fix:** convert to `settings.yaml` + `catalog.yaml`. The `phi2-single`
profile becomes a catalog **model** (`phi-2`, `source: hf://microsoft/phi-2`)
+ **endpoint** (`phi-2`, `engine: vllm`, `runtime.max_model_len: 2048`).

**B2. `reproduce/olmo_models/config/infer_stack/{config.yaml,models.yaml}`**
— same pattern; six `*-single` profiles
(`allenai-olmo-7b-single`, `allenai-olmo-1-7-7b-single`,
`allenai-olmo-2-1124-7b-instruct-single`,
`allenai-olmo-2-1124-13b-instruct-single`,
`allenai-olmo-2-0325-32b-instruct-single`,
`allenai-olmoe-1b-7b-0125-instruct-single`).
→ **Fix:** convert each to a catalog model + endpoint pair in
`catalog.yaml`. Drop `active_profile` (no active-profile concept).
Preserve `tensor_parallel_size`/`max_model_len` under
`endpoints.<name>.runtime`.

### C. Profile-existence preflight scripts

**C1. `dev/e2e-tests/05_check_profiles.sh`** and
**C2. `reproduce/olmo_models/05_check_profiles.sh`** — both run
`available="$(infer-stack list-profiles ...)"` and assert each
`<preset>-single` profile is present. → **Fix:** call
`infer-stack catalog endpoint list` and assert the new **endpoint**
names exist. Update the "profiles not defined" error text + the comment
pointing at `models.yaml` to point at `catalog.yaml`.

### D. Smoke / full grid runners (host vLLM via LiteLLM)

Files (4): `dev/e2e-tests/10_run_smoke_grid.sh`,
`dev/e2e-tests/15_run_full_grid.sh`,
`reproduce/olmo_models/10_run_smoke_grid.sh`,
`reproduce/olmo_models/15_run_full_grid.sh`.

Each does, per model:
- `LITELLM_PORT="$(infer-stack env --key INFER_STACK_LITELLM_PORT)"`
- `LITELLM_MASTER_KEY="$(infer-stack env --key LITELLM_MASTER_KEY)"`
- (e2e only) `infer-stack down`
- `infer-stack switch --profile "$profile" --apply --yes`
- `infer-stack wait-ready`

→ **Fix:**
- `infer-stack env --key NAME` → `infer-stack env NAME` (×2 each).
  Re-verify the port key name / value (gateway default now `14042`).
- `infer-stack down` → `infer-stack release --all --evict`.
- `infer-stack switch --profile X --apply --yes` →
  `infer-stack acquire X --yes` (acquire already waits; the following
  `wait-ready` becomes redundant but harmless once renamed).
- `infer-stack wait-ready` → `infer-stack wait` (or drop, since `acquire`
  waits by default — keep one explicit `wait` for clarity).
- The loop variable `$profile`/`$serving` is now an **endpoint** name;
  rename for legibility and make sure it matches the `catalog.yaml`
  endpoint, not a `-single` profile.

### E. `manage.py`-driven service starters (reproduce/) — ❄️ FROZEN (out of scope per §0)

> All three dirs below are archival per §0 (gpt-oss/finish/kubeai). Left
> broken with a note; not fixed. Retained as reference for if they're
> ever revived.

**E1. `reproduce/gpt_oss_20b_core_grid/10_start_service.sh`** &
**E2. `reproduce/finish_qwen25_gptoss/10_start_service.sh`** — use
`python manage.py status [--format json]` (+ `.active_profile`
extraction), `manage.py switch "$PROFILE" --apply`,
`manage.py setup --backend compose --profile "$PROFILE"`,
`manage.py render`, `manage.py up -d`. → **Fix:** replace the
`active_profile` gate with `infer-stack leases --json` parsing (or just
idempotently `acquire`); `setup … --profile` → one-time
`config init --backend compose`; `switch … --apply` / `render` +
`up -d` → `infer-stack acquire <endpoint>` (or `render` + `apply`).
`finish_qwen25_gptoss/00_check_env.sh` line 69:
`manage.py list-profiles | grep -q pythia-qwen25-gptoss-mixed-4x96` →
`infer-stack catalog endpoint list` against the new endpoint name(s).
Note: `pythia-qwen25-gptoss-mixed-4x96` was a *multi-model profile*;
under the new model it becomes a **bundle** of endpoints
(`catalog bundle add`) served together.

**E3. `reproduce/small_models_kubeai/05_deploy_models.sh`** — kubeai path:
`manage.py setup --backend kubeai --profile …`, `manage.py validate`,
`manage.py deploy`, `manage.py switch … --apply --namespace …`,
`manage.py status`. → **Fix:** `config init --backend kubeai`,
`catalog validate`, then `acquire --backend kubeai`. ⚠️ The
`--namespace` flag and a distinct `deploy` verb may not exist in the new
CLI — **verify the kubeai backend surface on-machine** before finalizing
this script's fixes.

### F. Bundle-writer env sourcing (reproduce/) — ❄️ FROZEN (out of scope per §0)

> Every file in this group lives under an archival dir per §0. Not
> fixed. (The in-scope e2e + olmo flows call `export-benchmark-bundle`
> directly from their grid runners — covered in §D, not here — and pass
> `--base-url`/`--api-key-value` from `infer-stack env`, so they don't
> depend on a separate `generated/.env`.) Reference only:

Files: `reproduce/gpt_oss_20b_core_grid/05_write_bundle.sh`,
`reproduce/gpt_oss_20b_vllm/05_write_bundle.sh`,
`reproduce/finish_qwen25_gptoss/05_write_bundle.sh`,
`reproduce/qwen2_72b_vllm/05_write_bundle.sh`,
`reproduce/small_models_kubeai/10_write_bundle.sh`, and the validators
`*/15_validate_server.sh`, `reproduce/finish_qwen25_gptoss/16_curl_test_bundle.sh`.

These default `LITELLM_ENV_FPATH` to
`submodules/infer_stack/generated/.env` (old path) and call
`python -m eval_audit.integrations.infer_stack export-benchmark-bundle`.
→ **Fix:** (a) the `export-benchmark-bundle` invocation depends on the
adapter fix in §A — keep the same CLI flags if we preserve the adapter
contract; (b) repoint `LITELLM_ENV_FPATH` to the new managed env-file
(`$(infer-stack env)` prints its path) or read keys directly via
`infer-stack env LITELLM_MASTER_KEY`.

### G. Docs & low-priority comment cleanup

- `reproduce/olmo_models/README.md`, `dev/e2e-tests/README.md`,
  `reproduce/finish_qwen25_gptoss/README.md`,
  `reproduce/gpt_oss_20b_core_grid/README.md`,
  `reproduce/*/README.md`, `reproduce/README.md` — update all
  `switch/wait-ready/env --key/list-profiles/-single profile` references
  to the new verbs + catalog/endpoint vocabulary.
- `docs/planning/olmo-smoke-grouped-runner.md` (esp. the documented
  `switch --profile … --apply` + `wait-ready` recipe and `-single`
  profile naming). Other planning docs reference the old INFER_STACK env
  contract.
- `_lib.sh` files (`reproduce/olmo_models/_lib.sh`,
  `dev/e2e-tests/_lib.sh`): comments referencing "profile to switch
  into", `phi2-single`, and `INFER_STACK_ALLOWED_GPUS` —
  re-verify GPU-allowlist env var name under the new leasing model
  (GPU selection moved to `skip_display_gpus` / placement).
- Stale TODO-only mentions (no code change needed beyond wording):
  `eval_audit/model_registry.py` (docstring + "verify infer_stack
  profiles can switch"), `eval_audit/reports/filter_analysis_text.py`
  (TODO comment), `configs/virtual-experiments/e2e-phi2-hf.yaml`
  ("no infer-stack" comment is still accurate — no change).

## 3. Strategic decision (the one real fork): how the adapter gets serving facts

The contract module is gone. Three ways to feed `adapter.py`:

1. **Catalog-resolver (recommended).** New
   `resolve_serving_facts(endpoint, config_dir)` reads the new
   `Catalog` for model_ref/tokenizer/`max_model_len`, derives base_url
   from config, and takes the chat-vs-completions + access-kind from the
   preset spec (move that distinction into `PRESET_CONFIGS`, since the
   profile names that encoded it are gone). Smallest downstream blast
   radius; keeps `materialize_benchmark_bundle` output identical.
2. **Preset-self-contained (decouple).** Push *all* serving facts
   (base_url, served name, protocol mode, tokenizer, max_model_len) into
   `PRESET_CONFIGS`/profile specs and delete the infer_stack import
   entirely. Most robust against future infer_stack churn; duplicates
   facts that already live in `catalog.yaml`.
3. **Leasing-descriptor.** Acquire a lease and read
   `build_descriptor()` / `backend.access()` for the realized base_url +
   served name. Most "correct" but requires a *live* served endpoint at
   bundle-export time — a heavier coupling than today.

Recommendation: **#1**, falling back to **#2** for facts the catalog
doesn't carry (protocol mode, HELM aliasing). This preserves the
existing pipeline contract and test surface with the least churn.

## 4. New-API replacement map (per old field / verb)

Verified against the new tree (`infer_stack/leasing/{catalog,models,envfile,compose}.py`,
`config.py`, `paths.py`).

### 4A. Old contract dict fields the adapter consumes → new source

The adapter reads these in `_select_service` / `_select_access` /
`_model_deployment_entry`:

| Old contract field | Adapter use | New source |
|---|---|---|
| `model.served_model_name` / `model_ref` | request/served name (`vllm_model_name`, `openai_model_name`) | ✅ `Catalog.load(cfg).resolve_endpoint(ep).served['served_model_name']` / `.spec['hf_model_id']` |
| `runtime.max_model_len` | `max_sequence_length` / gen budget | ✅ `EndpointRequest.capacity['max_model_len']` (`models.py:245`) |
| `model.logical_model_name` | HELM model **alias** (`model_name`) | ⚠️ **no catalog field** → preset `helm_model_name` (see §5.G1) |
| `model.tokenizer_name` | HELM tokenizer alias | ⚠️ **no catalog field at all** → preset `helm_tokenizer_name` (see §5.G1) |
| `protocol.mode` (`chat`/`completions`) | picks `OpenAIClient` vs `OpenAILegacyCompletionsClient` vs `VLLMChatClient`/`VLLMClient` | ⚠️ **not modeled** (front door is always OpenAI `/v1`) → preset `protocol_mode` (see §5.G2) |
| `access.default.kind` (`vllm-direct`/`openai-compatible`) | client class + auth branch | preset `access_kind`; under default-B all presets serve openai-compatible via the gateway, so `vllm-direct` is fallback-only (§5.G3) |
| `access.default.base_url` | client `base_url` | ✅ deterministic gateway `http://127.0.0.1:14042/v1` for **all** presets (`compose.py:978`); the vllm-direct dynamic-port path is fallback-only (§5.G3) |
| `access.default.request_model_name` | request name | ✅ descriptor `['endpoints'][ep]` / `served['served_model_name']` (`envfile.py:71`) |
| `access.default.auth_env_name` | API-key env | ✅ constant `LITELLM_MASTER_KEY` (`compose.py:58`); vllm-direct → none |
| `backend` (`compose`/`kubeai`) | auth branching | ✅ `infer-stack config get backend` / preset `backend` |

### 4B. Adapter functions → disposition

| Old | Replacement |
|---|---|
| `adapter.load_profile_contract()` → `infer_stack.contracts.load_profile_contract` | new `resolve_serving_facts(endpoint, config_dir)` over `Catalog.resolve_endpoint()` + config |
| `_select_service(contract)` | collapses — a catalog endpoint **is** the single service |
| `_select_access(service, kind)` | collapses — no `access.{default,additional}` list; gateway only (vllm-direct port discovery is a fallback path, §5.G3) |
| `_model_deployment_entry`, `_default_deployment_name`, `_resolve_api_key`, `_manifest_doc`, `materialize_benchmark_bundle`, `_assert_helm_aliases_exist` | **unchanged** — consume resolved facts, never import infer_stack; only their input source changes |
| `_profile_specs` / `PRESET_CONFIGS` | extend with `protocol_mode`; `profile` field becomes a catalog **endpoint** name |

### 4C. Operational CLI verbs → new verbs

| Old | New |
|---|---|
| `list-profiles` | `catalog endpoint list` |
| `switch --profile X --apply --yes` | `acquire X --yes` (render+apply+wait) |
| `setup --backend compose --profile X` | `config init --backend compose` (one-time) |
| `render` → `up -d` | `render` → `apply` (or just `acquire`) |
| `wait-ready` | `wait` |
| `down` | `release --all --evict` (or `stack down` raw) |
| `status --format json` → `.active_profile` | `leases --json` (no `active_profile`; see §5.G4) |
| `validate` | `catalog validate` |
| `deploy` / `switch … --namespace` (kubeai) | `acquire … --backend kubeai` (⚠️ surface unverified — §5.G6) |

### 4D. Config / env / Python-module replacements

| Old | New |
|---|---|
| `infer-stack env --key NAME` | `infer-stack env NAME` (positional); bare `env` prints `.env` path |
| `submodules/infer_stack/generated/.env` | `data_root()/leasing/compose/.env` — discover via `$(infer-stack env)` |
| `INFER_STACK_LITELLM_PORT` (configurable) | gateway fixed default **14042** |
| `LITELLM_MASTER_KEY` | ✅ unchanged (`API_KEY_ENV` constant) |
| `config.yaml` + `models.yaml` (`active_profile`, `vllm_models:`, `profiles:`) | `settings.yaml` + `catalog.yaml` (`models`/`endpoints`/`runtime_hosts`/`bundles`) |
| `infer_stack.config.initial_config()` | **removed** → `infer_stack.paths.load_settings()` / `get_setting()`; `save_yaml`/`load_yaml`/`dump_yaml` still in `infer_stack.config` |
| `infer_stack.contracts` / `resolver` / `validator` | `infer_stack.leasing` (`Catalog`, `EndpointRequest`, `build_descriptor`, `ComposeBackend`) |

## 5. Handling functionality with no new-API equivalent

Six facts/capabilities the new API does **not** provide. The unifying
principle: **HELM-domain facts (alias, tokenizer, protocol) never
belonged in a serving layer — pull them into eval_audit's presets;
transport facts stay resolved from infer_stack.**

### G1 — HELM model alias + tokenizer (`logical_model_name`, `tokenizer_name`)
The catalog's structural fields are engine-only (`model_ref`,
`served_name`, `chat_template`, …); there is **no tokenizer and no HELM
alias**. → **Make `PRESET_CONFIGS.helm_model_name` /
`helm_tokenizer_name` authoritative.** The resolver returns only
`served_model_name` + `hf_model_id` + `max_model_len`. Adapter
resolution order:
- `model_name` (HELM alias) = preset `helm_model_name`; if absent,
  fall back to catalog `hf_model_id` and let the existing
  `_assert_helm_aliases_exist()` fail loudly if that isn't a registered
  HELM alias (no silent wrong alias).
- `tokenizer_name` = preset `helm_tokenizer_name`, defaulting to catalog
  `hf_model_id`.
- **Action:** audit all 14 presets; backfill `helm_*` for any that
  leaned on the old contract default (most already set them — e.g.
  vicuna's `hf-internal-testing/llama-tokenizer`). Keep
  `_assert_helm_aliases_exist` as the guardrail. **Cost: low.**

### G2 — Protocol mode (chat vs completions)
The old `gpt-oss-20b-completions` vs `-chat` profile split surfaced as
`protocol.mode`; the new world has no such field (LiteLLM front door is
always OpenAI `/v1`). The catalog's `chat_template` is a hint, not
authoritative. → **Add an explicit `protocol_mode: "chat" | "completions"`
to the preset/profile spec.** `_benchmark_client_class(protocol_mode,
access_kind)` stays; `protocol_mode` now comes from the preset
(default `"chat"`). Only the two gpt-oss presets set it explicitly.
**Cost: low (one preset field + drop the contract read).**

### G3 — vllm-direct transport → route through the gateway (DEFAULT: Option B)
Old `access_kind: vllm-direct` presets (OLMo ×6, qwen2_72b) pointed
HELM's `VLLMClient`/`VLLMChatClient` **directly at the vLLM server**,
bypassing LiteLLM. The new leasing descriptor / `backend.access()` only
hands back the **gateway** URL (`http://127.0.0.1:14042/v1`).

**Why this is second-order (the reason B is the default).** The official
HELM runs for these open-weight models were served on **TogetherAI**, so
the local side is *already* a different deployment no matter what — that
cross-deployment gap is the first-order variable the audit measures. The
local choice of `VLLMClient`-direct vs `OpenAIClient`-via-LiteLLM sits
underneath it and is second-order. For **generative** scenarios at
temperature 0, LiteLLM is a single-backend pass-through router (it does
not re-run the model), so direct vs proxied yields the same greedy token
stream. The chat-vs-completions prompt-wrapping concern is **G2
(`protocol_mode`)**, not this — it is preserved regardless of transport.

**The one residual risk — and its gate.** The only mechanism where the
proxy could bite is logprob/`echo` scoring for `multiple_choice_joint`
(most of the OLMo grid): HELM scores choices via `echo=True` +
prompt-token `logprobs` on `/v1/completions`. With `protocol_mode` kept
at `completions`, the gateway client is `OpenAILegacyCompletionsClient`,
which speaks the **same** `/v1/completions` echo+logprobs API as
`VLLMClient`; the only question is whether LiteLLM forwards it
faithfully. → **Gate: one-time fidelity check** — send
`/v1/completions` with `echo=True, logprobs=5, max_tokens=0` through the
gateway and direct to vLLM and diff `logprobs.token_logprobs`. If they
match, B is fully equivalent for these tasks.

- **Option B (DEFAULT — gateway).** Route the 6 ex-vllm-direct presets
  through the deterministic gateway: `base_url = :14042/v1`, client class
  per `protocol_mode` (`OpenAILegacyCompletionsClient` / `OpenAIClient`),
  auth `LITELLM_MASTER_KEY`. **Keeps the resolver pure-static** (catalog
  + config, no port discovery), one client path to maintain, and is the
  **consistent choice for cross-machine validation** (a fixed gateway URL
  is identical on yardrat/namek/aiq-gpu; direct `18000+i` ports renumber
  per machine/run). Adapter/test consequence: these presets' generated
  entries flip from `VLLMClient` / no-auth → `OpenAI*` /
  `LITELLM_MASTER_KEY`; update the `vllm-direct` integration-test
  assertions accordingly (folds into §A3). `access_kind: vllm-direct`
  becomes a **fallback-only** marker.
- **Option A (FALLBACK — only if the gate fails).** Keep `VLLMClient`
  and resolve the direct base_url *after* `render`/`acquire` from the
  rendered `docker-compose.yml` (the published
  `VLLM_HOST_PORT_BASE(18000) + creation-order-index` port,
  `compose.py:165,526` — dynamic, not in the catalog). Preserves the
  exact client class but makes the resolver stack-state-dependent and the
  bundle's port fragile across up/down cycles.
- **Option C (LONG-TERM).** Ask infer_stack upstream for a per-endpoint
  host-port pin so the direct URL is static — gives A's identical recipe
  with B's determinism. We own the submodule, so feasible later.
- **Plan:** run the gate check during §A; default to **B**; keep **A**
  documented as insurance; file **C** upstream. Because B keeps the
  resolver pure-static, **G3 no longer blocks the resolver signature** —
  it is no longer the first domino.

### G4 — `active_profile` status gate — ❄️ out of scope per §0
> Only the frozen `reproduce/*/10_start_service.sh` starters use this
> gate; the in-scope olmo/e2e grid runners don't. Reference only.

`reproduce/*/10_start_service.sh` gate on
`manage.py status --format json → .active_profile` to decide whether to
switch. The new model is reference-counted and idempotent. → **Drop the
gate; call `infer-stack acquire <endpoint>` unconditionally** (it
coalesces onto an existing deployment). If an explicit check is still
wanted, parse `infer-stack leases --json` for the endpoint name instead
of scraping stdout. **Cost: low; removes brittle parsing.**

### G5 — `simulate_hardware` + `describe-contract`
`load_profile_contract(simulate_hardware_spec=…)` let bundle export run
**without GPUs** (CI, or a non-serving host) by simulating an inventory
for the resolver. → **No longer needed:** `Catalog.resolve_endpoint()`
is hardware-free, and the gateway base_url is deterministic from config,
so declarative export still works offline for the openai-compatible path
(the only thing that needs a live/rendered stack is G3's vllm-direct
port). **Plan:** drop `simulate_hardware`/`vllm_root` GPU-simulation from
the resolver; have `__main__` accept-and-ignore `--simulate-hardware`
for one release (deprecation) then remove. Repoint `describe-contract`
to a slim `describe-endpoint` that prints the resolved facts dict
(served name, max_model_len, base_url, HELM aliases, protocol mode) —
keep for debugging. The test fixture no longer needs hardware
simulation, just a `catalog.yaml`. **Cost: low; net simplification.**

### G6 — kubeai `deploy` / `switch --namespace` — ❄️ CLOSED (no kubeai, per §0)
> Resolved by the §0 decision not to use kubeai;
> `small_models_kubeai` is frozen, so the unverified kubeai surface no
> longer needs investigation. Reference only.

`reproduce/small_models_kubeai/05_deploy_models.sh` uses a distinct
`deploy` verb and `--namespace`. The new CLI exposes backends as
`--backend kubeai` on `acquire`; a separate `deploy` verb and
`--namespace` flag are **not confirmed present**. → **Plan: verify the
kubeai surface on-machine before editing this script.** If `--namespace`
is gone, namespace selection likely moved into `config`/`catalog`
(kubeai cluster settings) — adjust the config-dir reschema (§B) to carry
it rather than passing it per-command. This is the one script whose fix
is **blocked on verification**; sequence it last. **Cost: unknown until
verified.**

## 6. Validation

- `python -m py_compile` on `adapter.py`, `__main__.py`.
- `pytest tests/test_infer_stack_integration.py` after the fixture +
  resolver rewrite (fixture → new `catalog.yaml`; drop/xfail the
  gpt-oss/qwen/kubeai assertions for the frozen presets; keep phi-2 +
  olmo).
- Re-run the e2e phi-2 smoke grid (`dev/e2e-tests/`) end-to-end on a GPU
  box: `05_check_profiles.sh` → `10_run_smoke_grid.sh`, confirming
  `acquire`/`wait`/`env` behave and a bundle is produced.
- Run the olmo smoke grid (`reproduce/olmo_models/`) for **≥2 models**
  back-to-back — this exercises **C-1** (confirm the second model isn't
  blocked/OOM'd by the first still holding GPUs; i.e. the per-iteration
  `release` works) and **C-2** (confirm `infer-stack env` reads the same
  `.env` the script sources).

## 7. Suggested sequencing (in-scope = e2e + olmo only)

0. **Pin** infer_stack at a tag (Q1) and `uv pip install -e
   submodules/infer_stack` so the CLI on PATH matches the vendored
   source (C-8).
1. **§A** adapter + `__main__` + test rewrite — `load_profile_contract`
   → pure-static `resolve_serving_facts` (catalog `{served_model_name,
   max_model_len}` + preset `{helm aliases, tokenizer, protocol_mode}`;
   `base_url`/`api_key`/`access_kind` stay caller-supplied). Fold in
   **G1** (move olmo tokenizer/model aliases from `models.yaml` into the
   presets) and **G2** (`protocol_mode` per model). Unblocks both grids.
2. **§B** config-dir reschema → `settings.yaml` + `catalog.yaml`
   (`litellm: true`, `ui: false` per C-4): e2e first, then olmo.
3. **§C** (`05_check_profiles` → `catalog endpoint list`) + **§D** grid
   runners for both dirs — verb swaps **plus C-1** (insert `release`
   between models) **plus C-2** (`INFER_STACK_DATA_DIR`).
4. **Validate** per §6 (e2e grid, then olmo ≥2-model grid).
5. **§G** docs/comments for the two in-scope dirs only.

> Frozen (per §0), not sequenced: §2.E, §2.F, the archival `reproduce/*`
> dirs, G4, G6/kubeai.

## 8. Latent semantic hazards (won't error — they silently misbehave)

These are paradigm-shift traps, not verb renames. The old API was
stateless/declarative ("resolve a profile"); the new one is stateful,
reference-counted, and dynamically-placed ("lease a deployment"). Scope
tag is relative to §0.

| # | Hazard | In scope? | Fix |
|---|---|---|---|
| **C-1** | `acquire` **accumulates** (demand ref-counted, `ledger.py:88`) — old `switch` *replaced*. The olmo loop (`10_run_smoke_grid.sh:57`) brings up one model at a time with no teardown → all 6 stack up → OOM | ✅ **yes** | insert `release`/`evict` between models, or use `infer-stack run --endpoint X -- <cmd>` (acquire→run→auto-release) |
| **C-2** | `config_root` vs `data_root` are **separate** (`paths.py:44-45`); scripts set only `INFER_STACK_CONFIG_DIR`, so the managed `.env`/ledger land in the default `data_root`, not where `LITELLM_ENV_FPATH` expects | ✅ **yes** | also set `INFER_STACK_DATA_DIR` (repo-local); derive paths via `infer-stack env` / `config paths` |
| **C-3** | name chain fractured: catalog **model** ≠ **endpoint** alias ≠ **public_name** (LiteLLM-registered) ≠ HELM request name ≠ HELM alias. Mismatch → gateway 404, empty results | ✅ **yes** | resolver keeps `openai_model_name == endpoint.public_name == served name`; assert at export |
| **C-4** | bigger/optional compose footprint — `acquire` brings up Open WebUI + Postgres by default; **LiteLLM is now optional** (`config init` prompts). If litellm off, default-B base_url breaks | ✅ **yes** | pin `litellm: true`, `ui: false` in the shipped `settings.yaml` |
| **C-5** | sizing moved resolver → **static catalog** (`tensor_parallel_size`, `gpu_memory_utilization`, `max_model_len`). Forget tp on the 32B olmo → OOM | ✅ **yes** (olmo 32B) | set `runtime.*` explicitly per endpoint in `catalog.yaml`; `catalog suggest` can seed |
| **C-6** | multi-model presets → **bundles** with dynamic placement; co-residency no longer guaranteed | ❄️ archival | n/a (frozen presets) |
| **C-7** | two "backend" namespaces: manifest `backend: tmux` (HELM exec) vs infer_stack `backend: compose` (serving) | ✅ aware | don't conflate when editing presets |
| **C-8** | installed `infer-stack` (PATH) vs vendored submodule source (adapter `sys.path` import) can skew | ✅ **yes** | `uv pip install -e submodules/infer_stack` after the pin (seq step 0) |
| **C-9** | GPU default flipped to use-all + opt-in display-skip. `INFER_STACK_ALLOWED_GPUS` *is* still honored (`context.py:88`), and olmo `_lib.sh` sets it | ✅ low | keep setting the env var; no change needed |
| **C-10** | stale + machine-specific hardcoded paths (a frozen `05_write_bundle.sh` defaults to `…/jon.crall/…/generated/.env`) | ❄️ archival | n/a |
| **C-11** | `render` now renders the **whole desired set**, not one model (tied to C-1) | ✅ aware | don't assume `render` is per-model |

**Out of scope entirely (not affected):** the EEE-only path
(`from_eee`, `compare_pair_eee`, `build_virtual_experiment` over EEE
artifacts) consumes HELM/EEE artifacts directly and never touches
infer_stack.
</content>

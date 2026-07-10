# dev/era-tests: the pre-v0.5 validation gates as a turnkey dev runbook

**Status**: plan, ready to implement.
**Branch**: implement on `impl/era-pinned-helm-containers` (all era code lives
there; it descends from `impl/run-from-run-spec` @ `6092144`).
**Supersedes**: the runbook half of
[`era-pinned-helm-containers-plan.md`](era-pinned-helm-containers-plan.md) §6
(`reproduce/classic_era_replay/` moves to `dev/era-tests/`).
**Prerequisite**: Phase 0 below — the CRITICAL findings in
[`era-pinned-review-findings-2026-07-10.md`](era-pinned-review-findings-2026-07-10.md)
break the exact path this runbook drives.

## Goal

Rebuild `reproduce/classic_era_replay/` as **`dev/era-tests/`**, mirroring the
shape and conventions of [`dev/e2e-tests/`](../../dev/e2e-tests/): numbered
`NN_verb_noun.sh` stages, a shared `_lib.sh`, checked-in serving config and
presets, and per-scenario virtual-experiment manifests — so that one model runs
**end-to-end through both classic eras** (Stages 3→6, official comparison
included) with essentially no per-user setup. The current runbook's tier-2
(the actual e2e) is entirely hand-fed via `ladder.env`
(`ERA_PRESET`/`SOURCES_FPATH`/`IMAGE_REF`); after this migration those are all
checked-in defaults.

What the runbook must exercise ("all capabilities"):

- **both era images** (`helm-v0.2.4`, `helm-v0.3.0`) — build, label guard, shim;
- **both HELM methods** the pinned corpus uses — `generation` and
  `multiple_choice_joint` (the shim client's echo/logprobs path);
- **the full pipeline**: era bundle export → frozen exact-path manifest →
  containerized era replay against host vLLM → index → compose (official
  pairing) → aggregate summary;
- **the pre-v0.5 gates** (validation-ladder rungs 0–2, 5) as preflights, kept
  PASS/FAIL/SKIP-tabled.

## Test subject (verified against the corpus on 2026-07-10)

**`eleutherai/pythia-6.9b`** — the only audit-corpus model with a full official
packet at **both** classic eras: 74 runs each under
`/data/crfm-helm-public/classic/benchmark_output/runs/{v0.2.4,v0.3.0}/`.
Grid = 2 eras × 1 model × 2 scenarios:

| run entry (official dir name minus `,model=…` where noted) | method | why |
|---|---|---|
| `synthetic_reasoning_natural:difficulty=easy,model=eleutherai_pythia-6.9b` | `generation` | the plan's flagship rung-3 demo: local replay expected to recover ~20% vs the official 0% (known Together deployment artifact) |
| `mmlu:subject=us_foreign_policy,method=multiple_choice_joint,model=eleutherai_pythia-6.9b,data_augmentation=canonical` | `multiple_choice_joint` (`max_tokens=1`, `num_outputs=5`, `temperature=0`) | exercises the shim client's logprob fidelity — the main correctness risk |

Verified facts the implementer can rely on:

- Both run dirs exist at both eras; `run_spec.json` carries exactly the six
  classic keys (`name`, `scenario_spec`, `adapter_spec`, `metric_specs`,
  `data_augmenter_spec`, `groups`); `adapter_spec.model =
  eleutherai/pythia-6.9b`; official `max_eval_instances = 1000`.
- Official artifacts have **no `per_instance_stats.json`** (only
  `stats.json` + display files) — see Open Question 3.
- HF weights: `hf://EleutherAI/pythia-6.9b`, context window 2048, ungated.

## Phase 0 — fix the review findings (BLOCKING)

Do **not** build the runbook first. Findings 1, 2, 3, and 5 of
[`era-pinned-review-findings-2026-07-10.md`](era-pinned-review-findings-2026-07-10.md)
each independently break the exact path this runbook drives — Finding 2
(pyhocon dot-splitting kills the credentials lookup) names **`pythia-6.9b`**
specifically, and Finding 3 means the v0.2.4 image cannot even build. Follow
that document's own "Suggested fix order / commit grouping" (six ordered
commits: shim era-compat → host-side export → bridge/guard → dockerfile ENV →
resolver hardening → optional cleanups). After the fixes:

```bash
.venv/bin/python -m pytest tests/test_eras.py tests/test_eras_hostside.py \
  tests/test_eras_pipeline.py tests/test_era_shim_imports.py -q -o addopts=""
# plus the touched-area suites listed at the end of the findings doc
```

Note for Phase 2/3: the Finding-5 fix makes `--base-url` **required** for era
exports, and the Finding-2 fix moves the API key into the era `client_spec`
args — i.e. after Phase 0 the era export takes `--base-url` + `--api-key-value`
exactly like the e2e vllm export does. The grid scripts below assume that
post-fix contract. **Re-read the fixed `bundle_export.py` before writing the
grid scripts** in case the fix commits refined flag semantics.

## Phase 1 — checked-in serving config + presets

### 1a. `dev/era-tests/config/infer_stack/{catalog.yaml,settings.yaml}`

Copy `dev/e2e-tests/config/infer_stack/settings.yaml` verbatim (`backend:
compose`, `litellm: true`, `ui: false`, `data_dir: /data/service/infer-stack`).
Catalog, modeled on the phi-2 one:

```yaml
models:
  pythia-6.9b:
    source: hf://EleutherAI/pythia-6.9b
endpoints:
  pythia69b-single:
    engine: vllm
    reclaim: stop
    model: pythia-6.9b
    protocol: completions
    runtime:
      max_model_len: 2048
      gpu_memory_utilization: 0.8
      max_num_batched_tokens: 2048
      max_num_seqs: 16
      enable_prefix_caching: true
```

### 1b. Two era presets in `eval_audit/integrations/infer_stack/preset_configs.yaml`

One preset **per era**, each carrying **both** scenarios' run entries (unlike
e2e's one-preset-per-scenario: there, three variants of the *same* scenario
would pool into one packet; here the two scenarios have different logical run
keys, so one experiment per era composes cleanly). Follow the existing preset
shape (see `e2e-phi_2-vllm-philosophy` as the reference; the exporter resolves
`era` from the preset — `resolved_era = era or preset_cfg.get("era")` in
`bundle_export.py` — so the grid never passes `--era`):

```yaml
era-pythia_6_9b-v0_2_4:
  profile: "pythia69b-single"
  bundle_name: "era-pythia_6_9b-v0_2_4"
  access_kind: "openai-compatible"
  era: "helm-v0.2.4"
  # NO model_deployment_name: era replay is verbatim by-name (the deployment
  # is registered under the official model name; there is no rewrite target).
  profiles:
    - profile: "pythia69b-single"
      helm_model_name: "eleutherai/pythia-6.9b"
      protocol_mode: "completions"
      helm_max_sequence_and_generated_tokens_length: 2048
  smoke_manifest:
    experiment_name: "era-pythia_6_9b-v0_2_4-smoke"
    description: "Era e2e smoke: pythia-6.9b @ helm-v0.2.4 (5 instances/run)."
    run_entries:
      - "synthetic_reasoning_natural:difficulty=easy,model=eleutherai/pythia-6.9b"
      - "mmlu:subject=us_foreign_policy,method=multiple_choice_joint,model=eleutherai/pythia-6.9b,data_augmentation=canonical"
    suite: "era-pythia_6_9b-v0_2_4-smoke"
    max_eval_instances: 5
    precomputed_root: "/data/crfm-helm-public/classic"
    container_network: "host"
    container_gpus: "none"
    hf_cache_dir: "~/.cache/eval-audit-hf"
  full_manifest:
    # same, with -full names and max_eval_instances: 1000 (the official value —
    # verbatim replay; the materializer's max_eval_instances edit is valid at
    # all eras)
    ...
era-pythia_6_9b-v0_3_0:
  # identical except era: helm-v0.3.0 and the _v0_3_0 names
```

Implementation cautions:

- **Quote every scalar** (the catalog docstring requires it — version-like
  strings must round-trip as strings).
- The run-entry strings above must **discover exactly one official run dir**
  under the classic root per era at export time (`--freeze-rel-paths` fails
  loud on `NO_MATCH`/`AMBIGUOUS` — that failure is the test working). Note the
  classic root spans suites v0.2.2–v0.4.0; if a bare entry is ambiguous across
  suite versions, narrow `precomputed_root` per preset to
  `/data/crfm-helm-public/classic/benchmark_output/runs/<suite>` **only if
  needed** — check how `find_best_precomputed_run`/the freeze scopes suites
  first, and prefer the broad root if it disambiguates by era. Whatever
  disambiguation is needed, it must keep the two presets pointing at
  *different* suite dirs (v0.2.4 vs v0.3.0).
- `container_network: host` + `container_gpus: none` because the era container
  is a CPU-only HTTP client of the host-served vLLM (same reasoning as the e2e
  vllm transport); `hf_cache_dir` because the era container constructs
  scenarios in-process (dataset fetches).
- Verify the exporter forwards these container keys for era bundles
  (`_CONTAINER_SPEC_KEYS` in `bundle_export.py`) and that lease facts
  (endpoint `pythia69b-single`) are baked as they are for e2e presets.

### 1c. Per-era virtual-experiment manifests

`configs/virtual-experiments/era-pythia-v024.yaml` and `era-pythia-v030.yaml`,
mirroring `e2e-phi2-vllm.yaml` but pointing at **runbook-scoped** official
artifacts (see Phase 2, step 25 — the canonical
`$STORE_ROOT/indexes/official_public_index.csv` currently has **zero
classic-track rows**, so pointing at it would pair nothing):

```yaml
schema_version: 1
name: era-pythia-v024
description: >
  Era-pinned replay packet: pythia-6.9b @ helm-v0.2.4 (synthetic_reasoning_natural
  easy + mmlu us_foreign_policy), paired against the official classic v0.2.4 runs.
  same_deployment resolves 'unknown' for era pairs (pre-v0.5 adapter_spec has no
  model_deployment field) — expected, not a bug.
scope:
  models:
    - "regex:^eleutherai/pythia-6\\.9b$"
sources:
  - kind: audit_index
    fpath: /data/crfm-helm-audit-store/indexes/audit_results_index.csv
    include_experiments:
      - era-pythia_6_9b-v0_2_4-full
  - kind: official_public_index
    fpath: /data/crfm-helm-audit-store/indexes/era-tests/official_public_index.csv
    pre_filter:
      kind: helm_stage1
      inventory_fpath: /data/crfm-helm-audit-store/analysis/era-tests/filter_inventory.json
output:
  root: /data/crfm-helm-audit-store/virtual-experiments/era-pythia-v024
```

(Scope-limit the official side to the right era: check whether the composer
scope supports `suite_version`/track filtering; if not, the v0.2.4 and v0.3.0
official rows for the same run entry have different `logical_run_key` suites —
verify pairing picks the right era's official per packet, and if both eras'
official rows land in one packet, add a `suite_version` scope facility or
split the scoped index per era in step 25. **Resolve this empirically; do not
guess.**)

## Phase 2 — the runbook `dev/era-tests/`

Mirror `dev/e2e-tests` conventions exactly: every script `#!/usr/bin/env bash`,
header comment explaining *why*, `set -euo pipefail`, `source _lib.sh`,
`cd "$ROOT"`, ends with `OK: …` + `Next: ./NN_….sh`. Failure style: `FAIL: …`
to stderr + indented remedy + `exit 1`; `WARN:` for can't-validate-continue.

### `_lib.sh`

Copy the generic machinery from `dev/e2e-tests/_lib.sh` **verbatim** (the
prior exploration confirmed these blocks are model-agnostic): root resolution,
`STORE_ROOT`/`RESULTS_ROOT`/`PYTHON_BIN`, the 3-tier `INFER_STACK_DATA_DIR`
resolution + `_e2e_yaml_scalar` + writability/NFS guard,
`EVAL_AUDIT_SKIP_LOCAL_REPEAT`/`EVAL_AUDIT_GROUP_STRIP` exports,
`e2e_clear_results`-style helper. Rename the prefix `e2e_` → `era_`
consistently. Changes:

- `INFER_STACK_CONFIG_DIR` default → `$ERA_DIR/config/infer_stack`.
- Targets carry the era; the image is derived, not hand-set:

```bash
# name:era:endpoint — one row per era packet (the model/scenarios are fixed
# inside the preset named by field 1).
ERA_TARGETS=(
  "era-pythia_6_9b-v0_2_4:helm-v0.2.4:pythia69b-single"
  "era-pythia_6_9b-v0_3_0:helm-v0.3.0:pythia69b-single"
)
era_name()     { printf '%s\n' "${1%%:*}"; }
era_key()      { local rest="${1#*:}"; printf '%s\n' "${rest%%:*}"; }
era_endpoint() { printf '%s\n' "${1##*:}"; }
era_image()    {  # <era-key> -> image ref; overridable per-era via env
  local key; key="$(era_key "$1")"
  local override_var="ERA_IMAGE_${key//[.-]/_}"     # e.g. ERA_IMAGE_helm_v0_2_4
  if [[ -n "${!override_var:-}" ]]; then printf '%s\n' "${!override_var}"; return; fi
  printf '%s:dev\n' "$(./docker/read_eras.py docker/eras.yaml "$key" image_name)"
}
```

- `PRECOMPUTED_ROOT` default `/data/crfm-helm-public/classic` (env-overridable);
  export `EVAL_AUDIT_ERA_API_KEY="${EVAL_AUDIT_ERA_API_KEY:-EMPTY}"`.
- Scenario→vexp-manifest mapping helper (`era_vexp_manifest`), case over the
  two names → `configs/virtual-experiments/era-pythia-v{024,030}.yaml`.
- **Drop `ladder.env`**: machine specifics resolve exactly like e2e (defaults +
  env overrides). Document every knob in the README's "Knobs" section instead.

### Stage scripts

| script | contents |
|---|---|
| `00_check_env.sh` | `eval-audit-check-env` (copy e2e's, 5 lines). |
| `05_check_profiles.sh` | e2e's endpoint preflight, checking `pythia69b-single` via `infer-stack catalog endpoint list` (same WARN-if-unlistable / FAIL-if-missing shape). |
| `06_check_era_images.sh` | Per `ERA_TARGETS` era: `docker image inspect $(era_image …)`; on missing → `FAIL` + remedy `ERA=<key> ./docker/build.sh` (mirror e2e 06's fail-with-remedy style — do **not** auto-build; building is a deliberate, cache-invalidating act). Deep probes per image, mirroring e2e's python-heredoc probe: `org.aiq.era` label equals the era key; `python -m helm_era_shim.replay --help` exits 0; `EVAL_AUDIT_ERA_KEY` env var is set in-container (regression guard for Finding 6). |
| `07_run_gate.sh` | **The moved pre-v0.5 gates** — absorb `05_ladder_gate.sh` minus its tier 2 (tier 2 *is* the grid now, steps 10–40): tier 0 = the four era pytest suites (repo `.venv` python, `-o addopts=""`); tier 1 per era = rung 2 instrument fidelity (absorb `15_instrument_fidelity.sh`, driving `drivers/dryrun_driver.py` + `drivers/instance_diff.py`) and rung 5 HF-fetch audit (absorb `50_hf_fetch_audit.sh`). Keep the PASS/FAIL/SKIP report table + `run_step`/`add` helpers and the exit-nonzero-iff-attempted-rung-failed contract verbatim. Logs land under `$ERA_OUT` (default `$ROOT/ladder-out`, env-overridable — keep the existing dirname so `.gitignore`/muscle memory survive). SKIP reasons must name the unlocking env var/step, e.g. `docker unavailable`, `PRECOMPUTED_ROOT missing`, `era image not built — run ./06…`. |
| `10_run_smoke_grid.sh` | Mirror e2e 10 structure exactly: `infer-stack gc --yes` (warn-continue) → one-time gateway bootstrap on the first target's endpoint (`acquire --no-wait --yes --env-file` → `infer-stack env LITELLM_MASTER_KEY` → `release --env-file --evict --yes`) → `run_one` per target → backstop gc → `E2E_KEEP_GOING`-equivalent (`ERA_KEEP_GOING`) fail-fast/collect. `run_one`: `python -m eval_audit.integrations.infer_stack export-benchmark-bundle --preset "$(era_name …)" --bundle-root "$STORE_ROOT/local-bundles/<name>" --freeze-rel-paths --precomputed-root "$PRECOMPUTED_ROOT" --base-url "$LITELLM_BASE_URL/v1" --api-key-value "$LEASE_MASTER_KEY"` (era + from-spec come from the preset; `--freeze-rel-paths` implies from-spec), then clear prior results (unconditional, like e2e) and `eval-audit-run "$bundle_root/smoke_manifest.yaml" --lease --run=1 --container-image "$(era_image …)"`. The exported manifests carry frozen `run_spec_sources` + `era:`, so there is **no separate make-manifest step** — the old `20_make_manifest.sh` is superseded. |
| `15_run_full_grid.sh` | Same, `full_manifest.yaml`, `-full` experiments; `Next: ./20_index_local.sh`. |
| `20_index_local.sh` | Copy e2e 20 (verify `-full` dirs, `eval-audit-index --results-root "$RESULTS_ROOT" --report-dpath "$STORE_ROOT/indexes"`). |
| `25_index_official_classic.sh` | **New — closes the classic-index gap.** Scoped Stage-1 pass over the classic corpus emitting the official index + filter inventory the vexp manifests point at: `eval-audit-index-historic "$PRECOMPUTED_ROOT" --out_official_index_dpath "$STORE_ROOT/indexes/era-tests" --out_inventory_json "$STORE_ROOT/analysis/era-tests/filter_inventory.json" --out_fpath <scratch>/run_specs.yaml --out_detail_fpath <scratch>/run_details.yaml`. **MUST override `--out_fpath`/`--out_detail_fpath`** (their defaults write the repo/store `configs/run_specs.yaml` + `run_details.yaml` — clobbering the curated corpus catalog is the failure mode to design against; send them to `$ERA_OUT/stage1/`). Never overwrite the canonical `$STORE_ROOT/indexes/official_public_index.csv` — it is modern-tracks-only today and regenerating it is out of scope. |
| `30_compose.sh` | Copy e2e 30 (require `audit_results_index.csv`; loop `ERA_TARGETS` → `era_vexp_manifest`; `python -m eval_audit.cli.build_virtual_experiment --manifest … --allow-single-repeat "$@"`; `VEXP_MANIFEST` override). Also require the step-25 outputs with a `run ./25… first` remedy. |
| `40_build_summary.sh` | Copy e2e 40 (read `name` + `output.root` from each manifest via inline python; `--no-canonical-scan`; scoped-inventory-or-`--no-filter-inventory` flag selection; end-of-run recap of report paths). |
| `README.md` | Rewrite of the current runbook README in the e2e README's structure: what/why, the target table (2 eras × 2 scenarios), invariants (verbatim replay; `same_deployment=unknown` expected; era↔image guard), the gates table (which old ladder rung each preflight is), the Steps block (`./00 … ./40`), Knobs, "what stays genuinely manual" (interpreting rung-2 divergence, constraints freeze, judging the ~20% rung-3 recovery, pre-warm-vs-filter for rung-5) — that section from the old README survives verbatim. |

### What moves vs dies (use `git mv` where content survives)

| old (`reproduce/classic_era_replay/`) | fate |
|---|---|
| `drivers/{dryrun_driver.py,instance_diff.py}` | `git mv` → `dev/era-tests/drivers/` (unchanged) |
| `05_ladder_gate.sh` | `git mv` → `dev/era-tests/07_run_gate.sh`, then strip tier 2 + rung-1 build (now `06`'s remedy), point at `_lib.sh` |
| `15_instrument_fidelity.sh`, `50_hf_fetch_audit.sh` | `git mv` → `dev/era-tests/` keeping names as helpers invoked by `07` (they already take `ERA` env), or inline into `07` if trivially small — implementer's call, but keep per-rung logs |
| `00_build_era_image.sh` | delete; its body becomes `06`'s remedy text + the README's build section (the smoke checks it did move into `06`) |
| `10_export_bundle.sh`, `20_make_manifest.sh`, `30_run.sh` | delete; superseded by the grid (export bakes frozen sources + era into runnable manifests) |
| `ladder.env.example` | delete; knobs documented in README, defaults in `_lib.sh` |
| `README.md` | rewritten (above) |

Update every cross-reference: `grep -rn "classic_era_replay" docs/ docker/ dev/
reproduce/ README.md` and fix each (era plan §6, `docker/README.md`, the
findings doc's verify steps, journal references can stay historical).

## Phase 3 — validation (acceptance gates for the implementing agent)

1. `bash -n` every script; `python -m py_compile` on every touched `.py`.
2. Tier-0 pytest (the four era suites) green via the repo `.venv` with
   `-o addopts=""`.
3. `./dev/era-tests/06_check_era_images.sh` with no images present prints the
   FAIL + `ERA=… ./docker/build.sh` remedy and exits 1 (don't build to test
   this — that's the point).
4. Build both era images; `06` passes all probes (label, shim `--help`,
   `EVAL_AUDIT_ERA_KEY` set).
5. `./07_run_gate.sh` on a docker host with the corpus mounted: tier 0 + both
   eras' rung 2/5 attempted; table printed; exit code honors the contract.
6. Export both presets with `--freeze-rel-paths` against
   `/data/crfm-helm-public/classic` — each must freeze exactly 2 sources per
   era with the correct per-era suite dir in the rel-paths (this is the
   ambiguity check from Phase 1b).
7. `eval-audit-run <smoke manifest>` **preview** (no `--run`) shows the era
   pipeline selected + the image guard passing; then the smoke grid end-to-end
   on the GPU host (pythia-6.9b vLLM lease).
8. `25 → 30 → 40`: composed packets pair each local run with the **same-era**
   official row; `same_deployment=unknown`; the summary renders. The
   synthetic_reasoning_natural packet should show the local recovering ≫0%
   score vs the official 0% (the flagship result).
9. `python -m pytest tests/ -q -o addopts=""` for the touched-area suites
   listed in the findings doc.

## Open questions (settle empirically, in order)

1. **Freeze disambiguation across classic suites** (Phase 1b caution): does the
   broad classic root freeze the right per-era rel-path, or must
   `precomputed_root` be per-suite? Decides preset contents.
2. **Composer era-scoping** (Phase 1c caution): with both eras' official rows
   in one scoped index, does each packet pair the correct era's official? If
   not: per-era scoped indexes from step 25 (`--suite_pattern v0.2.4` etc.,
   two invocations) is the cheap fix.
3. **`require_per_instance_stats: True`** is hardcoded in the generated
   manifests (`_manifest_doc`). Officials at v0.2.4/v0.3.0 ship no
   `per_instance_stats.json`; confirm (a) the era-shim *local* runs do produce
   it (era `run_benchmarking` should), and (b) Stage 5 degrades to run-level
   comparison for the official side rather than erroring (the
   `--instance-source` policy in `eval_audit/reports/core_metrics.py` is the
   relevant machinery). If (a) fails, the manifest flag needs an era carve-out
   — fix it in `_manifest_doc`, not by hand-editing generated manifests.
4. **HF-fetch health of the two scenario families** (`synthetic_reasoning_natural`
   fetches nothing external? `mmlu` via the era `datasets` against the 2026
   Hub): rung 5 of `07` answers this before the grid; if mmlu no longer
   fetches cleanly under era `datasets`, pre-warm the cache (mount
   `hf_cache_dir`) — never patch the image at run time.
5. Whether `eval-audit-run --lease` needs anything era-specific for the
   `container_gpus: none` + `--network host` combination (it shouldn't — the
   bridge already handles this for e2e vllm targets; verify the era pipeline
   node forwards `EVAL_AUDIT_ERA_API_KEY` per the 30_run.sh contract).

## Design decisions already made (do not relitigate)

- **One preset per era carrying both scenarios** (different logical run keys →
  no packet pooling; halves the grid loop vs per-scenario presets).
- **Grid rows are eras, not scenarios** — the era is the unit of provenance
  (one manifest = one era = one image), so it's the unit of the loop.
- **Fail-with-remedy, not auto-build, for era images** (matches e2e 06;
  image builds invalidate caching and must be deliberate).
- **Scoped official index under `indexes/era-tests/`, never the canonical
  path** — the canonical CSV is modern-only today; this runbook must not
  masquerade a classic-scoped regeneration as the canonical artifact.
- **`ladder.env` dies** — e2e proves defaults + env overrides suffice; a
  second config surface is drift waiting to happen.
- **No incomparable-style negative control initially.** The natural era
  negative control (run a v0.2.4 spec under the v0.3.0 image) is precisely
  what the era↔image guard makes impossible, and `06`/preview already prove
  the guard trips. Revisit only if a recipe-deviation control is wanted later.

## Suggested commit sequence

1. Phase 0 finding fixes (their own plan's grouping, ~5 commits).
2. `feat(era-tests): checked-in serving config + era presets + vexp manifests`
   (Phase 1 — inert until the runbook exists; keep `python -m pytest
   tests/test_exporter_freeze.py` green).
3. `feat(era-tests): dev runbook mirroring dev/e2e-tests (moves the pre-v0.5
   gates)` (Phase 2 — the `git mv`s + new scripts + README + cross-ref
   updates + step-25).
4. `docs(era-tests): validation results` + journal entry after Phase 3 runs
   (record which open questions resolved which way).

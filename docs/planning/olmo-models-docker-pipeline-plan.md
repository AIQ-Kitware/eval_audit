# Run the OLMo reproduction through the Docker pipeline

> **Superseded (historical design narrative).** This plan introduced
> containerized execution as an *opt-in* with an `OLMO_CONTAINER=0` host-venv
> fallback. Containerization is now **mandatory** — the host-venv path and the
> `OLMO_CONTAINER` toggle have been removed (`build_schedule_params` requires a
> container image; the grids always pass `--container-image`). Leasing is the
> orthogonal axis. References to `OLMO_CONTAINER` / the fallback below are kept as
> a record of the original design.

**Goal:** Make [`reproduce/olmo_models/`](../../reproduce/olmo_models/) execute
HELM inside the pinned `eval-audit-helm-runner` image (the containerized Stage 3
"docker pipeline") instead of the host venv, **by default**, with an
`OLMO_CONTAINER=0` fallback to the host-venv path. The model is still **served on
the host** (vLLM behind the LiteLLM gateway via infer-stack); only *where HELM
runs* changes. Experiment names are unchanged, so the existing `olmo-models`
grouped report and the index→compose→summary stages need no changes — runs just
gain a per-run `container_provenance.json` sidecar.

**Why:** containerizing HELM pins the software environment so it stops being a
confounding variable in the reproducibility comparison (the core research
question — see
[`docs/helm-reproduction-research-journal.md`](../../docs/helm-reproduction-research-journal.md)).
The phi-2 e2e suite already proves the exact shape we need: model served on the
host, HELM in the container, reaching the host endpoint via `--network host`
(see [`docs/container-execution.md`](../../docs/container-execution.md) and
[`dev/e2e-tests/`](../../dev/e2e-tests/)).

---

## Key principle: zero Python code changes

The containerized phi-2 e2e example (commit `4d5c411`) added docker execution
with **no changes to any Python module** — no `export-benchmark-bundle` change,
no `eval-audit-run` change. It only (a) put container fields in a preset, carried
into the generated manifest by the existing `_CONTAINER_SPEC_KEYS` forwarding in
[`adapter.py:991-1030`](../../eval_audit/integrations/infer_stack/adapter.py#L991-L1030);
(b) wired the grid scripts; and (c) added a preflight. This plan does the same.

Two existing facts make it work for OLMo's **same-experiment-name toggle**
(rather than the e2e style of a separate container experiment):

- **The `--container-image` run flag already exists.**
  `eval-audit-run --container-image <ref>` overrides the manifest and is the
  documented way to turn the container on at run time
  ([`run.py:33-41`](../../eval_audit/cli/run.py#L33-L41),
  [`kwdagger_bridge.py:170-172`](../../eval_audit/integrations/kwdagger_bridge.py#L170-L172)).
- **Container settings are inert without an image.** `build_schedule_params`
  returns the bare pipeline *before* reading `container_network` /
  `hf_cache_dir` / `container_gpus`
  ([`kwdagger_bridge.py:127-128`](../../eval_audit/integrations/kwdagger_bridge.py#L127-L128)),
  and `_prepare_container_execution` returns early when no image is set
  ([`kwdagger_bridge.py:214-216`](../../eval_audit/integrations/kwdagger_bridge.py#L214-L216)).

So the recipe-level container settings live in the preset, and the **image — the
on/off switch — is supplied at run time** via the existing flag:

| `OLMO_CONTAINER` | grid passes | resolves to | container fields |
|---|---|---|---|
| `1` (default) | `eval-audit-run … --container-image <img>` | **docker** pipeline | active |
| `0` | `eval-audit-run …` (no flag) | **bare** host-venv pipeline | ignored (inert) |

Same `experiment_name` (`audit-<preset>-{smoke,full}`) either way.

---

## Container settings for OLMo (rationale)

- **`container_network: host`** — *required*. The model is served on the host, so
  the in-container HELM client must reach `http://localhost:<litellm_port>/v1`,
  which a default bridge container cannot see.
- **`container_gpus: none`** — the in-container HELM is only an OpenAI-compatible
  client to LiteLLM (plus CPU tokenization). The OLMo run entries (commonsense /
  gsm / legalbench / med_qa / mmlu / gpqa) are multiple-choice, exact-match, or
  classification metrics with **no LLM-judge annotator** that loads a local HF
  model — so the HELM container needs no GPU and must stay off the serving GPUs
  (now unrestricted by default — infer-stack uses all detected GPUs;
  `INFER_STACK_ALLOWED_GPUS` pins it on a shared machine). *If a scenario ever
  needs a local model, follow the scheduler's `$CUDA_VISIBLE_DEVICES` instead.*
- **`hf_cache_dir: ~/.cache/eval-audit-hf`** — dedicated, root-owned audit cache
  (the container runs as root), matching the e2e default.
- Gated **gpqa** already works: [`_lib.sh`](../../reproduce/olmo_models/_lib.sh)
  exports `HF_TOKEN` / `HUGGING_FACE_HUB_TOKEN` into the env `eval-audit-run`
  inherits, and the docker pipeline forwards them into the container with `-e`.

---

## Changes (data + scripts + docs only)

### 1. `eval_audit/integrations/infer_stack/adapter.py` — preset DATA (no logic)
Add `container_network: "host"`, `hf_cache_dir: "~/.cache/eval-audit-hf"`, and
`container_gpus: "none"` to the `smoke_manifest` **and** `full_manifest` of each
of the six OLMo presets (`allenai-olmo-7b`, `allenai-olmo-1-7-7b`,
`allenai-olmo-2-1124-7b-instruct`, `allenai-olmo-2-1124-13b-instruct`,
`allenai-olmo-2-0325-32b-instruct`, `allenai-olmoe-1b-7b-0125-instruct`).
**Do not** add `container_image` — that is the run-time toggle. Add one short
comment noting these fields are inert unless the run supplies `--container-image`
(the `OLMO_CONTAINER` switch). This is the same *kind* of edit the e2e example
made (it added an entire container preset); here it is three fields per existing
preset.

### 2. `reproduce/olmo_models/_lib.sh` — toggle knobs (mirror `dev/e2e-tests/_lib.sh:67-94`)
Add `OLMO_CONTAINER` (default `1`) and `OLMO_CONTAINER_IMAGE`
(default `eval-audit-helm-runner:dev`), with a comment on the served-on-host /
`--network host` model and the host-venv fallback.

### 3. `10_run_smoke_grid.sh` + `15_run_full_grid.sh`
Leave the `export-benchmark-bundle` call **unchanged**. At the `eval-audit-run`
call (smoke:
[`10_run_smoke_grid.sh:83`](../../reproduce/olmo_models/10_run_smoke_grid.sh#L83);
full: the matching call), append `--container-image "$OLMO_CONTAINER_IMAGE"` when
`OLMO_CONTAINER != 0`, and omit it otherwise — build it as an args array, exactly
as the export call already is.

### 4. `reproduce/olmo_models/07_check_container_image.sh` — NEW preflight
Adapt
[`dev/e2e-tests/06_check_container_image.sh`](../../dev/e2e-tests/06_check_container_image.sh)
(OLMo's `06` is already `06_check_hf_auth.sh`, so this is `07`). Gate on
`OLMO_CONTAINER`; verify `docker` is on `PATH` and `OLMO_CONTAINER_IMAGE` exists
locally; otherwise fail with a `./docker/build.sh` hint. No-op when
`OLMO_CONTAINER=0`.

### 5. `reproduce/olmo_models/README.md` — docs
Add `./docker/build.sh` as a prerequisite and `./07_check_container_image.sh` to
the preflight sequence (`00 → 05 → 06 → 07 → 10`). Document the two knobs, the
`container_network: host` / `container_gpus: none` rationale, the
`OLMO_CONTAINER=0` host-venv fallback, and cross-link
[`docs/container-execution.md`](../../docs/container-execution.md).

### Explicitly unchanged (no code touched)
`infer_stack export-benchmark-bundle` and all `adapter.py` *logic*;
[`run.py`](../../eval_audit/cli/run.py),
[`run_from_manifest.py`](../../eval_audit/workflows/run_from_manifest.py),
[`kwdagger_bridge.py`](../../eval_audit/integrations/kwdagger_bridge.py);
`20_index_local.sh`, `30_compose.sh`, `40_build_summary.sh`;
[`configs/virtual-experiments/olmo-models.yaml`](../../configs/virtual-experiments/olmo-models.yaml)
and `olmo-models-smoke.yaml`; the infer-stack config / `models.yaml`. e2e's
container path is untouched.

---

## Verification

1. `git -C submodules/helm status --short` — confirm clean, so the pristine
   `git archive` image build includes the OLMo `model_metadata.yaml` /
   `tokenizer_configs.yaml` aliases the in-container HELM must resolve.
2. `./docker/build.sh` — build `eval-audit-helm-runner:dev`.
3. **Manifest shape (no run):** export the cheapest preset and confirm
   `smoke_manifest.yaml` / `full_manifest.yaml` carry `container_network: host`,
   `hf_cache_dir`, `container_gpus: none` — and **no** `container_image`.
4. **Toggle preview (no execution):**
   `eval-audit-run --run=0 <bundle>/smoke_manifest.yaml --container-image
   eval-audit-helm-runner:dev` → previewed params select the **docker** pipeline;
   the same command **without** the flag → the **bare** pipeline.
5. `python -m pytest tests/test_container_execution.py` — render path still green.
6. `./07_check_container_image.sh` — passes once the image is built.
7. **End-to-end smoke:** `./10_run_smoke_grid.sh` (cheapest model first). Confirm
   each run dir gets `container_provenance.json` + `DONE`, and that gpqa (gated)
   downloads via the forwarded HF token inside the container.
8. **Escape hatch:** `OLMO_CONTAINER=0 ./10_run_smoke_grid.sh` runs via the bare
   host-venv pipeline.

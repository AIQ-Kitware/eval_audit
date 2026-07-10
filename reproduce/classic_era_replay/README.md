# Classic-era (pre-v0.5) HELM replay runbook

This runbook drives the **era-pinned reproduction** path: verbatim, from-spec
replays of pre-v0.5 official HELM runs (classic-track `v0.2.4` / `v0.3.0`) inside
a CPU-only Docker image whose HELM harness is checked out at the era's release
commit, with era Python and era dep pins. Model inference stays out-of-process on
modern vLLM (infer-stack lease), so the era container is the **measurement
instrument only** — the cleanest form of the audit's question: hold the
instrument fixed at the era, and deployment is the only variable.

Design + rationale: [`docs/planning/era-pinned-helm-containers-plan.md`](../../docs/planning/era-pinned-helm-containers-plan.md).
Registry: [`docker/eras.yaml`](../../docker/eras.yaml). Image + freeze workflow:
[`docker/README.md`](../../docker/README.md). Shim:
[`docker/era_shim/`](../../docker/era_shim/).

## Why this exists

The single modern `helm-runner` image pins HELM 0.5.x + Python 3.11 and magnet's
from-spec CLI imports v0.5+ module paths — so it **cannot** replay the ~59% of the
corpus that is pre-v0.5. Each era gets its own image + a standalone shim
(`helm_era_shim.replay`) that decodes the run_spec.json into the *era* RunSpec and
drives era `run_benchmarking`.

## Invariants (read before running)

- **One manifest = one era = one image = one measurement instrument.** A mixed-era
  source set is a hard error at make-manifest time.
- **Verbatim replay.** A pre-v0.5 `adapter_spec` has no `model_deployment` field;
  routing to vLLM is purely by-name (the era shim registers a deployment under the
  exact official model name). No deployment rewrite — the materializer refuses to
  insert the field.
- **`same_deployment` resolves `unknown`** for era pairs (both sides lack the
  field). That is correct, not a bug — no Stage 5/6 changes.

## The validation ladder

The scripts map to the plan's validation ladder. Run them in order; each is a
gate on the next.

| Script | Ladder step | What it checks |
|---|---|---|
| `00_build_era_image.sh` | 1. Image sanity | Builds the era image; `helm_era_shim.replay --help` in-container; freeze + rebuild. |
| `10_export_bundle.sh` | (setup) | Exports an ERA bundle: era-schema `model_deployments.yaml` + frozen exact-path `run_spec_sources`. |
| `20_make_manifest.sh` | (setup) | `eval-audit-make-manifest --era auto` — resolves the era from the sources' rel-paths; validates exact-path-only. |
| `30_run.sh` | 3–4. End-to-end | `eval-audit-run` — the bridge selects the era pipeline and guards the image's `org.aiq.era` label; one full packet through Stages 3–6. |

Instrument-fidelity (ladder step 2 — byte-for-byte instance identity on the
pandas-sensitive `entity_matching` run, no model) and the HF-fetch audit (step 5)
are documented in the plan; run them against the built image before a full sweep.

## Prerequisites

- Docker (era images are CPU-only — no GPU needed to build them).
- A local vLLM server (or LiteLLM gateway) serving the official model, reachable
  from the container. Set `EVAL_AUDIT_ERA_BASE_URL` if not the default.
- The public HELM corpus mirror on disk (the `--precomputed-root`).
- `EVAL_AUDIT_ERA_API_KEY` if your endpoint needs one (default `EMPTY`; vLLM
  ignores it — but v0.2.4's `AutoClient` requires the key to *exist*).

Set the shared variables once:

```bash
export ERA=helm-v0.3.0                        # a key in docker/eras.yaml
export PRECOMPUTED_ROOT=/data/crfm-helm-public
export ERA_PRESET=<your-era-preset>           # infer-stack preset (protocol_mode, helm_model_name, ...)
```

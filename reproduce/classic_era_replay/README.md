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
gate on the next — or run **`05_ladder_gate.sh`**, which runs every rung the
current machine supports, SKIPs the rest naming the missing prerequisite, and
prints a PASS/FAIL/SKIP table.

| Script | Ladder step | What it checks |
|---|---|---|
| `05_ladder_gate.sh` | all | **The gate.** Tier 0 (host pytest incl. the static era-import checker) → tier 1 (rungs 1, 2, 5 per era, docker CPU-only) → tier 2 (rungs 3–4, only when the GPU env vars are set). Exit non-zero iff an *attempted* rung fails. |
| `00_build_era_image.sh` | 1. Image sanity | Builds the era image; `helm_era_shim.replay --help` in-container; freeze + rebuild. |
| `15_instrument_fidelity.sh` | 2. Instrument fidelity | Dry-runs the pandas-sensitive runs (`entity_matching` Abt_Buy + `math` + `raft`) in the era image via `drivers/dryrun_driver.py` (no model) and byte-diffs instance identity against the official `scenario_state.json` via `drivers/instance_diff.py`. |
| `10_export_bundle.sh` | (setup) | Exports an ERA bundle: era-schema `model_deployments.yaml` + frozen exact-path `run_spec_sources`. |
| `20_make_manifest.sh` | (setup) | `eval-audit-make-manifest --era auto` — resolves the era from the sources' rel-paths; validates exact-path-only. |
| `30_run.sh` | 3–4. End-to-end | `eval-audit-run` — the bridge selects the era pipeline and guards the image's `org.aiq.era` label; one full packet through Stages 3–6. |
| `50_hf_fetch_audit.sh` | 5. HF-fetch audit | One dry-run per classic scenario family (from `configs/run_details.yaml`) against the 2026 Hub; reports which families fetch cleanly vs need pre-warming/vendoring (filter reasons, not reproducibility failures). |

### Portability: run it on any machine without editing scripts

All machine specifics live in **one file**: copy `ladder.env.example` to
`ladder.env` (gitignored) on the target machine and fill in what that machine
has. Every script sources it; the gate SKIPs any rung whose prerequisites are
missing and names the variable that unlocks it. Typical flow:

```bash
# on the GPU machine
git clone <repo> && cd eval_audit && git checkout impl/era-pinned-helm-containers
cp reproduce/classic_era_replay/ladder.env.example reproduce/classic_era_replay/ladder.env
$EDITOR reproduce/classic_era_replay/ladder.env    # PRECOMPUTED_ROOT, HF_CACHE_DIR, ...
./reproduce/classic_era_replay/05_ladder_gate.sh   # runs tiers 0-1; tier 2 once the
                                                   # ERA_PRESET/SOURCES_FPATH/IMAGE_REF
                                                   # vars are filled in
```

What stays genuinely manual (by design, not omission): interpreting a rung-2
divergence (choosing new era pins is research), committing the constraints
freeze, judging the rung-3 result against the ~20%-recovery expectation, and
deciding pre-warm-vs-filter for rung-5 failures. The scripts produce the
evidence for each of those decisions; they don't make them.

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

# Open-judge experiment (aiq-gpu) — rejudge gpt-oss-20b with open-weight judges

Reproduce selected HELM **closed-judge** benchmark scores (`gpt-oss-20b`
XSTest, WildBench) while varying the judge among open-weight models
(**Qwen3.5-27B**, **Qwen3.6-35B-A3B**). The candidate responses are frozen
once (content-addressed **response snapshots**) and fanned out across
independently attributable **judgment attempts** — the same responses,
different judge. See [`docs/planning/open-judge-plan.md`](../../docs/planning/open-judge-plan.md).

**Run on aiq-gpu** (the corpus + GPUs live there). Everything through step
`10` is CPU-only; `20` is the first live judge run.

## What's already validated (2026-07-17)

Phases 1–3 passed on the real corpus: the audit accepts XSTest + WildBench,
snapshots build, and the identity-replay gate reproduced the published
judge metrics **exactly** (xstest max_err 0; wildbench max_err ~2e-14). So
steps `05`/`08`/`09` are re-runs that should stay green.

## Steps

```bash
./00_check_env.sh                    # eval-audit env + resolved paths
./03_check_judge_serving.sh          # judge endpoints defined + min_vram_gib declared
./05_audit_source_artifacts.sh       # Phase 1: audit gpt-oss-20b closed-judge rows
./08_build_response_snapshots.sh     # Phase 2: freeze supported runs (needs jq)
./09_verify_official_identity_replay.sh   # Phase 3 STOP GATE: exact replay
./10_prompt_length_preflight.sh      # §14.3: size max_model_len from real prompts
./20_smoke_xstest_qwen35.sh          # Milestone B: LIVE 20-instance XSTest smoke
./30_analyze_judges.sh xstest        # judge-substitution report
```

## The serving model (how a rejudge run works)

Unlike the candidate runbooks, this does **not** use kwdagger per-run
leasing. A judge endpoint is leased for the whole pass (the model must stay
up across many judge requests): `20_smoke` runs
`infer-stack acquire qwen3.5-27b-judge --no-wait` (holds the lease while
the weights load — the default wait-mode releases the lease if a slow
first-time load misses its 600s timeout) then `infer-stack wait` (up to
`OJ_LEASE_WAIT_TIMEOUT`, default 1h — the first acquire downloads tens of
GiB of judge weights), exports the sidecar bundle against the live gateway
(`eval-audit-export-judge-bundle`), runs `eval-audit-rejudge-helm`
in-process against the gateway, then releases on exit (trap). No candidate
inference happens — only the annotation stage runs, and the runner proves
every candidate response byte-unchanged before writing the artifact.

## Judges & catalog

Two v1 arms, single replica, static routing (no dynamic routing in v1).
[`config/infer_stack/catalog.yaml`](config/infer_stack/catalog.yaml)
declares both endpoints with `placement.min_vram_gib` (60 for the 27B TP1,
40/shard for the 35B-A3B TP2) — no GPU pinning. JudgeSpecs live in
[`configs/open_judge/`](../../configs/open_judge/); their `temperature`/
`max_tokens` are `null` = use each benchmark's **official** judge budget
(safety 256, WildBench 2000) so prompts and budgets match the official
annotators. The HELM registry sidecars (model metadata + tokenizer) are
hand-authored there and copied into the exported bundle.

`max_model_len` in the catalog is a starting value (32768). Run `10` and
raise it if the recommendation exceeds it.

## Success criteria (Milestone B)

1. `03`/`09`/`10` all pass (endpoints declared, replay exact, prompts fit).
2. `20` leases the judge, serves it, and writes a `helm_rejudge_v1`
   artifact for 20 XSTest instances with `parse_status=ok` on (nearly) all,
   raw judge responses retained, and `safety_score:judge=qwen3_5_27b` stats.
3. `30` renders the comparison vs the official GPT/Llama baseline.

## Investigation triggers (do not silently filter — plan §20)

Stop and inspect if: parser failures exceed ~1%; `finish_reason=length`
recurs (raise the endpoint's `max_model_len` / the judge `max_tokens`);
final content is often empty while reasoning is nonempty (thinking-mode
issue — see plan §13); or replicate variation is large.

## What comes next (not this runbook yet)

Milestone C (WildBench smoke) reuses `20`'s pattern against the wildbench
snapshot; the 100-instance replicated pilot (Milestone D) and the kwdagger
rejudge pipeline (Commit 11, grouped-by-judge-arm scheduling) are the
scale-out once the smoke path is proven. The remaining safety benchmarks +
Omni-MATH (Commit 14) reuse the same annotators/metrics.

## Knobs (env vars)

- `OJ_CORPUS` (default `/data/crfm-helm-public`)
- `OJ_ROOT` (default `$AUDIT_STORE_ROOT/open-judge`) — snapshots/results/cache/analysis
- `OJ_SMOKE_INSTANCES` (default `20`)
- `OJ_PROMPT_TOKENIZER` (default `Qwen/Qwen3.5-27B`)
- `INFER_STACK_CONFIG_DIR` — standard; defaults to this runbook's `config/infer_stack/`.
  Deliberately **no** `INFER_STACK_ALLOWED_GPUS` (eligibility is declared in the catalog).

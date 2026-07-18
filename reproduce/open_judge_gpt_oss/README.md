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

The smoke is parameterized — `./20_smoke.sh [BENCHMARK] [JUDGE]` with
`BENCHMARK` in `{xstest, wildbench}` and `JUDGE` in `{qwen35, qwen36}`
(defaults `xstest qwen35`; `20_smoke_xstest_qwen35.sh` is the back-compat
wrapper for that default). Milestone C is `./20_smoke.sh wildbench qwen35`;
the second arm is `./20_smoke.sh xstest qwen36` (and `wildbench qwen36`).
`OJ_SMOKE_INSTANCES` overrides the 20-instance subset.

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

## Success criteria (Milestone B) — ACHIEVED 2026-07-18

1. `03`/`09`/`10` all pass (endpoints declared, replay exact, prompts fit).
2. `20` leases the judge, serves it, and writes a `helm_rejudge_v1`
   artifact for 20 XSTest instances with `parse_status=ok` on (nearly) all,
   raw judge responses retained, and `safety_score:judge=qwen3_5_27b` stats.
3. `30` renders the comparison vs the official GPT/Llama baseline.

**Result:** 18/20 `parse_status=ok`; the 2 failures are legitimate
token-budget truncations (`finish_reason=length`, no `</think>` — the judge
was still mid-thinking at the 1280-token cap), not parser bugs. Every one of
the 18 parsed Qwen scores exactly matched **both** official judges → 18/18
verdict agreement with the GPT-4o+Llama ensemble. Two lessons folded back
into the code: Qwen thinking judges draft the answer tags as placeholders
inside their reasoning, so the parsers strip everything up to the last
`</think>` (`strip_thinking`); and a parser behavior change must bump the
JudgeSpec `parser_version` or the attempt cache serves stale results.

## Investigation triggers (do not silently filter — plan §20)

Stop and inspect if: parser failures exceed ~1%; `finish_reason=length`
recurs (raise the endpoint's `max_model_len` / the judge `max_tokens`);
final content is often empty while reasoning is nonempty (thinking-mode
issue — see plan §13); or replicate variation is large.

## The overnight run (`50_overnight_run.sh`)

Once the smokes pass, `50` is the unattended full run: full benchmarks x both
judge arms x replicates, then the per-benchmark analysis. It leases ONE judge
at a time (the model stays up across its whole arm, so the slow first-acquire
weight download happens twice total), rejudges each configured
(benchmark, replicate) at `OJ_PARALLELISM` concurrency, releases, then runs
`eval-audit-analyze-judges` per benchmark. It is idempotent (a completed
attempt is served from its DONE gate) and never aborts the night on a single
rejudge failure — every attempt's status is in the tail summary and in
`$OJ_ANALYSIS_ROOT/overnight-logs/overnight.log`.

Scope is env-tunable per (benchmark, judge); an empty replicate list skips
that pair. Defaults encode the full v1 experiment: both benchmarks x both
judges x3 replicates (~21 h at parallelism 8 on the smoke timings). XSTest is
cheap (~1–7 s/inst); WildBench+qwen35 (the dense 27B, ~20 s/inst with the
real-run headroom) is the cost center. Narrow via env for a quick run.

    OJ_REPS_XSTEST_QWEN35    (default "0 1 2")
    OJ_REPS_XSTEST_QWEN36    (default "0 1 2")
    OJ_REPS_WILDBENCH_QWEN35 (default "0 1 2")
    OJ_REPS_WILDBENCH_QWEN36 (default "0 1 2")
    OJ_PARALLELISM           (default 8)

Measured smoke agreement (20-instance subsets, official ensemble): XSTest
qwen35 18/18 & qwen36 20/20 exact verdict match; WildBench (1–10 scale)
qwen35 mean|Δ|=0.43, qwen36 mean|Δ|=1.42 (the A3B scores WildBench
systematically ~1 pt lower — a real judge-substitution effect the full run
characterizes, not a defect).

Real-run judge budget: `reasoning_headroom_tokens=4096` in both JudgeSpecs
(vs the smokes' effective official-only budget) — the thinking judges need it
(WildBench truncated ~50% at a 3024-token cap). At temperature 0 `max_tokens`
is a ceiling, so the extra headroom is free for judgments that finish early;
it only lets the long-thinkers complete. Both benchmarks' max prompt + 6144
output stay under the catalog `max_model_len=32768`.

## What comes next (beyond the overnight run)

The kwdagger rejudge pipeline (Commit 11, grouped-by-judge-arm scheduling)
replaces `50`'s serial per-judge leasing for larger fan-out; the remaining
safety benchmarks + Omni-MATH (Commit 14) reuse the same annotators/metrics.

## Knobs (env vars)

- `OJ_CORPUS` (default `/data/crfm-helm-public`)
- `OJ_ROOT` (default `$AUDIT_STORE_ROOT/open-judge`) — snapshots/results/cache/analysis
- `OJ_SMOKE_INSTANCES` (default `20`)
- `OJ_PARALLELISM` (default `8`) — concurrent judge requests in the overnight run
- `OJ_REPS_{XSTEST,WILDBENCH}_{QWEN35,QWEN36}` — per-pair replicate lists (see above)
- `OJ_PROMPT_TOKENIZER` (default `Qwen/Qwen3.5-27B`)
- `INFER_STACK_CONFIG_DIR` — standard; defaults to this runbook's `config/infer_stack/`.
  Deliberately **no** `INFER_STACK_ALLOWED_GPUS` (eligibility is declared in the catalog).

# Changelog — Master Collaborative Reference, revision 2026-07-27

Baseline: `Master_Collaborative_Reference_Consensus_2026-07-15c` plus the
`Consensus_ACCEPTED_2026-07-15d` reply, which is now folded into this package at
`validation/consensus_accepted_2026-07-15d/`.

Window covered: **2026-07-16 through 2026-07-27** — 127 commits (107 Jon Crall,
20 Edward Wang). The chronology through 2026-07-14 is unchanged; superseded
rulings are marked in place rather than rewritten.

Sources used for this revision: `dev/journals/claude.md` entries 2026-07-15 →
2026-07-23, `dev/journals/codex.md` 2026-07-17, the pre-registered
`reproduce/olmo_models_combined/deployment_match/overnight_confirm_plan.md`, the
substrate-recovery and contamination sections of
`docs/helm-reproduction-research-journal.md`, `docs/planning/{open-judge-plan,
tmlr-paper-thesis, compute-run-spec-freeze-plan, edward-task-queue}.md`,
`docs/papers/tmlr-2026/`, and the Git history.

---

## Headline changes

### 1. The fp32 / substrate thread is closed and attributed

New chapter **"Closing the Substrate Question"** (`chap:substrate-closed`).

The confirm step the 2026-07-15 consensus demanded was executed:

| recipe | substrate recovered | IFEval drift vs official |
|---|---|---|
| bf16 + modern chat template | none | +0.10 |
| vLLM fp32 + old-template rendering | precision, template | +0.07 |
| HF fp32 + old-template rendering (either decode) | precision, template, engine | **exact** |

- The registered prediction (fp32 → full recovery) was **refuted**: the
  ordinary-path fp32 replay left +0.067 (7B) / +0.082 (13B) at full *n*
  (1082 per-instance rows per side).
- HF-fp32 then reproduced the official completions **byte-exactly** (32/32, all
  four forward-pass cells, both models).
- A greedy-vs-HELM-decode probe produced byte-identical completions, excluding
  decode semantics and attributing the residual to **pure engine numerics**.

Status moves: OLMO-001 preliminary → established-for-one-cell; OLMO-002
conditional → established for OLMo-2 instruct; new claims ENGINE-001, ENGINE-002.

### 2. The dense OLMo-2 Hugging Face divergence is withdrawn

`sec:residual-resolved`. It was the dense-model probe artifact the 15c review
insisted on keeping as a live candidate — unpinned template rendering plus
`device_map=auto` sharding in our own sweep. The 15c wording is retained above the
resolution. The execution-stack sensitivity claim survives on different (measured)
evidence.

### 3. The open-judge extension became an executed experiment

New chapter **"The Open-Judge Experiment"** (`chap:openjudge`). Identity-replay
gate passes on 6/6 benchmarks (max error 0 → 1.95e-14); v1 full run 12/12 attempts;
judge-size ladder 0.8B→35B-A3B. Findings: open judges match closed judges on label
metrics (κ 0.936/0.928 vs the official pair's own 0.829); judge non-determinism at
T=0 is universal (87–96% of judgments differ across replicates) while metric
fragility is metric-dependent (0.2–0.7% vs 43–46%). Carried with its caveats:
single candidate, 14.2% truncation on one arm, ~99% one-class safety sets, and a
contamination caveat that makes every agreement figure an upper bound.

### 4. A net-new-model extension path

New chapter **"Extending to Net-New Models: The Qwen3.5 Arc"** (`chap:qwen35`),
including the self-critique that our own compute runs still store a mutable run-key
string as their source of truth (`sec:freeze-critique`).

### 5. Paper direction and manuscript

New chapter **"Paper Direction and the Manuscript"** (`chap:paperdirection`): three
adversarial thesis rounds, the single-candidate diagnosis, verified novelty
citations (JuStRank 2412.09569; SLMJury 2606.07810), and the 2026-07-27 TMLR
rewrite (Goodman's trichotomy credited, taxonomy claim narrowed, eligibility gate
made target-relative, bibliography verified with four records fixed).

---

## Section-level edits to existing material

| Location | Change |
|---|---|
| Title page / abstract | Evidence cutoff → 2026-07-27; abstract records the byte-exact result and the judge experiment |
| Ch. 1, consensus rulings | fp32 row and dense-OLMo-2 row marked **Resolved**; `86ec84af` row records the 15d furnished SHAs |
| Ch. 1, new §"Consensus closure (2026-07-15d)" | acceptance, furnished hashes, run-artifact acceptance audit, agreed thesis sentence |
| Ch. 1, new §"What changed in the continuation revision" | this changelog in narrative form |
| Ch. 1, new §"Authorship and division of work from 2026-07-15" | second contributor; 107/20 commit split |
| §`sec:fp32` | status box points forward to the confirmation |
| §`sec:substrate` | HF-in-process routing switch still unwired; notes the resulting gap (completion-level vs metric-level evidence) |
| §`sec:judge` | status box pointing to the executed experiment |
| Synthesis, headline claims | claim 2 rewritten; two new established claims (engine; judge non-determinism) |
| Synthesis, methodology | three additions: register the prediction, gate the reconstruction, pin every axis |
| Synthesis, provenance recommendation | three fields → four (engine and version), plus judge fields and an explicit unrecoverability limit |
| Synthesis, identifiability map | dtype row (recovered); engine row (load-bearing); new judge row |
| Synthesis, open threads | rewritten: discharged items separated from the 11 open ones, ordered by what the paper needs |
| App. gotchas | **G14** (compile cache keyed narrower than the config space), **G15** (from-spec vs compute run entries); title G1–G15 |
| App. unrecorded parameters | engine promoted to Tier 1 with a measured effect; new rows for decode semantics, compiled-graph provenance, judge identity, hosted batch composition; four-field recommendation |
| App. deliverables | timeline extended through 2026-07-27; commit statistics; new runbooks, tools, and planning docs |
| App. interpretive revision | new §"Second interpretive pass (2026-07-27)" — three claims up, one down, one withdrawn |
| App. claim matrix | 3 rows revised, 8 rows added |
| App. source inventory | new sources; explicit note that no commit ledger is packaged for this window |
| App. preservation checklist | engine build, compiled-artifact provenance, judge fields; preserve raw artifacts not only derived layers |
| `strategy/main.tex` | new addendum section: what held, what changed, what is now on the critical path |

## Data files

- **New canonical:** `chronicle/data/master_claim_evidence_ledger_2026-07-27.csv`
  (30 claims; 11 new, 8 revised; adds a `revision` column). The 15c ledger is
  retained beside it for the audit trail.
- **New canonical:** `chronicle/data/store_status_ledger_2026-07-27.csv`
  (12 stores). Merges the observed `run_artifact_freshness` values from the 15d
  disk audit and adds the five stores created in this window.
- **Folded in:** `validation/consensus_accepted_2026-07-15d/` — acceptance letter,
  `SOURCE_HASHES_claude_2026-07-15d.txt`, `store_provenance_audit_2026-07-15d.csv`.

## Known gaps in this package

1. **PDFs are not rebuilt.** No LaTeX toolchain was available on the host that
   produced this revision. The 15c PDFs are retained under `*_2026-07-15c*.pdf`
   and `chronicle/main_2026-07-15c_SUPERSEDED.pdf`; they do **not** reflect the
   updated `.tex` sources. Rebuild with `pdflatex main.tex` (×2, for the ToC and
   cross-references) before circulating.
2. **No commit ledger for this window.** `chronicle/appendices/*_commit_ledger.tex`
   still cover only through 2026-07-14 and should be regenerated.
3. **`SOURCE_HASHES.txt` is the 15c manifest** and no longer covers this package;
   see `SOURCE_HASHES_2026-07-27.txt`.
4. **The new empirical results are unpreserved.** Every figure in the two new
   empirical chapters is read from journal records of runs on Kitware GPU hosts.
   None of those stores has been copied, hashed, or bundled — see
   `store_status_ledger_2026-07-27.csv`, where this is the most overdue action.

# Patched Collaborative Master Reference + Reply (2026-07-15b)

This package returns the **collaborative master reference with the factual corrections
applied inline**, plus the reasoning and machine-readable ledgers behind them. It was
prepared from the **live** `eval_audit` repository (HEAD `86ec84af` on
`impl/run-from-run-spec`), **including `/data`** — which the source tarball in the
original review (`b8b858697e7c`) excludes. Most of the reconciliation could not have
been done from that `/data`-less archive.

## Contents

- `Master_Collaborative_HELM_Internship_Reference_2026-07-15.tex` — the master
  reference, **now patched** (compiles standalone; the absent commit-ledger `\input`s
  are guarded). See its new §"Corrections applied in this master revision (2026-07-15b)"
  in Chapter 1 for the changelog.
- `Master_Collaborative_corrections_2026-07-15b.diff` — unified diff (pristine → patched),
  so the exact edits are auditable (9 hunks).
- `Claude_Response_to_Master_Collaborative_2026-07-15.md` / `.tex` — the response letter
  (the *why* behind each change). The banner at the top marks what is applied vs. still
  proposed.
- `Claude_Response_Decisions_2026-07-15.csv` — point-by-point rulings.
- `Claude_Response_Store_Status_2026-07-15.csv` — the verified `/data` store
  reconciliation (per-store on-disk state, dates, packaging-vs-evidential status, action).

**No PDF** — this host has no TeX toolchain. Compile the `.tex` with `pdflatex` (two
passes) to render.

## What changed in the master doc (applied inline)

- **A1** Commit `86ec84af`: `Unresolved` → `Established` (verified as current HEAD).
- **A2** Store status split three ways, replacing the single "not packaged" label:
  **GPT-OSS fresh** (current-code, preserve only), **OLMo stale** (on-disk `olmo-7b`
  halving `0.295/0.144`; regenerate), **Qwen genuinely absent**.
- **A3** The 59% classic-track figure now carries its source.
- **B1** OLMo-2 HF divergence keeps a dense-model probe artifact on the candidate-cause
  list (no over-correction into a confirmed finding).
- **B2** "apparently HF-produced" → "recorded as a `HuggingFaceClient` deployment".
- **B3** GPT-OSS-vs-OLMo `ifeval` drift ratio: "two orders of magnitude" → "≈30×".
- **B6 (part)** commit-ledger `\input`s guarded so the doc compiles standalone.

## Still proposed (NOT applied — please confirm)

- **B4** Consolidate the several overlapping claim ledgers into one canonical
  machine-readable source.
- **B5** Demote the vendor-named validation matter out of Chapter 1.
- **B6 (part)** Align the `README`/directory tree with the flat package layout.

## Repo state on our side

The standalone local chronology was removed and the repository consolidated onto this
collaborative master reference (commit `7a3e728e`). The master doc itself and this
reply package are left untracked (they are your artifacts to re-ingest), not committed.

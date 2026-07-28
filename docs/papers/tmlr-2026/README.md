# TMLR paper draft — *A Benchmark Recipe Is Not an Experiment*

Scaffolded first draft, generated from the master collaborative reference
(`docs/Master_Collaborative_Reference_2026-07-15c/`) and the consensus
thesis/story. Every claim is scoped to the current evidence; parts that depend on
experiments not yet run are visibly marked.

## Layout

```
main.tex              preamble, title, abstract, wiring
references.bib        bibliography (entries marked "% VERIFY" need checking)
sections/
  introduction.tex    reframing + thesis + contributions
  related_work.tex    HELM, EEE (detection→attribution), serving nondeterminism, provenance
  taxonomy.tex        five coordinates; eligibility gate; two axes (three reproduction targets, six causes); per-parameter identifiability
  system.tex          EvalAudit: from-spec replay, substrate sweep, era containers, layered diff
  cases.tex           OLMo (recoverable) · RedPajama (proxy-era) · GPT-J/NeoX/OPT (non-identifiable) · GPT-OSS (candidate)
  provenance.tex      the 3-field standard + release checklist
  limitations.tex     threats to validity (each pending item flagged)
  conclusion.tex
  appendix.tex        gotchas (G1–G13), unrecorded-parameter taxonomy, preservation checklist, claim ledger
```

## Compiling

`main.tex` **compiles as-is** in a fallback `article` layout. For the real TMLR
format, drop the official `tmlr.sty` (+ `tmlr.bst`) into this directory — the
preamble auto-detects it via `\IfFileExists`. Then:

```
pdflatex main && bibtex main && pdflatex main && pdflatex main
```

The source defines **no custom macros** — no `\newcommand`, `\newcolumntype`, or
`\definecolor`. Everything is written out at its use site (`\texttt{...}` for
code and file names, the draft markers below, inline `>{...}X` weights in the
`tabularx` preambles), so any one file compiles against any copy of the
preamble and survives being lifted into another document. Keep it that way.

## What is real vs. scaffolded

Structural check (run in this dir):

```
grep -rc -F 'fcolorbox{orange}{yellow!12}' sections/*.tex   # 6 scaffold blocks
grep -ro -F 'textsc{pending}' sections/*.tex main.tex | wc -l   # 9 pending markers
```

These are the exhaustive list of gaps. The load-bearing ones:

| Gap | Marker | Status |
|---|---|---|
| Coverage-funnel counts (Table 2) | §5.1 pending | numbers read from derived store layers; OLMo/GPT-OSS rows need **regeneration from preserved raw runs** |
| Ordinary-path OLMo confirmation (held-out) | §5.1.1 scaffold + pending | experiment designed, **not run** |
| Regenerated OLMo aggregate heatmap | §5.1 scaffold | store stale → **re-run** |
| GPT-OSS promoted to a result | §5.1.3 scaffold | candidate; run artifacts pruned → **re-run + preserve** |
| RedPajama validation-ladder table | §5.2 scaffold | raw runs **survive** → directly packageable |
| OLMo confirmation results table | §5.1.1 scaffold | awaits the run |
| Corpus-wide denominator | §7 pending | regenerate from a pinned manifest |
| Cross-machine baseline | §7 pending | scoped, not run |
| Artifact DOI / git bundle | §8 scaffold | at camera-ready |

Everything else (the taxonomy, system, the two *confirmed* OLMo attributions, the
G13 non-identifiability result, the provenance standard, the appendices) is drawn
from completed work in the master reference.

## Epistemic discipline (matches the consensus ledger)

- The **float32** result is a *discovery probe* + a *conditional deductive* argument,
  **not** a completed reproduction (Table `tab:fp32`).
- The **dense OLMo-2 HF divergence** is reported as *unresolved*, with a probe-artifact
  hypothesis kept live.
- **Non-identifiability** is scoped *relative to the surviving evidence examined*.
- **Report freshness ≠ result acceptance**: no number resting on an unpreserved/stale
  store is cited.

Authorship (`\author`) and the EEE citation (`eee2026`) are placeholders to confirm.

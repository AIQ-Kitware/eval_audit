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
  taxonomy.tex        four coordinates; preconditions for an attempt (execution *and* evidence); two axes (three reproduction targets, five causes); per-parameter identifiability
  methodology.tex     §4 --- from-spec replay, substrate sweep, era containers, layered diff + pairing strictnesses
  results.tex         §5 --- pairing coverage · OLMo (recoverable) · RedPajama (proxy-era) · GPT-J/NeoX/OPT (non-identifiable) · GPT-OSS (candidate)
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
grep -ro -F 'textsc{pending}' sections/*.tex main.tex | wc -l   # 11 pending markers
```

These are the exhaustive list of gaps. Located by section name rather than
number, since the numbers move. The load-bearing ones:

| Gap | Marker | Status |
|---|---|---|
| Corpus-wide exclusion profile | limitations, pending | taxonomy makes typed exclusion an outcome; **results populate none of it** — needs a Stage-1 pass over a frozen manifest |
| RedPajama official all-zero scenario | results, RedPajama case, pending | `synthetic_reasoning_natural` official is 0.0000 on all six metrics, local 0.147–0.184 — **diagnose before publishing the ladder** |
| Coverage-funnel counts (Table 2) | results §5.1, pending | read from derived layers; raw runs **survive**, so re-render + hash, not re-run |
| Ordinary-path OLMo confirmation | limitations, scaffold + pending | experiment designed, **not run**; the design lives with the limitation it answers |
| OLMo confirmation results table | limitations, scaffold | awaits the run above |
| Regenerated OLMo aggregate heatmap | results, OLMo case, scaffold | store stale; raw runs survive → **re-render** under fixed code |
| GPT-OSS promoted to a result | results, GPT-OSS probe, scaffold | candidate; artifacts survive but unhashed → **re-render + hash + audit** |
| RedPajama validation-ladder table | results, RedPajama case, scaffold | run-level agreement now reported; **byte-match + SKIP counts** still to extract |
| Cross-machine baseline | limitations, pending | scoped, not run |
| Artifact DOI / git bundle | conclusion, scaffold | at camera-ready |

Everything else (the taxonomy, the methodology, the two *confirmed* OLMo attributions, the
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

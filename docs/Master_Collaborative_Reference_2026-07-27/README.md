# Master Collaborative Kitware Internship Reference Set — Revision 2026-07-27

This revision continues the `2026-07-15c` consensus package through 2026-07-27. It
folds in the `2026-07-15d` acceptance (consensus reached, no open disagreements) and
records the twelve days of work that followed — 127 commits, three substantive
additions, and one withdrawn claim.

**Start here:** [`CHANGELOG_2026-07-27.md`](CHANGELOG_2026-07-27.md) lists every
change against `2026-07-15c`. Then read chronicle §"What changed in the continuation
revision (2026-07-27)".

## Primary documents

- `chronicle/main.tex` — master chronology, evidence audit, technical history,
  corrected claim status, and publication roadmap. **Four new chapters**:
  the Qwen3.5 extension, the open-judge experiment, closing the substrate question,
  and paper direction / manuscript.
- `strategy/main.tex` — consensus paper framing and plan, with a 2026-07-27 addendum
  recording which parts held.
- `validation/consensus_response_to_claude.{tex,md}` and
  `validation/consensus_accepted_2026-07-15d/` — the point-by-point exchange and its
  closure.

## Canonical ledgers

- `chronicle/data/master_claim_evidence_ledger_2026-07-27.csv` — **source of truth
  for claim status** (30 claims). Supersedes the `2026-07-15c` ledger, retained
  beside it.
- `chronicle/data/store_status_ledger_2026-07-27.csv` — run-artifact / comparison /
  report freshness per store, with observed disk-level values merged from the 15d
  audit.

Prose tables are readable snapshots. Check or regenerate them from the canonical
ledgers before submission.

## The headline result of this revision

For OLMo-2 7B and 13B instruct on IFEval, the published number is reproduced
**byte-exactly** once three unrecorded substrate variables are recovered together,
and each accounts for a measured slice of the drift:

| recipe | IFEval drift vs official |
|---|---|
| bf16 + modern chat template | +0.10 |
| vLLM fp32 + old-template rendering | +0.07 |
| HF fp32 + old-template rendering | **exact** |

The +0.07 is pure vLLM↔Hugging Face fp32 engine numerics — same weights, same
precision, same prompt, same greedy decode. The registered prediction (that fp32
alone would recover the official) was **refuted**, which is what makes the layered
decomposition a result rather than a confirmation.

Scope, stated up front: one benchmark, one family, two sizes, instruct only, with
byte-exactness measured at the completion level on 32 instances per model. Turning
this into a population claim is the prospective census, which is specified and
unstarted.

## Evidence caveats

1. **Nothing new here is packaged evidence.** Both new empirical chapters are
   written from journal records of runs executed on Kitware GPU hosts. Those stores
   have not been copied, hashed, or bundled. The 2026-07-15d audit additionally
   found the two flagship *older* stores pruned to their derived layers, so for
   GPT-OSS and `olmo-models-combined` "regenerate" means **re-run**. Report
   freshness is not run-artifact freshness — the freshest reports sit on the
   least-preserved inputs.
2. **The PDFs are stale.** No LaTeX toolchain was available where this revision was
   produced. `chronicle/main_2026-07-15c_SUPERSEDED.pdf` and the other
   `*_2026-07-15c.pdf` files are the previous revision's output and do not reflect
   the updated sources. Rebuild before circulating:
   ```bash
   cd chronicle && pdflatex main.tex && pdflatex main.tex   # twice: ToC + refs
   cd ../strategy && pdflatex main.tex && pdflatex main.tex
   ```
3. **No commit ledger covers this window.** `chronicle/appendices/` still ends at
   2026-07-14.
4. **Judge agreement figures are upper bounds** until a candidate released after the
   judges' training cutoff is rejudged; see chronicle §"The contamination caveat".

## Evidence-access caveat (carried forward, still binding)

The supplied source archive excludes `/data`, ignored files, and post-archive Git
objects. Live-repository and `/data` observations are preserved as
collaborator-verified attestations. They become packaged evidence only when the
corresponding Git bundle, manifests, raw stores, inputs, and hashes are included.
The 2026-07-15d reply furnished commit and tree SHAs plus store artifact hashes; a
Git bundle was deliberately withheld because the repository is private, so that row
reads *reference-furnished, bundle pending release*.

## Authorship

Work through 2026-07-14 is Edward Wang's internship record. From 2026-07-15, Jon
Crall led most of the execution recorded in the four new chapters (107 of the
window's 127 commits; the remaining 20 are Edward Wang's TMLR manuscript work). See
chronicle §"Authorship and division of work from 2026-07-15". The contribution
statement of any manuscript drawn from this document must reflect that.

## Directory notes

- `chronicle/appendices/` — commit ledgers required by the LaTeX source (through
  2026-07-14 only).
- `chronicle/sources/` — the two source chronologies preserved verbatim.
- `sources/chatgpt-deep-research-report.md` — external research synthesis behind the
  artifact / procedural / claim-level reproduction distinction.
- `validation/claude_patch/` — the 2026-07-15b patch, response, diff, and ledgers.
- `validation/consensus_accepted_2026-07-15d/` — acceptance letter, furnished source
  hashes, store-provenance audit.

## Evidence cutoff

Technical evidence cutoff: **2026-07-27**. Chronology through 2026-07-14 unchanged
from revision 2026-07-15c. Collaborative interpretation revised 2026-07-15;
consensus reached 2026-07-15d; continuation revision 2026-07-27.

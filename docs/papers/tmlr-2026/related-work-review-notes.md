# Proposed edits blocked by the freeze on `sections/related_work.tex`

`related_work.tex` is frozen. Nothing here has been applied. Each item states the
problem, the exact change, and what happens if it is not done.

Companion file: [`introduction-review-notes.md`](introduction-review-notes.md).

Last updated: 2026-07-28. Reflects the tree at commit `ef777bf1`. §3 now has
**four** coordinates — recipe, deployment, execution instrument, residual — with
record defects (migrations, carried-forward outputs, damage in transit) moved out
of the decomposition and into the preconditions of §3.2, since they are evidence
failing rather
than causes of a score. §3.1 was then cut back to the coordinate list plus three
paragraphs; the engine-boundary, scorer, and judge-recursion paragraphs are gone,
and their content is carried by the list items' role definitions or dropped. Item
3 survives that cut and is restated against the list; a fourth item on the judge
was withdrawn (see *Not proposed*).

---

## 1. `sec:model` and `sec:taxonomy` are stale after the §3 rename — LOW, mechanical

**Problem.** §3 is now titled *A Taxonomy of Benchmark Reproduction* but is still
labelled `sec:model`, while `sec:taxonomy` labels only §3.4 (*Axis 2: why two
executions disagree*). So in the source, "the taxonomy" and `\ref{sec:taxonomy}`
denote different scopes: the section as a whole versus one of its two axes.

Nothing is wrong in the rendered output today — every reference resolves and every
one means what its surrounding prose says. The risk is future: someone writing
`\ref{sec:taxonomy}` to mean "the taxonomy section" will silently point at §3.4,
and the error will not surface as a build warning.

**Why it is not fixed.** The rename requires editing `\ref{sec:taxonomy}` inside
`related_work.tex:13`, which is frozen.

**Exact change, if §2 is ever unfrozen.** Rename in `taxonomy.tex`:

| Label | Currently on | Should be |
|---|---|---|
| `sec:model` → `sec:taxonomy` | §3 (the section) | §3 |
| `sec:taxonomy` → `sec:causes` | §3.4 (axis 2) | §3.4 |
| `sec:system` → `sec:methods` | §4, now titled *Methodology* | §4 |
| `sec:cases` → `sec:results` | §5, now titled *Results* | §5 |

The last two joined the list when §4 and §5 were retitled. `sec:system` is the
blocker again: `related_work.tex:15` references it ("our version-pinned ``era''
containers (§…)"), so the label cannot be renamed while §2 is frozen. `sec:cases`
is referenced only from editable files (`taxonomy.tex:67`,
`limitations.tex:36`) and could be renamed today, but renaming one of a stale
pair and not the other would be worse than leaving both.

Then update all five references:

- `sections/results.tex:4` — `sec:model` → `sec:taxonomy`
- `sections/methodology.tex:4` — `sec:model` → `sec:taxonomy`
- `sections/results.tex` — `sec:taxonomy` → `sec:causes` (in the classic case)
- `sections/taxonomy.tex:10` — `sec:taxonomy` → `sec:causes` (internal, in the
  "taxonomy these coordinates imply" paragraph)
- **`sections/related_work.tex:13`** — `sec:taxonomy` → `sec:causes` (the blocker;
  "That attribution is the second axis of our taxonomy (§…)")

**If not done:** no output defect. Source-clarity only.

---

## 2. ~~`system.tex:4` calls §3 "the measurement model"~~ — RESOLVED

`system.tex:4` (now `methodology.tex`) read "EvalAudit operationalizes the measurement model of
\S\ref{sec:model}", pointing at a title that no longer existed after the §3
rename. Fixed in passing when §4 was retitled *Methodology*: the sentence now
reads "operationalizes the taxonomy of \S\ref{sec:model}". Kept as a heading so
the item numbering below does not shift.

Note that `introduction.tex:18` still says "We give a **measurement model** of how
a benchmark score is generated". That is §1's own phrasing for the decomposition,
not a cross-reference to a section title, so it is not a defect — but if §1 is
ever unfrozen, "measurement model" and "taxonomy" are two names for §3.1 and one
should go.

---

## 3. "execution substrate" and "instrument" are both used, neither defined — MEDIUM

**Problem.** §3.1's coordinate list defines each coordinate by the role it plays,
and the deployment/instrument boundary runs *through* the serving engine: the
deployment item ends "…and the client or engine **family** the deployment names",
the instrument item includes "the serving engine's **build and kernels**". §2 uses
two umbrella terms across that boundary, neither of them defined at the point of
use:

- `related_work.tex:17` opens "What lies downstream is the **execution
  substrate**…" and then enumerates engine choice, attention kernel, precision,
  quantization, batching, and checkpoint drift. Under §3.1 that list spans the
  deployment (precision, quantization, checkpoint) and the instrument (engine
  build, kernel, batching). "Substrate" is a reasonable umbrella for the union,
  but the paper never says it is one.
- The same paragraph later says "For model-graded metrics the **instrument**
  extends to the judge" — the first use of the §3 coordinate name, two pages
  before §3.1 defines it, and in a paragraph whose other term is "substrate".

**Proposed (two clauses, no restructuring):**

1. Line 17, first sentence: "What lies downstream is the execution substrate ---
   the deployment and execution instrument of \S\ref{sec:model} taken together
   --- and a second line of work establishes that each of its components moves
   outputs on its own."
2. Same line, judge sentence: "…the **execution instrument** (\S\ref{sec:model})
   extends to the judge…" so the term's one pre-definition use is visibly a
   forward reference.

Either alone helps; the first matters more, because it is what tells a reader
that §2's single umbrella becomes two coordinates in §3.

**If not done:** §2 and §3 read as two decompositions of the same territory, and
the engine — the one component §3.1's two list items deliberately split — sits
inside §2's undivided "substrate" with no signal that §3 will divide it.

---

## Not proposed

- No content changes to §2. The pass ending at `67c2decb` — Goodman crediting, the
  execution-substrate merge, the forward/inverse differentiation, the validity
  demotion, the provenance correction, titles, and order — closed everything found.
  Item 3 above is new, and arises from §3.1's role-based coordinate list rather
  than from that pass.

- **Withdrawn: "the instrument extends to the judge" places the whole judge.**
  Raised 2026-07-28 against `related_work.tex:17`, on the grounds that §3.1 read a
  model-graded metric as nesting a second run inside the scorer — judge with its
  own recipe, deployment, and instrument — so §2 assigning all of it to the
  instrument was a mismatch. §3.1's judge paragraph was then cut deliberately, and
  with it the recursion. Nothing in §3 now places the judge across coordinates, so
  §2's sentence contradicts nothing and the item has no basis. What §3 still says
  about the judge is consistent with it: §3.2 treats a withdrawn judge as
  blocking procedural reproduction, and target (1) treats the judge's forward pass
  as the one step it cannot re-execute.

  Revive only if the recursion returns to §3. Two things went with the cut and are
  recorded here so they are not lost by accident: that a judge substitution is the
  nested-run analogue of a declared proxy era, and that official run specs name an
  annotator class with empty args while the judge models are hard-coded per
  harness version — making judge identity a recipe slot whose value the instrument
  version supplies, the same shape as the chat-template flag and the unpinned
  dtype. `eval_audit/judge_registry.py` is the source for the second.

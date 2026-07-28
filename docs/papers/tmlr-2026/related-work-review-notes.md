# Proposed edits blocked by the freeze on `sections/related_work.tex`

`related_work.tex` is frozen. Nothing here has been applied. Each item states the
problem, the exact change, and what happens if it is not done.

Companion file: [`introduction-review-notes.md`](introduction-review-notes.md).

Last updated: 2026-07-27. Reflects the tree at commit `1719e9e6`.

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

Then update all five references:

- `sections/cases.tex:4` — `sec:model` → `sec:taxonomy`
- `sections/system.tex:4` — `sec:model` → `sec:taxonomy`
- `sections/cases.tex:211` — `sec:taxonomy` → `sec:causes`
- `sections/taxonomy.tex:10` — `sec:taxonomy` → `sec:causes` (internal, in the
  "taxonomy these coordinates imply" paragraph)
- **`sections/related_work.tex:13`** — `sec:taxonomy` → `sec:causes` (the blocker;
  "That attribution is the second axis of our taxonomy (§…)")

**If not done:** no output defect. Source-clarity only.

---

## 2. `system.tex:4` calls §3 "the measurement model" — LOW, and not blocked

**Problem.** `system.tex:4` reads "EvalAudit operationalizes the measurement model
of \S\ref{sec:model}". After the rename, §3 is a taxonomy, and the phrase points at
a title that no longer exists.

**Not blocked** — `system.tex` is editable. Listed here only to keep the
consequences of the §3 rename in one place. Suggested wording: "operationalizes
the taxonomy of §3", or "operationalizes the decomposition of §3.1" if the intent
was specifically the five coordinates.

**If not done:** a reader following the cross-reference finds a section whose title
does not match the phrase that sent them there.

---

## Not proposed

- No content changes to §2. The pass ending at `67c2decb` — Goodman crediting, the
  execution-substrate merge, the forward/inverse differentiation, the validity
  demotion, the provenance correction, titles, and order — closed everything found.

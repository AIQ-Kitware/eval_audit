# Proposed edits to `sections/introduction.tex` — for review

`introduction.tex` is owned by Edward. Nothing here has been applied. Each item
states the problem, the proposed text, and what happens if it is not done.

Last updated: 2026-07-27. Reflects the intro at commit `6cf60125`.

---

## 1. The introduction never uses "identifiability" — HIGH

**Problem.** `grep -ci identifi sections/introduction.tex` returns **0**. The word
appears in the paper title, four times in the abstract, throughout §3 and §5, and
the §2 block "Identifiability, structural versus practical" is built on it. A
reader reaching §2 has met the concept only in the title.

This got worse recently. The §2 block was shortened (`ac8eafec`) to open with
"We use *identifiable* in the sense fixed in §3.1" — it now assumes the reader
already has the term rather than introducing it.

Contribution 4 already *makes* the claim in plain words ("remain underdetermined
from the surviving evidence"); it just does not name it.

**Proposed (one word, contribution 4, line 24):**

> …can be attributed to recoverable execution parameters or remain
> **\emph{non-identifiable}** --- underdetermined from the surviving evidence.

**History.** This was applied in `6e6e0696`, then silently removed by my own
`f71fa8d6` when I rewrote that region for the Goodman repositioning. I reported
`f71fa8d6` as complete without noticing. Flagging so it isn't mistaken for a
deliberate editorial choice.

**If not done:** §2's identifiability block and Table 1 read as imported
machinery, and the title's second noun is never set up.

---

## 2. "coordinate" is used before Eq. 1 exists — MEDIUM

**Problem.** Contribution 1 ends "…and which **coordinate** is responsible when a
result disagrees with the record." *Coordinate* only means something once the
generative model $Y = F(R,D,I,A,U)$ is on the table in §3.1. The earlier wording
"which coordinate **of the model**" at least hinted at a referent; that clause is
now gone.

**Proposed:** either

- "…and which **part of the recorded experiment** is responsible when a result
  disagrees with the record." (no forward dependency), or
- "…and which **coordinate of the measurement model** is responsible…" (keeps the
  term, restores the pointer).

**If not done:** minor. One undefined term in a contributions list.

---

## 3. Copy errors — MEDIUM, mechanical

Three, all pre-existing:

- **Line 4, stray conjunction.** "…the question of what is required to reproduce
  the experiments preserved in that record **and** becomes increasingly
  important." Delete "and".
- **Line 4, subject–verb.** "Although exact reproduction of evaluations for older
  models **are** of limited practical priority" — subject is *reproduction*, so
  **is**.
- **Line 31, missing word.** "it becomes **ever important** to understand" →
  "ever **more** important".

---

## 4. Open question: should contribution 1 credit Goodman? — LOW, judgment call

`6cf60125` deliberately removed `\citep{goodman2016does}` from contribution 1, on
the grounds that §2 now credits Goodman at length and naming the three targets
outright reads better in a contributions list.

Defensible. The residual risk is that a reviewer who reads only the abstract and
the contributions list takes the whole taxonomy as ours. Since two of the three
targets are Goodman's (see §2), that reading would overstate the claim.

Cheapest mitigation if wanted, without restoring the citation: change "A taxonomy
of benchmark reproduction" to "**An attribution taxonomy** for benchmark
reproduction" — signals that attribution is the novel axis without adding
apparatus.

Leaving as-is is also fine. Recording it so the decision is visible rather than
accidental.

---

## 5. The "fair comparisons" claim is the one contamination hook — LOW

**Problem.** Paragraph 4 argues that understanding historical benchmark context
is "essential for making fair comparisons with modern systems." That is the only
sentence in the paper that invites a contamination objection: if historical
numbers are being recommended for cross-generation comparison, a reviewer can
note that contamination is a larger obstacle to fair comparison than
reproducibility is.

Elsewhere the paper is immune — reconstructibility is orthogonal to validity —
and §8 now carries a "Score validity is out of scope" paragraph
(`sainz2023contamination`) stating that boundary. This sentence is the one place
where the two axes touch.

**Proposed:** no wording change required if you are content to let §8 carry it.
If you want the intro airtight, narrow the claim from *fair comparisons* to
*correctly interpreting what a historical number measured*, which is what the
paper actually supports.

**If not done:** low risk. §8 covers it, and the objection is narrow.

---

## Not proposed

- Rewriting the two motivating questions in ¶10 — they work and match §3's gate
  and axes.
- Adding the "layer" vocabulary — deliberately dropped from §2 (`e1d6fca4`);
  the intro's two uses of "layer" mean something else (a layer of provenance to
  preserve), and that sense should stay.

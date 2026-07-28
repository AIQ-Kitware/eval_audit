# Proposed edits to `sections/introduction.tex` — for review

`introduction.tex` is owned by Edward. Nothing here has been applied. Each item
states the problem, the proposed text, and what happens if it is not done.

Last updated: 2026-07-28. Reflects the intro at commit `6cf60125`; items 6--8
follow the §3 rework, which ended at `4a11f89f` with **four** coordinates —
recipe, deployment, execution instrument, residual — of which three are
controllable, and **five** causes on axis 2. The former fourth coordinate
("artifact interpretation", briefly "record history") was retired: a defect in
the stored record is evidence failing rather than a cause of the score, so it is
now a gate reason. Items 7 and 8 are where §1 still reflects the old scheme.

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

## 6. "deployment" is used for what §3.1 now calls the instrument — HIGH

**Problem.** §3.1 was sharpened (2026-07-28) so each coordinate is defined by the
role it plays, and the deployment/instrument boundary is now explicit:

- **deployment** — *what fixes the function to be computed*: weights, model and
  tokenizer revision, chat template, load precision, quantization, and the client
  or engine **family**.
- **execution instrument** — *what fixes how that function is numerically
  realized*: harness version, the engine's **build and kernels**, package
  versions, hardware, device topology, batching and caching.

The intro uses "deployment" for the union of both. Line 8 lists "serving backend,
software dependencies, numerical precision, batching behavior, hardware and
runtime configuration" and then says these "are **deployment properties** rather
than conventional benchmark parameters." Under §3.1, four of those six are
instrument properties. A reader who arrives at §3.1 with the intro's sense of the
word has to unlearn it at exactly the point the paper is drawing its central
distinction.

**Proposed (line 8, one phrase):**

> Many of these factors are difficult to recover because they are
> **execution properties** rather than conventional benchmark parameters…

"Execution properties" is neutral between the two coordinates and needs no
forward reference. If you would rather name the split in the intro, the
alternative is "…because they are properties of the **deployment and the
execution instrument** rather than of the benchmark recipe" — but that spends a
forward reference on machinery §3.1 defines properly two pages later.

**If not done:** the paper's most-used technical term means two different things
in §1 and §3, and the §3.1 boundary paragraph reads as a correction of the intro
rather than as a definition.

---

## 7. "execution environment" vs. "execution instrument" — MEDIUM

**Problem.** Line 12 lists the four places a discrepancy can be explained:
"differences in the recorded recipe, model deployment, **execution environment**,
or **interpretation of the resulting artifacts**." Two mismatches with §3, and the
second is now the larger one.

*Naming.* The third item is *execution environment* here and *execution
instrument* everywhere else (§3.1, §3.4, §5, §7, Table 1). Close enough that a
reader assumes they are the same thing and then wonders why the paper renamed it.

*Count.* The list used to track §3's four controllable coordinates in order. §3
now has **three** — recipe, deployment, instrument — and its fourth member no
longer exists. What "artifact interpretation" covered was redistributed: schema
fields a newer harness wrote and per-instance row ordering are the **instrument**
(the harness generated the record that way), our own dedup / normalization /
key-granularity decisions are audit-tool correctness and sit in §8, and
post-execution change to the stored artifact — migrations, carried-forward
copies, damage in transit — is now a **gate** concern rather than a coordinate,
because it is a defect in the evidence rather than a cause of the score.

**Proposed:** change "execution environment" to "execution instrument", and drop
the fourth item so the list reads "…the recorded recipe, model deployment, or
execution instrument." If you want to keep a fourth clause, "…or the later
history of the resulting artifacts" is accurate and points at the gate, but the
shorter version aligns §1 with §3.4 exactly.

**Related, same line:** "model deployment" here vs. "deployment" as the §3.1
coordinate name — harmless, since the modifier reads as descriptive.

**If not done:** a reader tracking the decomposition sees §1 name four things and
§3 name three, with one term differing between them.

---

## 8. Contribution 2's localization list crosses the coordinate boundaries — LOW

**Problem.** Line 20 says failures can be localized to "prompts and adaptation,
model deployment, tokenizer and chat-template behavior, numerical configuration,
serving infrastructure, or **artifact interpretation**." Under §3.1, items 2–4 are
all the *deployment* coordinate (tokenizer and chat template and precision are
deployment members), "serving infrastructure" straddles the deployment/instrument
line the §3.1 boundary paragraph now draws through the engine, and the final item
names a coordinate that no longer exists (see item 7).

This remains the least urgent of the three. The list reads as an informal
enumeration of what the tooling can separate, not as a claim about the taxonomy,
and it predates the coordinates — "artifact interpretation" is defensible here as
plain English for what the tooling does when it reads two stores.

**Proposed:** leave it, or align it exactly with §3.4's five causes if you want §1
and §3 to be readable as the same scheme. If only one word changes, make the last
item "artifact **provenance**", which is true of what the tooling separates and
does not collide with a retired coordinate name.

**If not done:** no defect a reviewer would name; a careful reader may read the
list as a competing decomposition.

---

## Not proposed

- Rewriting the two motivating questions in ¶10 — they work and match §3's gate
  and axes.
- Adding the "layer" vocabulary — deliberately dropped from §2 (`e1d6fca4`);
  the intro's two uses of "layer" mean something else (a layer of provenance to
  preserve), and that sense should stay.

# Freeze the expanded spec for de-novo compute runs — status & plan

**Status (2026-07-21): PROPOSED.** No code yet. This plan closes the one
provenance gap the run-key critique exposes (see the 2026-07-21 entry in
[`dev/journals/claude.md`](../../dev/journals/claude.md)): our *reproduction*
path already treats the frozen `run_spec.json` as the durable handle
([[all-reproductions-must-be-from-spec]]), but our *compute* path still keeps a
HELM run-key **string** as the stored source of truth and re-expands it under
the installed crfm-helm at execution time.

**Scope:** every compute runbook that authors `run_entries` as run-key strings
and exports a bundle *without* `--from-spec` — today
[`reproduce/qwen35_vllm/`](../../reproduce/qwen35_vllm/),
[`reproduce/qwen35_small_vllm/`](../../reproduce/qwen35_small_vllm/),
[`reproduce/open_judge_gpt_oss/`](../../reproduce/open_judge_gpt_oss/), and the
gpt-oss core grids — all sourced from
[`eval_audit/integrations/infer_stack/preset_configs.yaml`](../../eval_audit/integrations/infer_stack/preset_configs.yaml).

**Depends on (all IMPLEMENTED):**
[`run-from-run-spec-json-plan.md`](run-from-run-spec-json-plan.md) (the replay
pipeline) and the `export-benchmark-bundle --from-spec --freeze-rel-paths`
machinery the OLMo/e2e reproductions already ride
([`olmo-from-run-spec-migration-plan.md`](olmo-from-run-spec-migration-plan.md)).
This plan reuses that replay path — it does **not** invent a second executor.

---

## 1. The central insight

A run key is a **lossy name**; the `RunSpec` is the ground truth. HELM's
original sin — the one this project criticizes — is treating the *mutable name*
as the durable handle and regenerating the spec on demand through a
version-sensitive expander. The same string
(`mmlu:subject=abstract_algebra,method=multiple_choice_joint,model=…,data_augmentation=canonical,model_deployment=…`)
expands to *different* specs across HELM versions as defaults, adapter specs,
instructions, and `max_eval_instances` drift. G13 in the corpus is that
fragility fully matured: classic officials carry a stored class-path that **no
released HELM expander resolves**, needing shim canonicalization.

The from-spec discipline is the fix: *the expanded spec, not its name, is the
durable handle.* We already enforce it for **historical** reproductions. The
gap is that we do **not** enforce it for our **own freshly-computed** runs,
which have no prior official spec and so must be authored — today as key
strings, expanded live at execution time. That inherits exactly the
version-coupling we criticize, deferred onto the future reader of our results.

**This is not hypocrisy — it is an incomplete application of our own rule.**
For de-novo compute there is genuinely no prior spec to replay, so *someone*
must author the run, and HELM's only authoring interface is
`run_entries` → expander. The fragility is specifically **cross-version**: at
authoring time, under a single pinned HELM build, key→spec is deterministic.
The rule that dissolves the tension is a lifecycle constraint, not a new
executor:

> **Expand once at authoring, then freeze.** The expander touches each compute
> run exactly once, at birth; its output `run_spec.json` becomes the canonical,
> content-addressed, archived artifact and the source of truth for every
> downstream re-run.

Do that and the compute corpus becomes from-spec-reproducible like everything
else, and our results stop resting on a mutable name.

---

## 2. What's true today (the gap, concretely)

- **Source of truth is a string.** `preset_configs.yaml` stores `run_entries`
  as run-key DSL strings. `export-benchmark-bundle` (no `--from-spec`) copies
  them into `{smoke,full}_manifest.yaml`; `eval-audit-run` schedules them; HELM
  expands each string → `run_spec.json` **inside the run dir at execution
  time**, entangled with the GPU job.
- **The frozen spec exists but is a by-product, not the handle.** Each run dir
  ends up with a `run_spec.json` (the `40_verify_artifacts.sh` scripts look for
  it), but nothing promotes it to a canonical artifact, content-addresses it, or
  routes a re-run through it. Re-running re-expands the string.
- **No expander-version provenance.** Nothing records *which* crfm-helm build
  expanded the string, so a future reader cannot tell whether their re-expansion
  matches ours — the precise ambiguity from-spec was introduced to kill.

---

## 3. Proposed resolution

Decouple **authoring** (key string, human-editable) from **identity**
(frozen, content-addressed spec) by inserting a freeze step **before** any GPU
time, then routing compute execution through the existing `--from-spec` replay.

### Change 1 — offline expand-and-freeze step *(core)*
Add a bundle-build mode that expands each authored `run_entry` into its
`run_spec.json` under the **pinned** crfm-helm **without running inference**,
then writes each spec into the bundle as a **content-addressed** artifact
(hash in the filename, per [[kwdagger-job-identity-caching]] — so an edited key
that changes the spec invalidates cleanly). Surface as either a new CLI
(`eval-audit-freeze-run-specs`) or a `--freeze-specs` flag on
`export-benchmark-bundle`. Emit a `frozen_specs/manifest.json` listing
`{run_key → spec_hash → path}`.
**Open question F1 (must resolve first):** does HELM expose run-key → `RunSpec`
expansion *without* executing? `helm-run` writes `run_spec.json` before
inference; confirm whether a `--dry-run`/`--skip-completed` or a direct
`run_spec_factory` call yields the spec offline. If not, the freeze step runs a
`--max-eval-instances 0`/1 pass purely to harvest specs.

### Change 2 — route compute execution through the frozen specs
Once frozen, the smoke/full runs replay via the **existing**
`export-benchmark-bundle --from-spec` path against `frozen_specs/`, exactly as
the OLMo reproduction does. The run-key string is now a **transient authoring
input**; the frozen spec is what executes and what identifies the run. No new
executor, no second code path — this is the same replay OLMo/e2e already trust.

### Change 3 — expander-version provenance + drift guard
Stamp the frozen-spec manifest with the crfm-helm build (version + git sha).
Add a preflight (`eval-audit-freeze-run-specs --check`) that **re-expands** the
authored keys under the currently-installed HELM and diffs against the frozen
specs — a mismatch means the expander drifted (the G13 failure mode) and is a
hard preflight failure, not a silent re-expansion. This is the guard the
reproduction path gets for free and the compute path currently lacks.

### Change 4 — docs + paper framing
- Extend the from-spec discipline doc and each compute runbook README to state
  the rule: *run keys are a transient authoring input, never a durable
  identity; the frozen `run_spec.json` is the handle for compute runs too.*
- Update the paper's methods framing per the journal: present this as evidence
  *for* the thesis (HELM's error is treating the mutable name as the handle; we
  inherit the name only momentarily, at birth, then freeze), with the honest
  caveat that until Changes 1–2 land the presets still keep the key string as
  the stored source of truth — so the discipline is **aspirational today**.

### Change 5 *(optional, stronger)* — author structured RunSpecs
Skip the DSL-parse fragility even at authoring time by letting presets carry a
structured `RunSpec` (or freeze immediately on first author) instead of the
run-key string. Deferred: it changes the human-editable surface and is only
worthwhile if the string DSL itself proves to be a drift source in Change 3's
diffs.

---

## 4. Verification

- **Determinism test:** author key → freeze → replay, twice, asserting
  byte-identical `run_spec.json` and identical `spec_hash` across runs.
- **Drift-detector test:** mutate a default the expander honors (or point at a
  second pinned HELM) and assert Change 3's preflight fails loudly rather than
  silently re-expanding.
- **Corpus regression:** a frozen qwen3.5 compute run re-runs from its own
  frozen spec and lands identical metrics to its original live-expanded run
  (isolates "did freezing change anything?" to zero).
- Compile gates per `CLAUDE.md`; the freeze/expand logic stays HELM-import-light
  where testable (mirror the rejudge-matrix planner's no-scheduler unit-test
  posture).

---

## 5. Sequencing

1. **F1 spike** — determine HELM's offline expansion capability (blocks Change 1's shape).
2. **Change 1** — freeze step + content-addressed store + manifest.
3. **Change 3** — version stamp + drift guard (cheap, high-value; land alongside 1).
4. **Change 2** — flip qwen35_vllm (smallest) to frozen-spec replay; validate; then qwen35_small_vllm, open_judge, gpt-oss.
5. **Change 4** — docs/paper once one runbook is proven on the frozen path.
6. **Change 5** — only if Change 3 shows DSL-parse drift.

---

## 6. Open questions

- **F1:** offline run-key → `RunSpec` without inference (above). Everything else
  is shaped by the answer.
- **Storage locus:** frozen specs under the bundle (`$BUNDLE_ROOT/frozen_specs/`)
  vs the audit store. Bundle keeps them next to the manifest that references
  them; the store makes them survive bundle rebuilds. Lean bundle-first,
  promote to store if cross-bundle reuse appears.
- **Retrofit vs greenfield:** do we re-freeze already-executed compute runs
  from their by-product `run_spec.json` (cheap, no GPU) so the *existing*
  qwen3.5 corpus gains a canonical handle retroactively? Likely yes — it makes
  the current results citable under the new discipline without re-running.

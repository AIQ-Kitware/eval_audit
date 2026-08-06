# Phase 3 — Behavior-Equivalence Test Matrix

**Status:** DESIGN. Companion to [`phase3-comparison-core-unification.md`](phase3-comparison-core-unification.md).
**Revised 2026-06-11** alongside the design doc's §0 research-context revision.

**Purpose.** Phase 3 replaces the forked comparison cores (`HelmRunDiff` vs `normalized.compare`) with
one `NormalizedDiff`, and adds substitution-aware comparisons for the open-judge extension. Because
this touches research-meaningful numbers, every sub-stage must be proven to leave outputs equivalent
(or, for the new substitution features, to leave *non-extension* outputs untouched). This doc defines
*what equivalent means*, *what inputs span the behavior space*, and *the exact assertions* that gate
each sub-stage from §4 of the design doc.

---

## 1. The golden rule

> Capture every current output **before** writing any `NormalizedDiff` code. Each sub-stage re-runs
> the same inputs and diffs against the captured baseline. A sub-stage is mergeable **only** if its
> row in the matrix is green.

The baseline is captured **twice** for EEE-backed components, once per instance-source policy
(design doc §3.7): `eee-only` (instances strictly from EEE — captured today via
`EVAL_AUDIT_EEE_STRICT=1`) and `helm-preferred` (HELM-derived instances when an origin exists —
today's implicit fallback behavior). After sub-stage 4.5 these become *declared policies* with
recorded `instance_source` provenance; the matrix pins **both** behaviors. Neither column is "the"
reference anymore — the requirement is that each policy reproduces its own pinned baseline and that
the choice is never disk-state-implicit.

---

## 2. Input axes (what spans the behavior space)

A fixture pair is one cell in the cross-product of these axes. Not all combinations are valid; §3
lists the concrete required set.

| Axis | Values | Why it matters |
|---|---|---|
| **A. Source artifacts** | `helm×helm`, `helm×eee`, `eee×eee` | The three ways `NormalizedRun`s reach the diff; `helm×eee` exercises the `helm_compat` shim path |
| **B. Recipe facts availability** | native `recipe_facts`, sidecar `run_spec.json`, none | Drives `diagnosis` populated vs `comparability_unknown:*`; under R1 the HELM renderer must *fail loudly* (not degrade) when a HELM-format component lacks run_spec |
| **C. Agreement regime** | exact (`abs_tol=0` → 1.0), drift (dips below 1.0 mid-sweep), self-compare (official vs itself → strict 1.0) | Curves must match across the whole `abs_tol=0…0.1` sweep, not just the endpoints |
| **D. Instance join outcome** | clean join, `join_failed` cells, sample-id drift between sides | The instance-source question lives here; HELM vs EEE join keys can disagree |
| **E. Metric domain** | binary (exact_match), bounded-overlap, continuous (bleu/f1), **judge-derived (e.g. `safety_gpt_score`)** | `metric_domain` branches; the judge-derived class drives the R2 metric-class split |
| **F. Multiplicity** | single official+single local, `local_repeat` (multi-attempt) | `local_repeat` comparison generation in the planner |
| **G. Instance-source policy** | `eee-only` vs `helm-preferred` | Replaces the old strict-mode axis; both policies pinned (§1) |
| **H. Substitution declaration** (new, R2) | none, declared `judge` substitution, undeclared judge difference | Drives `intended_substitution:judge` vs drift labels vs `substitution_not_observed:judge` |

---

## 3. Required fixture set

Reuse and extend the existing fixture at
[`tests/fixtures/eee_only_demo/`](../../tests/fixtures/eee_only_demo/) (built by `build_fixture.py`)
and the HELM-shaped fixtures behind `test_core_metrics_single_run` /
`test_rebuild_core_report` / `test_compare_pair_eee`.

| Fixture | Axes covered | Source | Status |
|---|---|---|---|
| **F1 — HELM self-compare** | A:helm×helm, C:self (strict 1.0), B:run_spec present | existing HELM fixture | reuse |
| **F2 — HELM official vs local, real drift** | A:helm×helm, C:drift, D:clean + some join_failed, E:mixed | existing `rebuild_core_report` fixture | reuse |
| **F3 — EEE-only pair, no recipe facts** | A:eee×eee, B:none, → all facts `unknown` | `eee_only_demo` fixture | reuse (`test_compare_pair_eee`) |
| **F4 — EEE-only pair, with sidecar** | A:eee×eee, B:sidecar, → facts populate | `eee_only_demo` + `run_spec.json` sidecar | reuse (`test_compare_pair_eee`) |
| **F5 — EEE pair with native recipe_facts** | A:eee×eee, B:native (incl. judge identity) | **new** — extend `build_fixture.py` to emit a `recipe_facts` block | **build for 4.1/4.7** |
| **F6 — HELM-origin EEE (instance-source probe)** | A:eee×eee, D:sample-id drift, G:both policies | **new** — EEE artifact whose `Origin.helm_run_path` exists and whose EEE/HELM instance ids differ | **build for 4.5** |
| **F7 — local_repeat** | F:multi-attempt local | existing multi-attempt fixture in `test_packet_driven_summary_loading` | reuse |
| **F8 — mixed-format packet** | A:helm×eee in one packet | **new** — official HELM run + local EEE artifact, same logical key | **build for 4.3/4.6** |
| **F9 — judge substitution pair** (new, R2) | H:declared judge substitution, E:judge-derived + deterministic metrics in one run, B:both sides carry judge identity | **new** — official run with closed-judge identity (e.g. a `safety_gpt_score`-class metric annotated by a closed model) vs local re-run with an open judge; deterministic metrics agree, judge-derived metrics differ | **build for 4.9** |
| **F10 — undeclared judge difference** | H:undeclared | **new** — same pair as F9 but the comparison intent carries no `substitutions` declaration | **build for 4.9** (cheap variant of F9) |

---

## 4. Equivalence assertions per output

For each fixture pair, capture and compare these artifacts. "Equivalent" is defined per-row — some
fields must be **identical**, some are **tolerance-bounded** (floating point), some are **expected to
differ** (and that difference is asserted, not ignored). Keys added by Phase 3
(`instance_source`, `substitutions`, metric-class splits) are **additive**: baseline comparison
ignores their absence in the old snapshots but pins their values in new ones.

| Output | File | Equivalence definition |
|---|---|---|
| **Run-level agreement rows** | `core_metric_report.json` → `pairs[].run_rows` | numeric fields equal within `atol=1e-9` (same data, same arithmetic); keys/order identical |
| **Instance-level agreement rows** | `…json` → `pairs[].inst_rows` | same `atol=1e-9`; **row set** (by `join_key`) identical |
| **Per-metric agreement curves** | `…json` → `pairs[].per_metric_curves` | at every `abs_tol` in the sweep, `agreement_ratio` equal within `atol=1e-9`; same metric set |
| **`diagnosis` block** | `…json` → `pairs[].diagnosis` | **label string identical**; reason set identical; priority identical (this is what 4.2 proves). For F9: label = `intended_substitution:judge`; for F10: drift label |
| **comparability_facts** | `…json` → `comparability_facts` | `status` (`yes`/`no`/`unknown`) **identical** per fact; `values` set identical. `same_judge` appears from 4.9 on; F9/F10 pin it `no` |
| **warnings** | `warnings.json` / `warnings.txt` | `comparability_unknown:*` / `comparability_drift:*` set **identical**; F9 adds none for the declared substitution; F10 must warn |
| **`instance_source` provenance** (from 4.5) | `…json` → per-component | matches the declared policy on every component; never absent |
| **metric-class split** (from 4.9) | `…json` → per-class agreement | F9: deterministic class gates at tolerance (the control); judge-derived class difference is *reported, not gated* — its value is pinned as the fixture's expected shift |
| **management summary text** | `core_metric_management_summary.txt` | identical after normalizing timestamps/paths |
| **PNG figures** | `core_metric_report.png` | structural, not pixel: same number of subplots, same series labels, same data extents |
| **Stage-6 aggregate** | `build_reports_summary` outputs over a multi-packet run | identical after normalizing timestamps/paths (proves the aggregate still reads the new per-packet JSON; gains `instance_source` + metric-class columns additively) |

### Tolerances

- **Agreement / curve numerics:** `atol=1e-9`. Rationale: `NormalizedDiff` runs the *same*
  `normalized.compare` arithmetic that already powers `skip_diagnosis=True` in production — this is
  not a re-derivation, so equality should be exact-to-FP-noise. **If any cell needs a looser
  tolerance, that is a finding to investigate, not a knob to widen.**
- **Diagnosis / facts / warnings:** exact (categorical).
- **Judge-derived metric shift (F9):** not gated by tolerance — the shift is the extension's
  *measurement*. The fixture pins its expected value so regressions in the comparison code are
  still caught.
- **Timestamps, absolute paths, `generated_utc`:** normalized out before comparison.

---

## 5. Sub-stage → gate mapping

Each design-doc sub-stage is green only when its listed cells pass.

**E1 — EEE-only operability gate (used by 4.4, replaces the old import-isolation gate):** run the
EEE-only entry points (`from-eee`, `compare-pair-eee`) against F3/F4/F5 in an environment with **zero
HELM artifacts on disk**; the full report tree must build, and every component must record
`instance_source: eee`. (The old `sys.modules`/static-grep isolation tests are **optional, deferred**
— keep the test sketches from the hard-split doc §4 on file for if a paper claim needs them again.)

| Sub-stage | Gate |
|---|---|
| **4.0** lift + broaden metric taxonomy | full suite unchanged; `normalized.compare` imports 0 `helm.*`; judge-class unit tests (e.g. `safety_gpt_score` → judge-derived) |
| **4.1** recipe_facts accessor (+ judge fields) | F3 → all `unknown`; F4 → populated; F5 → populated from native block incl. judge identity. No numeric change anywhere. |
| **4.2** `normalized/diagnose.py` | F1,F2,F4,F5,F8: `diagnosis` block **byte-identical** to `HelmRunDiff._diagnose_repro` on the same inputs (unit test, before any wiring). F9/F10 substitution labels unit-tested against the spec. |
| **4.3** `NormalizedDiff` | F1–F8: every output row in §4 equivalent to captured baseline; **stop if any numeric cell exceeds `atol=1e-9`** |
| **4.4** EEE-only operability | **E1 green**; outputs unchanged on F3–F5 |
| **4.5** instance-source explicitness | F6 under both policies reproduces its own pinned baseline; the same artifact tree yields **identical** results regardless of unrelated disk state; `instance_source` recorded everywhere; F6 policy-diff (which cells move between `present`/`join_failed`) captured as a fixture artifact and asserted stable |
| **4.6** HELM path → NormalizedDiff | F1,F2,F8: HELM-path outputs equivalent to pre-4.6; `HelmRunDiff` still invoked for run_spec semantic diff; **loud-failure test**: HELM-format component without run_spec.json errors instead of degrading |
| **4.7** native recipe_facts | F5: diagnosis populates with **no sidecar present**, incl. `same_judge` |
| **4.8** docs/retire | confirm `--skip-diagnosis` + `EVAL_AUDIT_EEE_STRICT` removal leaves the suite green |
| **4.9** open-judge extension | F9: pairs, `same_judge: no`, diagnosis `intended_substitution:judge`, deterministic-class control gates at tolerance, judge-class shift pinned. F10: drift label + warning. Filter-report shows `judge_substitution_planned` as a distinct selection path. **All non-extension fixtures (F1–F8) byte-identical** — the extension must be invisible unless opted into. |

---

## 6. The instance-source probe (sub-stage 4.5)

The hard-split doc's hot finding stands: the EEE loader silently overwrites EEE-derived instances
with HELM-derived ones when `Origin.helm_run_path` exists, so *the same EEE artifact produces
different numbers depending on whether HELM run dirs are also on disk*. The revised fix
(design doc §3.7) is explicitness, not removal: `helm-preferred` is a legitimate, *declared* policy
for the R1 HELM-driven analysis; `eee-only` is the declared policy for EEE-only mode.

**F6 is the dedicated probe.** It is an EEE artifact whose `helm_run_path` exists and whose
EEE-derived vs HELM-derived instance ids deliberately disagree on a few cells. The test asserts:

1. Policy `eee-only`: instances come **only** from EEE; some cells land in `join_failed` — and the
   test asserts *which* ones, pinning the EEE-only join behavior.
2. Policy `helm-preferred`: instances come from HELM; those cells land in `present`. Equally pinned.
3. The **diff between (1) and (2)** is emitted as a fixture artifact and asserted stable — it is the
   "what the instance-source choice changes" evidence that any analysis mixing modes must cite.
4. **No disk-state sensitivity:** deleting the HELM run dir under policy `eee-only` changes nothing;
   deleting it under `helm-preferred` is a hard error for HELM-format components and a *recorded*
   degradation (`instance_source: eee` + warning) for EEE components — never a silent number change.

---

## 7. Test files to add

| File | Covers |
|---|---|
| `tests/test_phase3_diagnose_equivalence.py` | 4.2 — `normalized.diagnose` vs `HelmRunDiff._diagnose_repro`, fixtures F1/F2/F4/F5/F8; substitution-label unit tests (F9/F10 spec) |
| `tests/test_phase3_normalized_diff.py` | 4.3/4.6 — full §4 output equivalence, fixtures F1–F8 (slow; `--run-slow`) |
| `tests/test_phase3_instance_source.py` | 4.4 — gate E1 (full tree with zero HELM artifacts; `instance_source: eee` everywhere) |
| `tests/test_phase3_instance_source.py` | 4.5 — F6 probe, both policies, disk-state insensitivity |
| `tests/test_phase3_judge_substitution.py` | 4.9 — F9/F10 end-to-end, filter-report selection path |
| extend `tests/fixtures/eee_only_demo/build_fixture.py` | F5 (`recipe_facts` incl. judge identity), F6 (drift), F8 (mixed-format), F9/F10 (judge pair) |

---

## 8. Capture harness (run once, before any Phase 3 code)

```
# Pseudocode for the baseline snapshot the matrix diffs against.
for fixture in [F1..F8]:                      # F9/F10 have no "current" behavior to snapshot;
    for policy in (eee_only, helm_preferred): # their expected outputs are specified, not captured
        env = {"EVAL_AUDIT_EEE_STRICT": "1"} if policy == eee_only else {}
        run core_metrics (current code) on fixture -> out_dir
        normalize(out_dir)            # strip timestamps, abs paths, generated_utc
        snapshot[fixture, policy] = hash_tree(out_dir) + parsed_json(out_dir)
persist snapshot as tests/fixtures/phase3_baseline/  (committed)
```

Every sub-stage's test re-runs the relevant `(fixture, policy)` cells through the *new* code and
asserts equality against `phase3_baseline/` per the §4 definitions. The baseline is committed so the
gate is reproducible across machines and the "stop condition" (numbers moved) is unambiguous.
F9/F10 are *specification* fixtures: their expected outputs are written down first (the substitution
labels, the deterministic-class control, the pinned judge-class shift) and the implementation is
built to meet them.

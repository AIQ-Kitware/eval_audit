# Phase 3 — Behavior-Equivalence Test Matrix

**Status:** DESIGN. Companion to [`phase3-comparison-core-unification.md`](phase3-comparison-core-unification.md).
The refactor plan requires Phase 3 to begin with *"a written design doc + a behavior-equivalence test
matrix"* — this is that matrix.

**Purpose.** Phase 3 replaces the forked comparison cores (`HelmRunDiff` vs `normalized.compare`) with
one `NormalizedDiff`. Because this touches research-meaningful numbers, every sub-stage must be proven
to leave outputs equivalent. This doc defines *what equivalent means*, *what inputs span the behavior
space*, and *the exact assertions* that gate each sub-stage from §4 of the design doc.

---

## 1. The golden rule

> Capture every current output **before** writing any `NormalizedDiff` code. Each sub-stage re-runs
> the same inputs and diffs against the captured baseline. A sub-stage is mergeable **only** if its
> row in the matrix is green.

The baseline is captured **twice** for the EEE path: once with `EVAL_AUDIT_EEE_STRICT` unset (current
default, HELM-fallback active) and once with `EVAL_AUDIT_EEE_STRICT=1` (honest EEE-only). Per the
hard-split doc, **the strict column is the reference for the published paper numbers**; the non-strict
column exists to *measure* the fallback's effect (which cells move), not to preserve it.

---

## 2. Input axes (what spans the behavior space)

A fixture pair is one cell in the cross-product of these axes. Not all combinations are valid; §3
lists the concrete required set.

| Axis | Values | Why it matters |
|---|---|---|
| **A. Source artifacts** | `helm×helm`, `helm×eee`, `eee×eee` | The three ways `NormalizedRun`s reach the diff; `helm×eee` exercises the `helm_compat` shim path |
| **B. Recipe facts availability** | native `recipe_facts`, sidecar `run_spec.json`, none | Drives `diagnosis` populated vs `comparability_unknown:*` |
| **C. Agreement regime** | exact (`abs_tol=0` → 1.0), drift (dips below 1.0 mid-sweep), self-compare (official vs itself → strict 1.0) | Curves must match across the whole `abs_tol=0…0.1` sweep, not just the endpoints |
| **D. Instance join outcome** | clean join, `join_failed` cells, sample-id drift between sides | The §6 hot-finding lives here; HELM vs EEE join keys can disagree |
| **E. Metric domain** | binary (exact_match), bounded-overlap, continuous (bleu/f1) | `metric_domain` / discrete-vs-continuous rendering branches |
| **F. Multiplicity** | single official+single local, `local_repeat` (multi-attempt) | `local_repeat` comparison generation in the planner |
| **G. EEE strict mode** | `EVAL_AUDIT_EEE_STRICT` unset vs `=1` | Measures the silent HELM fallback (sub-stage 4.5) |

---

## 3. Required fixture set

Reuse and extend the existing fixture at
[`tests/fixtures/eee_only_demo/`](../../tests/fixtures/eee_only_demo/) (built by `build_fixture.py`)
and the HELM-shaped fixtures behind `test_core_metrics_single_run` /
`test_rebuild_core_report` / `test_compare_pair_eee`.

| Fixture | Axes covered | Source | Status |
|---|---|---|---|
| **F1 — HELM self-compare** | A:helm×helm, C:self (strict 1.0), B:native(run_spec present) | existing HELM fixture | reuse |
| **F2 — HELM official vs local, real drift** | A:helm×helm, C:drift, D:clean + some join_failed, E:mixed | existing `rebuild_core_report` fixture | reuse |
| **F3 — EEE-only pair, no recipe facts** | A:eee×eee, B:none, → all 5 facts `unknown` | `eee_only_demo` fixture | reuse (`test_compare_pair_eee`) |
| **F4 — EEE-only pair, with sidecar** | A:eee×eee, B:sidecar, → facts populate | `eee_only_demo` + `run_spec.json` sidecar | reuse (`test_compare_pair_eee`) |
| **F5 — EEE pair with native recipe_facts** | A:eee×eee, B:native | **new** — extend `build_fixture.py` to emit a `recipe_facts` block | **build for 4.1/4.7** |
| **F6 — HELM-origin EEE (fallback-sensitive)** | A:eee×eee, D:sample-id drift, G:both strict modes | **new** — EEE artifact whose `Origin.helm_run_path` exists and whose EEE/HELM instance ids differ | **build for 4.5** (this is the hot-finding probe) |
| **F7 — local_repeat** | F:multi-attempt local | existing multi-attempt fixture in `test_packet_driven_summary_loading` | reuse |
| **F8 — mixed-format packet** | A:helm×eee in one packet | **new** — official HELM run + local EEE artifact, same logical key | **build for 4.3/4.6** |

---

## 4. Equivalence assertions per output

For each fixture pair, capture and compare these artifacts. "Equivalent" is defined per-row — some
fields must be **identical**, some are **tolerance-bounded** (floating point), some are **expected to
differ** (and that difference is asserted, not ignored).

| Output | File | Equivalence definition |
|---|---|---|
| **Run-level agreement rows** | `core_metric_report.json` → `pairs[].run_rows` | numeric fields equal within `atol=1e-9` (same data, same arithmetic); keys/order identical |
| **Instance-level agreement rows** | `…json` → `pairs[].inst_rows` | same `atol=1e-9`; **row set** (by `join_key`) identical |
| **Per-metric agreement curves** | `…json` → `pairs[].per_metric_curves` | at every `abs_tol` in the sweep, `agreement_ratio` equal within `atol=1e-9`; same metric set |
| **`diagnosis` block** | `…json` → `pairs[].diagnosis` | **label string identical**; reason set identical; priority identical (this is what 4.2 proves) |
| **comparability_facts** | `…json` → `comparability_facts` | `status` (`yes`/`no`/`unknown`) **identical** per fact; `values` set identical |
| **warnings** | `warnings.json` / `warnings.txt` | `comparability_unknown:*` / `comparability_drift:*` set **identical** |
| **management summary text** | `core_metric_management_summary.txt` | identical after normalizing timestamps/paths |
| **PNG figures** | `core_metric_report.png` | structural, not pixel: same number of subplots, same series labels, same data extents (compare the figure's backing data, not bytes) |
| **Stage-6 aggregate** | `build_reports_summary` outputs over a multi-packet run | identical after normalizing timestamps/paths (proves the aggregate still reads the new per-packet JSON) |

### Tolerances

- **Agreement / curve numerics:** `atol=1e-9`. Rationale: `NormalizedDiff` runs the *same*
  `normalized.compare` arithmetic that already powers `skip_diagnosis=True` in production — this is not
  a re-derivation, so equality should be exact-to-FP-noise, not approximate. **If any cell needs a
  looser tolerance, that is a finding to investigate, not a knob to widen.**
- **Diagnosis / facts / warnings:** exact (they are categorical).
- **Timestamps, absolute paths, `generated_utc`:** normalized out before comparison.

---

## 5. Sub-stage → gate mapping

Each design-doc sub-stage is green only when its listed cells pass.

| Sub-stage | Gate |
|---|---|
| **4.0** lift `helm.metrics` | full suite unchanged; `python -c "import eval_audit.normalized.compare, sys; assert not [m for m in sys.modules if m.startswith('eval_audit.helm')]"` passes |
| **4.1** recipe_facts accessor | F3 → all `unknown`; F4 → populated; F5 → populated from native block. No numeric change anywhere. |
| **4.2** `normalized/diagnose.py` | F1,F2,F4,F5,F8: `diagnosis` block **byte-identical** to `HelmRunDiff._diagnose_repro` on the same inputs (unit test, before any wiring) |
| **4.3** `NormalizedDiff` | F1–F8: every output row in §4 equivalent to captured baseline; **stop if any numeric cell exceeds `atol=1e-9`** |
| **4.4** `eee_only/` renderer | F3,F4,F5: outputs unchanged **and** `import eval_audit.cli.from_eee` → 0 `eval_audit.helm.*` in `sys.modules`; static grep test (hard-split §4) green |
| **4.5** remove HELM fallback | F6 strict vs non-strict diff **captured and reviewed**; default-strict outputs == prior `EVAL_AUDIT_EEE_STRICT=1` baseline exactly. Any moved cell logged as a paper artifact. |
| **4.6** HELM path → NormalizedDiff | F1,F2,F8: HELM-path outputs equivalent to pre-4.6; `HelmRunDiff` still invoked for run_spec semantic diff only |
| **4.7** native recipe_facts | F5: diagnosis populates with **no sidecar present** |
| **4.8** docs/retire | n/a (docs); confirm `--skip-diagnosis` removal leaves the suite green |

---

## 6. The hot-finding probe (sub-stage 4.5) — call it out explicitly

The hard-split doc's ⚠️ finding: the EEE loader silently overwrites EEE-derived instances with
HELM-derived ones when `Origin.helm_run_path` exists, so *the same EEE artifact produces different
numbers depending on whether HELM run dirs are also on disk*.

**F6 is the dedicated probe.** It is an EEE artifact whose `helm_run_path` exists and whose
EEE-derived vs HELM-derived instance ids deliberately disagree on a few cells. The test asserts:

1. With `EVAL_AUDIT_EEE_STRICT=1` (target default): instances come **only** from EEE; some cells land
   in `join_failed` — and the test asserts *which* ones, so the EEE-only join behavior is pinned.
2. With the flag unset (current default): instances come from HELM; those cells land in `present`.
3. The **diff between (1) and (2)** is emitted as a fixture artifact and asserted stable — it is the
   "which cells move" evidence the paper needs.

After 4.5 removes the fallback from the EEE-only loader, mode (2) is no longer reachable on the
EEE-only path; the test flips to asserting (1) is the *only* behavior.

---

## 7. Test files to add

| File | Covers |
|---|---|
| `tests/test_phase3_diagnose_equivalence.py` | 4.2 — `normalized.diagnose` vs `HelmRunDiff._diagnose_repro`, fixtures F1/F2/F4/F5/F8 |
| `tests/test_phase3_normalized_diff_equivalence.py` | 4.3/4.6 — full §4 output equivalence, fixtures F1–F8 (slow; `--run-slow`) |
| `tests/test_eee_only_isolation.py` | 4.4 — `sys.modules` runtime check + static grep (verbatim from hard-split §4) |
| `tests/test_phase3_eee_strict_fallback.py` | 4.5 — F6 hot-finding probe, both strict modes |
| extend `tests/fixtures/eee_only_demo/build_fixture.py` | F5 (`recipe_facts`), F6 (drift), F8 (mixed-format) |

---

## 8. Capture harness (run once, before any Phase 3 code)

```
# Pseudocode for the baseline snapshot the matrix diffs against.
for fixture in [F1..F8]:
    for strict in (unset, "1"):
        env = {"EVAL_AUDIT_EEE_STRICT": strict} if strict else {}
        run core_metrics (current code) on fixture -> out_dir
        normalize(out_dir)            # strip timestamps, abs paths, generated_utc
        snapshot[fixture, strict] = hash_tree(out_dir) + parsed_json(out_dir)
persist snapshot as tests/fixtures/phase3_baseline/  (committed)
```

Every sub-stage's test re-runs the relevant `(fixture, strict)` cells through the *new* code and
asserts equality against `phase3_baseline/` per the §4 definitions. The baseline is committed so the
gate is reproducible across machines and the "stop condition" (numbers moved) is unambiguous.

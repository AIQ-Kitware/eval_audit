# Plan: Robust, order-insensitive run-key matching in `core_report_planner`

## Objective
Replace the planner's order-sensitive, string-variant matching with a single
**canonical-key** equivalence so that local and official runs describing the
same logical run are paired regardless of token order or non-semantic
bookkeeping tokens — without over-matching or regressing the pairs that already
match.

## Root cause recap (what we're fixing)
Matching compares logical-key *strings* (separator permutations + `groups=`
stripping) but never canonicalizes **token order**. Proven on the OLMo MMLU
case: after `groups=` stripping the two keys are the *same token set in a
different order* —

- local: `mmlu:subject=abstract_algebra,method=multiple_choice_joint,eval_split=test,model=allenai_olmo-1.7-7b`
- official: `mmlu:subject=abstract_algebra,method=multiple_choice_joint,model=allenai_olmo-1.7-7b,eval_split=test,groups=mmlu_abstract_algebra`

→ empty intersection → `missing_official_component` for all 114 MMLU runs
(57 `allenai/olmo-1.7-7b` + 57 full-sweep `allenai/olmo-7b`), even though the
public counterparts exist in the official index.

## The three sites that must change (all currently order-sensitive)
1. **`_prefilter_index_rows`** ([core_report_planner.py:311-359](../../eval_audit/planning/core_report_planner.py#L311-L359))
   — narrows official rows via `_row_logical_keys(row) & wanted_keys` plus a
   `groups`-only fallback.
2. **`build_packet_intents` filter** ([core_report_planner.py:1039-1068](../../eval_audit/planning/core_report_planner.py#L1039-L1068))
   — keeps officials whose `_logical_key_variants` intersect the local variants.
3. **`build_packet_intents` grouping key** ([core_report_planner.py:1076-1089](../../eval_audit/planning/core_report_planner.py#L1076-L1089))
   — **the decisive site**: components are bucketed by
   `canonical_key = _strip_groups_token(raw_key)`, still a raw (order-sensitive)
   string. This is what actually decides whether an official and a local land in
   the same packet.

Fixing only the prefilter would not help — the grouping key is the determinant.

## Design: one canonical key, reused everywhere
Add a single normalizer in [`eval_audit/helm/run_entries.py`](../../eval_audit/helm/run_entries.py)
(so the planner *and* `compare_batch` share it, unifying the two matchers that
have drifted apart):

```python
# eval_audit/helm/run_entries.py
BOOKKEEPING_TOKENS = ("groups", "model_deployment")  # non-semantic for comparison

def canonical_logical_key(key: str | None, *, drop_tokens=BOOKKEEPING_TOKENS) -> str | None:
    """Order-insensitive canonical form of a HELM logical run key.

    parse -> drop bookkeeping-only tokens -> canonicalize_kv (model '/'<->'_',
    mmlu_pro subject->subset) -> re-serialize with kv SORTED by key.
    Token order and groups=/model_deployment= no longer affect equality;
    semantic tokens (eval_split, method, subject, model, ...) are preserved.
    """
    if not key:
        return key
    bench, kv = parse_run_name_to_kv(key)
    if not bench:
        return key
    kv = {k: v for k, v in kv.items() if k not in drop_tokens}
    kv = canonicalize_kv(kv, benchmark=bench)
    return format_run_name_from_kv(bench, {k: kv[k] for k in sorted(kv)})
```

Both OLMo keys collapse to
`mmlu:eval_split=test,method=multiple_choice_joint,model=allenai_olmo-1.7-7b,subject=abstract_algebra`
→ equal. Crucially it does **not** drop `eval_split`, so the lite
(no `eval_split`) and full-sweep (`eval_split=test`) recipes stay distinct — no
false merge.

This is a **symmetric equivalence** (the right tool for grouping), unlike
`run_dir_matches_requested`'s subset test (the right tool for "does this
official satisfy this request"). Keep the subset matcher available as a
documented fallback for asymmetric cases, but grouping uses the canonical key.

## Supersedes the prior `groups=` stripping change
`canonical_logical_key` drops `groups=` (it is in `BOOKKEEPING_TOKENS`) **and**
sorts tokens, so it strictly supersedes the earlier, order-blind groups-stripping
work. That change is no longer *required* — it becomes dead code this plan
removes, not logic kept alongside the new key (leaving both would re-create two
competing normalizers, the exact divergence we are ending). Retire **all** of:

| Existing groups-stripping site | Disposition under canonical key |
|---|---|
| `_logical_key_variants` `GROUP_STRIP` branch ([:209+](../../eval_audit/planning/core_report_planner.py#L209)) | Redundant — canonical variant already drops `groups=` |
| `_prefilter_index_rows` `GROUP_STRIP` fallback ([:335-359](../../eval_audit/planning/core_report_planner.py#L335-L359)) | Redundant — canonical-form intersection covers it |
| `build_packet_intents` grouping `canonical_key = _strip_groups_token(...)` ([:1081-1089](../../eval_audit/planning/core_report_planner.py#L1081-L1089)) | **Replaced** by `canonical_logical_key` |
| module-level + nested `_strip_groups_token` ([:257](../../eval_audit/planning/core_report_planner.py#L257), [:336](../../eval_audit/planning/core_report_planner.py#L336)) | Removable once the above are migrated |

Two things to **migrate, not delete**:

1. **The user-facing diagnostic.** Groups-stripping drives the
   `canonicalization_stripped_groups:original_keys=...` warning attached to
   affected comparisons + `packet_warnings`
   ([:975-983](../../eval_audit/planning/core_report_planner.py#L975-L983)).
   Generalize it (e.g. `keys_canonicalized:original_keys=...`) driven by
   `canonical_logical_key(raw) != raw` and the raw keys that merged into one
   canonical group — this preserves *and broadens* the signal (now also reports
   order/separator normalization, not just `groups=`).
2. **The `EVAL_AUDIT_GROUP_STRIP` env flag.** Its only purpose was to opt into
   groups-stripping for matching — fully covered now. See the decision point
   below; recommended path deprecates it to a no-op, which also removes the
   footgun where a runbook forgetting to set `GROUP_STRIP=1` silently
   reintroduces the order/`groups` mismatch.

Safety check: `groups=mmlu_<subject>` is HELM's leaderboard-aggregation grouping
metadata, not part of run identity — the existing code already treats it as
non-essential, so dropping it in the canonical key loses no comparison-relevant
information.

## File-by-file changes

**`eval_audit/helm/run_entries.py`**
- Add `BOOKKEEPING_TOKENS` + `canonical_logical_key()` (above). Unit-test in
  isolation. Reuses the existing `parse_run_name_to_kv` / `canonicalize_kv` /
  `format_run_name_from_kv` building blocks.

**`eval_audit/planning/core_report_planner.py`**
- `_logical_key_variants` ([:209](../../eval_audit/planning/core_report_planner.py#L209)):
  add `canonical_logical_key(key)` to the returned variant set (minimal-touch —
  the existing variant-intersection filter at site 2 then benefits for free).
- `_prefilter_index_rows` (site 1): intersect on canonical forms; keep the
  diagnostics dict, add a `canonicalized` count.
- `build_packet_intents` grouping (site 3): set
  `canonical_key = canonical_logical_key(raw_key) or raw_key`. This is the
  load-bearing change.
- Retire the now-dead `groups=` stripping paths and generalize its diagnostic —
  see [Supersedes the prior `groups=` stripping change](#supersedes-the-prior-groups-stripping-change)
  for the full list of sites and the migrate-not-delete items.

**Decision point — `EVAL_AUDIT_GROUP_STRIP`:** canonicalization subsumes
`groups=` stripping *and* adds order-insensitivity (see the section above).
Recommend making it **always-on** (order-sensitivity is never desirable) and
deprecating the flag to a no-op, after confirming no runbook relies on
`GROUP_STRIP=0`. Conservative fallback: keep canonicalization gated behind the
existing flag (the OLMo runbook already sets it = 1). Recommended path is
always-on; flagged for the owner's call.

**`eval_audit/workflows/compare_batch.py`** (stretch, recommended): route its
matching through the same `canonical_logical_key` so the two pipelines cannot
drift again.

## Testing
- **Characterization (write first, must fail before the fix):** in
  `tests/test_plan_core_report_packets.py`, add the exact OLMo-1.7 local+official
  key pair and assert they land in one packet with an enabled `official_vs_local`.
- **Unit:** `canonical_logical_key` — order-invariance, idempotence,
  `groups`/`model_deployment` dropped, `eval_split`/`subject`/`model` preserved,
  `mmlu_pro subject<->subset`, slash/underscore model forms.
- **Negative controls (guard against over-matching):** different `subject`,
  different `model`, and `eval_split=test` vs `eval_split=valid` must produce
  **distinct** keys.
- **Regression:** assert the currently-matching OLMo pairs still match (snapshot
  their canonical keys); run existing `tests/test_rebuild_core_report.py`,
  `tests/test_virtual_experiment.py`, `tests/test_compare_batch.py`.

## Validation on real data
1. Re-run `reproduce/olmo_models/30_compose.sh` on aiq-gpu; check
   `experiment_summary.json`: expect `n_skipped` 114 -> ~0 (only genuinely
   unmatched survive) and `n_built` 35 -> ~149.
2. **Diff matched pairs before/after** to prove no previously-built pair
   regressed (only additions).
3. Re-run the other manifests that enable an official source —
   `heatmap-paper-slim`, `open-helm-models-reproducibility`,
   `pythia-mmlu-stress` — and confirm match counts only increase, no new false
   merges.

## Risks & mitigations
| Risk | Mitigation |
|---|---|
| **False merges** (distinct runs collapse) | Drop only sanctioned bookkeeping tokens; keep benchmark prefix + all semantic kv; negative-control tests above. |
| **More officials per key** -> `multiple_official_candidates_after_latest_per_track` | Already handled by `_latest_official_selection` ([:843](../../eval_audit/planning/core_report_planner.py#L843)); verify the warning fires correctly rather than crashing. |
| **Performance** | Canonical key is O(1) hashable; grouping stays O(n). Avoid pairwise `run_dir_matches_requested` in the hot path. |
| **Determinism** (CLAUDE.md reproducibility guarantee) | Sorted kv -> stable, reproducible keys. |
| **`GROUP_STRIP` behavior change** | Audit runbooks for `GROUP_STRIP=0` reliance before deprecating; otherwise keep gated. |

## Rollout
1. Characterization + unit tests (red).
2. `canonical_logical_key` + wire the 3 sites + retire the dead `groups=`
   stripping paths and generalize its diagnostic (tests green).
3. Regression suite + negative controls.
4. Real-data validation on OLMo + the 3 official-enabled manifests; before/after
   pair diff.
5. (Stretch) migrate `compare_batch`; (docs) update the matching note in
   [docs/pipeline.md](../pipeline.md) and add a `dev/journals/claude.md` entry
   capturing why order-insensitive canonicalization replaced the variant approach.

## Out of scope
Model-alias reconciliation across genuinely different ids (handled here only via
`/`<->`_`); the `compare_batch` migration beyond the shared helper; any change to
tolerance-sweep / report rendering.

## Background / provenance
This plan is the outcome of diagnosing why the OLMo virtual-experiment report
left 114 of 149 packets as `rebuild_failed: No enabled comparisons were
available`. The investigation established that the public counterparts exist in
`official_public_index.csv`, pass the manifest scope, and are **not** dropped by
the Stage-1 pre_filter (which only writes a sankey inventory) — they die at the
planner's order-sensitive logical-key matching. See the matcher comparison in
[docs/pipeline.md](../pipeline.md) and the parallel (order-insensitive) matcher
already used by `compare_batch` and MAGNeT's `run_dir_matches_requested`.

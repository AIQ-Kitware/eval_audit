"""Report shaping for a single analyzed HELM run: the summary dict,
rich text summary, and plain-text renderer consumed by
:class:`eval_audit.helm.analysis.HelmRunAnalysis`.

Split out of ``eval_audit.helm.analysis`` on 2026-06-11
(Phase 2 of docs/historical/planning/repo-refactor-plan.md). Pure relocation:
function bodies are unchanged.
"""
from __future__ import annotations
from collections import Counter
from typing import Any
import ubelt as ub
from eval_audit.utils import hashers as helm_hashers
from eval_audit import metrics_taxonomy as helm_metrics


def summary_dict(
    self,
    *,
    level: int | str = 1,
    short_hash: int = 12,
    include_headline_instances: bool | None = None,
    drop_zero_count: bool = False,
) -> dict[str, Any]:
    """
    Programmatic single-run summary.

    This is intended to be:
      * easy to print for a human,
      * stable-ish enough to compare between runs,
      * informative enough to decide if you need HelmRunDiff.

    Parameters
    ----------
    level:
        Numeric detail level. Higher => more details.

        Suggested semantics (current implementation):
          - level <= 0: ultra-lite (one-line-friendly fields)
          - level >= 1: include run-level inventories (families by class/support)
          - level >= 10: include a few headline instances (truncated prompt/completion)

        Back-compat: 'lite' maps to 0. (The level >= 5 instance-level
        join inventories were retired 2026-07-12 — plan item A2; the
        ``instance_stats`` key stays in the output as ``None`` for shape
        stability. Per-instance comparison lives in
        :mod:`eval_audit.normalized`.)

    short_hash:
        Hash prefix length used in small signatures.

    include_headline_instances:
        If None: enabled when level >= 10.
        If True: include exemplar instance variants with truncated text.

    drop_zero_count:
        If True: ignore count==0 rows in certain inventories. (Default False because
        “unsupported” (count==0) is explicitly useful in this summary.)

    Returns
    -------
    dict
        Structured summary with run-level inventories.
    """
    # Backwards compatibility
    if isinstance(level, str):
        if level == 'lite':
            level = 0
        else:
            raise KeyError(level)

    if include_headline_instances is None:
        include_headline_instances = level >= 10

    cache_key = (
        'summary_dict_v5',
        level,
        short_hash,
        include_headline_instances,
        drop_zero_count,
    )
    if cache_key in self._cache:
        return self._cache[cache_key]

    def _short_sig(obj) -> str | None:
        if obj is None:
            return None
        try:
            return helm_hashers.stable_hash36(obj)[:short_hash]
        except Exception:
            return None

    def _family_support_inventory(rows: list[dict[str, Any]], *, name_getter):
        """
        Build family inventories grouped by:
            metric_class -> supported/unsupported -> family -> count
        """
        fam_counts: dict[str, dict[str, Counter]] = {}
        metric_counts: dict[str, dict[str, Counter]] = {}
        split_counts: dict[str, dict[str, Counter]] = {}

        for row in rows:
            c = int(row.get('count', 0) or 0)
            if drop_zero_count and c == 0:
                continue

            name_obj = name_getter(row)
            metric = (
                name_obj.get('name', None)
                if isinstance(name_obj, dict)
                else None
            )
            split = (
                name_obj.get('split', None)
                if isinstance(name_obj, dict)
                else None
            )

            mclass, _ = helm_metrics.classify_metric(metric)
            fam = helm_metrics.metric_family(metric)

            support = 'supported' if c > 0 else 'unsupported'
            fam_counts.setdefault(mclass, {}).setdefault(support, Counter())[
                fam
            ] += 1
            metric_counts.setdefault(mclass, {}).setdefault(support, Counter())[
                metric
            ] += 1
            split_counts.setdefault(mclass, {}).setdefault(support, Counter())[
                split
            ] += 1

        # Convert counters to stable sortable lists (count desc, then name)
        out = {}
        for mclass in sorted(fam_counts.keys()):
            out[mclass] = {}
            for support in ('supported', 'unsupported'):
                counter = fam_counts[mclass].get(support, Counter())
                out[mclass][support] = sorted(
                    counter.items(), key=lambda kv: (-kv[1], str(kv[0]))
                )
            # quick per-class totals
            out[mclass]['n_rows_supported'] = int(
                sum(fam_counts[mclass].get('supported', {}).values())
            )
            out[mclass]['n_rows_unsupported'] = int(
                sum(fam_counts[mclass].get('unsupported', {}).values())
            )
            # unique metric counts (per support)
            out[mclass]['n_unique_metrics_supported'] = int(
                sum(
                    1
                    for k, v in metric_counts[mclass]
                    .get('supported', {})
                    .items()
                    if k is not None
                )
            )
            out[mclass]['n_unique_metrics_unsupported'] = int(
                sum(
                    1
                    for k, v in metric_counts[mclass]
                    .get('unsupported', {})
                    .items()
                    if k is not None
                )
            )

        # Signature for “is the inventory basically the same?”
        try:
            sig_items = []
            for mclass, d in out.items():
                for support in ('supported', 'unsupported'):
                    for fam, cnt in d.get(support, []):
                        sig_items.append((mclass, support, fam, cnt))
            sig_items = sorted(sig_items)
            inv_sig = helm_hashers.stable_hash36(sig_items)[:short_hash]
        except Exception:
            inv_sig = None

        return out, inv_sig

    # --- core run identity ---
    spec = self.run_spec() or {}
    scen = self.scenario() or {}
    scen_state = self.scenario_state() or {}
    request_states = scen_state.get('request_states', []) or []
    stats = self.stats() or []

    label = self.name or str(self.run.path.name)

    # --- request_state base/variant inventory ---
    base_keys = []
    n_rs_pert = 0
    for rs in request_states:
        inst = rs.get('instance') or {}
        base_keys.append(
            (inst.get('id', None), rs.get('train_trial_index', None))
        )
        if inst.get('perturbation', None):
            n_rs_pert += 1
    base_counter = Counter(base_keys)
    n_bases = len(base_counter)
    n_variants = len(request_states)
    max_variants_per_base = max(base_counter.values()) if base_counter else 0

    # --- run-level stat inventories ---
    run_stats_total = len(stats)
    run_stats_nonzero = sum(
        1 for r in stats if int(r.get('count', 0) or 0) != 0
    )
    run_stats_with_mean = sum(
        1 for r in stats if (int(r.get('count', 0) or 0) != 0) and ('mean' in r)
    )
    run_stats_pert = sum(
        1
        for r in stats
        if isinstance(r.get('name', None), dict)
        and bool(r['name'].get('perturbation', None))
    )

    # signatures: run spec / scenario / stat-name-set
    stat_name_ids = []
    for r in stats:
        try:
            stat_name_ids.append(
                helm_hashers.stat_name_id(
                    r.get('name', None), count=r.get('count', None)
                )
            )
        except Exception:
            stat_name_ids.append(
                ub.urepr(r.get('name', None), compact=1, nl=0, nobr=1)
            )
    stat_name_ids = sorted(set(stat_name_ids))

    run_stats_fams, run_stats_fams_sig = _family_support_inventory(
        stats,
        name_getter=lambda r: (r.get('name', None) or {}),
    )

    # Instance-level join inventories retired (A2): the key survives as
    # None so downstream shape assumptions hold; per-instance comparison
    # is served by eval_audit.normalized.
    inst_info = None

    # --- headline instances (IDs + truncated text) ---
    headline = None
    if include_headline_instances:
        headline = []
        # deterministic-ish: group by base, pick 3 unperturbed then 3 perturbed
        unp = []
        per = []
        for rs in request_states:
            inst = rs.get('instance') or {}
            if inst.get('perturbation', None):
                per.append(rs)
            else:
                unp.append(rs)

        def _sortkey(rs):
            inst = rs.get('instance') or {}
            pert = inst.get('perturbation', None) or {}
            pname = pert.get('name', None) if isinstance(pert, dict) else None
            return (
                str(inst.get('id', '')),
                int(rs.get('train_trial_index', 0) or 0),
                str(pname),
            )

        unp = sorted(unp, key=_sortkey)
        per = sorted(per, key=_sortkey)

        # keep small
        pick = unp[:3] + per[:3]
        for rs in pick:
            inst = rs.get('instance') or {}
            pert = inst.get('perturbation', None)
            pert_name = (
                pert.get('name', None) if isinstance(pert, dict) else None
            )
            pert_id = None
            try:
                pert_id = helm_hashers.perturbation_id(
                    pert, short_hash=short_hash
                )
            except Exception:
                pert_id = None

            req = rs.get('request') or {}
            res = rs.get('result') or {}
            comps = res.get('completions') or []
            comp_text = comps[0].get('text', None) if comps else None

            headline.append(
                {
                    'instance_id': inst.get('id', None),
                    'train_trial_index': rs.get('train_trial_index', None),
                    'split': inst.get('split', None),
                    'perturbation': pert_name,
                    'perturbation_id': pert_id,
                    'prompt': req.get('prompt', None),
                    'completion': comp_text,
                    'input': (inst.get('input') or {}).get('text', None)
                    if isinstance(inst.get('input', None), dict)
                    else inst.get('input', None),
                }
            )

    out: dict[str, Any] = {
        'label': label,
        'path': str(self.run.path),
        'run_spec_name': spec.get('name', None),
        'scenario_name': (spec.get('scenario_spec', {}) or {}).get(
            'class_name', None
        ),
        'signatures': {
            'run_spec_sig': _short_sig(spec),
            'scenario_sig': _short_sig(scen) if scen else None,
            'stats_name_sig': _short_sig(stat_name_ids),
            'run_stats_families_sig': run_stats_fams_sig,
            'instance_stats_families_sig': (inst_info or {}).get(
                'inventory_sig', None
            )
            if isinstance(inst_info, dict)
            else None,
        },
        'requests': {
            'request_states': n_variants,
            'bases': n_bases,
            'perturbed_request_states': n_rs_pert,
            'max_variants_per_base': max_variants_per_base,
        },
        'run_stats': {
            'total': run_stats_total,
            'nonzero': run_stats_nonzero,
            'with_mean': run_stats_with_mean,
            'perturbed': run_stats_pert,
            'families_by_class': run_stats_fams,
        },
        'instance_stats': inst_info,
        'headline_instances': headline,
    }

    self._cache[cache_key] = out
    return out


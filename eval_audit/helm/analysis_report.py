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
from eval_audit.helm import metrics as helm_metrics


def summary_dict(
    self,
    *,
    level: int | str = 1,
    short_hash: int = 12,
    include_instance_stats: bool | None = None,
    include_headline_instances: bool | None = None,
    drop_zero_count: bool = False,
    assert_join_assumptions: bool = False,
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
          - level >= 5: include instance-level inventories (joined per-instance stats)
          - level >= 10: include a few headline instances (truncated prompt/completion)

        Back-compat: 'lite' maps to 0.

    short_hash:
        Hash prefix length used in small signatures.

    include_instance_stats:
        If None: enabled when level >= 5.
        If True: always attempt to build instance inventories via joined_instance_stat_table().

    include_headline_instances:
        If None: enabled when level >= 10.
        If True: include exemplar instance variants with truncated text.

    drop_zero_count:
        If True: ignore count==0 rows in certain inventories. (Default False because
        “unsupported” (count==0) is explicitly useful in this summary.)

    assert_join_assumptions:
        If True, call the joiner with assertions enabled (loud failures). Keep
        False by default for runtime.

    Returns
    -------
    dict
        Structured summary with run-level and (optionally) instance-level inventories.
    """
    # Backwards compatibility
    if isinstance(level, str):
        if level == 'lite':
            level = 0
        else:
            raise KeyError(level)

    if include_instance_stats is None:
        include_instance_stats = level >= 5
    if include_headline_instances is None:
        include_headline_instances = level >= 10

    cache_key = (
        'summary_dict_v4',
        level,
        short_hash,
        include_instance_stats,
        include_headline_instances,
        drop_zero_count,
        assert_join_assumptions,
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

    # --- instance-level inventories via join ---
    inst_info = None
    if include_instance_stats:
        try:
            joined = self.joined_instance_stat_table(
                assert_assumptions=assert_join_assumptions,
                short_hash=short_hash,
            )
        except Exception as ex:
            inst_info = {'error': repr(ex)}
        else:
            # joined rows are "one per stat", so variants/base stats are reconstructed from request_state in rows
            # But we can count variants more directly from scenario_state:
            # (still useful to report join-derived counts)
            inst_rows_total = len(joined)
            inst_rows_nonzero = sum(1 for r in joined if int(r.count or 0) != 0)
            inst_rows_with_mean = sum(
                1
                for r in joined
                if (int(r.count or 0) != 0) and (r.mean is not None)
            )

            # per-instance family inventory: use the underlying stat dicts
            inst_stats = [r.stat for r in joined]
            inst_fams, inst_fams_sig = _family_support_inventory(
                inst_stats,
                name_getter=lambda s: (s.get('name', None) or {}),
            )

            inst_info = {
                'rows_total': inst_rows_total,
                'rows_nonzero': inst_rows_nonzero,
                'rows_with_mean': inst_rows_with_mean,
                'families_by_class': inst_fams,
                'inventory_sig': inst_fams_sig,
            }

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


def summary(
    self,
    *,
    level: int = 1,
    writer=None,
    short_hash: int = 12,
    include_instance_stats: bool | None = None,
    include_headline_instances: bool | None = None,
    family_topn: int = 12,
    drop_zero_count: bool = False,
    assert_join_assumptions: bool = False,
    prompt_chars: int = 80,
    completion_chars: int = 80,
    input_chars: int = 80,
) -> None:
    """
    Write a human-readable single-run summary.

    Writer style
    ------------
    `writer` should be a callable that accepts a single string.
      * Default: `rich.print` (interactive friendly)
      * To accumulate: pass `lines.append`

    Level semantics
    --------------
    This function uses numeric levels rather than 'line'/'page'.

      level <= 0:
          single line intended for side-by-side display

      level >= 1:
          includes run-level inventories:
            - counts
            - metric families grouped by: (metric_class -> supported/unsupported -> family)

      level >= 5:
          includes instance-level inventories via joined_instance_stat_table()
          (often the most informative “is this the same run?” signal)

      level >= 10:
          prints headline instances (ids + truncated input/prompt/completion) so you can
          immediately look them up by id.

    Glossary / labels
    -----------------
    requests.request_states:
        Number of entries in scenario_state['request_states'] (includes perturbation variants).

    requests.bases:
        Number of unique (instance_id, train_trial_index) pairs found in request_states.

    requests.max_variants_per_base:
        Max number of request_states sharing the same base key. >1 is typical with perturbations.

    run_stats.families_by_class:
        For run-level stats.json, counts grouped by:
          metric_class ('core', 'bookkeeping', 'untracked', ...)
            -> support ('supported' if count>0 else 'unsupported' if count==0)
              -> family (heuristic prefix family via metric_family())

        This is often better than raw metric lists for “shape of the run”.

    instance_stats.*:
        Same structure, but computed from per-instance stats (joined to request_states).
    """
    if writer is None:
        try:
            from rich import print as rich_print

            writer = rich_print
        except Exception:
            writer = print

    # Heuristic: only show core at low levels; add bookkeeping at mid; untracked at high.
    if level < 5:
        classes_to_show = ('core',)
    elif level < 10:
        classes_to_show = ('core', 'bookkeeping')
    else:
        classes_to_show = ('core', 'bookkeeping', 'untracked')

    info = self.summary_dict(
        level=level,
        short_hash=short_hash,
        include_instance_stats=include_instance_stats,
        include_headline_instances=include_headline_instances,
        drop_zero_count=drop_zero_count,
        assert_join_assumptions=assert_join_assumptions,
    )

    label = info.get('label') or ub.Path(info.get('path')).name
    sig = info.get('signatures') or {}
    req = info.get('requests') or {}
    rs = info.get('run_stats') or {}
    inst = info.get('instance_stats', None)

    # --- truncation helpers ---
    try:
        from kwutil.slugify_ext import smart_truncate
    except Exception:
        smart_truncate = None

    is_rich = (getattr(writer, '__module__', '') or '').startswith('rich')
    if is_rich:
        try:
            from rich.markup import escape as _escape
        except Exception:
            _escape = None
    else:
        _escape = None

    def _clip(text, n):
        if text is None:
            return None
        text = str(text).replace('\r\n', '\n')
        # Using repr to make output more structured.
        text = repr(text)
        if smart_truncate is not None:
            out = smart_truncate(
                text, max_length=n, hash_len=8, separator=' ', trunc_loc=0.5
            )
        else:
            out = text[:n] + '…' if len(text) > n else text
        if _escape is not None:
            out = _escape(out)
        return out

    # --- level 0: one-liner ---
    if level <= 0:
        writer(
            f'{label} | spec={info.get("run_spec_name")} | '
            f'sig(spec)={sig.get("run_spec_sig")} | '
            f'sig(stats)={sig.get("stats_name_sig")} | '
            f'req={req.get("request_states")} bases={req.get("bases")} maxvar={req.get("max_variants_per_base")}'
        )
        return None

    # --- header ---
    writer(f'Run: {label}')
    writer(f'  path: {info.get("path")}')
    writer(f'  run_spec_name: {info.get("run_spec_name")}')
    writer(f'  scenario: {info.get("scenario_name")}')
    writer('  signatures:')
    writer(f'    run_spec_sig:   {sig.get("run_spec_sig")}')
    writer(f'    scenario_sig:   {sig.get("scenario_sig")}')
    writer(f'    stats_name_sig: {sig.get("stats_name_sig")}')

    # --- requests ---
    writer('  requests:')
    writer(
        f'    request_states={req.get("request_states")} '
        f'bases={req.get("bases")} perturbed={req.get("perturbed_request_states")} '
        f'max_variants_per_base={req.get("max_variants_per_base")}'
    )

    # --- run-level stats ---
    writer('  run-level stats (stats.json):')
    writer(
        f'    total={rs.get("total")} nonzero={rs.get("nonzero")} '
        f'with_mean={rs.get("with_mean")} perturbed={rs.get("perturbed")}'
    )

    fams_by_class = rs.get('families_by_class') or {}
    writer('    families_by_class (class -> supported/unsupported -> family):')
    for cls in classes_to_show:
        if cls not in fams_by_class:
            continue
        cinfo = fams_by_class.get(cls) or {}
        writer(
            f'      {cls}: supported_rows={cinfo.get("n_rows_supported")} unsupported_rows={cinfo.get("n_rows_unsupported")}'
        )
        supp = cinfo.get('supported', [])[:family_topn]
        unsupp = cinfo.get('unsupported', [])[:family_topn]
        if supp:
            writer('        supported:')
            for fam, cnt in supp:
                writer(f'          {fam}: {cnt}')
        if unsupp and level >= 10:
            writer('        unsupported:')
            for fam, cnt in unsupp:
                writer(f'          {fam}: {cnt}')

    # --- instance-level stats ---
    if inst is None:
        writer('  instance-level stats: (disabled)')
    elif isinstance(inst, dict) and inst.get('error'):
        writer(f'  instance-level stats: ERROR {inst.get("error")}')
    else:
        writer(
            '  instance-level stats (joined per_instance_stats + request_states):'
        )
        writer(
            f'    rows_total={inst.get("rows_total")} '
            f'rows_nonzero={inst.get("rows_nonzero")} rows_with_mean={inst.get("rows_with_mean")} '
            f'families_sig={sig.get("instance_stats_families_sig")}'
        )
        inst_fams = inst.get('families_by_class') or {}
        writer(
            '    families_by_class (class -> supported/unsupported -> family):'
        )
        for cls in classes_to_show:
            if cls not in inst_fams:
                continue
            cinfo = inst_fams.get(cls) or {}
            writer(
                f'      {cls}: supported_rows={cinfo.get("n_rows_supported")} unsupported_rows={cinfo.get("n_rows_unsupported")}'
            )
            supp = cinfo.get('supported', [])[:family_topn]
            unsupp = cinfo.get('unsupported', [])[:family_topn]
            if supp:
                writer('        supported:')
                for fam, cnt in supp:
                    writer(f'          {fam}: {cnt}')
            if unsupp and level >= 10:
                writer('        unsupported:')
                for fam, cnt in unsupp:
                    writer(f'          {fam}: {cnt}')

    # --- headline instances (high level) ---
    headline = info.get('headline_instances', None)
    if headline and level >= 10:
        writer('  headline instances (lookup by instance_id):')
        for h in headline:
            iid = h.get('instance_id')
            tti = h.get('train_trial_index')
            split = h.get('split')
            pert = h.get('perturbation')
            pid = h.get('perturbation_id')

            writer(
                f'    - id={iid} tti={tti} split={split} pert={pert} pid={pid}'
            )
            inp = _clip(h.get('input'), input_chars)
            prm = _clip(h.get('prompt'), prompt_chars)
            cmp = _clip(h.get('completion'), completion_chars)
            if inp is not None:
                writer(f'      input: {inp}')
            if prm is not None:
                writer(f'      prompt: {prm}')
            if cmp is not None:
                writer(f'      completion: {cmp}')

    return None


def summary_text(self, *, level: int = 1, **kwargs) -> str:
    """
    Backwards-compatible string-returning helper.
    """
    lines: list[str] = []
    self.summary(level=level, writer=lines.append, **kwargs)
    return '\n'.join(lines)

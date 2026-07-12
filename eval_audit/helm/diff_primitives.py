"""Diff computation primitives: walkers, truncation/formatting, semantic
canonicalization, Coverage, and dataset-overlap computation. The
report-shaping consumer is :class:`eval_audit.helm.diff.HelmRunDiff`.

Split out of ``eval_audit.helm.diff`` on 2026-06-11
(Phase 2 of docs/historical/planning/repo-refactor-plan.md). Pure relocation:
function bodies are unchanged.
"""
from __future__ import annotations
import ubelt as ub
from collections import Counter
from dataclasses import dataclass
from eval_audit.utils import hashers as helm_hashers
from typing import Any, Callable, Iterable


def _format_bool(ok: bool) -> str:
    return '✅' if ok else '❌'


def _walker_diff(a: Any, b: Any, *, max_paths: int = 12) -> dict[str, Any]:
    """

    Return a dict with formatted lines for:
      - unique1: paths only in a
      - unique2: paths only in b
      - faillist: differing values at same path

    Each list is independently truncated to `max_paths`, with a final
    "<N more not shown>" line if needed.

    Example:
        >>> a = {'foo': {'bar': [1], 'baz': 1}}
        >>> b = {'foo': {'bar': [2], 'biz': 2}}
        >>> _walker_diff(a, b)

        >>> a = {
        >>>     "shared": {"same": 0, "chg": 1, "deep": {"x": 1}},
        >>>     "only_a_top": True,
        >>>     "only_a": {"k0": 0, "k1": 1, "k2": 2},
        >>>     "arr": [0, 1],
        >>> }
        >>> b = {
        >>>     "shared": {"same": 0, "chg": 2, "deep": {"x": 9, "y": 10}},
        >>>     "only_b_top": True,
        >>>     "only_b": {"j0": 0, "j1": 1, "j2": 2},
        >>>     "arr": [0, 2, 3],
        >>> }
        >>> _walker_diff(a, b)
    """
    walker_a = ub.IndexableWalker(a)
    walker_b = ub.IndexableWalker(b)
    info = walker_a.diff(walker_b)
    info.pop('passlist', None)

    def _format_path(path: Iterable[Any]) -> str:
        return '.'.join(map(str, path))

    def _truncate(lines: list[str], max_items: int) -> list[str]:
        """
        If truncation happens, append ONE final line: "<N more not shown>"
        where N is the correct remainder.
        """
        if max_items is None or max_items <= 0:
            return lines
        n = len(lines)
        if n <= max_items:
            return lines
        remain = n - max_items
        return lines[:max_items] + [f'<{remain} more not shown>']

    unique1 = sorted(info.get('unique1', []))
    unique2 = sorted(info.get('unique2', []))
    faillist = sorted(info.get('faillist', []), key=lambda d: d.path)

    out = info | {
        'unique1': _truncate(
            [
                _format_path(p) + ': ' + _smart_truncate(repr(walker_a[p]), 80)
                for p in unique1
            ],
            max_paths,
        ),
        'unique2': _truncate(
            [
                _format_path(p) + ': ' + _smart_truncate(repr(walker_b[p]), 80)
                for p in unique2
            ],
            max_paths,
        ),
        'faillist': _truncate(
            [
                f'{_format_path(d.path)}: {_smart_truncate(repr(d.value1), 80)} != {_smart_truncate(repr(d.value2), 80)}'
                for d in faillist
            ],
            max_paths,
        ),
    }
    return out


def _walker_diff_paths(a: Any, b: Any) -> dict[str, list[str]]:
    """Return full path-level differences (untruncated), path-only.

    The output is intentionally JSON-friendly and stable for diagnostics.
    """
    walker_a = ub.IndexableWalker(a)
    walker_b = ub.IndexableWalker(b)
    info = walker_a.diff(walker_b)

    def _format_path(path: Iterable[Any]) -> str:
        return '.'.join(map(str, path))

    unique1 = sorted(_format_path(p) for p in info.get('unique1', []))
    unique2 = sorted(_format_path(p) for p in info.get('unique2', []))
    faillist = sorted(
        _format_path(d.path) for d in info.get('faillist', [])
    )
    return {
        'unique1': unique1,
        'unique2': unique2,
        'faillist': faillist,
    }


def _default_writer(writer=None) -> Callable[[str], Any]:
    if writer is not None:
        return writer
    try:
        from rich import print as rich_print  # type: ignore
    except Exception:  # nocover
        return print
    else:
        return rich_print


def _escape_rich(text: str) -> str:
    """Escape rich markup (mainly brackets) without losing readability."""
    try:
        from rich.markup import escape  # type: ignore
    except Exception:  # nocover
        return text
    else:
        return escape(text)


def _sanitize_text(text: Any) -> str:
    if text is None:
        return ''
    s = str(text)
    # Drop most control chars except newlines/tabs.
    s = ''.join(
        (ch if (ch == '\n' or ch == '\t' or ord(ch) >= 32) else ' ') for ch in s
    )
    return s


def _smart_truncate(text: Any, max_chars: int) -> str:
    """Truncate long prompts/completions with a stable hash tail."""
    s = _sanitize_text(text)
    if max_chars <= 0:
        return _escape_rich(s)
    try:
        from kwutil.slugify_ext import smart_truncate  # type: ignore
    except Exception:  # nocover
        # fallback: hard truncate
        s2 = (s[:max_chars] + '…') if len(s) > max_chars else s
        return _escape_rich(s2)
    else:
        s2 = smart_truncate(
            s,
            max_length=max_chars,
            trunc_loc=0.5,
            hash_len=8,
            head='~',
            tail='~',
        )
        return _escape_rich(s2)


def _short_urepr(obj: Any, max_chars: int = 140) -> str:
    """Compact repr for diffs; keeps it readable and bounded."""
    try:
        s = ub.urepr(obj, nl=0, sv=1)
    except Exception:
        s = repr(obj)
    return _smart_truncate(s, max_chars)


def _coerce_path_token(tok: str) -> str | int:
    if tok.isdigit():
        try:
            return int(tok)
        except Exception:
            return tok
    return tok


def _path_get(obj: Any, path: str) -> tuple[Any, bool]:
    """Best-effort dotted-path getter supporting dict/list traversal."""
    cur = obj
    for raw_tok in path.split('.'):
        tok = _coerce_path_token(raw_tok)
        if isinstance(cur, dict):
            if tok in cur:
                cur = cur[tok]
            elif isinstance(tok, int) and str(tok) in cur:
                cur = cur[str(tok)]
            else:
                return None, False
        elif isinstance(cur, (list, tuple)):
            if isinstance(tok, int) and 0 <= tok < len(cur):
                cur = cur[tok]
            else:
                return None, False
        else:
            return None, False
    return cur, True


def _path_value_examples(
    a_obj: Any,
    b_obj: Any,
    paths: list[str],
    *,
    max_items: int = 20,
) -> list[dict[str, Any]]:
    """Return path-level value pairs for selected diff paths."""
    examples: list[dict[str, Any]] = []
    for p in sorted(paths):
        rec: dict[str, Any] = {'path': p}
        va, oka = _path_get(a_obj, p)
        vb, okb = _path_get(b_obj, p)
        rec['a'] = va if oka else None
        rec['b'] = vb if okb else None
        rec['a_found'] = bool(oka)
        rec['b_found'] = bool(okb)
        examples.append(rec)
        if len(examples) >= max_items:
            break
    return _json_compatible(examples)


# A3: one shared implementation (utils.jsonify) replaces the private copy
# here and the near-twin in normalized.diagnose. The shared version carries
# the IM-12 determinism fix (sets serialized in sorted order) that this
# module's old copy lacked.
from eval_audit.utils.jsonify import json_compatible as _json_compatible  # noqa: E402


def _preview_list(items: list[str], *, limit: int = 20) -> list[str]:
    """Return a stable preview list with an optional '<N more>' suffix."""
    if limit <= 0 or len(items) <= limit:
        return items
    remain = len(items) - limit
    return items[:limit] + [f'<{remain} more not shown>']


_RUNSPEC_EXEC_ADAPTER_NOISE_FIELDS = {
    # Added in newer HELM formats; often default/no-op in practice.
    'chain_of_thought_prefix',
    'chain_of_thought_suffix',
    'global_suffix',
    'num_trials',
}


def _classify_run_spec_path(path: str) -> str:
    """Classify run-spec diff paths into semantic buckets."""
    if path.startswith('metric_specs') or path.startswith('groups'):
        return 'evaluation'
    if path.startswith('adapter_spec.'):
        parts = path.split('.')
        field = parts[1] if len(parts) > 1 else ''
        if field in _RUNSPEC_EXEC_ADAPTER_NOISE_FIELDS:
            return 'nonsemantic'
        return 'execution'
    if path.startswith('scenario_spec') or path.startswith('data_augmenter_spec'):
        return 'execution'
    if path in {'name'}:
        return 'nonsemantic'
    return 'other'


def _classify_scenario_path(path: str) -> str:
    """Classify scenario diff paths into semantic buckets."""
    # scenario.output_path is environment-local and should not affect content
    if path == 'output_path' or path.endswith('.output_path'):
        return 'nonsemantic'
    return 'semantic'


def _canonicalize_metric_spec_for_semantic_diff(metric_spec: Any) -> Any:
    """Normalize one metric spec for order-insensitive semantic comparison."""
    if not isinstance(metric_spec, dict):
        return helm_hashers.canonicalize_for_hashing(metric_spec)
    out = {
        'class_name': metric_spec.get('class_name', None),
        'args': helm_hashers.canonicalize_for_hashing(
            metric_spec.get('args', None)
        ),
    }
    return out


def _canonicalize_run_spec_for_semantic_diff(run_spec: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize run_spec with order-insensitive handling for select lists."""
    spec = helm_hashers.canonicalize_for_hashing(run_spec)
    if not isinstance(spec, dict):
        return {'_invalid_spec': spec}
    spec = dict(spec)

    metric_specs = spec.get('metric_specs', None)
    if isinstance(metric_specs, list):
        canon_items = [
            _canonicalize_metric_spec_for_semantic_diff(ms)
            for ms in metric_specs
        ]
        canon_items = sorted(
            canon_items, key=lambda x: helm_hashers.stable_hash36(x)
        )
        spec['metric_specs'] = canon_items

    groups = spec.get('groups', None)
    if isinstance(groups, list):
        spec['groups'] = sorted(groups, key=lambda x: str(x))

    return spec


def _metric_specs_multiset_delta(
    metric_specs_a: Any,
    metric_specs_b: Any,
    *,
    short_hash: int = 12,
    max_items: int = 20,
) -> dict[str, Any]:
    """Order-insensitive multiset delta for run_spec.metric_specs."""
    specs_a = metric_specs_a if isinstance(metric_specs_a, list) else []
    specs_b = metric_specs_b if isinstance(metric_specs_b, list) else []

    def _make_id(ms: Any) -> tuple[str, dict[str, Any]]:
        canon = _canonicalize_metric_spec_for_semantic_diff(ms)
        sid = helm_hashers.stable_hash36(canon)[:short_hash]
        if isinstance(canon, dict):
            class_name = canon.get('class_name', None)
            args = canon.get('args', None)
        else:
            class_name = None
            args = canon
        rec = {
            'id': sid,
            'class_name': class_name,
            'args': args,
            'preview': _short_urepr(canon, max_chars=160),
        }
        return sid, rec

    id_to_rec: dict[str, dict[str, Any]] = {}
    a_ids: list[str] = []
    b_ids: list[str] = []
    for ms in specs_a:
        sid, rec = _make_id(ms)
        id_to_rec.setdefault(sid, rec)
        a_ids.append(sid)
    for ms in specs_b:
        sid, rec = _make_id(ms)
        id_to_rec.setdefault(sid, rec)
        b_ids.append(sid)

    a_counter = Counter(a_ids)
    b_counter = Counter(b_ids)
    keys = sorted(set(a_counter) | set(b_counter))
    added = []
    removed = []
    for sid in keys:
        ca = a_counter.get(sid, 0)
        cb = b_counter.get(sid, 0)
        if cb > ca:
            added.append(id_to_rec[sid] | {'count': cb - ca})
        if ca > cb:
            removed.append(id_to_rec[sid] | {'count': ca - cb})

    added = sorted(added, key=lambda r: (str(r.get('class_name')), r['id']))
    removed = sorted(removed, key=lambda r: (str(r.get('class_name')), r['id']))
    return _json_compatible(
        {
            'n_a': len(specs_a),
            'n_b': len(specs_b),
            'n_added': sum(r['count'] for r in added),
            'n_removed': sum(r['count'] for r in removed),
            'added': _preview_list(
                [ub.urepr(r, nl=0, compact=1) for r in added], limit=max_items
            ),
            'removed': _preview_list(
                [ub.urepr(r, nl=0, compact=1) for r in removed], limit=max_items
            ),
            'added_structured': added[:max_items],
            'removed_structured': removed[:max_items],
            'equal_as_multiset': (len(added) == 0 and len(removed) == 0),
        }
    )


@dataclass(frozen=True)
class Coverage:
    """Coverage bookkeeping for two key-sets."""

    n_a: int
    n_b: int
    n_isect: int
    n_union: int
    only_a: int
    only_b: int

    @classmethod
    def from_sets(cls, a: set[Any], b: set[Any]) -> 'Coverage':
        isect = a & b
        union = a | b
        return cls(
            n_a=len(a),
            n_b=len(b),
            n_isect=len(isect),
            n_union=len(union),
            only_a=len(a - b),
            only_b=len(b - a),
        )


def _fmt(x: Any) -> str:
    if x is None:
        return 'None'
    if isinstance(x, float):
        return f'{x:.4g}'
    return str(x)


def _key_to_serializable(key: Any) -> Any:
    """Convert various key types (dataclasses, tuples) into JSON-friendly types.

    - If object has ``as_tuple()``, use that and return a list.
    - If it's a tuple, return a list (JSON will accept either but list is explicit).
    - Otherwise fallback to string repr.
    """
    # dataclass-like keys (InstanceStatKey, InstanceVariantKey) implement as_tuple
    try:
        if hasattr(key, 'as_tuple') and callable(getattr(key, 'as_tuple')):
            return list(key.as_tuple())
    except Exception:
        pass
    if isinstance(key, tuple):
        return list(key)
    # lists are already JSON-safe
    if isinstance(key, list):
        return key
    # fallback: use a stable repr
    try:
        return ub.urepr(key, nl=0, compact=1)
    except Exception:
        return str(key)


def dataset_overlap_from_request_states(
    request_states_a: list[dict[str, Any]],
    request_states_b: list[dict[str, Any]],
    *,
    short_hash: int = 16,
    max_examples: int = 5,
) -> dict[str, Any]:
    """Compare two request_state lists at dataset/prompt/completion level.

    This is a pure function used by :meth:`HelmRunDiff.dataset_overlap_summary`.

    Example:
        >>> rs_a = [
        ...     {
        ...         'instance': {'id': 'id1', 'split': 'test', 'input': {'text': 'Q1'}},
        ...         'train_trial_index': 0,
        ...         'request': {'prompt': 'P1'},
        ...         'result': {'completions': [{'text': 'A1'}]},
        ...     },
        ...     {
        ...         'instance': {
        ...             'id': 'id1', 'split': 'test', 'input': {'text': 'Q1'},
        ...             'perturbation': {'name': 'dialect', 'prob': 1.0},
        ...         },
        ...         'train_trial_index': 0,
        ...         'request': {'prompt': 'P1-d'},
        ...         'result': {'completions': [{'text': 'A1d'}]},
        ...     },
        ... ]
        >>> rs_b = [
        ...     {
        ...         'instance': {'id': 'id1', 'split': 'test', 'input': {'text': 'Q1'}},
        ...         'train_trial_index': 0,
        ...         'request': {'prompt': 'P1x'},
        ...         'result': {'completions': [{'text': 'A1'}]},
        ...     },
        ... ]
        >>> info = dataset_overlap_from_request_states(rs_a, rs_b, max_examples=2)
        >>> assert info['base_coverage']['n_isect'] == 1
        >>> assert info['variant_coverage']['only_a'] == 1
        >>> assert info['content_equality']['prompt']['equal_ratio'] == 0.0
        >>> assert isinstance(info['mismatch_examples']['prompt'], list)
    """

    def _coerce_int(x: Any) -> int | None:
        try:
            if x is None:
                return None
            if isinstance(x, bool):
                return int(x)
            if isinstance(x, int):
                return x
            if isinstance(x, float) and x.is_integer():
                return int(x)
            if isinstance(x, str) and x.isdigit():
                return int(x)
        except Exception:
            pass
        return None

    def _base_key(rs: dict[str, Any]) -> tuple[Any, ...]:
        inst = rs.get('instance') or {}
        return (
            inst.get('id', None),
            _coerce_int(rs.get('train_trial_index', None)),
            inst.get('split', None),
        )

    def _variant_key(rs: dict[str, Any]) -> tuple[Any, ...]:
        inst = rs.get('instance') or {}
        pid = helm_hashers.perturbation_id(
            inst.get('perturbation', None), short_hash=short_hash
        )
        return _base_key(rs) + (pid,)

    def _index_unique(
        rows: list[dict[str, Any]], key_fn
    ) -> tuple[dict[tuple[Any, ...], dict[str, Any]], int]:
        out: dict[tuple[Any, ...], dict[str, Any]] = {}
        duplicates = 0
        for rs in rows:
            k = key_fn(rs)
            if k in out:
                duplicates += 1
                continue
            out[k] = rs
        return out, duplicates

    def _extract_input(rs: dict[str, Any]) -> Any:
        inst = rs.get('instance') or {}
        inp = inst.get('input', None)
        if isinstance(inp, dict) and 'text' in inp:
            return inp.get('text', None)
        return inp

    def _extract_prompt(rs: dict[str, Any]) -> Any:
        req = rs.get('request') or {}
        return req.get('prompt', None)

    def _extract_completion(rs: dict[str, Any]) -> Any:
        res = rs.get('result') or {}
        comps = res.get('completions') or []
        if not comps:
            return None
        first = comps[0]
        if isinstance(first, dict):
            return first.get('text', None)
        return first

    def _summarize(
        map_a: dict[tuple[Any, ...], dict[str, Any]],
        map_b: dict[tuple[Any, ...], dict[str, Any]],
        keys: set[tuple[Any, ...]],
        *,
        extractor,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        def _keysort(k: tuple[Any, ...]) -> str:
            try:
                return ub.urepr(k, nl=0, compact=1)
            except Exception:
                return str(k)

        comparable = 0
        mismatched = 0
        examples: list[dict[str, Any]] = []
        for k in sorted(keys, key=_keysort):
            va = extractor(map_a[k])
            vb = extractor(map_b[k])
            comparable += 1
            if va != vb:
                mismatched += 1
                if len(examples) < max_examples:
                    examples.append(
                        {
                            'key': _key_to_serializable(k),
                            'a': _short_urepr(va, max_chars=180),
                            'b': _short_urepr(vb, max_chars=180),
                        }
                    )
        return (
            {
                'comparable': comparable,
                'mismatched': mismatched,
                'equal_ratio': ratio(comparable, mismatched),
            },
            examples,
        )

    base_a, dup_base_a = _index_unique(request_states_a, _base_key)
    base_b, dup_base_b = _index_unique(request_states_b, _base_key)
    var_a, dup_var_a = _index_unique(request_states_a, _variant_key)
    var_b, dup_var_b = _index_unique(request_states_b, _variant_key)

    cov_base = Coverage.from_sets(set(base_a), set(base_b))
    cov_var = Coverage.from_sets(set(var_a), set(var_b))
    isect_base = set(base_a) & set(base_b)
    isect_var = set(var_a) & set(var_b)

    base_iou = (
        cov_base.n_isect / cov_base.n_union if cov_base.n_union else None
    )
    variant_iou = (
        cov_var.n_isect / cov_var.n_union if cov_var.n_union else None
    )

    input_eq, ex_input = _summarize(
        base_a, base_b, isect_base, extractor=_extract_input
    )
    prompt_eq, ex_prompt = _summarize(
        var_a, var_b, isect_var, extractor=_extract_prompt
    )
    completion_eq, ex_completion = _summarize(
        var_a, var_b, isect_var, extractor=_extract_completion
    )

    out = {
        'base_coverage': cov_base.__dict__,
        'variant_coverage': cov_var.__dict__,
        'base_iou': base_iou,
        'variant_iou': variant_iou,
        'content_equality': {
            'input': input_eq,
            'prompt': prompt_eq,
            'completion': completion_eq,
        },
        'duplicates': {
            'a': {'base': dup_base_a, 'variant': dup_var_a},
            'b': {'base': dup_base_b, 'variant': dup_var_b},
        },
        'mismatch_examples': {
            'input': ex_input,
            'prompt': ex_prompt,
            'completion': ex_completion,
        },
    }
    return _json_compatible(out)


def ratio(c: int, m: int) -> float | None:
    return (1.0 - (m / c)) if c else None

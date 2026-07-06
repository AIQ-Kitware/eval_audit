"""Instance-level stat join layer: keys, rows, and the joined table
used by :class:`eval_audit.helm.analysis.HelmRunAnalysis`.

Split out of ``eval_audit.helm.analysis`` on 2026-06-11
(Phase 2 of docs/planning/repo-refactor-plan.md). Pure relocation:
function bodies are unchanged.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Mapping
import ubelt as ub
from eval_audit.utils import hashers as helm_hashers
from eval_audit.utils.numeric import safe_float as _safe_float


@dataclass(frozen=True)
class StatMeta:
    """A compact, normalized view of a HELM stat row."""

    key: str
    metric: str | None
    split: str | None
    is_perturbed: bool
    pert_id: str | None
    family: str
    metric_class: str
    matched_prefix: str | None
    count: int
    mean: float | None
    name_obj: Any
    raw: Mapping[str, Any]


def _coerce_int(x: Any) -> int | None:
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
    return None


def _nice_perturbation_id(pert: Any, *, short_hash: int = 12) -> str | None:
    """
    Conservative “nice” perturbation id:
    - None if pert is falsy / not a dict
    - prefix = pert['name'] if present
    - suffix = stable hash of canonicalized dict
    """
    if not isinstance(pert, dict) or not pert:
        return None
    name = pert.get('name', 'pert')
    # Strip known unstable payloads if present (optional and conservative)
    canon = ub.udict(pert).copy()
    canon.pop('mapping_file_path', None)
    canon.pop('name_file_path', None)
    h = helm_hashers.stable_hash36(canon)[:short_hash]
    return f'{name}~{h}'


@dataclass(frozen=True, slots=True)
class InstanceVariantKey:
    """Identifies a specific evaluated variant of an instance."""

    instance_id: str | None
    train_trial_index: int | None
    perturbation_id: str | None

    @property
    def is_perturbed(self) -> bool:
        return self.perturbation_id is not None

    def as_tuple(self) -> tuple[Any, ...]:
        return (self.instance_id, self.train_trial_index, self.perturbation_id)


@dataclass(frozen=True, slots=True)
class InstanceStatKey:
    """Identifies a single metric row for a specific instance variant."""

    variant: InstanceVariantKey
    metric: str | None
    split: str | None
    sub_split: str | None
    stat_perturbation_id: str | None

    @property
    def is_perturbed(self) -> bool:
        """True if this row is a perturbed variant.

        The perturbation may be instance-level (``variant.perturbation_id``)
        or stat-level (``stat_perturbation_id``). Consumers must not check a
        bare ``perturbation_id`` attribute — it does not exist on this key
        (P0-6).
        """
        return (
            self.variant.perturbation_id is not None
            or self.stat_perturbation_id is not None
        )

    def as_tuple(self) -> tuple[Any, ...]:
        # legacy 7-tuple format
        return (
            self.variant.instance_id,
            self.variant.train_trial_index,
            self.variant.perturbation_id,
            self.metric,
            self.split,
            self.sub_split,
            self.stat_perturbation_id,
        )


@dataclass(frozen=True, slots=True)
class InstanceStatRow:
    """A joined per-instance stat row with attached request_state."""

    key: InstanceStatKey
    stat: dict[str, Any]
    request_state: dict[str, Any] | None

    @property
    def mean(self) -> float | None:
        return _safe_float(self.stat.get('mean', None))

    @property
    def count(self) -> int:
        return int(self.stat.get('count', 0) or 0)


class JoinedInstanceStatTable(ub.NiceRepr):
    """
    Join `scenario_state['request_states']` with `per_instance_stats`.

    This class is *pure*: it accepts JSON structures and builds indices.

    Example:
        >>> from eval_audit.helm.analysis import (
        ...     JoinedInstanceStatTable, InstanceVariantKey
        ... )
        >>> request_states = [
        ...     {
        ...         'instance': {'id': 'id1', 'split': 'test', 'input': {'text': 'hello'}},
        ...         'train_trial_index': 0,
        ...         'request': {'prompt': 'P0'},
        ...         'result': {'completions': [{'text': 'A'}]},
        ...     },
        ...     {
        ...         'instance': {
        ...             'id': 'id1', 'split': 'test', 'input': {'text': 'hello'},
        ...             'perturbation': {'name': 'dialect', 'prob': 1.0},
        ...         },
        ...         'train_trial_index': 0,
        ...         'request': {'prompt': 'P0 dialect'},
        ...         'result': {'completions': [{'text': 'B'}]},
        ...     },
        ... ]
        >>> perinstance_stats = [
        ...     # base stats split across two bundles
        ...     {'instance_id': 'id1', 'train_trial_index': 0,
        ...      'stats': [{'name': {'name': 'num_bytes', 'split': 'test'}, 'count': 1, 'mean': 10.0}]},
        ...     {'instance_id': 'id1', 'train_trial_index': 0,
        ...      'stats': [{'name': {'name': 'num_prompt_tokens', 'split': 'test'}, 'count': 1, 'mean': 3.0}]},
        ...     # perturbed stats: perturbation appears in the stat-name dict
        ...     {'instance_id': 'id1', 'train_trial_index': 0,
        ...      'stats': [{'name': {'name': 'num_bytes', 'split': 'test',
        ...                         'perturbation': {'name': 'dialect', 'prob': 1.0}},
        ...                'count': 1, 'mean': 12.0}]},
        ... ]
        >>> tbl = JoinedInstanceStatTable(request_states, perinstance_stats, short_hash=8)
        >>> _ = tbl.assert_assumptions()
        >>> variants = tbl.variant_keys_for_instance('id1')
        >>> assert len(variants) == 2
        >>> base = [v for v in variants if not v.is_perturbed][0]
        >>> pert = [v for v in variants if v.is_perturbed][0]
        >>> assert sorted([s['name']['name'] for s in tbl.stats_for_variant(base)]) == ['num_bytes', 'num_prompt_tokens']
        >>> # Key round-trip via legacy tuple
        >>> r0 = tbl.rows_for_variant(pert)[0]
        >>> k = r0.key.as_tuple()
        >>> assert tbl.get_row(k) is r0
    """

    def __init__(
        self,
        request_states: list[dict[str, Any]],
        perinstance_stats: list[dict[str, Any]],
        *,
        short_hash: int = 12,
    ):
        self.request_states = request_states
        self.perinstance_stats = perinstance_stats
        self.short_hash = short_hash

        self.request_state_by_variant: dict[
            InstanceVariantKey, dict[str, Any]
        ] = {}
        self.stats_by_variant: dict[
            InstanceVariantKey, list[dict[str, Any]]
        ] = {}
        self.rows_by_variant: dict[
            InstanceVariantKey, list[InstanceStatRow]
        ] = {}
        self.row_by_key: dict[InstanceStatKey, InstanceStatRow] = {}

        self.diagnostics: dict[str, Any] = {
            'request_state_duplicates': [],
            'unmatched_variants': [],
        }

        self._build()

    def __nice__(self):
        return f'variants={len(self.request_state_by_variant)} rows={len(self.row_by_key)}'

    def __len__(self):
        return len(self.row_by_key)

    def __iter__(self):
        """Iterate over InstanceStatRow objects (joined rows)."""
        return iter(self.row_by_key.values())

    # --- core build ---

    def _build(self):
        # 1) index request_states by variant
        dupes = []
        for rs in self.request_states:
            inst = rs.get('instance') or {}
            iid = inst.get('id', None)
            tti = _coerce_int(rs.get('train_trial_index', None))
            pid = _nice_perturbation_id(
                inst.get('perturbation', None), short_hash=self.short_hash
            )
            vk = InstanceVariantKey(iid, tti, pid)
            if vk in self.request_state_by_variant:
                dupes.append((vk, self.request_state_by_variant[vk], rs))
                continue
            self.request_state_by_variant[vk] = rs
        self.diagnostics['request_state_duplicates'] = dupes

        # 2) merge perinstance bundles into per-variant groups
        tmp: dict[InstanceVariantKey, list[dict[str, Any]]] = {}

        for row in self.perinstance_stats:
            iid = row.get('instance_id', None)
            tti = _coerce_int(row.get('train_trial_index', None))
            stats = row.get('stats', []) or []
            # group stats inside this row by their stat-name perturbation
            per_pid: dict[str | None, list[dict[str, Any]]] = {}
            for stat in stats:
                name_obj = stat.get('name', None) or {}
                stat_pid = None
                if isinstance(name_obj, dict):
                    stat_pid = _nice_perturbation_id(
                        name_obj.get('perturbation', None),
                        short_hash=self.short_hash,
                    )
                per_pid.setdefault(stat_pid, []).append(stat)

            for stat_pid, subset in per_pid.items():
                vk = InstanceVariantKey(iid, tti, stat_pid)
                tmp.setdefault(vk, []).extend(subset)

        self.stats_by_variant = tmp

        # 3) join into InstanceStatRow objects
        unmatched = []
        for vk, stats in self.stats_by_variant.items():
            rs = self.request_state_by_variant.get(vk, None)

            # fallback: if stat pid None but only one request variant exists for this base key
            if rs is None and vk.perturbation_id is None:
                candidates = [
                    k
                    for k in self.request_state_by_variant.keys()
                    if (
                        k.instance_id == vk.instance_id
                        and k.train_trial_index == vk.train_trial_index
                    )
                ]
                if len(candidates) == 1:
                    rs = self.request_state_by_variant[candidates[0]]

            if rs is None:
                unmatched.append(vk)

            rows = []
            for stat in stats:
                name_obj = stat.get('name', None) or {}
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
                sub_split = (
                    name_obj.get('sub_split', None)
                    if isinstance(name_obj, dict)
                    else None
                )
                stat_pid = None
                if isinstance(name_obj, dict):
                    stat_pid = _nice_perturbation_id(
                        name_obj.get('perturbation', None),
                        short_hash=self.short_hash,
                    )

                sk = InstanceStatKey(vk, metric, split, sub_split, stat_pid)
                row_obj = InstanceStatRow(sk, stat, rs)
                rows.append(row_obj)
                self.row_by_key[sk] = row_obj

            self.rows_by_variant[vk] = rows

        self.diagnostics['unmatched_variants'] = unmatched

    # --- assertions (optional) ---

    def assert_assumptions(self) -> 'JoinedInstanceStatTable':
        dupes = self.diagnostics.get('request_state_duplicates', [])
        assert not dupes, (
            f'Duplicate request_state variant keys. Example={dupes[:1]!r}'
        )

        unmatched = self.diagnostics.get('unmatched_variants', [])
        assert not unmatched, (
            f'Some perinstance variants could not be matched to request_states. Example={unmatched[:5]!r}'
        )
        return self

    # --- query helpers ---

    def variant_keys(self) -> list[InstanceVariantKey]:
        return sorted(
            self.request_state_by_variant.keys(),
            key=lambda k: (
                str(k.instance_id),
                k.train_trial_index or -1,
                str(k.perturbation_id),
            ),
        )

    def variant_keys_for_instance(
        self,
        instance_id: str,
        *,
        train_trial_index: int | None = None,
        include_perturbed: bool = True,
        include_unperturbed: bool = True,
    ) -> list[InstanceVariantKey]:
        out = []
        for k in self.request_state_by_variant.keys():
            if k.instance_id != instance_id:
                continue
            if (
                train_trial_index is not None
                and k.train_trial_index != train_trial_index
            ):
                continue
            if k.is_perturbed and not include_perturbed:
                continue
            if (not k.is_perturbed) and not include_unperturbed:
                continue
            out.append(k)
        return sorted(
            out,
            key=lambda k: (k.train_trial_index or -1, str(k.perturbation_id)),
        )

    def request_state(
        self, variant: InstanceVariantKey
    ) -> dict[str, Any] | None:
        return self.request_state_by_variant.get(variant, None)

    def rows_for_variant(
        self, variant: InstanceVariantKey
    ) -> list[InstanceStatRow]:
        return self.rows_by_variant.get(variant, [])

    def stats_for_variant(
        self, variant: InstanceVariantKey
    ) -> list[dict[str, Any]]:
        return [r.stat for r in self.rows_for_variant(variant)]

    def rows_for_instance(
        self, instance_id: str, *, include_perturbed: bool = True
    ) -> list[InstanceStatRow]:
        rows = []
        for vk in self.variant_keys_for_instance(
            instance_id,
            include_perturbed=include_perturbed,
            include_unperturbed=True,
        ):
            rows.extend(self.rows_for_variant(vk))
        return rows

    def stats_for_instance(
        self, instance_id: str, *, include_perturbed: bool = True
    ) -> list[dict[str, Any]]:
        return [
            r.stat
            for r in self.rows_for_instance(
                instance_id, include_perturbed=include_perturbed
            )
        ]

    def get_row(
        self, key: InstanceStatKey | tuple[Any, ...]
    ) -> InstanceStatRow | None:
        if isinstance(key, InstanceStatKey):
            return self.row_by_key.get(key, None)
        if isinstance(key, tuple) and len(key) == 7:
            vk = InstanceVariantKey(key[0], _coerce_int(key[1]), key[2])
            sk = InstanceStatKey(vk, key[3], key[4], key[5], key[6])
            return self.row_by_key.get(sk, None)
        raise TypeError(f'Unrecognized key type: {type(key)}')

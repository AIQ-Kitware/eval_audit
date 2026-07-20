"""Plan the rejudge job matrix for kwdagger fan-out (open-judge-plan Commit 11).

The overnight bash driver leases ONE judge at a time and is serial over judges,
so a 4-GPU host runs at 1/4 utilization while a TP1 judge arm holds a single
card. This module turns the same scope — benchmarks x judges x replicates —
into a flat job matrix that ``kwdagger schedule`` fans out, letting
infer-stack's admission queue decide what co-hosts (four small judges on four
cards co-host fine; two 27Bs on one card do not).

This module is deliberately **free of kwdagger and HELM imports** so the
planning logic is unit-testable without a scheduler or a GPU. The node that
consumes these rows lives in ``eval_audit.pipelines.rejudge_pipeline``.

Job ordering matters for cost, not correctness: rows are grouped by judge so a
judge's jobs are adjacent in the queue. Adjacent jobs keep infer-stack's demand
refcount above zero, which keeps the deployment resident between them; a queue
that interleaved judges would tear down and reload multi-GiB weights repeatedly
under a ``reclaim: stop`` endpoint policy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

#: Default replicate list when a (benchmark, judge) pair declares none.
DEFAULT_REPLICATES = (0, 1, 2)


class RejudgeMatrixError(ValueError):
    pass


@dataclass(frozen=True)
class JudgeArm:
    """One judge in the matrix: its spec file plus the endpoint to lease.

    ``spec_hash`` is the JudgeSpec content hash. It rides along as job identity
    so that EDITING a judge JSON produces a new job folder — a path alone would
    not, and the same trap (a config change invisible to a cache key) already
    cost us a stale ``cache-hit`` once, when a parser change did not move
    ``parser_version``.
    """

    judge_id: str
    judge_json: str
    lease_endpoint: str
    spec_hash: str

    def __post_init__(self) -> None:
        for name in ("judge_id", "judge_json", "lease_endpoint", "spec_hash"):
            if not str(getattr(self, name) or "").strip():
                raise RejudgeMatrixError(f"JudgeArm.{name} must be a nonempty string")


@dataclass(frozen=True)
class RejudgeMatrixSpec:
    """Everything needed to enumerate the job rows."""

    snapshots: Mapping[str, str]          # benchmark -> snapshot dpath
    judges: Sequence[JudgeArm]            # ordered; keep small -> large
    out_root: str
    cache_root: str
    #: ROOT for judge sidecar bundles. Each job gets its own
    #: ``<sidecar_config>/<judge_id>`` subdirectory, never this path directly —
    #: ``export_judge_bundle`` writes a model_deployments.yaml containing only
    #: the judges it was given, so two arms sharing one directory clobber each
    #: other's registration. The loser's deployment disappears, HELM falls back
    #: to the ``litellm/`` name prefix, and every judge request dies with
    #: OptionalDependencyNotInstalled (observed 2026-07-19: 14 of one arm's 15
    #: attempts destroyed while still reporting exit status 0).
    sidecar_config: str
    experiment_name: str = "open-judge"
    parallelism: int = 8
    max_instances: int | None = None
    #: (benchmark, judge_id) -> replicates. Falls back to ``replicates_by_benchmark``
    #: then :data:`DEFAULT_REPLICATES`. An EMPTY sequence skips that pair.
    replicates_by_pair: Mapping[tuple[str, str], Sequence[int]] = field(default_factory=dict)
    replicates_by_benchmark: Mapping[str, Sequence[int]] = field(default_factory=dict)

    def replicates_for(self, benchmark: str, judge_id: str) -> tuple[int, ...]:
        if (benchmark, judge_id) in self.replicates_by_pair:
            return tuple(self.replicates_by_pair[(benchmark, judge_id)])
        if benchmark in self.replicates_by_benchmark:
            return tuple(self.replicates_by_benchmark[benchmark])
        return tuple(DEFAULT_REPLICATES)


def build_rejudge_matrix(spec: RejudgeMatrixSpec) -> list[dict[str, Any]]:
    """Enumerate one job row per (judge, benchmark, replicate).

    Grouped by judge (outer loop) so a judge's rows are contiguous — see the
    module docstring on why that keeps weights resident. Within a judge,
    benchmarks follow the caller's ``snapshots`` order, which the runbook sets
    cheap-first so a truncated run still yields the complete cheap picture.
    """
    if not spec.judges:
        raise RejudgeMatrixError("no judges in the matrix")
    if not spec.snapshots:
        raise RejudgeMatrixError("no benchmark snapshots in the matrix")

    seen_ids: set[str] = set()
    rows: list[dict[str, Any]] = []
    for arm in spec.judges:
        if arm.judge_id in seen_ids:
            raise RejudgeMatrixError(f"duplicate judge_id {arm.judge_id!r} in the matrix")
        seen_ids.add(arm.judge_id)
        for benchmark, snapshot in spec.snapshots.items():
            replicates = spec.replicates_for(benchmark, arm.judge_id)
            for replicate in replicates:
                if not isinstance(replicate, int) or replicate < 0:
                    raise RejudgeMatrixError(
                        f"replicate for {benchmark}:{arm.judge_id} must be an int >= 0, "
                        f"got {replicate!r}"
                    )
                rows.append(
                    {
                        # --- identity (algo params) ---
                        "snapshot": str(snapshot),
                        "judge_json": str(arm.judge_json),
                        "judge_spec_hash": arm.spec_hash,
                        "replicate": replicate,
                        "experiment_name": spec.experiment_name,
                        "max_instances": spec.max_instances,
                        # --- placement / perf ---
                        "out_root": str(spec.out_root),
                        "cache_root": str(spec.cache_root),
                        # Private per judge: see RejudgeMatrixSpec.sidecar_config.
                        "sidecar_config": str(
                            Path(spec.sidecar_config) / arm.judge_id
                        ),
                        "parallelism": spec.parallelism,
                        "lease_endpoint": arm.lease_endpoint,
                        # --- labels (not consumed by the CLI; for reporting) ---
                        "_benchmark": benchmark,
                        "_judge_id": arm.judge_id,
                    }
                )
    if not rows:
        raise RejudgeMatrixError(
            "matrix is empty — every (benchmark, judge) pair declared zero replicates"
        )
    return rows


def load_judge_arm(judge_json: str | Path, lease_endpoint: str | None = None) -> JudgeArm:
    """Read a JudgeSpec JSON into a :class:`JudgeArm`.

    Imports :mod:`eval_audit.judging.specs` lazily so this module stays
    importable (and testable) without the HELM dependency chain.
    """
    from eval_audit.judging.specs import JudgeSpec

    path = Path(judge_json)
    fields = json.loads(path.read_text())
    fields.pop("judge_spec_hash", None)  # derived, never trusted from input
    judge = JudgeSpec(**fields)
    return JudgeArm(
        judge_id=judge.id,
        judge_json=str(path),
        lease_endpoint=lease_endpoint or judge.lease_endpoint,
        spec_hash=judge.spec_hash(),
    )


def summarize_matrix(rows: Iterable[dict[str, Any]]) -> str:
    """A human-readable plan: jobs per judge and per benchmark.

    Printed before submission so the fan-out size is an explicit, reviewed
    number rather than a surprise discovered by watching GPUs fill up.
    """
    rows = list(rows)
    by_judge: dict[str, int] = {}
    by_benchmark: dict[str, int] = {}
    for row in rows:
        by_judge[row["_judge_id"]] = by_judge.get(row["_judge_id"], 0) + 1
        by_benchmark[row["_benchmark"]] = by_benchmark.get(row["_benchmark"], 0) + 1
    lines = [f"rejudge matrix: {len(rows)} job(s)"]
    lines.append("  by judge (queue order):")
    for judge_id, count in by_judge.items():
        lines.append(f"    {judge_id:20} {count:4} job(s)")
    lines.append("  by benchmark:")
    for benchmark, count in sorted(by_benchmark.items()):
        lines.append(f"    {benchmark:20} {count:4} job(s)")
    return "\n".join(lines)


__all__ = [
    "DEFAULT_REPLICATES",
    "JudgeArm",
    "RejudgeMatrixError",
    "RejudgeMatrixSpec",
    "build_rejudge_matrix",
    "load_judge_arm",
    "summarize_matrix",
]

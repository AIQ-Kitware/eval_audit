"""Tell the operator, at execution time, when a run adds a competing attempt.

An experiment directory holds one job per run entry
(``<experiment>/helm/<job_id>/benchmark_output/runs/<suite>/<run_name>/``).
kwdagger keys a job on the hash of its algo params, so changing the recipe —
fixing a tokenizer flag, pinning a dtype — correctly mints a *new* job rather
than overwriting the old one. Both then sit in the same experiment, and
downstream every local run matching a logical key becomes a candidate for the
same packet. That is how ``audit-allenai-olmo-7b-lite-full`` came to hold two
attempts for 71 run entries (``docs/helm-gotchas.md`` §G14).

The artifact side of that is handled: the planner enables one
``official_vs_local`` against the newest attempt and demotes the rest, the
reporting layer selects rather than pools, and ``eval-audit-lint-store`` grades
what the choice was worth. What was missing is that nobody was *told* at the
moment it happened — the olmo case surfaced eight months later, while writing
up numbers computed from it.

This module supplies that. It is a diagnostic, never a gate:

* **Resume is untouched.** Re-running a manifest to finish a sweep that died
  part-way creates no new attempt for entries kwdagger skips, so nothing is
  reported. This is why detection is a **before/after diff** rather than a
  pre-flight prediction — predicting would mean guessing whether kwdagger will
  skip, and would fire on every resume.
* **A changed-recipe rerun is reported, not blocked.** The run proceeds; the
  operator learns which entries now hold competing attempts and which job ids
  they are, while it is still cheap to separate them with
  ``--experiment-name``.
* ``strict=True`` turns the report into a nonzero exit for unattended batch
  runs that would rather stop than discover it later. It does not preserve any
  older behavior — rerunning into a live experiment stays legal; the flag only
  chooses how loudly a real event is reported.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from eval_audit.run_entries import canonical_logical_key


@dataclass(frozen=True)
class ExistingAttempt:
    """One materialized run directory under an experiment."""

    job_id: str
    run_name: str
    logical_key: str
    completed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "run_name": self.run_name,
            "logical_key": self.logical_key,
            "completed": self.completed,
        }


@dataclass(frozen=True)
class AttemptCollision:
    """A logical run key that ends up with more than one local attempt."""

    logical_key: str
    prior_job_ids: list[str]
    added_job_ids: list[str]

    @property
    def n_attempts(self) -> int:
        return len(self.prior_job_ids) + len(self.added_job_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "logical_key": self.logical_key,
            "prior_job_ids": list(self.prior_job_ids),
            "added_job_ids": list(self.added_job_ids),
            "n_attempts": self.n_attempts,
        }


def scan_experiment_attempts(experiment_root: str | Path) -> dict[str, list[ExistingAttempt]]:
    """Map canonical logical run key -> attempts materialized under an experiment.

    Keys are canonicalized (``canonical_logical_key``) because the same recipe
    is written with different token order in different places; without it two
    attempts at one run entry look like two different entries.
    """
    helm_root = Path(experiment_root) / "helm"
    attempts: dict[str, list[ExistingAttempt]] = defaultdict(list)
    if not helm_root.is_dir():
        return {}
    for job_dpath in sorted(helm_root.iterdir()):
        if not job_dpath.is_dir():
            continue
        completed = (job_dpath / "DONE").exists()
        runs_root = job_dpath / "benchmark_output" / "runs"
        if not runs_root.is_dir():
            continue
        for suite_dpath in sorted(runs_root.iterdir()):
            if not suite_dpath.is_dir():
                continue
            for run_dpath in sorted(suite_dpath.iterdir()):
                # A run directory is one HELM run, identified by its spec.
                # HELM writes sibling directories under the suite that are not
                # runs (``eval_cache``); counting those would report every job
                # in the experiment as an attempt at one shared "run entry".
                if not run_dpath.is_dir() or not (run_dpath / "run_spec.json").is_file():
                    continue
                key = canonical_logical_key(run_dpath.name) or run_dpath.name
                attempts[key].append(
                    ExistingAttempt(
                        job_id=job_dpath.name,
                        run_name=run_dpath.name,
                        logical_key=key,
                        completed=completed,
                    )
                )
    return dict(attempts)


def diff_attempts(
    before: dict[str, list[ExistingAttempt]],
    after: dict[str, list[ExistingAttempt]],
) -> list[AttemptCollision]:
    """Run keys that came out of a run holding more than one attempt.

    Reports a key when it *ends* with several attempts and the run contributed
    to that — either by adding one to a key that already had some, or by adding
    several at once. A key that merely carried pre-existing duplicates through
    an untouched run is not this run's news, and is reported separately by
    :func:`preexisting_collisions`.
    """
    collisions: list[AttemptCollision] = []
    for key, attempts_after in sorted(after.items()):
        prior_ids = [attempt.job_id for attempt in before.get(key, [])]
        added_ids = [
            attempt.job_id for attempt in attempts_after if attempt.job_id not in set(prior_ids)
        ]
        if not added_ids or len(attempts_after) < 2:
            continue
        collisions.append(
            AttemptCollision(logical_key=key, prior_job_ids=prior_ids, added_job_ids=added_ids)
        )
    return collisions


def preexisting_collisions(
    attempts: dict[str, list[ExistingAttempt]],
) -> list[AttemptCollision]:
    """Keys that already hold several attempts before this run starts."""
    return [
        AttemptCollision(
            logical_key=key,
            prior_job_ids=[attempt.job_id for attempt in key_attempts],
            added_job_ids=[],
        )
        for key, key_attempts in sorted(attempts.items())
        if len(key_attempts) > 1
    ]


def render_collisions(
    experiment_name: str,
    collisions: list[AttemptCollision],
    *,
    preexisting: bool = False,
) -> str:
    verb = "already held" if preexisting else "now holds"
    lines = [
        f"{experiment_name} {verb} more than one local attempt for "
        f"{len(collisions)} run entr{'y' if len(collisions) == 1 else 'ies'}:"
    ]
    for collision in collisions[:20]:
        lines.append(f"  {collision.logical_key}  ({collision.n_attempts} attempts)")
        for job_id in collision.prior_job_ids[:4]:
            lines.append(f"    prior: {job_id}")
        if len(collision.prior_job_ids) > 4:
            lines.append(f"    prior: ... and {len(collision.prior_job_ids) - 4} more")
        for job_id in collision.added_job_ids[:4]:
            lines.append(f"    new:   {job_id}")
        if len(collision.added_job_ids) > 4:
            lines.append(f"    new:   ... and {len(collision.added_job_ids) - 4} more")
    if len(collisions) > 20:
        lines.append(f"  ... and {len(collisions) - 20} more run entries")
    lines.append(
        "Downstream treats the newest attempt as canonical and demotes the rest "
        "(docs/helm-gotchas.md §G14); run eval-audit-lint-store on the store to "
        "see what the choice is worth. To keep the attempts separate instead, "
        "re-run with a different --experiment-name."
    )
    return "\n".join(lines)


def report_attempt_collisions(
    experiment_name: str,
    before: dict[str, list[ExistingAttempt]],
    after: dict[str, list[ExistingAttempt]],
    *,
    strict: bool = False,
) -> dict[str, Any]:
    """Log what this run did to the experiment's attempt multiplicity.

    Returns a record for the caller's ``info`` payload. Raises ``SystemExit``
    only under ``strict``, and only for collisions *this run* created.
    """
    added = diff_attempts(before, after)
    carried = [
        collision
        for collision in preexisting_collisions(before)
        if collision.logical_key not in {item.logical_key for item in added}
    ]
    if added:
        logger.warning(render_collisions(experiment_name, added))
    if carried:
        logger.info(render_collisions(experiment_name, carried, preexisting=True))
    if strict and added:
        raise SystemExit(
            f"{len(added)} run entries gained a competing local attempt in "
            f"{experiment_name} (--strict-attempts)."
        )
    return {
        "n_run_keys": len(after),
        "collisions_added": [collision.to_dict() for collision in added],
        "collisions_preexisting": [collision.to_dict() for collision in carried],
    }

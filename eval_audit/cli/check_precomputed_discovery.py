"""Discovery dry-check for the from-spec migration (Change 4).

CPU-only. For each ``run_entries`` string in an infer-stack preset, resolve it
against a ``precomputed_root`` using the **same** token-subset matcher the
from-spec replay uses (``run_dir_matches_requested`` /
``find_best_precomputed_run`` in aiq-magnet), and classify each entry as:

* ``RESOLVED``  — exactly one official run dir matches (replay will use it);
* ``NO_MATCH``  — zero matches (the entry's tokens are not a subset of any
                  official dir name → discovery would fail at replay time);
* ``AMBIGUOUS`` — more than one matches (replay picks the best-scoring one
                  deterministically; reported so the operator can confirm).

For ``RESOLVED`` / ``AMBIGUOUS`` it also reports the matched official dir and its
recorded ``adapter_spec.model_deployment`` — the "from" name the deployment
rewrite replaces (migration plan §6 Change 1).

No GPU, no serving, no HELM execution — pure filesystem discovery. Use it to
**baseline** which current run-entries fail token-subset discovery before
reducing them to discovery keys, and to **validate** the reduced entries after.
See ``docs/planning/olmo-from-run-spec-migration-plan.md`` (Change 4).

Usage::

    python -m eval_audit.cli.check_precomputed_discovery \
        --preset allenai-olmo-2-0325-32b-instruct \
        --precomputed-root /data/crfm-helm-public/capabilities \
        --mode full

Exit code is nonzero if any entry is ``NO_MATCH`` (a hard discovery failure);
``AMBIGUOUS`` warns but does not fail unless ``--strict`` is given.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path


def _load_run_entries(preset: str, mode: str) -> list[str]:
    """Read a preset's ``run_entries`` for the smoke|full manifest block."""
    from eval_audit.integrations.infer_stack.adapter import PRESET_CONFIGS

    if preset not in PRESET_CONFIGS:
        raise SystemExit(
            f"unknown preset {preset!r}; known: {', '.join(sorted(PRESET_CONFIGS))}"
        )
    block = PRESET_CONFIGS[preset].get(f"{mode}_manifest")
    if not block or "run_entries" not in block:
        raise SystemExit(f"preset {preset!r} has no {mode}_manifest.run_entries")
    return list(block["run_entries"])


@dataclass
class _Run:
    name: str
    path: Path


def _enumerate_runs(root: Path) -> list[_Run]:
    """Enumerate every HELM run dir under ``root`` ONCE (the expensive step).

    Mirrors ``find_best_precomputed_run``'s inner loop so matching is faithful,
    but done a single time per root rather than once per run-entry — essential
    for the broad ``/data/crfm-helm-public`` root olmo-7b needs.
    """
    import warnings

    from magnet.backends.helm.cli.materialize_helm_run import (
        discover_benchmark_output_dirs,
    )
    from magnet.backends.helm.helm_outputs import HelmOutputs

    # magnet 0.0.2 deprecates the ``.name`` accessor we (and the library's own
    # find_best_precomputed_run) use to read the run-dir name; silence the noise.
    warnings.filterwarnings("ignore", category=FutureWarning, module="magnet")

    runs: list[_Run] = []
    for bo in discover_benchmark_output_dirs([root]):
        try:
            outputs = HelmOutputs.coerce(bo)
        except Exception:
            continue
        for suite in outputs.suites(pattern="*"):
            for run in suite.runs(pattern="*"):
                runs.append(_Run(name=run.name, path=Path(run.path)))
    return runs


def _official_deployment(run_dir: Path) -> str | None:
    """The official run's ``adapter_spec.model_deployment`` (rewrite 'from')."""
    try:
        spec = json.loads((run_dir / "run_spec.json").read_text())
        return (spec.get("adapter_spec") or {}).get("model_deployment")
    except Exception:
        return None


@dataclass
class _EntryResult:
    entry: str
    status: str  # RESOLVED | NO_MATCH | AMBIGUOUS
    candidates: list[_Run]
    best: _Run | None
    deployment: str | None


def _classify(entry: str, runs: list[_Run]) -> _EntryResult:
    from magnet.backends.helm.cli.materialize_helm_run import (
        match_score,
        run_dir_matches_requested,
    )

    # Conservative cheap pre-filter on the benchmark stem (the matcher requires
    # it to match anyway) so the authoritative matcher runs over far fewer dirs.
    stem = entry.split(":", 1)[0]
    prelim = [r for r in runs if r.name.split(":", 1)[0] == stem]
    cands = [
        r for r in prelim if run_dir_matches_requested(r.name, entry, run_dir=r.path)
    ]
    if not cands:
        return _EntryResult(entry, "NO_MATCH", [], None, None)
    best = min(cands, key=lambda r: match_score(r.name, entry))
    status = "RESOLVED" if len(cands) == 1 else "AMBIGUOUS"
    return _EntryResult(entry, status, cands, best, _official_deployment(best.path))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--preset",
        help="infer-stack preset key (entries read from its manifest block)",
    )
    ap.add_argument(
        "--entry",
        action="append",
        default=[],
        metavar="RUN_ENTRY",
        help="check this run-entry instead of (or in addition to) the preset's; "
        "repeatable. Lets you validate a reduced discovery key before editing the "
        "preset (migration plan Change 1).",
    )
    ap.add_argument("--precomputed-root", required=True, type=Path)
    ap.add_argument("--mode", choices=("smoke", "full"), default="full")
    ap.add_argument(
        "--strict",
        action="store_true",
        help="also fail (nonzero exit) on AMBIGUOUS entries",
    )
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = ap.parse_args(argv)

    root: Path = args.precomputed_root
    if not root.is_dir():
        raise SystemExit(f"precomputed_root does not exist: {root}")

    if not args.preset and not args.entry:
        raise SystemExit("provide --preset and/or one or more --entry")
    entries = list(args.entry)
    if args.preset:
        entries = _load_run_entries(args.preset, args.mode) + entries
    print(
        f"[discovery] preset={args.preset or '(--entry)'} mode={args.mode} "
        f"root={root} entries={len(entries)} — enumerating runs…",
        file=sys.stderr,
    )
    runs = _enumerate_runs(root)
    print(f"[discovery] {len(runs)} run dirs under root", file=sys.stderr)

    results = [_classify(e, runs) for e in entries]
    n = {"RESOLVED": 0, "NO_MATCH": 0, "AMBIGUOUS": 0}
    for r in results:
        n[r.status] += 1

    if args.json:
        print(
            json.dumps(
                {
                    "preset": args.preset,
                    "mode": args.mode,
                    "root": str(root),
                    "summary": n,
                    "entries": [
                        {
                            "entry": r.entry,
                            "status": r.status,
                            "n_candidates": len(r.candidates),
                            "matched_dir": r.best.name if r.best else None,
                            "official_deployment": r.deployment,
                        }
                        for r in results
                    ],
                },
                indent=2,
            )
        )
    else:
        for r in results:
            print(f"[{r.status:9}] {r.entry}")
            if r.best is not None:
                extra = "" if r.status == "RESOLVED" else f"  ({len(r.candidates)} candidates; best:)"
                print(f"            -> {r.best.name}{extra}")
                print(f"               deploy(official)={r.deployment}")
        print(
            f"\n[discovery] {args.preset}/{args.mode}: {len(entries)} entries — "
            f"{n['RESOLVED']} RESOLVED, {n['NO_MATCH']} NO_MATCH, "
            f"{n['AMBIGUOUS']} AMBIGUOUS"
        )

    failed = n["NO_MATCH"] > 0 or (args.strict and n["AMBIGUOUS"] > 0)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Precomputed-run discovery core: enumerate HELM run dirs and classify a
run-entry against them (token-subset match).

Promoted from ``cli/check_precomputed_discovery.py`` (R-3) to a public
library home so the export/freeze path in ``adapter.py`` no longer reaches
into a private CLI symbol. Both the CLI and the adapter import from here.
Pure relocation: function bodies are unchanged.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


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
    # P2: break match_score ties on the run name so the chosen "best" (and the
    # AMBIGUOUS ordering) is deterministic, not dependent on the unsorted walk
    # order this tool's docstring promises to avoid.
    best = min(cands, key=lambda r: (match_score(r.name, entry), r.name))
    status = "RESOLVED" if len(cands) == 1 else "AMBIGUOUS"
    return _EntryResult(entry, status, cands, best, _official_deployment(best.path))

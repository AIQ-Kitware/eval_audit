"""Historic public-run candidate lookup.

Given a requested run entry, find the matching run dirs inside a
precomputed public-HELM corpus (``benchmark_output`` trees). Moved out of
``workflows.compare_batch`` when that deprecated batch path was deleted
(2026-08-06); the surviving consumer is ``reports.portfolio``.
"""

from __future__ import annotations

import json
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any

from eval_audit.compat.helm_outputs import HelmOutputs
from eval_audit.run_entries import (
    canonicalize_kv,
    discover_benchmark_output_dirs,
    normalize_run_entry_for_historic_lookup,
    parse_run_name_to_kv,
    run_dir_matches_requested,
)


def load_run_spec_json(run_dir: str | Path) -> dict[str, Any]:
    run_dir = Path(run_dir)
    fpath = run_dir / "run_spec.json"
    if not fpath.exists():
        return {}
    return json.loads(fpath.read_text())


def collect_historic_candidates(
    precomputed_root: str | Path,
    run_entry: str,
) -> list[dict[str, Any]]:
    req_bench, _req_kv = parse_run_name_to_kv(run_entry)
    if not req_bench:
        return []
    public_lookup_entry = normalize_run_entry_for_historic_lookup(run_entry)
    benchmark_index = _historic_candidate_benchmark_index(str(Path(precomputed_root).expanduser().resolve()))
    candidates = []
    for candidate in benchmark_index.get(req_bench, ()):
        if run_dir_matches_requested(candidate["run_name"], public_lookup_entry):
            # Return fresh dicts so callers can mutate without poisoning the cache.
            candidates.append(dict(candidate))
    return candidates


@lru_cache(maxsize=4)
def _historic_candidate_benchmark_index(
    precomputed_root: str,
) -> dict[str, tuple[dict[str, Any], ...]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for bo in discover_benchmark_output_dirs([precomputed_root]):
        try:
            outputs = HelmOutputs.coerce(bo)
        except Exception:
            continue
        for suite in outputs.suites(pattern="*"):
            for run in suite.runs(pattern="*"):
                run_dir = Path(run.path)
                bench, cand_kv = parse_run_name_to_kv(run.name)
                if not bench:
                    continue
                run_spec = load_run_spec_json(run_dir)
                adapter_spec = run_spec.get("adapter_spec", {}) or {}
                metric_specs = run_spec.get("metric_specs", []) or []
                grouped[bench].append(
                    {
                        "run_dir": run_dir,
                        "run_name": run.name,
                        "run_name_benchmark": bench,
                        "run_name_kv": canonicalize_kv(cand_kv),
                        "source_root": bo,
                        "helm_version": run_dir.parent.name,
                        "requested_max_eval_instances": adapter_spec.get(
                            "max_eval_instances", None
                        ),
                        "model_deployment": adapter_spec.get(
                            "model_deployment", None
                        ),
                        "metric_class_names": [
                            m.get("class_name", None) for m in metric_specs
                        ],
                    }
                )
    return {bench: tuple(rows) for bench, rows in grouped.items()}

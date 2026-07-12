"""Exact-path freeze: resolve preset run-entries to pinned rel-paths.

Extracted from ``adapter.py`` (R-3, pure relocation). Holds the local-token
strip + the run_spec_sources freeze used by the --freeze-rel-paths export
path. adapter.py re-exports both (test_exporter_freeze imports them).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def _strip_local_deployment(
    run_entry: str, local_names: "frozenset[str]"
) -> tuple[str, str | None]:
    """Drop a ``model_deployment=<name>`` token from a run-entry for discovery,
    but ONLY when ``<name>`` is a LOCAL deployment (rel-path plan §6, the
    "local-only" rule). The official run dir never carries a local token, so it
    must be stripped to match; an official/private token (e.g. a stanfordhealthcare
    deployment) is kept so it still discriminates. Returns
    ``(discovery_query, stripped_local_name_or_None)``. (R-8: delegates to the
    shared conditional strip.)
    """
    from eval_audit.helm.run_entries import strip_model_deployment

    return strip_model_deployment(run_entry, only_names=local_names)


def _freeze_run_spec_sources(
    spec: dict[str, Any],
    *,
    precomputed_root: str,
    model_entries: list[dict[str, Any]],
    lease_facts: dict[str, Any] | None,
    runs: list[Any],
    omit_model_deployment: bool = False,
) -> list[dict[str, Any]]:
    """Resolve each preset run-entry to its EXACT rel-path once and freeze a
    ``run_spec_sources`` list (rel-path plan §4.5).

    This is the *only* remaining use of token-subset discovery: it runs here, at
    export, against a known corpus snapshot (``runs`` already enumerated under
    ``precomputed_root``), and pins the matched official run dir as a path relative
    to the root. The materialized-replay path then reads that exact path — no
    run-time discovery. A ``NO_MATCH`` / ``AMBIGUOUS`` entry is a hard error:
    freezing a wrong or ambiguously-chosen match would pin the wrong recipe.

    Each frozen source carries its own ``model_deployment`` (the LOCAL rewrite
    target) and ``lease_endpoint``, so a MULTI-deployment bundle freezes a per-run
    rewrite target — lifting the single-deployment restriction the discovery path
    imposes (``export_benchmark_bundle`` ``rewrite_deployment``).

    ``omit_model_deployment=True`` (the **era** path): a pre-v0.5 ``adapter_spec``
    has no ``model_deployment`` field, so replay is verbatim (by-name via the era
    deployment registry). Each source omits ``model_deployment`` entirely — the
    materializer would reject a rewrite anyway. The lease endpoint comes from the
    scalar for a single-endpoint bundle, or from the ``lease_endpoints`` map keyed
    on the run-entry's ``model=`` token (which equals the era deployment name) for
    a multi-endpoint bundle.
    """
    from eval_audit.integrations.infer_stack import discovery as dc

    root = Path(precomputed_root)
    local_names = frozenset(entry["name"] for entry in model_entries)
    single_name = model_entries[0]["name"] if len(model_entries) == 1 else None
    lease_scalar = (lease_facts or {}).get("lease_endpoint")
    lease_map = (lease_facts or {}).get("lease_endpoints") or {}

    sources: list[dict[str, Any]] = []
    for run_entry in spec["run_entries"]:
        query, local_token = _strip_local_deployment(run_entry, local_names)
        deployment = local_token or single_name
        if deployment is None and not omit_model_deployment:
            raise ValueError(
                f"cannot freeze run-entry {run_entry!r}: a multi-deployment bundle "
                "needs an inline model_deployment=<local> token to name the rewrite "
                "target, but none was present."
            )
        result = dc._classify(query, runs)
        if result.status != "RESOLVED":
            raise ValueError(
                f"cannot freeze run-entry {run_entry!r}: discovery is "
                f"{result.status} under {precomputed_root!r} "
                f"({len(result.candidates)} candidates). Narrow precomputed_root or "
                "fix the entry before exporting an exact-path bundle."
            )
        rel_path = str(Path(result.best.path).relative_to(root))
        source: dict[str, Any] = {
            "run_entry": run_entry,
            "rel_path": rel_path,
        }
        if not omit_model_deployment:
            source["model_deployment"] = deployment
        if omit_model_deployment:
            # Era: there is no local rewrite target, but the era deployment name
            # IS the official model name (entry["name"]), so a MULTI-endpoint era
            # bundle keys the lease map on the run-entry's model= token. Finding
            # 10: previously era used the scalar only, so a multi-endpoint bundle
            # froze NO lease_endpoint and the vLLM endpoint was never acquired.
            endpoint = lease_scalar
            if endpoint is None and lease_map:
                from eval_audit.helm.run_entries import parse_run_name_to_kv

                _, kv = parse_run_name_to_kv(run_entry)
                model = kv.get("model")
                endpoint = lease_map.get(model)
                if endpoint is None and len(lease_map) == 1:
                    endpoint = next(iter(lease_map.values()))
                if endpoint is None:
                    raise ValueError(
                        f"cannot freeze a lease endpoint for era run-entry "
                        f"{run_entry!r}: its model={model!r} is not in the bundle's "
                        f"lease_endpoints map {sorted(lease_map)!r}. Every era run "
                        "must map to a served endpoint (the era deployment name "
                        "equals the official model name)."
                    )
        else:
            endpoint = lease_scalar or lease_map.get(deployment)
        if endpoint:
            source["lease_endpoint"] = endpoint
        sources.append(source)
    return sources

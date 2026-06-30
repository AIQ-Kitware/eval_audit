from __future__ import annotations

import argparse
import dataclasses
import json
from pathlib import Path

from .adapter import export_benchmark_bundle, resolve_serving_facts


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="eval_audit integration layer for consuming infer_stack serving endpoints."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # Renamed from `describe-contract`: the contract module is gone, so this now
    # prints the catalog-resolved serving facts for one endpoint (served name,
    # backing HF model, served context window). Kept for debugging.
    s = sub.add_parser("describe-endpoint", aliases=["describe-contract"])
    s.add_argument("profile", help="Catalog endpoint name to resolve.")
    s.add_argument("--config-dir", default=None, help="infer-stack config dir holding catalog.yaml.")
    # Deprecated aliases — accepted so existing call sites don't break.
    s.add_argument("--vllm-root", default=None, help="Deprecated alias for --config-dir.")
    s.add_argument("--backend", default=None, help="Deprecated; accepted and ignored.")
    s.add_argument("--simulate-hardware", default=None, help="Deprecated; accepted and ignored.")
    s.set_defaults(cmd_name="describe-endpoint")

    s = sub.add_parser("export-benchmark-bundle")
    s.add_argument("profile", nargs="?", default=None)
    s.add_argument("--preset", default=None)
    s.add_argument("--bundle-root", default=None)
    s.add_argument("--config-dir", default=None, help="infer-stack config dir holding catalog.yaml.")
    s.add_argument("--backend", default=None, help="Deprecated; accepted and ignored.")
    s.add_argument("--simulate-hardware", default=None, help="Deprecated; accepted and ignored.")
    s.add_argument("--vllm-root", default=None, help="Deprecated alias for --config-dir.")
    s.add_argument("--access-kind", default=None)
    s.add_argument(
        "--protocol-mode",
        default=None,
        choices=["chat", "completions"],
        help=(
            "Override the HELM serving protocol (chat vs completions). Required "
            "when exporting a bare profile that has no preset to declare it; "
            "for presets it overrides the declared value."
        ),
    )
    s.add_argument("--base-url", default=None)
    s.add_argument("--api-key-value", default=None)
    s.add_argument(
        "--from-spec",
        action="store_true",
        help=(
            "Emit a faithful-replay bundle: the generated manifests carry "
            "from_run_spec: true + precomputed_root (the recipe source) + "
            "model_deployment (the bundle's own LOCAL deployment name). The replay "
            "rewrites the run_spec.json's adapter_spec.model_deployment to that "
            "local name, so the produced run records the served endpoint and the "
            "audit reports same_deployment=no. Default off keeps the run-entry path."
        ),
    )
    s.add_argument(
        "--precomputed-root",
        default=None,
        help=(
            "Override the from-spec recipe source root (the dir searched for the "
            "official run dir). Only used with --from-spec; otherwise the preset's "
            "smoke/full precomputed_root is used."
        ),
    )
    s.add_argument(
        "--freeze-rel-paths",
        action="store_true",
        help=(
            "Exact-path replay (rel-path plan §4.5): resolve each run-entry to its "
            "EXACT path relative to precomputed_root NOW (the only place token "
            "discovery runs) and freeze run_spec_sources into the generated "
            "manifests. The replay then addresses each official run by that pinned "
            "path and materializes a substituted copy host-side — no run-time "
            "discovery. Implies --from-spec; a NO_MATCH/AMBIGUOUS entry fails the "
            "export. Per-run rewrite targets make multi-deployment bundles work."
        ),
    )
    s.set_defaults(cmd_name="export-benchmark-bundle")

    args = parser.parse_args(argv)
    config_dir = args.config_dir or args.vllm_root
    if args.cmd_name == "describe-endpoint":
        facts = resolve_serving_facts(
            args.profile,
            config_dir=Path(config_dir) if config_dir else None,
        )
        print(json.dumps(dataclasses.asdict(facts), indent=2))
        return

    if args.profile is None and args.preset is None:
        raise SystemExit("Either a profile or a preset is required")
    result = export_benchmark_bundle(
        args.profile or "",
        preset=args.preset,
        bundle_root=Path(args.bundle_root) if args.bundle_root else None,
        backend=args.backend,
        config_dir=Path(config_dir) if config_dir else None,
        access_kind=args.access_kind,
        protocol_mode=args.protocol_mode,
        base_url=args.base_url,
        api_key_value=args.api_key_value,
        from_run_spec=args.from_spec,
        precomputed_root=args.precomputed_root,
        freeze_rel_paths=args.freeze_rel_paths,
    )
    print(json.dumps({
        "bundle_dir": str(result["bundle_dir"]),
        "bundle_path": str(result["bundle_path"]),
        "model_deployments_path": str(result["model_deployments_path"]),
        "benchmark_smoke_manifest_path": str(result["benchmark_smoke_manifest_path"]),
        "benchmark_full_manifest_path": str(result["benchmark_full_manifest_path"]),
    }, indent=2))


if __name__ == "__main__":
    main()

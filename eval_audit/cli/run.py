from __future__ import annotations

import argparse
import json

from eval_audit.infra.logging import setup_cli_logging
from eval_audit.workflows.run_from_manifest import run_from_manifest


def main(argv: list[str] | None = None) -> None:
    setup_cli_logging()
    parser = argparse.ArgumentParser(
        description="Preview or execute a kwdagger experiment from a manifest."
    )
    parser.add_argument("manifest")
    parser.add_argument(
        "--run",
        type=int,
        choices=[0, 1],
        default=0,
        help="Use 0 to preview generated kwdagger argv, 1 to execute it.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Alias for --run=0.",
    )
    parser.add_argument("--root-dpath", default=None)
    parser.add_argument("--queue-name", default=None)
    parser.add_argument("--devices", default=None)
    parser.add_argument("--tmux-workers", type=int, default=None)
    parser.add_argument("--backend", default=None)
    parser.add_argument(
        "--container-image",
        default=None,
        help=(
            "Run each HELM run-entry inside this Docker image (tag or digest "
            "ref). Overrides the manifest's container_image. The digest is "
            "resolved and pinned at schedule time."
        ),
    )
    parser.add_argument(
        "--lease",
        action="store_true",
        help=(
            "Bracket each HELM run with an infer-stack GPU lease "
            "(acquire --queue before, release after). Each run self-acquires "
            "its model so kwdagger can fan out many runs without a per-model "
            "serial serve loop. Works with or without --container-image: the "
            "lease acquires the model server's GPU, while the HELM client (an "
            "HTTP caller to the served endpoint) runs in the container if one is "
            "given, else in the host venv. Either way it uses no GPU "
            "(infer-stack owns them)."
        ),
    )
    parser.add_argument(
        "--lease-ttl",
        default=None,
        help=(
            "Soft TTL for each lease (e.g. 2h, 30m). Must exceed worst-case "
            "model-load + run; leaked leases are reclaimed after it. Overrides "
            "the manifest's lease_ttl (default 4h)."
        ),
    )
    parser.add_argument(
        "--lease-catalog",
        default=None,
        help=(
            "Path to the infer-stack catalog.yaml the lease resolves against. "
            "Overrides the manifest's lease_catalog (resolved to an absolute "
            "path so the lease works from any job cwd)."
        ),
    )
    parser.add_argument(
        "--no-queue",
        action="store_true",
        help=(
            "Use fail-fast acquire instead of the admission queue (acquire "
            "without --queue). Default is to queue-and-wait when the fleet is "
            "busy. Only meaningful with --lease."
        ),
    )
    args = parser.parse_args(argv)
    info = run_from_manifest(
        args.manifest,
        run=bool(0 if args.dry_run else args.run),
        root_dpath=args.root_dpath,
        queue_name=args.queue_name,
        devices=args.devices,
        tmux_workers=args.tmux_workers,
        backend=args.backend,
        container_image=args.container_image,
        lease=args.lease,
        lease_ttl=args.lease_ttl,
        lease_catalog=args.lease_catalog,
        lease_queue=not args.no_queue,
    )
    print(json.dumps(info, indent=2))


if __name__ == "__main__":
    main()

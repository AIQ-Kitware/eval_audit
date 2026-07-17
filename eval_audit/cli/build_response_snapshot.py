"""eval-audit-build-response-snapshot: freeze candidate responses.

Phase 2 of the open-judge experiment (docs/planning/open-judge-plan.md
§7): convert audited HELM source runs into immutable, content-addressed
response snapshots under ``<snapshot-root>/<response_set_hash>/``.
Every judge arm and replicate later refers to the same hash. Sources
are never modified; rebuilding an existing snapshot is a cache hit.

Example::

    eval-audit-build-response-snapshot \\
        --run-dir /data/crfm-helm-public/.../xstest:model=openai_gpt-oss-20b \\
        --snapshot-root /data/crfm-helm-audit-store/open-judge/response-snapshots
"""

from __future__ import annotations

import argparse
import sys

from eval_audit.infra.logging import setup_cli_logging
from eval_audit.judging.response_snapshot import SnapshotBuildError, build_response_snapshot


def main(argv: list[str] | None = None) -> None:
    setup_cli_logging()
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--run-dir",
        action="append",
        required=True,
        help="Source run directory to snapshot (repeatable).",
    )
    parser.add_argument(
        "--snapshot-root",
        required=True,
        help="Content-addressed snapshot store root.",
    )
    args = parser.parse_args(argv)

    n_failed = 0
    for run_dpath in args.run_dir:
        try:
            result = build_response_snapshot(run_dpath, args.snapshot_root)
        except SnapshotBuildError as ex:
            n_failed += 1
            print(f"FAIL {run_dpath}: {ex}", file=sys.stderr)
            continue
        state = "cache-hit" if result.cache_hit else "built"
        print(f"OK   {run_dpath}\n     -> {result.snapshot_dpath} ({state})")
    if n_failed:
        sys.exit(1)


if __name__ == "__main__":
    main()

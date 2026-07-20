"""eval-audit-schedule-rejudge: fan the rejudge matrix out via kwdagger.

Open-judge-plan Commit 11. The serial bash driver
(``reproduce/open_judge_gpt_oss/50_overnight_run.sh``) leases ONE judge at a
time, so a TP1 judge arm pins a single card and the rest of the host idles.
This builds the same scope — benchmarks x judges x replicates — as a flat job
matrix and hands it to ``kwdagger schedule``, which fans it out; infer-stack's
admission queue then decides what co-hosts.

Defaults to PREVIEW (``--run 0``): it prints the plan and the kwdagger argv so
the fan-out size is a reviewed number before anything is submitted.

Example::

    eval-audit-schedule-rejudge \\
        --snapshot xstest=/data/.../response-snapshots/<hash> \\
        --snapshot wildbench=/data/.../response-snapshots/<hash> \\
        --judge-json configs/open_judge/qwen3_5_9b.json \\
        --judge-json configs/open_judge/qwen3_5_27b.json \\
        --out-root /data/.../results --cache-root /data/.../cache \\
        --sidecar-config /data/.../judge-sidecars \\
        --replicates 0,1,2 --run 1
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys

from eval_audit.infra.logging import setup_cli_logging
from eval_audit.judging.rejudge_matrix import (
    RejudgeMatrixSpec,
    build_rejudge_matrix,
    load_judge_arm,
    summarize_matrix,
)

PIPELINE = "eval_audit.pipelines.rejudge_pipeline.rejudge_pipeline()"


def _parse_replicates(text: str) -> list[int]:
    text = text.strip()
    if not text:
        return []
    return [int(part) for part in text.split(",") if part.strip()]


def _parse_pair(text: str, what: str, allow_empty_value: bool = False) -> tuple[str, str]:
    """Split NAME=VALUE.

    ``allow_empty_value`` is required by the replicate overrides, where an
    EMPTY list is the documented way to skip a pair (``wildbench:judge=``);
    a snapshot path, by contrast, must be non-empty.
    """
    if "=" not in text:
        raise SystemExit(f"--{what} expects NAME=VALUE, got {text!r}")
    name, _, value = text.partition("=")
    if not name.strip():
        raise SystemExit(f"--{what} expects NAME=VALUE, got {text!r}")
    if not value.strip() and not allow_empty_value:
        raise SystemExit(f"--{what} expects a nonempty VALUE, got {text!r}")
    return name.strip(), value.strip()


def main(argv: list[str] | None = None) -> None:
    setup_cli_logging()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--snapshot", action="append", required=True, metavar="BENCHMARK=DPATH",
        help="Response snapshot for a benchmark (repeatable). Order is queue order.",
    )
    parser.add_argument(
        "--judge-json", action="append", required=True, metavar="PATH",
        help="JudgeSpec JSON (repeatable). Order is queue order — small judges first.",
    )
    parser.add_argument("--out-root", required=True)
    parser.add_argument("--cache-root", required=True)
    parser.add_argument("--sidecar-config", required=True)
    parser.add_argument("--experiment-name", default="open-judge")
    parser.add_argument("--parallelism", type=int, default=8)
    parser.add_argument(
        "--max-instances", type=int, default=None,
        help="Judge only the first N instances of each snapshot (a smoke subset).",
    )
    parser.add_argument(
        "--replicates", default="0,1,2",
        help="Default replicate list, comma-separated (default: 0,1,2).",
    )
    parser.add_argument(
        "--replicates-for", action="append", default=[], metavar="BENCHMARK=LIST",
        help="Per-benchmark replicate override, e.g. wildbench=0. Empty list skips it.",
    )
    parser.add_argument(
        "--replicates-for-pair", action="append", default=[],
        metavar="BENCHMARK:JUDGE=LIST",
        help="Per-(benchmark,judge) override, e.g. wildbench:qwen3_5_27b=0.",
    )
    parser.add_argument("--root-dpath", default=None, help="kwdagger root directory.")
    parser.add_argument("--queue-name", default=None)
    parser.add_argument("--backend", default=None)
    parser.add_argument("--tmux-workers", type=int, default=None)
    parser.add_argument("--lease-ttl", default=None)
    parser.add_argument("--lease-timeout", default=None)
    parser.add_argument("--lease-catalog", default=None)
    parser.add_argument(
        "--run", type=int, choices=[0, 1], default=0,
        help="0 to preview the plan + kwdagger argv (default), 1 to submit.",
    )
    args = parser.parse_args(argv)

    snapshots = dict(_parse_pair(s, "snapshot") for s in args.snapshot)
    judges = [load_judge_arm(p) for p in args.judge_json]

    by_benchmark = {}
    for item in args.replicates_for:
        name, value = _parse_pair(item, "replicates-for", allow_empty_value=True)
        by_benchmark[name] = _parse_replicates(value)
    by_pair = {}
    for item in args.replicates_for_pair:
        key, value = _parse_pair(item, "replicates-for-pair", allow_empty_value=True)
        if ":" not in key:
            raise SystemExit(f"--replicates-for-pair expects BENCHMARK:JUDGE=LIST, got {item!r}")
        benchmark, _, judge_id = key.partition(":")
        by_pair[(benchmark.strip(), judge_id.strip())] = _parse_replicates(value)

    spec = RejudgeMatrixSpec(
        snapshots=snapshots,
        judges=judges,
        out_root=args.out_root,
        cache_root=args.cache_root,
        sidecar_config=args.sidecar_config,
        experiment_name=args.experiment_name,
        parallelism=args.parallelism,
        max_instances=args.max_instances,
        replicates_by_benchmark=by_benchmark or {b: _parse_replicates(args.replicates) for b in snapshots},
        replicates_by_pair=by_pair,
    )
    rows = build_rejudge_matrix(spec)
    print(summarize_matrix(rows), file=sys.stderr)

    # Strip the reporting-only labels; kwdagger receives config keys the node declares.
    matrix = [{k: v for k, v in row.items() if not k.startswith("_")} for row in rows]

    argv_out = ["kwdagger", "schedule", "--pipeline", PIPELINE, "--matrix", json.dumps(matrix)]
    for flag, value in (
        ("--root-dpath", args.root_dpath),
        ("--queue-name", args.queue_name),
        ("--backend", args.backend),
        ("--tmux-workers", args.tmux_workers),
    ):
        if value is not None:
            argv_out += [flag, str(value)]
    # Lease knobs are per-job perf params; pass through when given.
    for flag, value in (
        ("lease_ttl", args.lease_ttl),
        ("lease_timeout", args.lease_timeout),
        ("lease_catalog", args.lease_catalog),
    ):
        if value is not None:
            for row in matrix:
                row[flag] = value
    argv_out[argv_out.index("--matrix") + 1] = json.dumps(matrix)

    if not args.run:
        print("# preview only (pass --run 1 to submit):", file=sys.stderr)
        print(" ".join(shlex.quote(a) for a in argv_out))
        return
    raise SystemExit(subprocess.call(argv_out))


if __name__ == "__main__":
    main()

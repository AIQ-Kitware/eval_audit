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
import shlex
import subprocess
import sys
from pathlib import Path

from eval_audit.infra.api import dump_yaml
from eval_audit.infra.logging import setup_cli_logging
from eval_audit.integrations.kwdagger_bridge import (
    KWDaggerRuntime,
    kwdagger_schedule_argv_from_runtime,
)
from eval_audit.judging.rejudge_matrix import (
    RejudgeMatrixSpec,
    build_rejudge_matrix,
    load_judge_arm,
    summarize_matrix,
)

PIPELINE = "eval_audit.pipelines.rejudge_pipeline.rejudge_pipeline()"

#: kwdagger matrix keys are NODE-prefixed (the candidate path uses ``helm.*``);
#: RejudgeNode is named "rejudge".
NODE = "rejudge"


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
    parser.add_argument("--backend", default="tmux")
    parser.add_argument("--tmux-workers", type=int, default=4,
                        help="Concurrent kwdagger workers (default 4: one per GPU).")
    parser.add_argument("--devices", default="0,1,2,3",
                        help="GPUs kwdagger may assign (default 0,1,2,3).")
    # Lease WORLD knobs. A kwdagger job is a fresh login shell and inherits no
    # INFER_STACK_* exports, so without these the acquire resolves infer-stack's
    # default world, cannot find the judge endpoint, and fails as a gating
    # precondition -- which skips the command and leaves NO log.
    parser.add_argument("--lease-catalog", default=None,
                        help="infer-stack catalog.yaml the acquire resolves against.")
    parser.add_argument("--lease-config-dir", default=None,
                        help="INFER_STACK_CONFIG_DIR for the job's acquire/release.")
    parser.add_argument("--lease-data-dir", default=None,
                        help="INFER_STACK_DATA_DIR for the job's acquire/release.")
    parser.add_argument("--lease-ttl", default=None, help="e.g. 6h.")
    parser.add_argument("--lease-timeout", default=None,
                        help="Acquire budget: admission-queue wait + cold load, e.g. 4h.")
    parser.add_argument("--no-skip-existing", action="store_true",
                        help="Recompute jobs whose node output already exists.")
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
        lease_knobs={
            "lease_catalog": args.lease_catalog,
            "lease_config_dir": args.lease_config_dir,
            "lease_data_dir": args.lease_data_dir,
            "lease_ttl": args.lease_ttl,
            "lease_timeout": args.lease_timeout,
        },
    )
    rows = build_rejudge_matrix(spec)
    print(summarize_matrix(rows), file=sys.stderr)

    # kwdagger's zip primitive: ONE submatrix entry per job, node-prefixed.
    # Reporting-only labels (leading underscore) are stripped.
    submatrices = [
        {f"{NODE}.{k}": v for k, v in row.items() if not k.startswith("_")}
        for row in rows
    ]
    params = {"pipeline": PIPELINE, "matrix": {"submatrices": submatrices}}

    root = Path(args.root_dpath or ".").expanduser().resolve()
    runtime = KWDaggerRuntime(
        queue_name=args.queue_name or "open-judge-rejudge",
        root_dpath=root,
        devices=args.devices,
        tmux_workers=args.tmux_workers,
        backend=args.backend,
        run=bool(args.run),
        skip_existing=not args.no_skip_existing,
    )

    if not args.run:
        # Preview keeps params INLINE so the command is readable end-to-end.
        argv_out = kwdagger_schedule_argv_from_runtime(runtime, dump_yaml(params))
        print("# preview only (pass --run 1 to submit):", file=sys.stderr)
        print(" ".join(shlex.quote(a) for a in argv_out))
        return

    # Execution MUST spill params to a .yaml PATH: a large fan-out overflows
    # ARG_MAX as an inline argument (OSError: Argument list too long). The
    # .yaml extension is load-bearing — kwdagger only treats --params as a file
    # when it exists AND carries an extension. Same contract the candidate path
    # documents in kwdagger_bridge.run_kwdagger_schedule.
    root.mkdir(parents=True, exist_ok=True)
    params_fpath = root / "kwdagger_rejudge_params.yaml"
    params_fpath.write_text(dump_yaml(params))
    argv_out = kwdagger_schedule_argv_from_runtime(runtime, str(params_fpath))
    print(f"params: {params_fpath}", file=sys.stderr)
    print(" ".join(shlex.quote(a) for a in argv_out), file=sys.stderr)
    raise SystemExit(subprocess.call(argv_out))


if __name__ == "__main__":
    main()

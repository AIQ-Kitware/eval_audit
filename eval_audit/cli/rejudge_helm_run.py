"""eval-audit-rejudge-helm: apply one judge to one response snapshot.

Phase 7 of the open-judge experiment (docs/planning/open-judge-plan.md
§12): annotation-only execution — no candidate inference, ever. The
judge configuration comes from a JSON file (the JudgeSpec fields; see
eval_audit/judging/specs.py) so runbooks declare judges as data.

Example::

    eval-audit-rejudge-helm \\
        --snapshot /data/.../response-snapshots/<response_set_hash> \\
        --judge-json configs/open_judge/qwen3_5_27b.json \\
        --replicate 0 \\
        --experiment-name gpt-oss-20b-open-judge-v1 \\
        --out-root /data/.../open-judge-results \\
        --cache-root /data/.../open-judge-cache \\
        --sidecar-config /data/.../judge-sidecars
"""

from __future__ import annotations

import argparse
import json

from eval_audit.infra.logging import setup_cli_logging
from eval_audit.judging.rejudge import run_rejudge
from eval_audit.judging.specs import JudgeSpec


def main(argv: list[str] | None = None) -> None:
    setup_cli_logging()
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--snapshot", required=True, help="Response-snapshot directory.")
    parser.add_argument(
        "--judge-json",
        required=True,
        help="JSON file holding the JudgeSpec fields (validated on load).",
    )
    parser.add_argument("--replicate", type=int, default=0)
    parser.add_argument("--experiment-name", default="open-judge")
    parser.add_argument("--out-root", required=True)
    parser.add_argument("--cache-root", required=True)
    parser.add_argument(
        "--sidecar-config",
        action="append",
        default=[],
        help="HELM config dir with judge model/deployment/tokenizer sidecars (repeatable).",
    )
    parser.add_argument("--parallelism", type=int, default=4)
    args = parser.parse_args(argv)

    with open(args.judge_json, "r", encoding="utf-8") as file:
        judge_fields = json.load(file)
    judge_fields.pop("judge_spec_hash", None)  # derived, never trusted from input
    judge = JudgeSpec(**judge_fields)

    result = run_rejudge(
        snapshot_dpath=args.snapshot,
        judge=judge,
        replicate=args.replicate,
        out_root=args.out_root,
        cache_root=args.cache_root,
        experiment_name=args.experiment_name,
        sidecar_config_dpaths=tuple(args.sidecar_config),
        parallelism=args.parallelism,
    )
    state = "cache-hit" if result.cache_hit else "completed"
    print(f"{state}: {result.out_dpath}")
    print(f"attempt_hash: {result.attempt_hash}")
    print(f"response_set_hash: {result.response_set_hash}")


if __name__ == "__main__":
    main()

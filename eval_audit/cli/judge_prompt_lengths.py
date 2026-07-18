"""eval-audit-judge-prompt-lengths: size max_model_len from judge prompts (§14.3).

Render every judge prompt a snapshot would send and report the length
distribution + a recommended ``max_model_len`` (max prompt tokens +
official output budget + margin). No judge request; no GPU. Pass
``--tokenizer <hf-id>`` for exact token counts, else a conservative
chars/token estimate is used (and flagged).

Example::

    eval-audit-judge-prompt-lengths \\
        /data/.../response-snapshots/*/ \\
        --tokenizer Qwen/Qwen3.5-27B \\
        --output /data/.../open-judge/prompt-lengths.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from eval_audit.infra.logging import setup_cli_logging
from eval_audit.judging.prompt_lengths import load_hf_token_counter, measure_prompt_lengths


def main(argv: list[str] | None = None) -> None:
    setup_cli_logging()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("snapshot", nargs="+", help="Response-snapshot directories.")
    parser.add_argument("--tokenizer", default=None, help="HF tokenizer id for exact counts.")
    parser.add_argument("--margin", type=int, default=1024, help="Safety margin tokens.")
    parser.add_argument("--output", default=None, help="JSON report path.")
    args = parser.parse_args(argv)

    counter = None
    if args.tokenizer:
        try:
            counter = load_hf_token_counter(args.tokenizer)
        except Exception as ex:  # noqa: BLE001
            print(f"WARN: could not load tokenizer {args.tokenizer!r}: {ex}; "
                  f"falling back to a chars/token estimate.", file=sys.stderr)

    reports = []
    overall_max = 0
    for snapshot in args.snapshot:
        report = measure_prompt_lengths(
            snapshot, tokenizer=counter, tokenizer_name=args.tokenizer, safety_margin=args.margin
        )
        reports.append(report.as_dict())
        overall_max = max(overall_max, report.recommended_max_model_len)
        est = " (estimated)" if report.token_estimated else ""
        max_tok = report.token_stats.get("max", 0)
        p99 = report.token_stats.get("p99", 0)
        print(
            f"{report.benchmark:<12} n={report.num_prompts:<5} "
            f"max_tok={max_tok:.0f} p99={p99:.0f}{est} "
            f"+budget={report.output_budget} +margin={report.safety_margin} "
            f"=> max_model_len>={report.recommended_max_model_len}"
        )

    print(f"\nrecommended max_model_len (all snapshots): >= {overall_max}")
    if args.output:
        out_fpath = Path(args.output)
        out_fpath.parent.mkdir(parents=True, exist_ok=True)
        with open(out_fpath, "w", encoding="utf-8") as file:
            json.dump(
                {"recommended_max_model_len": overall_max, "reports": reports}, file, indent=2
            )
            file.write("\n")
        print(f"report: {out_fpath}")


if __name__ == "__main__":
    main()

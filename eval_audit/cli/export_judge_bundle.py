"""eval-audit-export-judge-bundle: HELM sidecars for open-weight judges.

Phase 10 of the open-judge experiment (docs/planning/open-judge-plan.md
§15): resolve each JudgeSpec's infer-stack catalog endpoint and emit a
HELM config directory (model_deployments + copied metadata/tokenizer
sidecars + provenance manifest) that the rejudge runner registers. Run
on the serving host after the judge lease is up so the gateway base_url
and LiteLLM master key are the live ones.

Example::

    eval-audit-export-judge-bundle \\
        --judge-json configs/open_judge/qwen3_5_27b.json \\
        --judge-json configs/open_judge/qwen3_6_35b_a3b.json \\
        --config-dir reproduce/open_judge_gpt_oss/config/infer_stack \\
        --out /data/.../open-judge/judge-sidecars
"""

from __future__ import annotations

import argparse
import json
import os

from eval_audit.infra.logging import setup_cli_logging
from eval_audit.integrations.infer_stack.judge_bundle_export import export_judge_bundle
from eval_audit.integrations.infer_stack.serving_facts import LITELLM_AUTH_ENV
from eval_audit.judging.specs import JudgeSpec


def _load_judge(fpath: str) -> JudgeSpec:
    with open(fpath, "r", encoding="utf-8") as file:
        fields = json.load(file)
    fields.pop("judge_spec_hash", None)  # derived, never trusted from input
    return JudgeSpec(**fields)


def main(argv: list[str] | None = None) -> None:
    setup_cli_logging()
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--judge-json", action="append", required=True,
                        help="JudgeSpec JSON file (repeatable).")
    parser.add_argument("--config-dir", default=None,
                        help="infer-stack config dir holding catalog.yaml "
                             "(default: INFER_STACK_CONFIG_DIR).")
    parser.add_argument("--out", required=True, help="Output sidecar directory.")
    parser.add_argument("--base-url", default=None,
                        help="LiteLLM gateway base URL (default: deterministic gateway URL).")
    parser.add_argument("--infer-stack-revision", default=None,
                        help="infer-stack git revision, recorded in the manifest.")
    args = parser.parse_args(argv)

    judges = [_load_judge(fpath) for fpath in args.judge_json]
    # The LiteLLM master key is a runtime env secret (never a flag).
    api_key_value = os.environ.get(LITELLM_AUTH_ENV)

    out_dpath = export_judge_bundle(
        judges,
        out_dpath=args.out,
        config_dir=args.config_dir,
        base_url=args.base_url,
        api_key_value=api_key_value,
        infer_stack_revision=args.infer_stack_revision,
    )
    print(f"judge bundle: {out_dpath}")
    for judge in judges:
        print(f"  {judge.id}: {judge.model} -> {judge.model_deployment}")


if __name__ == "__main__":
    main()

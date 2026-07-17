from __future__ import annotations

import argparse
import json
from pathlib import Path


REQUIRED_FILES = (
    "run_spec.json",
    "stats.json",
    "per_instance_stats.json",
)

# The family map: adapter_spec.model -> the deployment that must have produced
# it (nlstrip = the newline-tolerant completions client, a declared
# substitution). A run claiming any other pairing is dirty.
EXPECTED_DEPLOYMENTS = {
    "qwen/qwen3.5-0.8b-base": "vllm/qwen3.5-0.8b-base-nlstrip-local",
    "qwen/qwen3.5-2b-base": "vllm/qwen3.5-2b-base-nlstrip-local",
    "qwen/qwen3.5-4b-base": "vllm/qwen3.5-4b-base-nlstrip-local",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify a HELM run directory used one of the expected local "
            "Qwen3.5 small-base model/deployment pairs."
        )
    )
    parser.add_argument("run_dir")
    parser.add_argument(
        "--expect-model",
        default=None,
        help="Require this exact model id (default: any family member).",
    )
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir).expanduser().resolve()
    missing = [name for name in REQUIRED_FILES if not (run_dir / name).exists()]
    if missing:
        raise SystemExit(f"Missing expected artifacts in {run_dir}: {', '.join(missing)}")

    run_spec = json.loads((run_dir / "run_spec.json").read_text())
    adapter_spec = run_spec.get("adapter_spec", {})
    model = adapter_spec.get("model")
    deployment = adapter_spec.get("model_deployment")

    if args.expect_model is not None and model != args.expect_model:
        raise SystemExit(f"Expected adapter_spec.model={args.expect_model!r}, found {model!r}")
    if model not in EXPECTED_DEPLOYMENTS:
        raise SystemExit(
            f"adapter_spec.model={model!r} is not a Qwen3.5 small-base family member "
            f"(expected one of {sorted(EXPECTED_DEPLOYMENTS)})"
        )
    expected_deployment = EXPECTED_DEPLOYMENTS[model]
    if deployment != expected_deployment:
        raise SystemExit(
            f"Expected adapter_spec.model_deployment={expected_deployment!r} "
            f"for {model!r}, found {deployment!r}"
        )

    summary = {
        "run_dir": str(run_dir),
        "model": model,
        "model_deployment": deployment,
        "artifacts_verified": list(REQUIRED_FILES),
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

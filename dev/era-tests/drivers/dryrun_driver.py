"""In-container dry-run driver for ladder rungs 2 (instrument fidelity) and 5
(HF-fetch audit).

Runs INSIDE the era image (bind-mounted read-only at /ladder; never installed),
importing the installed ``helm_era_shim`` so the decode + local-config path it
exercises is exactly the one the real replay uses. It then drives the era
``run_benchmarking`` with ``dry_run=True`` — the same thing ``helm-run
--dry-run`` does at both eras: scenario construction (datasets download here)
and adaptation (instance selection + prompt construction happen here) run for
real; no model requests are made, so no GPU, no vLLM, and no credentials are
needed.

Outputs land under ``<out_dpath>/benchmark_output/runs/<suite>/<run name>/``;
rung 2 diffs the produced ``scenario_state.json`` instance identity against the
official artifact (see instance_diff.py), rung 5 only requires this driver to
exit 0 (scenario data fetched + instances constructed).

Usage (inside the container):
    python /ladder/dryrun_driver.py <run_spec.json> <suite> <out_dpath> [max_eval_instances]

The optional ``max_eval_instances`` cap speeds up the rung-5 fetch audit
(dataset download + get_instances still run in full; only adaptation is
trimmed). Rung 2 MUST NOT pass it — capping changes instance selection, the
very thing rung 2 verifies.

stdlib + era helm + helm_era_shim only. Prints ``DRYRUN_OK <run dir>`` last.
"""
from __future__ import annotations

import dataclasses
import os
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        print(__doc__, file=sys.stderr)
        return 2
    spec_path, suite, out = Path(argv[0]), argv[1], Path(argv[2])
    cap = int(argv[3]) if len(argv) == 4 else None
    out.mkdir(parents=True, exist_ok=True)

    # The shim's own strict decode (dacite strict=True = the era-drift detector)
    # and local-config preparation — rung 2/5 must validate the REAL path.
    from helm_era_shim.replay import _decode_era_run_spec, _prepare_local_helm_config

    run_spec = _decode_era_run_spec(spec_path)
    if cap is not None:
        run_spec = dataclasses.replace(
            run_spec,
            adapter_spec=dataclasses.replace(run_spec.adapter_spec, max_eval_instances=cap),
        )
    prepared = _prepare_local_helm_config(
        out_dpath=out,
        local_path="prod_env",
        model_deployments_fpath=None,
        model_name=run_spec.adapter_spec.model,
    )

    from helm.benchmark.run import run_benchmarking
    from helm.common.authentication import Authentication

    output_path = out / "benchmark_output"
    output_path.mkdir(exist_ok=True)
    run_benchmarking(
        run_specs=[run_spec],
        auth=Authentication(""),
        url=None,
        local_path=os.fspath(prepared),
        num_threads=1,
        output_path=os.fspath(output_path),
        suite=suite,
        dry_run=True,  # <- the only difference from the real replay
        skip_instances=False,
        cache_instances=False,
        cache_instances_only=False,
        skip_completed_runs=False,
        exit_on_error=True,
        runner_class_name=None,
    )

    run_dir = output_path / "runs" / suite / run_spec.name.replace(os.path.sep, "_")
    print(f"DRYRUN_OK {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

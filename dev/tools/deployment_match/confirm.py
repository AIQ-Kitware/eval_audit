"""Phase 3 — confirm the winning cell against the official run.

The direct probe over a small sample *ranks* deployments cheaply; this step
*confirms* the winner at full scale, the authoritative way: produce a full local
from-spec HELM run served with the winning recipe, then compare it to the
official run with the audit's own ``build_pair_report`` (the same
``eval-audit-compare-pair`` metric-level comparison used everywhere else).

Two halves:

* **plan** (always): emit a one-endpoint ``catalog.yaml`` for the winning
  serve-recipe + a ``confirm_plan.md`` with the serve / from-spec-run /
  compare-pair commands. This is what an operator runs on a GPU host.
* **compare** (when ``--local-run`` is given): run ``build_pair_report`` on
  (official, local) and write ``pair_report.{json,txt}`` — CPU-only, no GPU.

If the winner relies on a NON-default request-time knob (``add_special_tokens
=False``) the plan says so loudly: a normal HELM run won't send it, so it must be
landed via the serve-time tokenizer override or a ``VLLMClient`` change.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


def _winner_catalog(best: dict[str, Any]) -> dict[str, Any]:
    """A one-endpoint infer-stack catalog for the winning serve-recipe."""
    endpoint = str(best.get("winner_cell", "winner")).split("::", 1)[0]
    serve = best.get("serve_time_knobs", {}) or {}
    native = (best.get("request_time_knobs", {}) or {}).get("native", {}) or {}
    protocol = native.get("protocol", "completions")
    runtime: dict[str, Any] = {
        "max_model_len": serve.get("max_model_len", 2048),
        "gpu_memory_utilization": 0.85,
        "max_num_batched_tokens": 2048,
        "max_num_seqs": 16,
        "extra_args": serve.get("extra_args") or [],
    }
    if serve.get("trust_remote_code"):
        runtime["trust_remote_code"] = True
    return {
        "models": {"target": {"source": f"hf://{serve.get('hf_source')}"}},
        "endpoints": {endpoint: {"engine": "vllm", "reclaim": "stop",
                                 "model": "target", "protocol": protocol,
                                 "runtime": runtime}},
        "bundles": {},
    }


def _plan_markdown(best: dict[str, Any], official_run: str, out_dir: Path) -> str:
    endpoint = str(best.get("winner_cell", "winner")).split("::", 1)[0]
    probe_only = (best.get("request_time_knobs", {}) or {}).get("probe_only", {}) or {}
    serve = best.get("serve_time_knobs", {}) or {}
    lines = [
        "# Confirm the winning deployment against the official run",
        "",
        f"- winner cell: `{best.get('winner_cell')}`  (composite {best.get('composite')})",
        f"- serve-time knobs: `{serve.get('extra_args')}`  source `{serve.get('hf_source')}`",
        f"- official run: `{official_run}`",
        "",
        "## 1. Serve the winning recipe (GPU host)",
        "```bash",
        f"export INFER_STACK_CONFIG_DIR={out_dir}/serve",
        f"infer-stack acquire {endpoint} --yes --env-file /tmp/confirm.env",
        "```",
        "",
        "## 2. Produce a full local from-spec run against it",
        "Replay the official run_spec verbatim through the served endpoint using the",
        "existing pipeline (see reproduce/olmo_models_combined for the exact runbook):",
        "```bash",
        "eval-audit-make-manifest ...            # from the official run_spec (from-spec)",
        f"eval-audit-run <manifest> --lease --lease-catalog {out_dir}/serve/catalog.yaml \\",
        f"    --container-image <runner-image>   # serves {endpoint}, records a local run dir",
        "```",
        "",
        "## 3. Compare local vs official (authoritative)",
        "```bash",
        f"{Path(__file__).name.replace('confirm.py', 'cli.py')} confirm \\",
        f"    --best {out_dir}/best_deployment.yaml --run {official_run} \\",
        "    --local-run <local_run_dir> --out <out>",
        "# (equivalently: eval-audit-compare-pair --run-a <official> --run-b <local> --report-dpath <out>)",
        "```",
    ]
    if probe_only:
        lines += [
            "",
            "## ⚠️ Winner uses a probe-only knob",
            f"The winner needs `{probe_only}`, which a normal HELM run does NOT send.",
            "Before step 2, land it the HELM-path-native way — prefer the serve-time",
            "`--tokenizer <sibling>` override (gateway-proof, as OLMo did in 74ba33d),",
            "or patch `helm.clients.vllm_client.VLLMClient` to set it. Otherwise the",
            "full run won't reproduce the probe result.",
        ]
    return "\n".join(lines) + "\n"


def confirm(best_path: str | Path, official_run: str | Path, out_dir: str | Path,
            *, local_run: str | Path | None = None) -> dict[str, Any]:
    best = yaml.safe_load(Path(best_path).read_text())
    out_dir = Path(out_dir)
    (out_dir / "serve").mkdir(parents=True, exist_ok=True)

    catalog = _winner_catalog(best)
    (out_dir / "serve" / "catalog.yaml").write_text(yaml.safe_dump(catalog, sort_keys=False))
    (out_dir / "serve" / "settings.yaml").write_text(
        "backend: compose\nlitellm: true\nui: false\n"
        "skip_display_gpus: false\nreverse_proxy: false\n")
    plan = _plan_markdown(best, str(official_run), out_dir)
    (out_dir / "confirm_plan.md").write_text(plan)

    result: dict[str, Any] = {"winner_cell": best.get("winner_cell"),
                              "plan": str(out_dir / "confirm_plan.md"),
                              "serve_catalog": str(out_dir / "serve" / "catalog.yaml")}

    if local_run is not None:
        report = _compare_pair(official_run, local_run, best, out_dir)
        result["pair_report"] = report
    return result


def _compare_pair(official_run: str | Path, local_run: str | Path,
                  best: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    """Run the audit's metric-level pair comparison (CPU; no GPU)."""
    from eval_audit.reports.pair_report import build_pair_report, write_text_report

    report = build_pair_report(
        run_a=str(official_run), run_b=str(local_run),
        label_a="official", label_b="local",
        display_label_a="official", display_label_b=str(best.get("winner_cell", "local")))
    json_path = out_dir / "pair_report.json"
    txt_path = out_dir / "pair_report.txt"
    try:
        import kwutil
        json_path.write_text(json.dumps(kwutil.Json.ensure_serializable(report),
                                        indent=2, ensure_ascii=False))
    except Exception:  # noqa: BLE001 - kwutil optional; fall back to a plain dump
        json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    write_text_report(report, txt_path)

    strict = (report.get("strict_summary", {}) or {})
    diag = (strict.get("diagnosis", {}) or {})
    overall = (strict.get("value_agreement", {}) or {}).get("overall", {}) or {}
    return {"json": str(json_path), "txt": str(txt_path),
            "diagnosis_label": diag.get("label"),
            "run_level_agree_ratio": overall.get("agree_ratio")}

"""Failure analysis: classify failed jobs from logs and row metadata.

Split out of ``eval_audit.workflows.build_reports_summary`` on
2026-06-11 (Phase 2 of docs/planning/repo-refactor-plan.md). Pure
relocation: function bodies are unchanged.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from eval_audit.reports.summary.common import _normalize_text


def _read_log_tail(job_dpath: Path, max_chars: int = 40000) -> str:
    """Concatenate the tails of ``helm-run.log`` and the wrapped command's
    stderr/stdout so the failure classifier sees errors raised both inside
    HELM (logged) and outside HELM (caught only by the parent shell — e.g.
    TypeError from ``run_spec_function(**args)`` before HELM's logger is up).
    """
    parts: list[str] = []
    for name in ("helm-run.log", "cmd_stderr.txt", "cmd_stdout.txt"):
        fpath = job_dpath / name
        if not fpath.exists():
            continue
        try:
            text = fpath.read_text(errors="ignore")
        except Exception:
            continue
        if not text:
            continue
        parts.append(text[-max_chars:])
    if not parts:
        return ""
    combined = "\n".join(parts)
    return combined[-max_chars:]


def _classify_failure(job_dpath: Path, row: dict[str, Any]) -> dict[str, Any]:
    log_tail = _read_log_tail(job_dpath)
    text = _normalize_text(log_tail)
    status = _normalize_text(row.get("status"))

    checks: list[tuple[str, str, list[str]]] = [
        (
            # Deliberate policy decision: by default we do not opt into
            # arbitrary code execution from third-party HuggingFace dataset
            # repositories. A future opt-in mechanism (per-scenario allow-list,
            # an ``--allow-arbitrary-code-execution`` knob, or similar) could
            # promote this from "blocked" to "permitted for X". For now,
            # surface as a known/expected blocker rather than an unknown
            # failure. Affected example: ``ought/raft``.
            "trust_remote_code_required",
            "dataset requires arbitrary code execution (trust_remote_code=True); blocked by current policy",
            [
                "contains custom code which must be executed",
                "trust_remote_code=true",
                "pass the argument `trust_remote_code=true`",
            ],
        ),
        (
            # Caught by the parent-shell stderr capture (cmd_stderr.txt)
            # because the TypeError fires before helm-run's own logger is up.
            # Reconstruction in eval_audit/helm/run_entries.py prevents new
            # occurrences; this rule classifies any historical failures.
            "malformed_run_entry",
            "run_entry passed kwargs the run_spec_function does not accept",
            [
                "got an unexpected keyword argument",
                "did you mean",
                "unknown run spec name",
            ],
        ),
        (
            "missing_openai_annotation_credentials",
            "run depends on OpenAI-backed annotation but no API key was configured",
            ["openai_api_key", "annotationexecutorerror", "api_key client option must be set"],
        ),
        (
            "missing_math_dataset",
            "required math dataset was not available in the environment",
            ["hendrycks/competition_math", "couldn't find 'hendrycks/competition_math'"],
        ),
        (
            "gated_dataset_access",
            "dataset exists but requires gated access credentials or approval",
            ["gated dataset on the hub", "ask for access", "datasetnotfounderror: dataset"],
        ),
        (
            "remote_dataset_download_failure",
            "dataset download failed from a remote source",
            ["failed with exit code 8: wget", "wget https://", "curl: ", "temporary failure in name resolution"],
        ),
        (
            "gpu_memory_or_cuda_failure",
            "job hit a CUDA or GPU-memory related failure",
            ["cuda out of memory", "outofmemoryerror", "cublas", "cuda error"],
        ),
        (
            "process_killed_or_resource_exhausted",
            "process looks to have been killed by the host or scheduler",
            ["killed", "exit code 137", "sigkill"],
        ),
        (
            "network_or_remote_service_failure",
            "remote service or network interaction failed",
            # P1-11: anchor the 429 signal — a bare "429" matched scores/ids in
            # unrelated log lines.
            ["connectionerror", "readtimeout", "maxretryerror", "429 too many requests", "http 429", "503 service unavailable"],
        ),
        (
            "filesystem_permission_failure",
            "filesystem permissions blocked the run",
            ["permission denied"],
        ),
        (
            "interrupted_run",
            "run was interrupted before completion",
            ["keyboardinterrupt", "cancellederror", "interrupted"],
        ),
        (
            # P1-11: the GENERIC file-not-found rule is checked LAST so specific,
            # higher-confidence signals (CUDA-OOM, killed, gated/remote dataset)
            # win — a CUDA-OOM traceback that also contains "no such file or
            # directory" must not be mislabelled a missing-dataset failure.
            "missing_dataset_or_cached_artifact",
            "required dataset or cached artifact was not available",
            ["filenotfounderror", "couldn't find", "no such file or directory"],
        ),
    ]

    for label, summary, patterns in checks:
        matched = [pat for pat in patterns if pat in text]
        if matched:
            return {
                "failure_reason": label,
                "failure_summary": summary,
                "failure_confidence": "heuristic_pattern_match",
                "matched_patterns": matched,
                "log_excerpt": log_tail[-2000:] if log_tail else None,
            }

    if status in {"running", "queued"}:
        return {
            "failure_reason": "not_finished_yet",
            "failure_summary": "job appears to be queued or still running",
            "failure_confidence": "status_only",
            "matched_patterns": [],
            "log_excerpt": log_tail[-2000:] if log_tail else None,
        }

    if not log_tail:
        return {
            "failure_reason": "missing_runtime_log",
            "failure_summary": "no runtime log was found for this job",
            "failure_confidence": "missing_evidence",
            "matched_patterns": [],
            "log_excerpt": None,
        }

    if "traceback" not in text and status in {"", "unknown", "computed", "reused"}:
        return {
            "failure_reason": "truncated_or_incomplete_runtime",
            "failure_summary": "job lacks complete run artifacts and the runtime log ends without a clear terminal exception",
            "failure_confidence": "weak_inference",
            "matched_patterns": [],
            "log_excerpt": log_tail[-2000:] if log_tail else None,
        }

    return {
        "failure_reason": "unknown_failure",
        "failure_summary": "no current rule explains this failure; manual drill-down recommended",
        "failure_confidence": "unknown",
        "matched_patterns": [],
        "log_excerpt": log_tail[-2000:] if log_tail else None,
    }
_FAILURE_CATEGORIES: dict[str, tuple[str, str]] = {
    # failure_reason -> (category_key, category_label)
    # P1-8: truncated_or_incomplete_runtime carries NO hardware evidence — it
    # is a weak inference from missing artifacts + a log ending without a
    # terminal exception. Do not chart it as "Hardware / Compute Timeout".
    "truncated_or_incomplete_runtime": ("incomplete_runtime", "Incomplete / Truncated Runtime"),
    "remote_dataset_download_failure": ("data_access", "Data Access Barrier"),
    "gated_dataset_access": ("data_access", "Data Access Barrier"),
    "missing_dataset_or_cached_artifact": ("data_access", "Data Access Barrier"),
    "missing_math_dataset": ("missing_infrastructure", "Missing Special Infrastructure"),
    "missing_openai_annotation_credentials": ("missing_infrastructure", "Missing Special Infrastructure"),
    "trust_remote_code_required": ("policy_blocked", "Policy Blocked (opt-in)"),
    "malformed_run_entry": ("recipe_error", "Recipe / Configuration Error"),
    # P1-8: positively identified infrastructure failures — previously
    # unmapped, so they charted as "Unknown / Other".
    "gpu_memory_or_cuda_failure": ("compute_resource", "GPU / Compute Resource"),
    "process_killed_or_resource_exhausted": ("compute_resource", "GPU / Compute Resource"),
    "network_or_remote_service_failure": ("network", "Network / Remote Service"),
    "filesystem_permission_failure": ("permissions", "Filesystem / Permissions"),
    "interrupted_run": ("interrupted", "Interrupted / Cancelled"),
    "not_finished_yet": ("unknown", "Unknown / Other"),
    "missing_runtime_log": ("unknown", "Unknown / Other"),
    "unknown_failure": ("unknown", "Unknown / Other"),
}
_FAILURE_CATEGORY_ORDER = [
    "incomplete_runtime",
    "compute_resource",
    "data_access",
    "network",
    "permissions",
    "missing_infrastructure",
    "policy_blocked",
    "recipe_error",
    "interrupted",
    "unknown",
]
_FAILURE_CATEGORY_LABELS = {
    "incomplete_runtime": "Incomplete / Truncated Runtime",
    "compute_resource": "GPU / Compute Resource",
    "data_access": "Data Access Barrier",
    "network": "Network / Remote Service",
    "permissions": "Filesystem / Permissions",
    "missing_infrastructure": "Missing Special Infrastructure",
    "policy_blocked": "Policy Blocked (opt-in)",
    "recipe_error": "Recipe / Configuration Error",
    "interrupted": "Interrupted / Cancelled",
    "unknown": "Unknown / Other",
}

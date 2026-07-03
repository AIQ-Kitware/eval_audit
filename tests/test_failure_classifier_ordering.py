"""P1-11 regression: failure-classifier rules must be anchored + ordered
most-specific-first, so a generic file-not-found does not shadow a CUDA-OOM and
a bare '429' in an unrelated log line does not read as a network failure."""
from __future__ import annotations

from pathlib import Path

from eval_audit.reports.summary.failure_triage import _classify_failure


def _classify(tmp_path: Path, log: str) -> str:
    (tmp_path / "helm-run.log").write_text(log)
    return _classify_failure(tmp_path, {"status": "failed"})["failure_reason"]


def test_cuda_oom_wins_over_generic_file_not_found(tmp_path):
    # A CUDA-OOM traceback commonly also contains a "No such file or directory"
    # line; the specific signal must win.
    log = (
        "Traceback (most recent call last):\n"
        "  ... FileNotFoundError: [Errno 2] No such file or directory: '/x'\n"
        "torch.cuda.OutOfMemoryError: CUDA out of memory. Tried to allocate ...\n"
    )
    assert _classify(tmp_path, log) == "gpu_memory_or_cuda_failure"


def test_bare_429_in_unrelated_line_is_not_a_network_failure(tmp_path):
    # A metric/id value containing 429 must not trip the network rule.
    log = "INFO computed exact_match=0.429 for sample id=id-4290\nall done\n"
    reason = _classify(tmp_path, log)
    assert reason != "network_or_remote_service_failure"


def test_anchored_429_still_detects_real_rate_limit(tmp_path):
    log = "requests.exceptions.HTTPError: 429 Too Many Requests for url ...\n"
    assert _classify(tmp_path, log) == "network_or_remote_service_failure"


def test_plain_missing_dataset_still_classified(tmp_path):
    log = "datasets FileNotFoundError: Couldn't find file at https://.../data.json\n"
    assert _classify(tmp_path, log) == "missing_dataset_or_cached_artifact"

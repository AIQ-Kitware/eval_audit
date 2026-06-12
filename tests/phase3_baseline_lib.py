"""Shared harness for the Phase 3 behavior-equivalence baseline.

Implements the capture/compare loop from
docs/planning/phase3-behavior-equivalence-matrix.md §8 for the fixture
cases that exist on disk today:

- **F3** — EEE-only pair, no recipe facts (facts collapse to unknown).
- **F4** — EEE-only pair with a sidecar ``run_spec.json`` on both sides.

Each case runs ``eval-audit-compare-pair-eee`` on the committed
``eee_only_demo`` fixture and reduces the result to a normalized,
machine-independent snapshot of ``core_metric_report.json`` +
``warnings.json``. Volatile content is substituted, not dropped:

- absolute paths (repo checkout, staging dirs, out dirs) → placeholders
- the 12-char path-derived hash embedded in component/job/packet ids
  (``from_eee._stable_short_hash(str(artifact_dir))``) → placeholders
- ``generated_utc`` values → ``<UTC>``

The committed snapshots under ``tests/fixtures/phase3_baseline/`` are
the gate every Phase 3 sub-stage re-runs against (the matrix's golden
rule). Capture once via ``capture_baseline.py``; compare via
``tests/test_phase3_baseline.py``.

The fixture's instance-source policy axis is degenerate today: the
demo artifacts have no HELM origin, so ``eee-only`` and
``helm-preferred`` coincide. The F6 probe fixture (sub-stage 4.5)
introduces the split.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "eee_only_demo" / "eee_artifacts"
BASELINE_DIR = REPO_ROOT / "tests" / "fixtures" / "phase3_baseline"

OFFICIAL_DIR = FIXTURE_ROOT / "official" / "imdb" / "toy" / "m1-small"
LOCAL_DIR = FIXTURE_ROOT / "local" / "primary" / "imdb" / "toy" / "m1-small"

CASES = ("f3_no_sidecar", "f4_with_sidecar")

#: Same sidecar payload as tests/test_compare_pair_eee.py so the two
#: suites pin the same F4 behavior.
SIDECAR_RUN_SPEC = {
    "name": "imdb:model=toy/m1-small,suite=eee_demo",
    "adapter_spec": {
        "model": "toy/m1-small",
        "model_deployment": "huggingface/toy-m1-small",
        "max_eval_instances": 4,
        "instructions": "Predict the sentiment of this review.",
    },
    "scenario_spec": {"class_name": "helm.IMDBScenario"},
}


def stage_case(case: str, work_dir: Path) -> tuple[Path, Path]:
    """Return (official_dir, local_dir) inputs for a case."""
    if case == "f3_no_sidecar":
        return OFFICIAL_DIR, LOCAL_DIR
    if case == "f4_with_sidecar":
        staging = work_dir / "staging"
        official_dst = staging / "official"
        local_dst = staging / "local"
        shutil.copytree(OFFICIAL_DIR, official_dst)
        shutil.copytree(LOCAL_DIR, local_dst)
        payload = json.dumps(SIDECAR_RUN_SPEC) + "\n"
        (official_dst / "run_spec.json").write_text(payload)
        (local_dst / "run_spec.json").write_text(payload)
        return official_dst, local_dst
    raise ValueError(f"unknown case {case!r}")


def run_case(case: str, work_dir: Path) -> dict[str, Any]:
    """Run compare-pair-eee for a case and return its normalized snapshot."""
    official_dir, local_dir = stage_case(case, work_dir)
    out_dir = work_dir / "out"
    cmd = [
        sys.executable, "-m", "eval_audit.cli.compare_pair_eee",
        "--official", str(official_dir),
        "--local", str(local_dir),
        "--out-dpath", str(out_dir),
        "--clean",
    ]
    subprocess.run(cmd, check=True, cwd=REPO_ROOT)
    return snapshot_outputs(out_dir, official_dir=official_dir, local_dir=local_dir)


def _path_hash_replacements(official_dir: Path, local_dir: Path) -> list[tuple[str, str]]:
    from eval_audit.normalized.eee_sources import stable_short_hash as _stable_short_hash

    return [
        (_stable_short_hash(str(official_dir)), "<OFFICIAL-HASH>"),
        (_stable_short_hash(str(local_dir)), "<LOCAL-HASH>"),
    ]


def _normalize(obj: Any, replacements: list[tuple[str, str]]) -> Any:
    if isinstance(obj, dict):
        # Component/job/packet ids embed the path-derived hash and are
        # used as dict KEYS (component_metadata, run_diagnostics,
        # artifact_formats), so keys get the same substitution as
        # values.
        return {
            _normalize(key, replacements): (
                "<UTC>" if key == "generated_utc" else _normalize(value, replacements)
            )
            for key, value in obj.items()
        }
    if isinstance(obj, list):
        return [_normalize(value, replacements) for value in obj]
    if isinstance(obj, str):
        for old, new in replacements:
            obj = obj.replace(old, new)
        return obj
    return obj


def snapshot_outputs(out_dir: Path, *, official_dir: Path, local_dir: Path) -> dict[str, Any]:
    replacements = [
        # Longest-first so dir prefixes don't shadow each other.
        (str(official_dir), "<OFFICIAL-DIR>"),
        (str(local_dir), "<LOCAL-DIR>"),
        (str(out_dir), "<OUT>"),
        (str(REPO_ROOT), "<REPO>"),
        *_path_hash_replacements(official_dir, local_dir),
    ]
    report = json.loads((out_dir / "core_metric_report.json").read_text())
    warnings = json.loads((out_dir / "warnings.json").read_text())
    return {
        "core_metric_report": _normalize(report, replacements),
        "warnings": _normalize(warnings, replacements),
    }


def baseline_fpath(case: str) -> Path:
    return BASELINE_DIR / f"{case}.json"


def load_baseline(case: str) -> dict[str, Any]:
    return json.loads(baseline_fpath(case).read_text())


def write_baseline(case: str, snapshot: dict[str, Any]) -> Path:
    fpath = baseline_fpath(case)
    fpath.parent.mkdir(parents=True, exist_ok=True)
    fpath.write_text(json.dumps(snapshot, indent=1, sort_keys=True) + "\n")
    return fpath

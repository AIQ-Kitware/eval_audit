"""Shared harness for the Phase 3 behavior-equivalence baseline.

Implements the capture/compare loop from
docs/planning/phase3-behavior-equivalence-matrix.md §8 for the fixture
cases that exist on disk today. Two families:

**EEE cells** (``eval-audit-compare-pair-eee`` on ``eee_only_demo``):

- **F3** — EEE-only pair, no recipe facts (facts collapse to unknown).
- **F4** — EEE-only pair with a sidecar ``run_spec.json`` on both sides.

**HELM cells** (``core_metrics`` via components/comparisons manifests,
driven over the committed HELM run fixture in the ``every_eval_ever``
submodule — the same one ``test_normalized_compare.py`` loads through
the full HELM->EEE conversion):

- **F1** — HELM self-compare (official vs itself) → clean diagnosis,
  strict agreement 1.0 across the whole abs_tol sweep.
- **F2** — HELM official vs a *drifted* local (deterministic numeric
  perturbation) → drift regime, ``core_metric_drift`` diagnosis.

Both families reduce their result to a normalized, machine-independent
snapshot of ``core_metric_report.json`` + ``warnings.json``. Volatile
content is substituted, not dropped:

- absolute paths (repo checkout, staging/run dirs, out dirs) →
  placeholders
- the 12-char path-derived hash embedded in EEE component/job/packet
  ids (``from_eee._stable_short_hash(str(artifact_dir))``) →
  placeholders (EEE cells only; HELM ids come from the manifest we
  author, so they carry no path hash)
- ``generated_utc`` values → ``<UTC>``

The HELM cells add no new volatile fields beyond paths + ``generated_utc``:
the HELM->EEE conversion cache lands outside the report and its uuids
never reach ``core_metric_report.json`` (component ids are authored in
the manifest; instance ids come from HELM ``instance_id``, which is
content). run_spec hashes are *content* and are kept verbatim.

The committed snapshots under ``tests/fixtures/phase3_baseline/`` are
the gate every Phase 3 sub-stage re-runs against (the matrix's golden
rule). Capture once via ``capture_baseline.py``; compare via
``tests/test_phase3_baseline.py``.

**Matrix cells still missing** (honest inventory — see A4 gate-prep,
docs/planning/repo-simplification-plan-2026-07-12.md): **F8** (mixed
HELM×EEE packet, matrix "build for 4.3/4.6") has no on-disk fixture —
no HELM run and EEE artifact share a logical run key, so a mixed packet
cannot be assembled from existing fixtures without *inventing* a new
coordinated one. F8 is intentionally NOT captured here; capturing it
requires first building that fixture (matrix §7, extend
``build_fixture.py``). F5/F6/F9/F10 are likewise spec/future cells.

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
# Shared EEE-demo fixture path + guard (IM-11). ``conftest`` is importable
# whenever tests/ is on sys.path — the same condition under which this library
# module is imported (bare ``from phase3_baseline_lib import ...``).
from conftest import (  # noqa: E402, F401
    # FIXTURE_ROOT is a re-export: test_phase3_baseline imports it from
    # this lib (F401 can't see re-export intent).
    EEE_DEMO_ROOT as FIXTURE_ROOT,
    EEE_DEMO_OFFICIAL_DIR as OFFICIAL_DIR,
    EEE_DEMO_LOCAL_DIR as LOCAL_DIR,
)
BASELINE_DIR = REPO_ROOT / "tests" / "fixtures" / "phase3_baseline"

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


#: Values that legitimately differ between two runs of the same code on the
#: same inputs, redacted by key so the baseline pins behavior rather than the
#: build. ``git_sha`` and the comparison ``digest`` that folds it in would
#: otherwise change on every commit, and a gate that must be re-captured every
#: commit teaches you to re-capture without reading the diff. The *component*
#: digests are deliberately NOT redacted: they hash committed fixture content,
#: so pinning them makes the baseline notice a fixture changing underneath it.
#: Digest construction itself is covered directly by tests/test_digests.py.
_VOLATILE_KEYS = {
    "generated_utc": "<UTC>",
    "git_sha": "<GIT-SHA>",
    "digest": "<DIGEST>",
}


def _normalize(obj: Any, replacements: list[tuple[str, str]]) -> Any:
    if isinstance(obj, dict):
        # Component/job/packet ids embed the path-derived hash and are
        # used as dict KEYS (component_metadata, run_diagnostics,
        # artifact_formats), so keys get the same substitution as
        # values.
        return {
            _normalize(key, replacements): (
                _VOLATILE_KEYS[key]
                if key in _VOLATILE_KEYS
                else _normalize(value, replacements)
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


# ---------------------------------------------------------------------------
# HELM cells (F1 self-compare, F2 official-vs-drifted-local)
# ---------------------------------------------------------------------------

#: Committed HELM run fixture (every_eval_ever submodule). Same run
#: test_normalized_compare.py drives through the HELM->EEE conversion.
HELM_FIXTURE_RUN = (
    REPO_ROOT
    / "submodules"
    / "every_eval_ever"
    / "tests"
    / "data"
    / "helm"
    / "mmlu:subject=philosophy,method=multiple_choice_joint,model=openai_gpt2"
)

HELM_CASES = ("f1_helm_self", "f2_helm_drift")


def helm_fixture_available() -> bool:
    """True when the submodule HELM fixture is present (checked out)."""
    return HELM_FIXTURE_RUN.exists()


def _apply_drift(local_run: Path) -> None:
    """Introduce real, deterministic numeric drift into the LOCAL copy.

    Makes F2 exercise the drift regime (agreement dips below 1.0 mid-sweep
    and recovers as abs_tol grows), not the exact-match regime:

    * instance-level: flip the base ``exact_match`` mean 0->1 on the first
      three per-instance bundles that carry it.
    * run-level: bump the base (unperturbed) ``exact_match`` /
      ``quasi_exact_match`` test-split aggregate mean to a distinct value.

    Deterministic: same inputs, same edits, byte-stable JSON.
    """
    pis_path = local_run / "per_instance_stats.json"
    per_instance = json.loads(pis_path.read_text())
    flipped = 0
    for bundle in per_instance:
        if flipped >= 3:
            break
        for stat in bundle.get("stats", []):
            name = stat.get("name", {})
            if name.get("name") == "exact_match" and "perturbation" not in name:
                stat["sum"] = stat["min"] = stat["max"] = stat["mean"] = 1.0
                stat["sum_squared"] = 1.0
                flipped += 1
                break
    pis_path.write_text(json.dumps(per_instance) + "\n")

    stats_path = local_run / "stats.json"
    stats = json.loads(stats_path.read_text())
    for stat in stats:
        name = stat.get("name", {})
        if (
            name.get("name") in ("exact_match", "quasi_exact_match")
            and name.get("split") == "test"
            and "perturbation" not in name
        ):
            stat["sum"] = stat["min"] = stat["max"] = stat["mean"] = 0.5
            stat["sum_squared"] = 0.25
    stats_path.write_text(json.dumps(stats) + "\n")


def stage_helm_case(case: str, work_dir: Path) -> tuple[Path, Path]:
    """Stage (official, local) HELM run dirs for a case under ``work_dir``.

    Copies the read-only submodule fixture into ``work_dir`` so the
    HELM->EEE conversion cache and any drift edits stay in the tempdir.
    """
    if case not in HELM_CASES:
        raise ValueError(f"unknown HELM case {case!r}")
    runs = work_dir / "runs"
    official = runs / "official"
    local = runs / "local"
    shutil.copytree(HELM_FIXTURE_RUN, official)
    shutil.copytree(HELM_FIXTURE_RUN, local)
    if case == "f2_helm_drift":
        _apply_drift(local)
    return official, local


def _helm_component(
    component_id: str,
    run_path: Path,
    *,
    source_kind: str,
    tag: str,
    display_name: str,
    attempt_uuid: str | None = None,
    machine_host: str | None = None,
) -> dict[str, Any]:
    return {
        "component_id": component_id,
        "run_path": str(run_path),
        "job_path": None,
        "source_kind": source_kind,
        "tags": [tag],
        "display_name": display_name,
        "attempt_uuid": attempt_uuid,
        "attempt_identity": attempt_uuid,
        "machine_host": machine_host,
        "experiment_name": "phase3-helm",
        "max_eval_instances": 10,
    }


#: Faithful comparability facts for the fixture (run_spec.json is
#: identical on both sides in both cells, so every fact is ``yes``; the
#: F2 drift lives in the metric values, i.e. a same-recipe reproducibility
#: drift, which is exactly what the ``core_metric_drift`` diagnosis pins).
_HELM_FACTS = {
    "same_base_model": {"status": "yes", "values": ["openai/gpt2"]},
    "same_scenario_class": {
        "status": "yes",
        "values": ["helm.benchmark.scenarios.mmlu_scenario.MMLUScenario"],
    },
    "same_deployment": {"status": "yes", "values": ["huggingface/gpt2"]},
    "same_adapter_instructions": {"status": "yes", "values": ["<instructions>"]},
    "same_max_eval_instances": {"status": "yes", "values": [10]},
}


def run_helm_case(case: str, work_dir: Path) -> dict[str, Any]:
    """Run core_metrics for a HELM case and return its normalized snapshot."""
    official, local = stage_helm_case(case, work_dir)
    out_dir = work_dir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    run_entry = "mmlu:subject=philosophy"
    components_manifest = {
        "report_dpath": str(out_dir),
        "packet_id": f"phase3-{case}",
        "run_entry": run_entry,
        "experiment_name": "phase3-helm",
        "planner_version": "phase3.baseline",
        "selected_public_track": "main",
        "warnings": [],
        "caveats": [],
        "official_selection": {
            "policy_name": "phase3_baseline",
            "selected_public_track": "main",
            "retained_component_ids": ["official-run"],
            "discarded_component_ids": [],
            "warnings": [],
        },
        "comparability_facts": _HELM_FACTS,
        "components": [
            _helm_component(
                "official-run",
                official,
                source_kind="official",
                tag="official",
                display_name="official: mmlu",
            ),
            _helm_component(
                "local-attempt-a",
                local,
                source_kind="local",
                tag="local",
                display_name="local 1: mmlu",
                attempt_uuid="attempt-a",
                machine_host="host-a",
            ),
        ],
    }
    comparisons_manifest = {
        "report_dpath": str(out_dir),
        "run_entry": run_entry,
        "experiment_name": "phase3-helm",
        "comparisons": [
            {
                "comparison_id": "official_vs_local",
                "comparison_kind": "official_vs_local",
                "component_ids": ["official-run", "local-attempt-a"],
                "enabled": True,
                "reference_component_id": "official-run",
                "notes": None,
                "comparability_facts": {},
                "warnings": [],
                "caveats": [],
            }
        ],
    }
    components_fpath = out_dir / "components_manifest.json"
    comparisons_fpath = out_dir / "comparisons_manifest.json"
    components_fpath.write_text(json.dumps(components_manifest, indent=2) + "\n")
    comparisons_fpath.write_text(json.dumps(comparisons_manifest, indent=2) + "\n")

    cmd = [
        sys.executable, "-m", "eval_audit.reports.core_metrics",
        "--report-dpath", str(out_dir),
        "--components-manifest", str(components_fpath),
        "--comparisons-manifest", str(comparisons_fpath),
    ]
    subprocess.run(cmd, check=True, cwd=REPO_ROOT)
    return _snapshot_report_dir(out_dir, [
        (str(official), "<OFFICIAL-DIR>"),
        (str(local), "<LOCAL-DIR>"),
        (str(out_dir), "<OUT>"),
        (str(REPO_ROOT), "<REPO>"),
    ])


def _snapshot_report_dir(
    out_dir: Path, replacements: list[tuple[str, str]]
) -> dict[str, Any]:
    """Normalize core_metric_report.json + warnings.json under ``out_dir``."""
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

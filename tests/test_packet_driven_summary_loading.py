from __future__ import annotations

import json
from pathlib import Path

from eval_audit.reports.core_packet import comparison_sample_latest_name
from eval_audit.workflows import analyze_experiment, build_reports_summary


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


_DEFAULT_OFFICIAL_CURVE = [
    {"abs_tol": 0.0, "agree_ratio": 0.4},
    {"abs_tol": 0.001, "agree_ratio": 0.4},
    {"abs_tol": 0.05, "agree_ratio": 0.8},
    {"abs_tol": 0.1, "agree_ratio": 0.9},
]


def _write_core_report_packet(
    report_dir: Path,
    *,
    experiment_name: str,
    run_entry: str,
    single_run: bool,
    official_curve: list[dict] | None = None,
) -> None:
    local_a = report_dir / "runs" / "local_a"
    official = report_dir / "runs" / "official"
    local_a.mkdir(parents=True, exist_ok=True)
    official.mkdir(parents=True, exist_ok=True)
    local_b = report_dir / "runs" / "local_b"
    if not single_run:
        local_b.mkdir(parents=True, exist_ok=True)

    components = [
        {
            "component_id": "local-a",
            "run_path": str(local_a),
            "job_path": None,
            "source_kind": "local",
            "tags": ["local"],
            "display_name": "local 1: local_a",
            "attempt_uuid": "attempt-a",
            "attempt_identity": "attempt-a",
            "machine_host": "host-a",
            "experiment_name": experiment_name,
        },
        {
            "component_id": "official-run",
            "run_path": str(official),
            "job_path": None,
            "source_kind": "official",
            "tags": ["official"],
            "display_name": "official: official",
            "attempt_uuid": None,
            "attempt_identity": None,
            "machine_host": None,
            "experiment_name": experiment_name,
        },
    ]
    if not single_run:
        components.insert(
            1,
            {
                "component_id": "local-b",
                "run_path": str(local_b),
                "job_path": None,
                "source_kind": "local",
                "tags": ["local", "repeat"],
                "display_name": "local 2: local_b",
                "attempt_uuid": "attempt-b",
                "attempt_identity": "attempt-b",
                "machine_host": "host-b",
                "experiment_name": experiment_name,
            },
        )

    comparisons = [
        {
            "comparison_id": "official_vs_local",
            "comparison_kind": "official_vs_local",
            "component_ids": ["official-run", "local-a"],
            "enabled": True,
            "reference_component_id": "official-run",
        }
    ]
    if not single_run:
        comparisons.append(
            {
                "comparison_id": "local_repeat",
                "comparison_kind": "local_repeat",
                "component_ids": ["local-a", "local-b"],
                "enabled": True,
                "reference_component_id": "local-a",
            }
        )

    _write_json(
        report_dir / "components_manifest.json",
        {
            "report_dpath": str(report_dir),
            "run_entry": run_entry,
            "experiment_name": experiment_name,
            "components": components,
        },
    )
    _write_json(
        report_dir / "comparisons_manifest.json",
        {
            "report_dpath": str(report_dir),
            "run_entry": run_entry,
            "experiment_name": experiment_name,
            "comparisons": comparisons,
        },
    )

    report_payload = {
        "generated_utc": "20260421T000000Z",
        "run_spec_name": "toy-run",
        "diagnostic_flags": [],
        "run_diagnostics": {
            "local-a": {"empty_completion_rate": 0.2, "output_token_count": {"mean": 4.0}},
            "official-run": {"empty_completion_rate": 0.0, "output_token_count": {"mean": 6.0}},
            **(
                {"local-b": {"empty_completion_rate": 0.1, "output_token_count": {"mean": 5.0}}}
                if not single_run else {}
            ),
        },
        "pairs": [
            {
                "comparison_id": "official_vs_local",
                "comparison_kind": "official_vs_local",
                "core_metrics": ["exact_match"],
                "diagnosis": {"label": "core_metric_drift", "primary_reason_names": ["core_metric_drift"]},
                "run_level": {"overall_quantiles": {"abs_delta": {"p90": 0.2, "max": 0.3}}},
                "instance_level": {
                    "agreement_vs_abs_tol": official_curve or _DEFAULT_OFFICIAL_CURVE,
                    "per_metric_agreement": {},
                },
            },
            *(
                [
                    {
                        "comparison_id": "local_repeat",
                        "comparison_kind": "local_repeat",
                        "core_metrics": ["exact_match"],
                        "diagnosis": {"label": "stable", "primary_reason_names": ["stable"]},
                        "run_level": {"overall_quantiles": {"abs_delta": {"p90": 0.01, "max": 0.02}}},
                        "instance_level": {
                            "agreement_vs_abs_tol": [{"abs_tol": 0.0, "agree_ratio": 1.0}],
                            "per_metric_agreement": {},
                        },
                    }
                ]
                if not single_run else []
            ),
        ],
    }
    _write_json(report_dir / "core_metric_report.json", report_payload)
    _write_json(
        report_dir / "warnings.json",
        {
            "packet_warnings": ["suspicious_case"] if single_run else ["suspicious_case", "repeat_case"],
            "packet_caveats": [],
            "comparisons": [
                {
                    "comparison_id": item["comparison_id"],
                    "enabled": item["enabled"],
                    "disabled_reason": item.get("disabled_reason"),
                    "warnings": item.get("warnings", []),
                    "caveats": item.get("caveats", []),
                }
                for item in comparisons
            ],
        },
    )
    (report_dir / "warnings.txt").write_text("packet_warnings:\n  - suspicious_case\n")


def test_analyze_experiment_summary_uses_packet_manifests_for_single_and_multi_run(tmp_path):
    single_report = tmp_path / "single_report"
    multi_report = tmp_path / "multi_report"
    _write_core_report_packet(single_report, experiment_name="exp-single", run_entry="bench:model=single", single_run=True)
    _write_core_report_packet(multi_report, experiment_name="exp-multi", run_entry="bench:model=multi", single_run=False)

    single_summary = analyze_experiment._summarize_core_report(
        single_report / "core_metric_report.json",
        experiment_name="exp-single",
    )
    multi_summary = analyze_experiment._summarize_core_report(
        multi_report / "core_metric_report.json",
        experiment_name="exp-multi",
    )

    assert single_summary["run_entry"] == "bench:model=single"
    assert single_summary["analysis_single_run"] is True
    assert single_summary["components_manifest"].endswith("components_manifest.json")
    assert single_summary["comparisons_manifest"].endswith("comparisons_manifest.json")
    assert single_summary["warnings_manifest"].endswith("warnings.json")
    assert multi_summary["run_entry"] == "bench:model=multi"
    assert multi_summary["analysis_single_run"] is False
    assert multi_summary["repeat_instance_agree_0"] == 1.0


def test_build_reports_summary_loads_rows_from_packet_manifests_without_selection_sidecar(tmp_path, monkeypatch):
    canonical_root = tmp_path / "audit_store" / "analysis" / "experiments"
    publication_root_link = tmp_path / "publication" / "core-run-analysis"
    legacy_repo_root = tmp_path / "legacy_repo"
    # Place the report under the canonical (audit-store) location, mirroring
    # the post-rename layout where experiments_analysis_root is the live
    # storage path and publication/legacy roots only host symlinks (or stale
    # pre-migration data we may still be scanning).
    report_dir = canonical_root / "exp-a" / "core-reports" / "core-metrics-bench-model-a"
    _write_core_report_packet(report_dir, experiment_name="exp-a", run_entry="bench:model=a", single_run=False)

    # _load_all_repro_rows lives in reports.summary.loading and binds the
    # path helpers from its own module namespace; patch them there.
    from eval_audit.reports.summary import loading as summary_loading

    monkeypatch.setattr(summary_loading, "experiments_analysis_root", lambda: canonical_root)
    monkeypatch.setattr(summary_loading, "publication_experiments_root", lambda: publication_root_link)
    monkeypatch.setattr(summary_loading, "legacy_repo_publication_root", lambda: legacy_repo_root)

    rows = build_reports_summary._load_all_repro_rows()

    assert len(rows) == 1
    row = rows[0]
    assert row["experiment_name"] == "exp-a"
    assert row["run_entry"] == "bench:model=a"
    assert row["analysis_single_run"] is False
    assert row["official_diagnosis"] == "core_metric_drift"
    assert row["repeat_diagnosis"] == "stable"
    assert row["components_manifest"].endswith("components_manifest.json")
    assert row["comparisons_manifest"].endswith("comparisons_manifest.json")


def test_tol010_sankey_buckets_on_the_0p01_curve_point_not_0p1(tmp_path, monkeypatch):
    """P0-1 regression: the tol010 variant is titled abs_tol=0.010 and must
    bucket on the 0.01 curve point, not the 0.1 point. A steep curve where the
    0.01 point is 'low agreement' but the 0.1 point is 'near-exact' would have
    been mislabelled a 10x-looser 'high agreement' by the old wiring."""
    canonical_root = tmp_path / "audit_store" / "analysis" / "experiments"
    report_dir = canonical_root / "exp-a" / "core-reports" / "core-metrics-bench-model-a"
    # 0.01 -> 0.30 (low), 0.1 -> 0.99 (near exact): distinct buckets.
    steep_curve = [
        {"abs_tol": 0.0, "agree_ratio": 0.20},
        {"abs_tol": 0.001, "agree_ratio": 0.25},
        {"abs_tol": 0.01, "agree_ratio": 0.30},
        {"abs_tol": 0.05, "agree_ratio": 0.60},
        {"abs_tol": 0.1, "agree_ratio": 0.99},
    ]
    _write_core_report_packet(
        report_dir,
        experiment_name="exp-a",
        run_entry="bench:model=a",
        single_run=False,
        official_curve=steep_curve,
    )

    from eval_audit.reports.summary import loading as summary_loading

    monkeypatch.setattr(summary_loading, "experiments_analysis_root", lambda: canonical_root)
    monkeypatch.setattr(summary_loading, "publication_experiments_root", lambda: tmp_path / "pub")
    monkeypatch.setattr(summary_loading, "legacy_repo_publication_root", lambda: tmp_path / "legacy")

    rows = build_reports_summary._load_all_repro_rows()
    assert len(rows) == 1
    row = rows[0]
    # The loading row carries a dedicated 0.01 key, distinct from the 0.1 key.
    assert row["official_instance_agree_010"] == 0.30
    assert row["official_instance_agree_01"] == 0.99

    from eval_audit.reports.summary.classification import _bucket_agreement

    # The tol010 sankey (as wired in build_reports_summary) buckets on the
    # 0.01 point; the OLD wiring (agree_01 = 0.99) would land a different,
    # looser bucket.
    tol010_rows = build_reports_summary._build_repro_sankey_rows_at_tol(
        rows, rows, "official_instance_agree_010"
    )
    assert tol010_rows[0]["agreement"] == _bucket_agreement(0.30)
    assert _bucket_agreement(0.30) != _bucket_agreement(0.99)


def test_sample_artifact_lookup_is_derived_from_packet_comparison_ids(tmp_path):
    report_dir = tmp_path / "report"
    _write_core_report_packet(report_dir, experiment_name="exp-a", run_entry="bench:model=a", single_run=False)

    artifact_names = build_reports_summary._prioritized_example_artifact_names(report_dir)

    assert "components_manifest.json" in artifact_names
    assert "comparisons_manifest.json" in artifact_names
    assert "warnings.json" in artifact_names
    assert "warnings.txt" in artifact_names
    assert comparison_sample_latest_name("official_vs_local") in artifact_names
    assert comparison_sample_latest_name("local_repeat") in artifact_names
    assert "instance_samples_official_vs_kwdagger.txt" not in artifact_names


def test_prioritized_breakdown_text_uses_plain_paths():
    summary = {
        "definitions": {"rank_population": "all analyzed rows"},
        "rows": [
            {
                "priority_rank": 1,
                "dimension": "benchmark",
                "dimension_value": "boolq",
                "bucket_class": "score_ge_95",
                "n_analyzed": 3,
                "target_bucket_count": 2,
                "target_bucket_share": 2 / 3,
                "selection_reason": "interesting slice",
                "n_attempted": 3,
                "n_completed": 3,
                "dominant_bucket": "high_agreement_0.95+",
                "interesting_flags": [],
                "breakdown_dir": "/tmp/breakdown",
                "breakdown_index_dir": "/tmp/breakdown-index",
                "example_run_entries": ["bench:model=a"],
                "example_report_dirs": ["/tmp/report-a"],
            }
        ],
    }

    lines = build_reports_summary._format_prioritized_breakdown_summary_text(
        scope_title="toy-scope",
        generated_utc="20260421T000000Z",
        summary=summary,
    )
    text = "\n".join(lines)

    assert "/tmp/breakdown" in text
    assert "/tmp/breakdown-index" in text
    assert "/tmp/report-a" in text
    assert "[link=" not in text

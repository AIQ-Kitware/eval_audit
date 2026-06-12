from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from eval_audit.reports import core_metrics


def _write_manifest(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n")


def test_plot_layout_controls_suptitle_spacing():
    calls = {}

    class FakeFigure:
        def set_constrained_layout_pads(self, **kwargs):
            calls["pads"] = kwargs

        def suptitle(self, text, **kwargs):
            calls["suptitle"] = (text, kwargs)

    layout = core_metrics.PlotLayout(
        suptitle_y=1.04,
        constrained_h_pad=0.25,
        constrained_hspace=0.18,
        constrained_w_pad=0.09,
        constrained_wspace=0.07,
    )
    core_metrics._set_suptitle(FakeFigure(), "Demo", fontsize=13, plot_layout=layout)

    assert calls["pads"] == {
        "h_pad": 0.25,
        "hspace": 0.18,
        "w_pad": 0.09,
        "wspace": 0.07,
    }
    assert calls["suptitle"] == ("Demo", {"fontsize": 13, "y": 1.04})


def test_plot_layout_defaults_are_code_level_controls():
    default = core_metrics.PlotLayout()

    assert default.figure_scale > 0
    assert default.suptitle_y is not None
    assert default.constrained_h_pad is not None
    assert default.constrained_hspace is not None
    assert default.constrained_w_pad is not None
    assert default.constrained_wspace is not None


def test_scaled_figsize_uses_plot_layout_scale():
    assert core_metrics._scaled_figsize(10, 5, core_metrics.PlotLayout(figure_scale=1.5)) == (15, 7.5)
    assert core_metrics._scaled_figsize(10, 5, core_metrics.PlotLayout(figure_scale=0)) == (10, 5)


def test_subplot_adjust_kwargs_allow_explicit_margins():
    class FakeFigure:
        def get_size_inches(self):
            return (10, 5)

    layout = core_metrics.PlotLayout(
        constrained_h_pad=0.5,
        constrained_w_pad=0.5,
        constrained_hspace=0.3,
        constrained_wspace=0.2,
        subplot_left=0.11,
        subplot_right=0.93,
        subplot_bottom=0.17,
        subplot_top=0.81,
    )

    assert core_metrics._subplot_adjust_kwargs(FakeFigure(), layout, top=0.9) == {
        "left": 0.11,
        "right": 0.93,
        "bottom": 0.17,
        "top": 0.81,
        "hspace": 0.3,
        "wspace": 0.2,
    }


def test_metric_domain_helper_is_conservative():
    assert core_metrics._metric_domain("exact_match") == (0.0, 1.0)
    assert core_metrics._metric_domain("unknown_metric") is None


def test_axis_hints_apply_only_for_known_safe_domains():
    class FakeAxis:
        def __init__(self):
            self.xlim = None
            self.ylim = None

        def set_xlim(self, lower, upper):
            self.xlim = (lower, upper)

        def set_ylim(self, lower, upper):
            self.ylim = (lower, upper)

    known = {"core_metrics": ["exact_match"]}
    mixed = {"core_metrics": ["exact_match", "unknown_metric"]}

    x_axis = FakeAxis()
    core_metrics._apply_xlim_hint(
        x_axis,
        core_metrics._pair_metric_domain(known),
        [0.0, 1e-2, 1.0],
    )
    assert x_axis.xlim == (0.0, 1.0)

    y_axis = FakeAxis()
    core_metrics._apply_abs_delta_ylim_hint(
        y_axis,
        core_metrics._pair_metric_domain(mixed),
        [0.0, 0.5],
    )
    assert y_axis.ylim is None


def test_single_run_instance_rows_honor_manifest_component(monkeypatch):
    component = {"component_id": "c1", "artifact_format": "eee"}
    calls = []
    marker = object()

    # _single_run_instance_core_rows lives in reports.core_metric_curves and
    # binds its collaborators from that module's namespace; patch them there.
    from eval_audit.reports import core_metric_curves

    monkeypatch.setattr(
        core_metric_curves,
        "_load_component_run",
        lambda arg: calls.append(("component", arg)) or marker,
    )
    monkeypatch.setattr(
        core_metric_curves,
        "_load_normalized",
        lambda arg: calls.append(("raw", arg)) or object(),
    )
    monkeypatch.setattr(
        core_metric_curves.ncompare,
        "instance_core_score_records",
        lambda nrun: calls.append(("records", nrun)) or [],
    )

    rows = core_metrics._single_run_instance_core_rows(
        "/tmp/raw-run",
        "demo",
        component=component,
    )

    assert rows.empty
    assert calls == [("component", component), ("records", marker)]


@pytest.mark.slow
def test_core_metrics_single_run_uses_manifests_and_writes_comparability_block(tmp_path, monkeypatch):
    report_dpath = tmp_path / "report"
    report_dpath.mkdir()
    local_run = tmp_path / "runs" / "local-run"
    official_run = tmp_path / "runs" / "official-run"
    for run_dpath in [local_run, official_run]:
        run_dpath.mkdir(parents=True)
        (run_dpath / "run_spec.json").write_text(
            json.dumps(
                {
                    "name": "toy-run",
                    "adapter_spec": {
                        "model": "toy-model",
                        "model_deployment": "toy-deploy",
                        "max_eval_instances": 100,
                    },
                    "scenario_spec": {"class_name": "helm.benchmark.scenarios.toy.ToyScenario"},
                }
            )
        )

    components_manifest = {
        "report_dpath": str(report_dpath),
        "packet_id": "toy-packet",
        "run_entry": "toy:model=x",
        "experiment_name": "toy-exp",
        "planner_version": "planner.v1",
        "selected_public_track": "main",
        "warnings": ["comparability_drift:same_deployment"],
        "caveats": ["same_deployment=no values=['toy-deploy', 'other-deploy']"],
        "official_selection": {
            "policy_name": "latest_suite_version_per_public_track",
            "selected_public_track": "main",
            "retained_component_ids": ["official-toy-run"],
            "discarded_component_ids": [],
            "warnings": [],
        },
        "comparability_facts": {
            "same_base_model": {"status": "yes", "values": ["toy-model"]},
            "same_scenario_class": {"status": "yes", "values": ["helm.benchmark.scenarios.toy.ToyScenario"]},
            "same_deployment": {"status": "no", "values": ["toy-deploy", "other-deploy"]},
            "same_adapter_instructions": {"status": "unknown", "values": []},
            "same_max_eval_instances": {"status": "yes", "values": [100]},
        },
        "components": [
            {
                "component_id": "local-attempt-a",
                "run_path": str(local_run),
                "job_path": None,
                "source_kind": "local",
                "tags": ["local"],
                "display_name": "local 1: local-run",
                "attempt_uuid": "attempt-a",
                "attempt_identity": "attempt-a",
                "machine_host": "host-a",
                "experiment_name": "toy-exp",
                "max_eval_instances": 100,
            },
            {
                "component_id": "official-toy-run",
                "run_path": str(official_run),
                "job_path": None,
                "source_kind": "official",
                "tags": ["official"],
                "display_name": "official: official-run",
                "attempt_uuid": None,
                "attempt_identity": None,
                "machine_host": None,
                "experiment_name": "toy-exp",
                "max_eval_instances": 100,
            },
        ],
    }
    comparisons_manifest = {
        "report_dpath": str(report_dpath),
        "run_entry": "toy:model=x",
        "experiment_name": "toy-exp",
        "comparisons": [
            {
                "comparison_id": "official_vs_local",
                "comparison_kind": "official_vs_local",
                "component_ids": ["official-toy-run", "local-attempt-a"],
                "enabled": True,
                "reference_component_id": "official-toy-run",
                "notes": None,
                "comparability_facts": {"same_deployment": {"status": "no", "values": ["toy-deploy", "other-deploy"]}},
                "warnings": ["comparability_drift:same_deployment"],
                "caveats": ["same_deployment=no values=['toy-deploy', 'other-deploy']"],
            }
        ],
    }
    components_fpath = report_dpath / "components_manifest.json"
    comparisons_fpath = report_dpath / "comparisons_manifest.json"
    _write_manifest(components_fpath, components_manifest)
    _write_manifest(comparisons_fpath, comparisons_manifest)

    thresholds = [0.0, 1e-3, 1e-2, 0.1, 1.0]
    right_pair = {
        "label": "official_vs_local",
        "core_metrics": ["exact_match"],
        "diagnosis": {"label": "core_metric_drift", "primary_reason_names": ["core_metric_drift"]},
        "run_level": {
            "n_rows": 1,
            "overall_quantiles": {"abs_delta": {"min": 0.2, "p50": 0.2, "p90": 0.2, "p95": 0.2, "p99": 0.2, "max": 0.2}},
            "by_metric": [{"metric": "exact_match", "count": 1, "abs_delta": {"p50": 0.2, "p90": 0.2, "p95": 0.2, "p99": 0.2, "max": 0.2}}],
            "agreement_vs_abs_tol": [{"abs_tol": t, "agree_ratio": 0.0 if t == 0.0 else 1.0, "matched": 1, "count": 1} for t in thresholds],
        },
        "instance_level": {
            "n_rows": 2,
            "overall_quantiles": {"abs_delta": {"min": 0.0, "p50": 0.25, "p90": 0.5, "p95": 0.5, "p99": 0.5, "max": 0.5}},
            "by_metric": [{"metric": "exact_match", "count": 2, "abs_delta": {"p50": 0.25, "p90": 0.5, "p95": 0.5, "p99": 0.5, "max": 0.5}}],
            "agreement_vs_abs_tol": [{"abs_tol": t, "agree_ratio": 0.5 if t < 0.1 else 1.0, "matched": 1, "count": 2} for t in thresholds],
            "per_metric_agreement": {
                "exact_match": [{"abs_tol": t, "agree_ratio": 0.5 if t < 0.1 else 1.0} for t in thresholds]
            },
        },
        "_instance_rows": [
            {"metric": "exact_match", "a": 1.0, "b": 0.5, "abs_delta": 0.5, "rel_delta": 0.5},
            {"metric": "exact_match", "a": 0.0, "b": 0.0, "abs_delta": 0.0, "rel_delta": 0.0},
        ],
    }

    monkeypatch.setattr(core_metrics, "_infer_run_spec_name", lambda *args: "toy-run")
    monkeypatch.setattr(core_metrics, "_build_pair", lambda *args, **kwargs: dict(right_pair))
    monkeypatch.setattr(
        core_metrics,
        "_run_diagnostics",
        lambda run_path: {
            "n_request_states": 2,
            "n_with_completions": 2,
            "empty_completion_count": 0,
            "empty_completion_rate": 0.0,
            "output_token_count": {"mean": 3.0, "p50": 3.0, "p90": 3.0, "max": 3.0},
            "stats_means": {"num_output_tokens": {"test": 3.0}, "finish_reason_unknown": {"test": 0.0}},
        },
    )
    _fake_instance_rows = lambda run_path, label, **_: pd.DataFrame(  # noqa: E731
        [
            {"run": label, "metric": "exact_match", "value": 1.0 if "local" in label else 0.5},
            {"run": label, "metric": "exact_match", "value": 0.0},
        ]
    )
    monkeypatch.setattr(core_metrics, "_single_run_instance_core_rows", _fake_instance_rows)
    # Same Phase 2 binding issue as _single_run_core_stat_index below:
    # the figure writers in reports.core_metric_plots bind this from
    # their own namespace.
    from eval_audit.reports import core_metric_plots

    monkeypatch.setattr(core_metric_plots, "_single_run_instance_core_rows", _fake_instance_rows)
    # Stage-5 added a ``component=`` kwarg so the loader can pick the
    # EEE artifact when planner manifests carry one. Accept it here
    # so the monkeypatched stand-in matches the real signature.
    _fake_stat_index = lambda run_path, **_: {  # noqa: E731
        "exact_match": SimpleNamespace(metric="exact_match", mean=1.0 if "local" in run_path else 0.5)
    }
    monkeypatch.setattr(core_metrics, "_single_run_core_stat_index", _fake_stat_index)
    # The run-level table writers moved to reports.core_metric_tables in
    # the Phase 2 split and bind _single_run_core_stat_index from their
    # own module namespace; patching only the facade no longer reaches
    # them (this was a latent break — the synthetic runs here carry only
    # run_spec.json, so the unpatched loader fails on required files).
    from eval_audit.reports import core_metric_tables

    monkeypatch.setattr(core_metric_tables, "_single_run_core_stat_index", _fake_stat_index)

    base_argv = [
        "--report-dpath", str(report_dpath),
        "--components-manifest", str(components_fpath),
        "--comparisons-manifest", str(comparisons_fpath),
    ]

    core_metrics.main(base_argv)

    # Canonical lightweight outputs are always present
    for name in [
        "core_metric_report.png",
        "core_metric_management_summary.txt",
        "core_runlevel_table.csv",
        "warnings.json",
        "warnings.txt",
    ]:
        assert (report_dpath / name).exists(), f"canonical output missing: {name}"

    # Heavy pairwise PNG plots are NOT rendered by default
    for name in [
        "core_metric_distributions.png",
        "core_metric_overlay_distributions.png",
        "core_metric_ecdfs.png",
        "core_metric_per_metric_agreement.png",
    ]:
        assert not (report_dpath / name).exists(), f"heavy artifact should not be auto-rendered: {name}"

    assert not (report_dpath / "core_metric_three_run_distributions.png").exists()

    text = (report_dpath / "core_metric_management_summary.txt").read_text()
    assert f"report_dpath: {report_dpath}" in text
    assert f"components_manifest: {components_fpath}" in text
    assert f"comparisons_manifest: {comparisons_fpath}" in text
    assert "warnings_and_caveats:" in text
    assert "selected_components:" in text
    assert "comparisons:" in text
    assert "comparability:" in text
    assert "on_demand_heavy_pairwise_plots" in text, "management summary must note on-demand rendering"
    assert "render_heavy_pairwise_plots.sh" in text
    warnings_payload = json.loads((report_dpath / "warnings.json").read_text())
    assert warnings_payload["packet_warnings"] == ["comparability_drift:same_deployment"]

    # With --render-heavy-pairwise-plots flag, heavy outputs ARE written
    core_metrics.main(base_argv + ["--render-heavy-pairwise-plots"])

    for name in [
        "core_metric_distributions.png",
        "core_metric_overlay_distributions.png",
        "core_metric_ecdfs.png",
        "core_metric_per_metric_agreement.png",
    ]:
        assert (report_dpath / name).exists(), f"heavy PNG plot missing with --render-heavy-pairwise-plots: {name}"

    # Sidecar legend artifacts (short alias -> long display_name) ride along
    # with the ECDF and overlay distribution plots so the legend stays legible.
    for name in [
        "core_metric_ecdfs_label_legend.png",
        "core_metric_ecdfs_label_legend.txt",
        "core_metric_overlay_distributions_label_legend.png",
        "core_metric_overlay_distributions_label_legend.txt",
    ]:
        assert (report_dpath / name).exists(), f"sidecar label legend missing: {name}"

    # Snapshot canonical (non-plot) latest-alias content + mtime before the
    # plots-only redraw so we can verify they are not modified. After the
    # history retirement (2026-04-28) the *.* paths are the actual
    # files (not symlinks into .history/), so the right "did this change?"
    # signal is mtime, not symlink target divergence.
    pre_redraw_canonical = {
        name: (report_dpath / name).stat().st_mtime_ns
        for name in [
            "core_metric_report.json",
            "core_metric_report.txt",
            "core_metric_management_summary.txt",
            "warnings.json",
            "warnings.txt",
            "core_runlevel_table.csv",
        ]
        if (report_dpath / name).exists()
    }
    pre_redraw_ecdf_mtime = (report_dpath / "core_metric_ecdfs.png").stat().st_mtime_ns

    with monkeypatch.context() as m:
        def fail_heavy_plot(*args, **kwargs):
            raise AssertionError("--plot_target core_metric_report must not render heavy plots")

        m.setattr(core_metrics, "_plot_pair_metric_distributions", fail_heavy_plot)
        m.setattr(core_metrics, "_plot_run_metric_distributions", fail_heavy_plot)
        m.setattr(core_metrics, "_plot_per_metric_agreement", fail_heavy_plot)
        core_metrics.main(
            base_argv + [
                "--render-heavy-pairwise-plots",
                "--plots-only",
                "--plot_target", "core_metric_report",
            ]
        )

    # --plot_target=core_metric_report must NOT touch the ecdfs plot.
    assert (report_dpath / "core_metric_ecdfs.png").stat().st_mtime_ns == pre_redraw_ecdf_mtime

    # --plots-only (no --plot_target) must refresh plot files but leave
    # canonical (non-plot) artifacts exactly as they were. Fast iteration on
    # plot styling should not churn the JSON/text/management/warnings/runlevel
    # surface.
    core_metrics.main(
        base_argv + [
            "--render-heavy-pairwise-plots",
            "--plots-only",
            "--plot_figure_scale", "1.2",
            "--plot_suptitle_y", "1.03",
            "--plot_constrained_h_pad", "0.25",
            "--plot_constrained_hspace", "0.16",
            "--plot_constrained_w_pad", "0.09",
            "--plot_constrained_wspace", "0.07",
        ]
    )

    for name, prior_mtime in pre_redraw_canonical.items():
        assert (report_dpath / name).stat().st_mtime_ns == prior_mtime, (
            f"--plots-only must not modify canonical artifact: {name}"
        )
    new_ecdf_mtime = (report_dpath / "core_metric_ecdfs.png").stat().st_mtime_ns
    assert new_ecdf_mtime != pre_redraw_ecdf_mtime, "--plots-only must rewrite the ecdfs plot"
    assert (report_dpath / "core_metric_ecdfs_label_legend.png").exists()


def test_diagnostic_flags_use_manifest_semantics_not_component_order():
    components = [
        {
            "component_id": "local-attempt-a",
            "source_kind": "local",
            "tags": ["local"],
        },
        {
            "component_id": "official-run",
            "source_kind": "official",
            "tags": ["official"],
        },
    ]
    comparisons = [
        {
            "comparison_id": "official_vs_local",
            "comparison_kind": "official_vs_local",
            "component_ids": ["local-attempt-a", "official-run"],
            "enabled": True,
            "reference_component_id": "official-run",
        }
    ]
    run_diagnostics = {
        "local-attempt-a": {
            "empty_completion_rate": 0.3,
            "output_token_count": {"mean": 3.0},
        },
        "official-run": {
            "empty_completion_rate": 0.0,
            "output_token_count": {"mean": 3.0},
        },
    }

    flags = core_metrics._diagnostic_flags(run_diagnostics, components, comparisons)

    assert "official_vs_local:empty_completion_pathology" in flags

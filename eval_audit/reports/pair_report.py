from __future__ import annotations

import argparse

from eval_audit.infra.logging import setup_cli_logging
import datetime as datetime_mod
import json
from pathlib import Path
from typing import Any

import kwutil

from eval_audit.helm.diff import HelmRunDiff
from eval_audit.infra.fs_publish import write_text_atomic
from eval_audit.normalized import NormalizedRunRef, SourceKind, load_run
from eval_audit.normalized.diff import (
    NormalizedDiff,
    agreement_curve,
    group_quantiles,
    metric_quantiles,
)
from eval_audit.normalized.helm_compat import helm_view

# R-2 (2026-07-06): the run-level / instance-level agreement, distance, and
# tolerance-sweep numbers now come from the unified normalized comparison core
# (NormalizedDiff), not the retired legacy half of HelmRunDiff. HelmRunDiff is
# kept here only for the run_spec/scenario *semantic* diagnosis, which is
# meaningful only over raw HELM run dirs (which this CLI always has).
#
# Behavior deltas vs the legacy path (see docs/eee-vs-helm-metadata.md and audit
# item IM-13):
#   * Agreement/distance are computed over the normalized join
#     (``(sample_hash|sample_id, metric_id)``) and over *core* metrics only —
#     the legacy path joined per-stat over all metric classes with a different
#     key granularity. The numbers are the same ones the production
#     core_metric_report already publishes.
#   * Tolerance is pure ``abs_tol`` (rel_tol is dropped). The curve is therefore
#     a true function of abs_tol, so ``tolerance_highlights`` and
#     ``tolerance_highlights_abs_only`` are identical.


def load_yaml_or_default(text: str | None, default: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if text is None:
        return default
    data = kwutil.Yaml.coerce(text)
    if not isinstance(data, list):
        raise TypeError('Tolerance config must decode to a list of dictionaries')
    return data


def default_tolerances() -> list[dict[str, Any]]:
    return [
        {'name': 'strict', 'abs_tol': 0.0, 'rel_tol': 0.0},
        {'name': 'tiny', 'abs_tol': 1e-12, 'rel_tol': 1e-6},
        {'name': 'small', 'abs_tol': 1e-9, 'rel_tol': 1e-4},
        {'name': 'medium', 'abs_tol': 1e-6, 'rel_tol': 1e-3},
        {'name': 'loose', 'abs_tol': 1e-3, 'rel_tol': 1e-2},
        {'name': 'xloose', 'abs_tol': 1e-2, 'rel_tol': 1e-1},
        {'name': 'xxloose', 'abs_tol': 1e-1, 'rel_tol': 1.0},
        {'name': 'extreme', 'abs_tol': 1.0, 'rel_tol': 10.0},
    ]


def abs_only_tolerances(tolerances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Same abs_tol grid with rel_tol forced to 0 (P1-13).

    Retained for the cross-machine overlay loader and its regression test. On
    the normalized core rel_tol is *always* 0 (the agreement curve is a pure
    function of abs_tol), so ``tolerance_highlights`` already equals
    ``tolerance_highlights_abs_only``; this helper keeps the abs_tol grid
    explicit for callers that build their own sweeps.
    """
    return [
        {'name': cfg.get('name', 'unnamed'), 'abs_tol': cfg.get('abs_tol', 0.0), 'rel_tol': 0.0}
        for cfg in tolerances
    ]


def validate_run_dir(run_dpath: Path) -> None:
    required_files = [
        'run_spec.json',
        'scenario_state.json',
        'stats.json',
        'per_instance_stats.json',
    ]
    missing_files = [name for name in required_files if not (run_dpath / name).exists()]
    if missing_files:
        missing_text = ', '.join(missing_files)
        raise SystemExit(
            f'Run artifacts are incomplete for {run_dpath}. '
            f'Missing required files: {missing_text}'
        )


def _tolerance_highlights(
    curve: list[dict[str, Any]], tolerances: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Zip a NormalizedDiff agreement curve back onto the named tolerance grid.

    ``curve`` is computed at the abs_tol points drawn from ``tolerances`` (same
    order), so the two align positionally. ``rel_tol`` is reported as 0.0 — the
    normalized core does not apply a relative tolerance.
    """
    highlights: list[dict[str, Any]] = []
    for cfg, row in zip(tolerances, curve):
        highlights.append({
            'name': cfg.get('name', 'unnamed'),
            'abs_tol': float(cfg.get('abs_tol', 0.0) or 0.0),
            'rel_tol': 0.0,
            'agree_ratio': row.get('agree_ratio'),
        })
    return highlights


def _agreement_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Strict (abs_tol=0) run/instance agreement in the legacy nested shape.

    Only the fields downstream consumers read are populated
    (``overall.agree_ratio`` / ``means.agree_ratio`` + the comparable count).
    Sourced from the normalized core rows, so this is core-metric agreement over
    the normalized join — see the module-level behavior-delta note.
    """
    n = len(rows)
    if n:
        agree = sum(1 for r in rows if float(r['abs_delta']) <= 0.0)
        agree_ratio: float | None = agree / n
        mismatched = n - agree
    else:
        agree_ratio = None
        mismatched = 0
    return {'comparable': n, 'mismatched': mismatched, 'agree_ratio': agree_ratio}


def build_pair_report(
    *,
    run_a: str | Path,
    run_b: str | Path,
    label_a: str = "A",
    label_b: str = "B",
    display_label_a: str | None = None,
    display_label_b: str | None = None,
    run_tolerances_yaml: str | None = None,
    instance_tolerances_yaml: str | None = None,
) -> dict[str, Any]:
    run_a_dpath = Path(run_a).expanduser().resolve()
    run_b_dpath = Path(run_b).expanduser().resolve()
    validate_run_dir(run_a_dpath)
    validate_run_dir(run_b_dpath)

    # Fully normalize both runs (populates instances) so the unified
    # comparison core can compute agreement. The raw HELM JSONs stay reachable
    # via helm_view() for the semantic diagnosis below.
    nrun_a = load_run(
        NormalizedRunRef.from_helm_run(run_a_dpath, source_kind=SourceKind.OFFICIAL)
    )
    nrun_b = load_run(
        NormalizedRunRef.from_helm_run(run_b_dpath, source_kind=SourceKind.LOCAL)
    )
    ndiff = NormalizedDiff(nrun_a, nrun_b, label=f'{label_a}_vs_{label_b}')

    run_tolerances = load_yaml_or_default(run_tolerances_yaml, default_tolerances())
    instance_tolerances = load_yaml_or_default(instance_tolerances_yaml, default_tolerances())
    run_thresholds = [float(cfg.get('abs_tol', 0.0) or 0.0) for cfg in run_tolerances]
    inst_thresholds = [float(cfg.get('abs_tol', 0.0) or 0.0) for cfg in instance_tolerances]

    run_curve = agreement_curve(ndiff.run_rows, run_thresholds)
    inst_curve = agreement_curve(ndiff.inst_rows, inst_thresholds)

    # Semantic diagnosis stays HelmRunDiff's (run_spec.json semantic diff),
    # sourced from the raw HELM artifacts behind helm_view(). summary_dict no
    # longer carries value/instance agreement (R-2); we inject the normalized
    # agreement blocks below so the pair_report.json shape is unchanged.
    diff = HelmRunDiff(
        helm_view(nrun_a),
        helm_view(nrun_b),
        a_name=label_a,
        b_name=label_b,
    )
    strict_summary = diff.summary_dict(level=20)
    strict_summary['value_agreement'] = {'overall': _agreement_block(ndiff.run_rows)}
    strict_summary['instance_value_agreement'] = {'means': _agreement_block(ndiff.inst_rows)}

    tolerance_highlights = {
        'run_level': _tolerance_highlights(run_curve, run_tolerances),
        'instance_level': _tolerance_highlights(inst_curve, instance_tolerances),
    }
    return {
        'generated_utc': datetime_mod.datetime.now(datetime_mod.UTC).strftime('%Y%m%dT%H%M%SZ'),
        'inputs': {
            'run_a': str(run_a_dpath),
            'run_b': str(run_b_dpath),
            'label_a': label_a,
            'label_b': label_b,
        },
        'display_labels': {
            'label_a': display_label_a or label_a,
            'label_b': display_label_b or label_b,
        },
        'strict_summary': strict_summary,
        'distance_summary': {
            'run_level': {
                'overall': group_quantiles(ndiff.run_rows),
                'by_metric': metric_quantiles(ndiff.run_rows),
            },
            'instance_level': {
                'overall': group_quantiles(ndiff.inst_rows),
                'by_metric': metric_quantiles(ndiff.inst_rows),
            },
        },
        'tolerance_highlights': tolerance_highlights,
        # rel_tol is always 0 on the normalized core, so the abs-only curve is
        # identical to the joint one; both keys are emitted for compatibility
        # with the cross-machine overlay loader.
        'tolerance_highlights_abs_only': tolerance_highlights,
    }


def write_text_report(report: dict[str, Any], out_fpath: Path) -> None:
    strict = report.get('strict_summary', {}) or {}
    diag = strict.get('diagnosis', {}) or {}
    run_dist = report.get('distance_summary', {}).get('run_level', {}) or {}
    inst_dist = report.get('distance_summary', {}).get('instance_level', {}) or {}
    sweep_hits = report.get('tolerance_highlights', {}) or {}
    display = report.get('display_labels', {}) or {}
    label_a = display.get('label_a') or report.get('inputs', {}).get('label_a')
    label_b = display.get('label_b') or report.get('inputs', {}).get('label_b')

    lines = []
    lines.append('Audit Pair Comparison')
    lines.append('')
    lines.append(f"generated_utc: {report.get('generated_utc')}")
    lines.append(f"run_a: {report.get('inputs', {}).get('run_a')}")
    lines.append(f"run_b: {report.get('inputs', {}).get('run_b')}")
    lines.append(f'label_a: {label_a}')
    lines.append(f'label_b: {label_b}')
    lines.append('')
    lines.append(f"diagnosis_label: {diag.get('label')}")
    lines.append(f"primary_reason_names: {diag.get('primary_reason_names')}")
    lines.append('')

    lines.append('strict_agreement:')
    overall = (strict.get('value_agreement', {}) or {}).get('overall', {}) or {}
    lines.append(f"  run_level_agree_ratio: {overall.get('agree_ratio')}")
    means = (strict.get('instance_value_agreement', {}) or {}).get('means', {}) or {}
    lines.append(f"  instance_level_agree_ratio: {means.get('agree_ratio')}")
    lines.append('')

    lines.append('distance_summary:')
    lines.append(f"  run_level_count: {(run_dist.get('overall', {}) or {}).get('count')}")
    lines.append(f"  run_level_abs_p50: {((run_dist.get('overall', {}) or {}).get('abs_delta', {}) or {}).get('p50')}")
    lines.append(f"  run_level_abs_p90: {((run_dist.get('overall', {}) or {}).get('abs_delta', {}) or {}).get('p90')}")
    lines.append(f"  run_level_abs_max: {((run_dist.get('overall', {}) or {}).get('abs_delta', {}) or {}).get('max')}")
    lines.append(f"  instance_level_count: {(inst_dist.get('overall', {}) or {}).get('count')}")
    lines.append(f"  instance_level_abs_p50: {((inst_dist.get('overall', {}) or {}).get('abs_delta', {}) or {}).get('p50')}")
    lines.append(f"  instance_level_abs_p90: {((inst_dist.get('overall', {}) or {}).get('abs_delta', {}) or {}).get('p90')}")
    lines.append(f"  instance_level_abs_max: {((inst_dist.get('overall', {}) or {}).get('abs_delta', {}) or {}).get('max')}")
    lines.append('')

    lines.append('tolerance_sweep_run_level:')
    for row in sweep_hits.get('run_level', []):
        lines.append(
            f"  {row.get('name')}: abs_tol={row.get('abs_tol')} rel_tol={row.get('rel_tol')} agree_ratio={row.get('agree_ratio')}"
        )
    lines.append('')
    lines.append('tolerance_sweep_instance_level:')
    for row in sweep_hits.get('instance_level', []):
        lines.append(
            f"  {row.get('name')}: abs_tol={row.get('abs_tol')} rel_tol={row.get('rel_tol')} agree_ratio={row.get('agree_ratio')}"
        )
    write_text_atomic(out_fpath, '\n'.join(lines) + '\n')


def main(argv: list[str] | None = None) -> None:
    setup_cli_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-a', required=True)
    parser.add_argument('--run-b', required=True)
    parser.add_argument('--label-a', default='A')
    parser.add_argument('--label-b', default='B')
    parser.add_argument('--display-label-a', default=None)
    parser.add_argument('--display-label-b', default=None)
    parser.add_argument('--report-dpath', required=True)
    parser.add_argument('--run-tolerances-yaml', default=None)
    parser.add_argument('--instance-tolerances-yaml', default=None)
    args = parser.parse_args(argv)

    report_dpath = Path(args.report_dpath).expanduser().resolve()
    report_dpath.mkdir(parents=True, exist_ok=True)
    report = build_pair_report(
        run_a=args.run_a,
        run_b=args.run_b,
        label_a=args.label_a,
        label_b=args.label_b,
        display_label_a=args.display_label_a,
        display_label_b=args.display_label_b,
        run_tolerances_yaml=args.run_tolerances_yaml,
        instance_tolerances_yaml=args.instance_tolerances_yaml,
    )
    json_fpath = report_dpath / 'pair_report.json'
    txt_fpath = report_dpath / 'pair_report.txt'
    report = kwutil.Json.ensure_serializable(report)
    write_text_atomic(json_fpath, json.dumps(report, indent=2, ensure_ascii=False))
    write_text_report(report, txt_fpath)
    print(f'Wrote pair report: {json_fpath}')
    print(f'Wrote pair text: {txt_fpath}')


if __name__ == '__main__':
    setup_cli_logging()
    main()

from __future__ import annotations

import argparse

from loguru import logger

from eval_audit.infra.logging import rich_link, setup_cli_logging
from pathlib import Path
from typing import Any

from eval_audit.infra.fs_publish import write_text_atomic
from eval_audit.normalized import NormalizedRunRef, SourceKind, load_run
from eval_audit.normalized.diff import NormalizedDiff
from eval_audit.reports.core_packet import comparison_sample_latest_name
# R-10: single _infer_run_spec_name (was duplicated verbatim here + in
# core_metric_curves). Keep the core_metric_curves copy as canonical.
from eval_audit.reports.core_metric_curves import _infer_run_spec_name

from eval_audit.infra.profiling import profile


def _fmt(value: Any) -> str:
    try:
        return f'{float(value):.6g}'
    except (TypeError, ValueError):
        return str(value)


def _render_instance_samples(
    diff: NormalizedDiff,
    *,
    label: str,
    top_n: int,
    writer,
) -> None:
    """Writer-style instance-level report over the normalized core rows.

    R-2 (2026-07-06): re-homed off the retired ``HelmRunDiff.summarize_instances``
    onto ``NormalizedDiff``. The rows are the *core-metric* per-instance
    agreement rows the production core_metric_report already uses (join key
    ``(sample_hash|sample_id, metric_id)``). Relative to the legacy report this
    drops the prompt/input/completion excerpts, the request_state diff, and the
    perturbed/unperturbed split (none survive on the normalized instance rows,
    which are numeric-only) and shows core metrics only. It gains a consistent,
    deterministic view sourced from the same numbers the agreement curves use.
    """
    rows = diff.inst_rows
    stats = diff.inst_stats
    n_rows = len(rows)
    mismatched = sum(1 for r in rows if float(r['abs_delta']) > 0.0)
    agree_ratio = (n_rows - mismatched) / n_rows if n_rows else None

    writer(f'Instance-level diff: {label}:A vs {label}:B')
    writer(
        f'  coverage: joined_pairs={int(stats.get("n_joined_pairs", 0))} '
        f'compared_core_rows={n_rows} '
        f'nonfinite_dropped={int(stats.get("n_nonfinite_dropped", 0))}'
    )
    writer(
        f'  agreement (core metrics, abs_tol=0): comparable={n_rows} '
        f'mismatched={mismatched} agree_ratio={_fmt(agree_ratio)}'
    )

    by_metric: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if float(row['abs_delta']) <= 0.0:
            continue
        by_metric.setdefault(str(row.get('metric', 'unknown')), []).append(row)

    if not by_metric:
        writer('  (no core-metric mismatches at abs_tol=0)')
        return

    writer('  top mismatches:')
    for metric in sorted(by_metric):
        items = sorted(
            by_metric[metric],
            key=lambda r: (-float(r['abs_delta']), str(r.get('sample_id'))),
        )[:top_n]
        writer(f'  [core, {metric}]:')
        for rank, item in enumerate(items, start=1):
            a = float(item['a'])
            b = float(item['b'])
            abs_d = float(item['abs_delta'])
            writer(
                f'   {rank:2d}. sample_id: {item.get("sample_id")}'
            )
            writer(
                f'      A={_fmt(a)}  B={_fmt(b)}  Δ(B-A)={_fmt(b - a)}  |Δ|={_fmt(abs_d)}'
            )


@profile
def write_pair_samples(
    *,
    run_a: str,
    run_b: str,
    label: str,
    report_dpath: str | Path,
    top_n: int = 8,
) -> Path:
    report_dpath = Path(report_dpath).expanduser().resolve()
    report_dpath.mkdir(parents=True, exist_ok=True)

    # Fully normalize both runs (populates instances) so the unified
    # comparison core can join per-instance core-metric rows.
    nrun_a = load_run(
        NormalizedRunRef.from_helm_run(run_a, source_kind=SourceKind.OFFICIAL)
    )
    nrun_b = load_run(
        NormalizedRunRef.from_helm_run(run_b, source_kind=SourceKind.LOCAL)
    )
    diff = NormalizedDiff(nrun_a, nrun_b, label=f'{label}_vs_local')

    run_spec_name = _infer_run_spec_name(run_a, run_b)
    lines: list[str] = []
    lines.append('Instance Sample Inspection')
    lines.append(f'label: {label}')
    lines.append(f'run_spec_name: {run_spec_name}')
    lines.append(f'run_a: {Path(run_a).expanduser().resolve()}')
    lines.append(f'run_b: {Path(run_b).expanduser().resolve()}')
    lines.append('')
    _render_instance_samples(diff, label=label, top_n=top_n, writer=lines.append)
    out_fpath = report_dpath / comparison_sample_latest_name(label)
    write_text_atomic(out_fpath, '\n'.join(lines) + '\n')
    return out_fpath


@profile
def main(argv: list[str] | None = None) -> None:
    setup_cli_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-a', required=True)
    parser.add_argument('--run-b', required=True)
    parser.add_argument('--label', required=True)
    parser.add_argument('--report-dpath', required=True)
    parser.add_argument('--top-n', type=int, default=8)
    # R-2 removed --show-details/--level: they only gated the legacy
    # excerpt/class rendering that no longer exists (no inert flags).
    args = parser.parse_args(argv)

    out_fpath = write_pair_samples(
        run_a=args.run_a,
        run_b=args.run_b,
        label=args.label,
        report_dpath=args.report_dpath,
        top_n=args.top_n,
    )
    latest_fpath = Path(args.report_dpath).expanduser().resolve() / comparison_sample_latest_name(args.label)
    logger.info(f'Wrote instance sample report: {rich_link(out_fpath)}')
    logger.info(f'Updated latest link: {rich_link(latest_fpath)}')


if __name__ == '__main__':
    setup_cli_logging()
    main()

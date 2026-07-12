"""Plotly bar-chart helpers and emitters for the Stage 1 filter report.

Split out of ``eval_audit.reports.filter_analysis`` on 2026-06-11
(Phase 2 of docs/historical/planning/repo-refactor-plan.md). Pure relocation:
function bodies are unchanged.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any
import pandas as pd
import safer
from eval_audit.infra.logging import rich_link
from eval_audit.infra.plotly_env import configure_plotly_chrome
from loguru import logger


def _title_with_n(title: str, n: int) -> str:
    return f'{title} n={n}'


from eval_audit.reports._plotly_bars import (  # E2
    bar_count_label as _bar_count_label,
    bar_tickangle as _bar_tickangle,
    bar_tickfont_size as _bar_tickfont_size,
)


def _bar_axis_values(rows: list[dict[str, Any]], x: str) -> list[str]:
    unique_x = []
    seen = set()
    for row in rows:
        value = str(row.get(x) or 'unknown')
        if value not in seen:
            seen.add(value)
            unique_x.append(value)
    return unique_x


from eval_audit.utils.coercion import abbreviate_label as _abbreviate_label  # R-6


def _bar_chart_layout(rows: list[dict[str, Any]], x: str, *, compact: bool = False) -> dict[str, Any]:
    unique_x = _bar_axis_values(rows, x)
    longest_label = max((len(value) for value in unique_x), default=0)
    if compact:
        n_bars = max(len(unique_x), 1)
        height = min(max(520, 14 * n_bars + 240), 1000)
        width = min(max(1100, 36 * n_bars, 14 * longest_label * n_bars), 1600)
        return {
            'width': width,
            'height': height,
            'margin': {'b': min(max(120, 8 * longest_label), 220), 't': 80, 'l': 70, 'r': 30},
        }
    height = max(650, 24 * max(len(unique_x), 1) + 260)
    width = max(1400, 120 * max(len(unique_x), 1), 18 * longest_label * max(len(unique_x), 1))
    max_width = int(height * 2.5)
    width = min(width, max_width)
    return {
        'width': width,
        'height': height,
        'margin': {'b': max(180, 12 * longest_label)},
    }


def _bar_chart_xaxis_update(
    rows: list[dict[str, Any]],
    *,
    x: str,
    xaxis_title: str | None,
    compact: bool,
) -> dict[str, Any]:
    unique_x = _bar_axis_values(rows, x)
    n_bars = len(unique_x)
    title_text = _bar_count_label(x, n_bars, axis_title=xaxis_title)
    if not compact:
        return {
            'title_text': title_text,
            'tickangle': -45,
            'automargin': True,
        }
    return {
        'title_text': title_text,
        'tickmode': 'array',
        'tickvals': unique_x,
        'ticktext': [_abbreviate_label(value) for value in unique_x],
        'tickangle': _bar_tickangle(n_bars),
        'tickfont': {'size': _bar_tickfont_size(n_bars)},
        'automargin': True,
    }


def _emit_bar_chart(
    rows: list[dict[str, Any]],
    *,
    report_dpath: Path,
    x: str,
    y: str,
    title: str,
    stem: str,
    stamp: str,
    interactive_dpath: Path,
    static_dpath: Path,
    xaxis_title: str | None = None,
) -> dict[str, str | None]:
    if not rows:
        return {'html': None, 'png': None, 'plotly_error': None}
    del report_dpath, stamp  # vestigial; kept in signature for backwards-compat
    html_fpath = interactive_dpath / f'{stem}.html'
    png_fpath = static_dpath / f'{stem}.png'
    html_out = None
    png_out = None
    plotly_error = None
    try:
        import plotly.express as px

        configure_plotly_chrome()
        n_bars = len({str(row.get(x) or 'unknown') for row in rows})
        if n_bars > 75:
            logger.warning(f'Chart {stem!r} is rendering {n_bars} bars; rendering the full chart anyway.')
        fig = px.bar(pd.DataFrame(rows), x=x, y=y, title=title)
        fig.update_layout(**_bar_chart_layout(rows, x))
        fig.update_xaxes(**_bar_chart_xaxis_update(rows, x=x, xaxis_title=xaxis_title, compact=False))
        with safer.open(html_fpath, 'w', make_parents=True, temp_file=True) as fp:
            fig.write_html(fp, include_plotlyjs='cdn')
        logger.debug(f'Write to 📝: {rich_link(html_fpath)}')
        html_out = str(html_fpath)
        try:
            fig.update_layout(**_bar_chart_layout(rows, x, compact=True))
            fig.update_xaxes(**_bar_chart_xaxis_update(rows, x=x, xaxis_title=xaxis_title, compact=True))
            with safer.open(png_fpath, 'wb', make_parents=True, temp_file=True) as fp:
                fig.write_image(fp, format='png', scale=1.0)
            logger.debug(f'Write 🖼: {rich_link(png_fpath)}')
            png_out = str(png_fpath)
        except Exception as ex:
            plotly_error = f'unable to write PNG: {ex!r}'
            logger.warning(plotly_error)
    except Exception as ex:
        plotly_error = f'unable to write chart: {ex!r}'
        logger.warning(plotly_error)
    return {'html': html_out, 'png': png_out, 'plotly_error': plotly_error}


def _emit_stacked_bar_chart(
    rows: list[dict[str, Any]],
    *,
    report_dpath: Path,
    x: str,
    y: str,
    color: str,
    title: str,
    stem: str,
    stamp: str,
    interactive_dpath: Path,
    static_dpath: Path,
    xaxis_title: str | None = None,
    yaxis_title: str | None = None,
    color_order: list[str] | None = None,
    n_facets_shown: int | None = None,
    n_facets_total: int | None = None,
) -> dict[str, str | None]:
    if not rows:
        return {'html': None, 'png': None, 'plotly_error': None}
    del report_dpath, stamp  # vestigial; kept in signature for backwards-compat
    html_fpath = interactive_dpath / f'{stem}.html'
    png_fpath = static_dpath / f'{stem}.png'
    html_out = None
    png_out = None
    plotly_error = None
    try:
        import plotly.express as px

        configure_plotly_chrome()
        category_orders = {}
        if color_order is not None:
            category_orders[color] = color_order
        n_bars = len({str(row.get(x) or 'unknown') for row in rows})
        if n_bars > 75:
            logger.warning(f'Chart {stem!r} is rendering {n_bars} bars; rendering the full chart anyway.')
        fig = px.bar(
            pd.DataFrame(rows),
            x=x,
            y=y,
            color=color,
            title=title,
            barmode='stack',
            category_orders=category_orders,
        )
        fig.update_layout(
            yaxis_title=yaxis_title if yaxis_title is not None else y.replace('_', ' '),
            **_bar_chart_layout(rows, x),
        )
        fig.update_xaxes(**_bar_chart_xaxis_update(rows, x=x, xaxis_title=xaxis_title, compact=False))
        with safer.open(html_fpath, 'w', make_parents=True, temp_file=True) as fp:
            fig.write_html(fp, include_plotlyjs='cdn')
        logger.debug(f'Write to 📝: {rich_link(html_fpath)}')
        html_out = str(html_fpath)
        try:
            fig.update_layout(**_bar_chart_layout(rows, x, compact=True))
            fig.update_xaxes(**_bar_chart_xaxis_update(rows, x=x, xaxis_title=xaxis_title, compact=True))
            with safer.open(png_fpath, 'wb', make_parents=True, temp_file=True) as fp:
                fig.write_image(fp, format='png', scale=1.0)
            logger.debug(f'Write 🖼: {rich_link(png_fpath)}')
            png_out = str(png_fpath)
        except Exception as ex:
            plotly_error = f'unable to write PNG: {ex!r}'
            logger.warning(plotly_error)
    except Exception as ex:
        plotly_error = f'unable to write chart: {ex!r}'
        logger.warning(plotly_error)
    return {'html': html_out, 'png': png_out, 'plotly_error': plotly_error}

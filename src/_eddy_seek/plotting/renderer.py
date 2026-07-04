"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Shared Plotly builders for session record primitives.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from ..common import session_artifact_filename
from ._plotly import (
    apply_axes_theme,
    freq_marker,
    go,
    header_table,
    marker_outline,
    plotly_available,
    single_xy_layout,
    write_html,
)
from .primitives import (
    BoxRecord,
    MarkerRecord,
    ScatterMode,
    ScatterRecord,
    StatsRecord,
)

if TYPE_CHECKING:
    from ..session import SeekSession

logger = logging.getLogger(__name__)


def plot_filename(
    session_id: str,
    when: datetime | None = None,
    *,
    suffix: str = "",
    run_id: str | None = None,
) -> str:
    return session_artifact_filename(
        session_id, when, suffix=suffix, run_id=run_id, ext="html"
    )


def add_scatter(
    fig: Any,
    record: ScatterRecord,
    search_for: Literal["min", "max"],
    color: str,
    *,
    row: int | None = None,
    col: int | None = None,
) -> None:
    if go is None or not record.cloud.xs:
        return
    mode = record.mode.value if isinstance(record.mode, ScatterMode) else record.mode
    freqs = record.cloud.freqs
    marker: dict[str, Any]
    if freqs is not None:
        marker = freq_marker(list(freqs), search_for, size=9, opacity=1.0)
    else:
        marker = {"size": 9, "color": color}
    trace = go.Scatter(
        x=list(record.cloud.xs),
        y=list(record.cloud.ys),
        mode=mode,
        name=record.label,
        line={"color": color, "width": 1},
        marker=marker,
        text=([f"{freq:.1f} Hz" for freq in freqs] if freqs is not None else None),
        hovertemplate=(
            f"{record.label}<br>x=%{{x:.4f}} y=%{{y:.4f}} %{{text}}<extra></extra>"
        ),
        legendgroup=record.label,
    )
    if row is not None and col is not None:
        fig.add_trace(trace, row=row, col=col)
    else:
        fig.add_trace(trace)


def add_marker(
    fig: Any,
    record: MarkerRecord,
    color: str,
    *,
    size: int = 11,
    row: int | None = None,
    col: int | None = None,
) -> None:
    if go is None:
        return
    trace = go.Scatter(
        x=[record.at.x],
        y=[record.at.y],
        mode="markers",
        name=record.label,
        marker={
            "size": size,
            "symbol": record.symbol,
            "color": color,
            "line": (
                {"width": 1, "color": marker_outline()}
                if record.symbol == "star"
                else None
            ),
        },
        legendgroup=record.label,
        showlegend=record.symbol == "star",
    )
    if row is not None and col is not None:
        fig.add_trace(trace, row=row, col=col)
    else:
        fig.add_trace(trace)


def add_box(
    fig: Any,
    record: BoxRecord,
    color: str,
    *,
    row: int | None = None,
    col: int | None = None,
) -> None:
    shape = {
        "type": "rect",
        "x0": record.bounds.lo.x,
        "x1": record.bounds.hi.x,
        "y0": record.bounds.lo.y,
        "y1": record.bounds.hi.y,
        "line": {"color": color, "width": 1, "dash": "dot"},
        "fillcolor": "rgba(0,0,0,0)",
    }
    if row is not None and col is not None:
        fig.add_shape(**shape, row=row, col=col)
    else:
        fig.add_shape(**shape)


def layout_with_stats(
    fig: Any,
    stats: StatsRecord,
    *,
    xaxis_title: str = "X offset (mm)",
    yaxis_title: str = "Y offset (mm)",
) -> None:
    fig.update_layout(
        xaxis_title=xaxis_title,
        yaxis_title=yaxis_title,
        **single_xy_layout(
            title=stats.title,
            tables=[header_table(list(stats.columns), list(stats.rows))],
            final=stats.footer,
        ),
    )
    apply_axes_theme(fig)


def write_figure(
    results_dir: Path,
    session_id: str,
    fig: Any,
    *,
    write_at: datetime | None = None,
    suffix: str = "",
    run_id: str | None = None,
) -> str | None:
    if not plotly_available() or fig is None:
        return None
    out_path = results_dir / plot_filename(
        session_id, write_at, suffix=suffix, run_id=run_id
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not write_html(str(out_path), fig):
        logger.warning(f"eddy_seek: failed to write plot to {out_path}")
        return None
    logger.info(f"eddy_seek: debug plot saved to {out_path}")
    return str(out_path)


def finalize_strategy_plot(ctx: SeekSession, strategy_name: str) -> str | None:
    from .registry import render_session_plot

    if not ctx.config.save_plots:
        return None
    fig = render_session_plot(
        strategy_name,
        ctx.recorder.records(),
        search_for=ctx.config.search_for,
    )
    if fig is None:
        return None
    return write_figure(
        Path(ctx.config.result_folder),
        ctx.session_id,
        fig,
        write_at=ctx.artifact_write_at,
        suffix=ctx.artifact_suffix(strategy_name),
        run_id=ctx.run_id,
    )

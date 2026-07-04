"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Repeatability scatter plot for EDDY_SEEK_ACCURACY.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from ..session import compute_accuracy_stats
from ._plotly import (
    THEME_COLORS,
    apply_axes_theme,
    go,
    marker_outline,
    plotly_available,
    xy_session_layout,
)
from .primitives import AccuracyRepeatRecord, pass_color
from .registry import StrategyPlotter, register_plotter


def write_accuracy_plot(*, repeats: list[AccuracyRepeatRecord]) -> Any | None:
    if not plotly_available() or go is None or len(repeats) < 2:
        return None

    offsets = [record.offset for record in repeats]
    stats = compute_accuracy_stats(offsets)
    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=[0.0],
            y=[0.0],
            mode="markers",
            name="session start",
            marker={"size": 10, "symbol": "circle-open", "color": THEME_COLORS.muted},
        )
    )
    fig.add_trace(
        go.Scatter(
            x=[stats.mean.x],
            y=[stats.mean.y],
            mode="markers",
            name="mean",
            marker={
                "size": 14,
                "symbol": "star",
                "color": THEME_COLORS.text,
                "line": {"width": 1, "color": marker_outline()},
            },
        )
    )

    repeat_xs = [record.offset.x for record in repeats]
    repeat_ys = [record.offset.y for record in repeats]
    fig.add_trace(
        go.Scatter(
            x=repeat_xs,
            y=repeat_ys,
            mode="lines+markers",
            name="repeat order",
            line={"color": THEME_COLORS.muted, "width": 1, "dash": "dot"},
            marker={"size": 11, "opacity": 0.0},
            showlegend=False,
            hoverinfo="skip",
        )
    )

    for record in repeats:
        color = pass_color(record.repeat_num)
        hover = (
            f"repeat {record.repeat_num}<br>"
            f"x={record.offset.x:+.4f} y={record.offset.y:+.4f} mm"
        )
        if record.session_plot_path:
            hover += f"<br>session plot: {record.session_plot_path}"
        fig.add_trace(
            go.Scatter(
                x=[record.offset.x],
                y=[record.offset.y],
                mode="markers+text",
                name=f"repeat {record.repeat_num}",
                text=[str(record.repeat_num)],
                textposition="top center",
                marker={
                    "size": 13,
                    "color": color,
                    "line": {"width": 1, "color": marker_outline()},
                },
                hovertemplate=hover + "<extra></extra>",
                legendgroup=f"repeat {record.repeat_num}",
            )
        )

    repeat_rows: list[dict[str, str]] = []
    for record, radial in zip(repeats, stats.radial):
        repeat_rows.append(
            {
                "repeat": str(record.repeat_num),
                "offset": f"({record.offset.x:+.4f}, {record.offset.y:+.4f})",
                "radial": f"{radial:.4f}",
                "plot": (
                    Path(record.session_plot_path).name
                    if record.session_plot_path
                    else ""
                ),
            }
        )

    x_lo, x_hi = stats.xs_range
    y_lo, y_hi = stats.ys_range
    x_span = x_hi - x_lo
    y_span = y_hi - y_lo
    fig.add_shape(
        type="rect",
        x0=x_lo,
        x1=x_hi,
        y0=y_lo,
        y1=y_hi,
        line={"color": THEME_COLORS.muted, "width": 1.5, "dash": "dash"},
        fillcolor="rgba(148,163,184,0.12)",
    )
    layout = xy_session_layout(
        f"EDDY_SEEK_ACCURACY ({len(repeats)} repeats)",
        columns=[
            ("repeat", "Repeat"),
            ("offset", "Offset (mm)"),
            ("radial", "Radial (mm)"),
            ("plot", "Session plot"),
        ],
        rows=repeat_rows,
        final=(
            f"Mean: ({stats.mean.x:+.4f}, {stats.mean.y:+.4f}) mm  "
            f"stdev: ({stats.std_x:.4f}, {stats.std_y:.4f}) mm  "
            f"max radial={stats.max_radial:.4f} mm  "
            f"max pair={stats.max_pair:.4f} mm"
        ),
    )
    layout["annotations"] = [
        {
            "x": (x_lo + x_hi) / 2,
            "y": y_lo,
            "text": f"ΔX = {x_span:.4f} mm",
            "showarrow": False,
            "yshift": -18,
            "font": {"size": 9, "color": THEME_COLORS.muted},
        },
        {
            "x": x_lo,
            "y": (y_lo + y_hi) / 2,
            "text": f"ΔY = {y_span:.4f} mm",
            "showarrow": False,
            "xshift": -52,
            "textangle": -90,
            "font": {"size": 9, "color": THEME_COLORS.muted},
        },
    ]
    fig.update_layout(
        xaxis_title="X offset from session start (mm)",
        yaxis_title="Y offset from session start (mm)",
        **layout,
    )
    apply_axes_theme(fig)
    return fig


@register_plotter("accuracy")
class AccuracyPlotter(StrategyPlotter):
    def render(
        self,
        records: Sequence[Any],
        *,
        search_for: Literal["min", "max"],
    ) -> Any | None:
        _ = search_for
        repeats = [
            record for record in records if isinstance(record, AccuracyRepeatRecord)
        ]
        return write_accuracy_plot(repeats=repeats)

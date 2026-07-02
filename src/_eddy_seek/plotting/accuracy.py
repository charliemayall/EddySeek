"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.

Repeatability scatter plot for EDDY_SEEK_ACCURACY.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..common import Position
from ..session import compute_accuracy_stats
from ._plotly import go, pass_color, plotly_available, session_stats_title


@dataclass(frozen=True, slots=True)
class AccuracyRepeatRecord:
    repeat_num: int
    offset: Position
    session_plot_path: str | None = None


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
            marker={"size": 10, "symbol": "circle-open", "color": "#888"},
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
                "color": "#111",
                "line": {"width": 1, "color": "white"},
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
            line={"color": "#AAA", "width": 1, "dash": "dot"},
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
                    "line": {"width": 1, "color": "white"},
                },
                hovertemplate=hover + "<extra></extra>",
                legendgroup=f"repeat {record.repeat_num}",
            )
        )

    repeat_lines = []
    for record, radial in zip(repeats, stats.radial):
        line = (
            f"Repeat {record.repeat_num}: ({record.offset.x:+.4f}, "
            f"{record.offset.y:+.4f}) mm  radial={radial:.4f} mm"
        )
        if record.session_plot_path:
            line += f"  plot={record.session_plot_path}"
        repeat_lines.append(line)

    fig.update_layout(
        title=session_stats_title(
            f"EDDY_SEEK_ACCURACY ({len(repeats)} repeats)",
            repeat_lines,
            final=(
                f"Mean: ({stats.mean.x:+.4f}, {stats.mean.y:+.4f}) mm  "
                f"stdev: ({stats.std_x:.4f}, {stats.std_y:.4f}) mm  "
                f"max radial={stats.max_radial:.4f} mm  "
                f"max pair={stats.max_pair:.4f} mm"
            ),
        ),
        xaxis_title="X offset from session start (mm)",
        yaxis_title="Y offset from session start (mm)",
        yaxis={"scaleanchor": "x", "scaleratio": 1},
        height=max(560, 120 + 40 * len(repeats)),
        margin={"t": max(120, 80 + 18 * len(repeats))},
        legend={"orientation": "h", "y": 1.02, "x": 0},
    )
    return fig

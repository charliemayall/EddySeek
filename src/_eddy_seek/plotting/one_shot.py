"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.

Heatmap debug plots for OneShotStrategy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ..common import Position
from ..continuous_motion import MotionSample
from ._plotly import go, plotly_available, xy_session_layout


@dataclass(frozen=True, slots=True)
class OneShotRecord:
    center: Position
    result: Position
    samples: list[MotionSample]
    box: tuple[float, float, float, float]
    z: list[list[float | None]]
    x_centers: list[float]
    y_centers: list[float]


def write_one_shot_plot(
    *,
    record: OneShotRecord | dict[str, Any],
    search_for: Literal["min", "max"],
) -> Any | None:
    if not plotly_available() or go is None:
        return None

    if isinstance(record, dict):
        record = OneShotRecord(
            center=record["center"],
            result=record["result"],
            samples=record["samples"],
            box=record["box"],
            z=record["z"],
            x_centers=record["x_centers"],
            y_centers=record["y_centers"],
        )

    x_lo, x_hi, y_lo, y_hi = record.box
    z_display = [
        [value if value is not None else float("nan") for value in row]
        for row in record.z
    ]
    freqs = [sample.freq for sample in record.samples]
    freq_range = (
        f"freq=[{min(freqs):.0f}, {max(freqs):.0f}] Hz" if freqs else "freq=n/a"
    )

    fig = go.Figure()
    fig.add_trace(
        go.Heatmap(
            x=record.x_centers,
            y=record.y_centers,
            z=z_display,
            colorscale="Viridis",
            reversescale=search_for == "min",
            colorbar={"title": "Hz", "x": 1.02, "xanchor": "left", "len": 0.75},
            hovertemplate="x=%{x:.4f} y=%{y:.4f} %{z:.1f} Hz<extra></extra>",
            name="binned mean",
        )
    )
    xs = [sample.offset.x for sample in record.samples]
    ys = [sample.offset.y for sample in record.samples]
    fig.add_trace(
        go.Scatter(
            x=xs,
            y=ys,
            mode="markers",
            name="raw samples",
            marker={"size": 3, "color": "rgba(255,255,255,0.35)"},
            hovertemplate="x=%{x:.4f} y=%{y:.4f}<extra></extra>",
        )
    )
    fig.add_shape(
        type="rect",
        x0=x_lo,
        x1=x_hi,
        y0=y_lo,
        y1=y_hi,
        line={"color": "#636EFA", "width": 1, "dash": "dot"},
        fillcolor="rgba(0,0,0,0)",
    )
    fig.add_trace(
        go.Scatter(
            x=[record.center.x],
            y=[record.center.y],
            mode="markers",
            name="start centre",
            marker={"size": 10, "symbol": "x", "color": "#636EFA"},
        )
    )
    fig.add_trace(
        go.Scatter(
            x=[record.result.x],
            y=[record.result.y],
            mode="markers",
            name="result",
            marker={
                "size": 14,
                "symbol": "star",
                "color": "#EF553B",
                "line": {"width": 1, "color": "white"},
            },
        )
    )

    pass_lines = [
        f"Result: ({record.result.x:+.4f}, {record.result.y:+.4f}) mm  "
        f"{len(record.samples)} samples  {freq_range}"
    ]
    final = f"Final: ({record.result.x:+.4f}, {record.result.y:+.4f}) mm"
    fig.update_layout(
        xaxis_title="X offset (mm)",
        yaxis_title="Y offset (mm)",
        **xy_session_layout(
            f"One shot  search={search_for}",
            pass_lines,
            final=final,
        ),
    )
    return fig

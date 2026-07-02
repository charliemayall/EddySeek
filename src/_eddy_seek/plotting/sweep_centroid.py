"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.

2D scatter debug plots for SweepCentroidStrategy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ..common import Phase, Position
from ..continuous_motion import MotionSample
from ._plotly import (
    freq_marker,
    go,
    pass_color,
    plotly_available,
    session_stats_title,
    square_xy_plot_layout,
)


@dataclass(frozen=True, slots=True)
class SweepCentroidPassRecord:
    pass_num: int
    phase: Phase
    center: Position
    result: Position
    moved: Position
    samples: list[MotionSample]
    box: tuple[float, float, float, float]


def write_sweep_centroid_session_plot(
    *,
    passes: list[SweepCentroidPassRecord],
    search_for: Literal["min", "max"],
) -> Any | None:
    if not plotly_available() or go is None or not passes:
        return None

    fig = go.Figure()
    for record in passes:
        color = pass_color(record.pass_num)
        label = f"pass {record.pass_num} ({record.phase.value})"
        xs = [sample.offset.x for sample in record.samples]
        ys = [sample.offset.y for sample in record.samples]
        freqs = [sample.freq for sample in record.samples]
        fig.add_trace(
            go.Scatter(
                x=xs,
                y=ys,
                mode="markers",
                name=f"{label} samples",
                marker=freq_marker(freqs, search_for),
                text=[f"{freq:.1f} Hz" for freq in freqs],
                hovertemplate=(
                    f"{label}<br>x=%{{x:.4f}} y=%{{y:.4f}} %{{text}}<extra></extra>"
                ),
                legendgroup=label,
            )
        )
        x_lo, x_hi, y_lo, y_hi = record.box
        fig.add_shape(
            type="rect",
            x0=x_lo,
            x1=x_hi,
            y0=y_lo,
            y1=y_hi,
            line={"color": color, "width": 1, "dash": "dot"},
            fillcolor="rgba(0,0,0,0)",
        )
        fig.add_trace(
            go.Scatter(
                x=[record.center.x],
                y=[record.center.y],
                mode="markers",
                name=f"{label} centre",
                marker={"size": 10, "symbol": "x", "color": color},
                legendgroup=label,
                showlegend=False,
            )
        )
        fig.add_trace(
            go.Scatter(
                x=[record.result.x],
                y=[record.result.y],
                mode="markers",
                name=f"{label} result",
                marker={
                    "size": 14 if record is passes[-1] else 11,
                    "symbol": "star",
                    "color": color,
                    "line": {"width": 1, "color": "white"},
                },
                legendgroup=label,
            )
        )

    pass_lines = []
    for record in passes:
        freqs = [sample.freq for sample in record.samples]
        freq_range = (
            f"freq=[{min(freqs):.0f}, {max(freqs):.0f}] Hz" if freqs else "freq=n/a"
        )
        pass_lines.append(
            f"Pass {record.pass_num} ({record.phase.value}): "
            f"result=({record.result.x:+.4f}, {record.result.y:+.4f}) mm  "
            f"moved=({record.moved.x:.4f}, {record.moved.y:.4f})  "
            f"{len(record.samples)} samples  {freq_range}"
        )
    final = passes[-1].result
    fig.update_layout(
        title=session_stats_title(
            f"Sweep centroid ({len(passes)} pass"
            f"{'' if len(passes) == 1 else 'es'})  search={search_for}",
            pass_lines,
            final=f"Final: ({final.x:+.4f}, {final.y:+.4f}) mm",
        ),
        xaxis_title="X offset (mm)",
        yaxis_title="Y offset (mm)",
        legend={"orientation": "h", "y": 1.02, "x": 0},
        **square_xy_plot_layout(title_lines=len(passes)),
    )
    return fig

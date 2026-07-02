"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.

3×3 grid debug plots for CentroidStrategy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ..common import Position
from ._plotly import freq_marker, go, pass_color, plotly_available, xy_session_layout


@dataclass(frozen=True, slots=True)
class CentroidPassRecord:
    pass_num: int
    center: Position
    result: Position
    moved: Position
    probes: list[tuple[Position, float]]


def write_centroid_session_plot(
    *,
    passes: list[CentroidPassRecord],
    search_for: Literal["min", "max"],
) -> Any | None:
    if not plotly_available() or go is None or not passes:
        return None

    fig = go.Figure()
    for record in passes:
        color = pass_color(record.pass_num)
        label = f"pass {record.pass_num}"
        if record.probes:
            xs = [position.x for position, _ in record.probes]
            ys = [position.y for position, _ in record.probes]
            freqs = [freq for _, freq in record.probes]
            fig.add_trace(
                go.Scatter(
                    x=xs,
                    y=ys,
                    mode="markers+lines",
                    name=f"{label} probes",
                    line={"color": color, "width": 1},
                    marker=freq_marker(freqs, search_for, size=9, opacity=1.0),
                    text=[f"{freq:.1f} Hz" for freq in freqs],
                    hovertemplate=(
                        f"{label}<br>x=%{{x:.4f}} y=%{{y:.4f}} %{{text}}<extra></extra>"
                    ),
                    legendgroup=label,
                )
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
        freqs = [freq for _, freq in record.probes]
        freq_range = (
            f"freq=[{min(freqs):.0f}, {max(freqs):.0f}] Hz" if freqs else "freq=n/a"
        )
        pass_lines.append(
            f"Pass {record.pass_num}: result=({record.result.x:+.4f}, "
            f"{record.result.y:+.4f}) mm  moved=({record.moved.x:.4f}, "
            f"{record.moved.y:.4f})  {freq_range}"
        )
    final = passes[-1].result
    fig.update_layout(
        xaxis_title="X offset (mm)",
        yaxis_title="Y offset (mm)",
        **xy_session_layout(
            f"Centroid alignment ({len(passes)} pass"
            f"{'' if len(passes) == 1 else 'es'})  search={search_for}",
            pass_lines,
            final=f"Final: ({final.x:+.4f}, {final.y:+.4f}) mm",
        ),
    )
    return fig

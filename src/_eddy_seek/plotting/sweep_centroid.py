"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

2D scatter debug plots for SweepCentroidStrategy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ..common import Offset, Phase
from ..movement.handler import MotionSample
from ._plotly import (
    apply_axes_theme,
    freq_marker,
    go,
    marker_outline,
    pass_color,
    plotly_available,
    xy_session_layout,
)


@dataclass(frozen=True, slots=True)
class SweepCentroidPassRecord:
    pass_num: int
    phase: Phase
    center: Offset
    result: Offset
    moved: Offset
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
                    "line": {"width": 1, "color": marker_outline()},
                },
                legendgroup=label,
            )
        )

    pass_rows: list[dict[str, str]] = []
    for record in passes:
        freqs = [sample.freq for sample in record.samples]
        pass_rows.append(
            {
                "pass": str(record.pass_num),
                "phase": record.phase.value,
                "result": f"({record.result.x:+.4f}, {record.result.y:+.4f})",
                "moved": f"({record.moved.x:.4f}, {record.moved.y:.4f})",
                "samples": str(len(record.samples)),
                "freq": (f"[{min(freqs):.0f}, {max(freqs):.0f}]" if freqs else "n/a"),
            }
        )
    final = passes[-1].result
    fig.update_layout(
        xaxis_title="X offset (mm)",
        yaxis_title="Y offset (mm)",
        **xy_session_layout(
            f"Sweep centroid ({len(passes)} pass"
            f"{'' if len(passes) == 1 else 'es'})  search={search_for}",
            columns=[
                ("pass", "Pass"),
                ("phase", "Phase"),
                ("result", "Result (mm)"),
                ("moved", "Moved (mm)"),
                ("samples", "Samples"),
                ("freq", "Freq (Hz)"),
            ],
            rows=pass_rows,
            final=f"Final: ({final.x:+.4f}, {final.y:+.4f}) mm",
        ),
    )
    apply_axes_theme(fig)
    return fig

"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Circle-harmonic session plot renderer.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any, Literal

from ..common import Offset
from ..plotting._plotly import (
    apply_axes_theme,
    freq_marker,
    go,
    header_table,
    make_subplots,
    marker_outline,
    multi_panel_layout,
    plotly_available,
)
from ..plotting.primitives import (
    CircleBootstrapRecord,
    CircleHarmonicPassRecord,
    pass_color,
)
from ..plotting.registry import StrategyPlotter, register_plotter


@register_plotter("circle_harmonic")
class CircleHarmonicPlotter(StrategyPlotter):
    def render(
        self,
        records: Sequence[Any],
        *,
        search_for: Literal["min", "max"],
    ) -> Any | None:
        if not plotly_available() or go is None or make_subplots is None:
            return None

        bootstrap = next(
            (record for record in records if isinstance(record, CircleBootstrapRecord)),
            None,
        )
        circles = [
            record for record in records if isinstance(record, CircleHarmonicPassRecord)
        ]
        if bootstrap is None and not circles:
            return None

        fig = make_subplots(
            rows=2,
            cols=1,
            shared_xaxes=False,
            vertical_spacing=0.14,
            row_heights=[0.5, 0.5],
            subplot_titles=("XY samples", "Circle harmonic (θ vs Hz)"),
        )
        pass_rows: list[dict[str, str]] = []

        if bootstrap is not None:
            color = pass_color(bootstrap.pass_num)
            label = f"pass {bootstrap.pass_num} (bootstrap)"
            freqs = bootstrap.samples.freqs or ()
            fig.add_trace(
                go.Scatter(
                    x=list(bootstrap.samples.xs),
                    y=list(bootstrap.samples.ys),
                    mode="markers",
                    name=f"{label} sweeps",
                    marker=freq_marker(list(freqs), search_for, size=4, opacity=0.55),
                    text=[f"{freq:.1f} Hz" for freq in freqs],
                    hovertemplate=(
                        f"{label}<br>x=%{{x:.4f}} y=%{{y:.4f}} %{{text}}<extra></extra>"
                    ),
                    legendgroup=label,
                ),
                row=1,
                col=1,
            )
            fig.add_shape(
                type="rect",
                x0=bootstrap.bounds.lo.x,
                x1=bootstrap.bounds.hi.x,
                y0=bootstrap.bounds.lo.y,
                y1=bootstrap.bounds.hi.y,
                line={"color": color, "width": 1, "dash": "dot"},
                fillcolor="rgba(0,0,0,0)",
                row=1,
                col=1,
            )
            fig.add_trace(
                go.Scatter(
                    x=[bootstrap.move.result.x],
                    y=[bootstrap.move.result.y],
                    mode="markers",
                    name=f"{label} result",
                    marker={
                        "size": 12,
                        "symbol": "star",
                        "color": color,
                        "line": {"width": 1, "color": marker_outline()},
                    },
                    legendgroup=label,
                ),
                row=1,
                col=1,
            )
            pass_rows.append(
                {
                    "pass": str(bootstrap.pass_num),
                    "kind": "bootstrap",
                    "result": (
                        f"({bootstrap.move.result.x:+.4f}, {bootstrap.move.result.y:+.4f})"
                    ),
                    "moved": bootstrap.move.moved.to_delta_str(),
                    "radius": "n/a",
                    "fit": "centroid",
                    "status": "ok",
                }
            )

        for record in circles:
            color = pass_color(record.pass_num)
            label = f"pass {record.pass_num} (circle r={record.radius:.2f})"
            freqs = record.samples.freqs or ()
            fig.add_trace(
                go.Scatter(
                    x=list(record.samples.xs),
                    y=list(record.samples.ys),
                    mode="markers",
                    name=f"{label} trace",
                    marker=freq_marker(list(freqs), search_for, size=3, opacity=0.45),
                    text=[f"{freq:.1f} Hz" for freq in freqs],
                    hovertemplate=(
                        f"{label}<br>x=%{{x:.4f}} y=%{{y:.4f}} %{{text}}<extra></extra>"
                    ),
                    legendgroup=label,
                ),
                row=1,
                col=1,
            )
            _add_circle_shape(
                fig,
                record.trace_center,
                record.radius,
                color,
            )
            thetas_deg = [math.degrees(theta) for theta in record.binned.thetas]
            fig.add_trace(
                go.Scatter(
                    x=thetas_deg,
                    y=list(record.binned.freqs),
                    mode="markers",
                    name=f"{label} binned",
                    marker={"size": 7, "color": color, "symbol": "circle"},
                    legendgroup=label,
                ),
                row=2,
                col=1,
            )
            if record.fit is not None:
                fit_x = [float(deg) for deg in range(361)]
                fit_y = [
                    record.fit.c0
                    + record.fit.a * math.cos(math.radians(deg))
                    + record.fit.b * math.sin(math.radians(deg))
                    for deg in fit_x
                ]
                fig.add_trace(
                    go.Scatter(
                        x=fit_x,
                        y=fit_y,
                        mode="lines",
                        name=f"{label} fit",
                        line={
                            "color": color,
                            "width": 2,
                            "dash": "dash" if record.rejected else "solid",
                        },
                        legendgroup=label,
                        showlegend=True,
                    ),
                    row=2,
                    col=1,
                )
            fig.add_trace(
                go.Scatter(
                    x=[record.move.result.x],
                    y=[record.move.result.y],
                    mode="markers",
                    name=f"{label} result",
                    marker={
                        "size": 13 if record is circles[-1] else 10,
                        "symbol": "star",
                        "color": color,
                        "line": {"width": 1, "color": marker_outline()},
                    },
                    legendgroup=label,
                ),
                row=1,
                col=1,
            )
            fit_summary = "failed"
            if record.fit is not None:
                fit_summary = (
                    f"amp={record.fit.amplitude:.2f} noise={record.fit.noise:.2f}"
                )
            status = record.reject_reasons if record.rejected else "ok"
            pass_rows.append(
                {
                    "pass": str(record.pass_num),
                    "kind": "circle",
                    "result": (
                        f"({record.move.result.x:+.4f}, {record.move.result.y:+.4f})"
                    ),
                    "moved": record.move.moved.to_delta_str(),
                    "radius": f"{record.radius:.3f}",
                    "fit": fit_summary,
                    "status": status,
                }
            )

        final = (
            circles[-1].move.result
            if circles
            else bootstrap.move.result
            if bootstrap
            else Offset.zero()
        )
        title = "Circle harmonic"
        if bootstrap is not None and circles:
            title = f"Circle harmonic ({1 + len(circles)} passes)"
        elif bootstrap is not None:
            title = "Circle harmonic (bootstrap only)"
        elif circles:
            title = (
                f"Circle harmonic ({len(circles)} circle pass"
                f"{'' if len(circles) == 1 else 'es'})"
            )

        fig.update_layout(
            **multi_panel_layout(
                rows=2,
                cols=1,
                title=title,
                tables=[
                    header_table(
                        [
                            ("pass", "Pass"),
                            ("kind", "Kind"),
                            ("result", "Result (mm)"),
                            ("moved", "Moved"),
                            ("radius", "Radius (mm)"),
                            ("fit", "Harmonic fit"),
                            ("status", "Status"),
                        ],
                        pass_rows,
                    )
                ],
                final=f"Final: ({final.x:+.4f}, {final.y:+.4f}) mm",
                row_height_px=380,
            )
        )
        fig.update_xaxes(title_text="X offset (mm)", row=1, col=1)
        fig.update_yaxes(
            title_text="Y offset (mm)",
            scaleanchor="x",
            scaleratio=1,
            row=1,
            col=1,
        )
        fig.update_xaxes(title_text="θ (deg)", range=[0, 360], row=2, col=1)
        fig.update_yaxes(title_text="Frequency (Hz)", row=2, col=1)
        apply_axes_theme(fig)
        return fig


def _add_circle_shape(fig: Any, center: Offset, radius: float, color: str) -> None:
    points = 72
    xs = [
        center.x + radius * math.cos(2.0 * math.pi * index / points)
        for index in range(points + 1)
    ]
    ys = [
        center.y + radius * math.sin(2.0 * math.pi * index / points)
        for index in range(points + 1)
    ]
    fig.add_trace(
        go.Scatter(
            x=xs,
            y=ys,
            mode="lines",
            line={"color": color, "width": 1, "dash": "dot"},
            showlegend=False,
            hoverinfo="skip",
        ),
        row=1,
        col=1,
    )

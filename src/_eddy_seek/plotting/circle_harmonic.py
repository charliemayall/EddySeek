"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Circle-harmonic strategy session plot.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any, Literal

from ._plotly import (
    THEME_COLORS,
    apply_axes_theme,
    go,
    header_table,
    make_subplots,
    multi_panel_layout,
    plotly_available,
)
from .primitives import CircleHarmonicPassRecord, pass_color
from .registry import StrategyPlotter, register_plotter
from .renderer import MarkerRecord, ScatterRecord, add_marker, add_scatter


def render_circle_harmonic_figure(
    records: Sequence[Any],
    *,
    search_for: Literal["min", "max"],
) -> Any | None:
    if not plotly_available() or go is None or make_subplots is None:
        return None

    passes = [
        record
        for record in records
        if isinstance(record, CircleHarmonicPassRecord)
    ]
    if not passes:
        return None

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=False,
        vertical_spacing=0.14,
        row_heights=[0.5, 0.5],
        subplot_titles=("XY samples", "f(θ)  1st + 2nd harmonic"),
    )
    pass_rows: list[dict[str, str]] = []

    for record in passes:
        color = pass_color(record.pass_num)
        if record.kind == "bootstrap":
            label = f"pass {record.pass_num} (bootstrap)"
        else:
            radius = record.radius if record.radius is not None else 0.0
            label = f"pass {record.pass_num} (circle r={radius:.2f})"
        add_scatter(
            fig,
            ScatterRecord(record.pass_num, f"{label} samples", record.samples),
            search_for,
            color,
            marker_size=3 if record.kind == "circle" else 4,
            marker_opacity=0.5,
            row=1,
            col=1,
        )
        add_marker(
            fig,
            MarkerRecord(
                record.pass_num,
                f"{label} result",
                record.move.result,
                "star",
            ),
            color,
            size=12 if record is passes[-1] else 10,
            row=1,
            col=1,
        )
        if record.kind == "circle" and record.binned:
            _add_theta_traces(fig, record, color, label)
        pass_rows.append(_pass_row(record))

    for deg, name in ((0, "long +X"), (90, "short +Y"), (180, "long -X"), (270, "short -Y")):
        fig.add_vline(
            x=deg,
            line_dash="dot",
            line_color=THEME_COLORS.muted,
            line_width=1,
            annotation_text=name,
            annotation_font_size=9,
            annotation_font_color=THEME_COLORS.muted,
            row=2,
            col=1,
        )

    final = passes[-1].move.result
    title = f"Circle harmonic ({len(passes)} passes)"
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
    apply_axes_theme(fig)
    fig.update_xaxes(title_text="X (mm)", row=1, col=1)
    fig.update_yaxes(title_text="Y (mm)", scaleanchor="x", scaleratio=1, row=1, col=1)
    fig.update_xaxes(title_text="θ (deg)", range=[-5, 365], row=2, col=1)
    fig.update_yaxes(title_text="Hz", row=2, col=1)
    return fig


def _add_theta_traces(
    fig: Any,
    record: CircleHarmonicPassRecord,
    color: str,
    label: str,
) -> None:
    thetas_deg = [math.degrees(theta) for theta, _ in record.binned]
    freqs = [freq for _, freq in record.binned]
    fig.add_trace(
        go.Scatter(
            x=thetas_deg,
            y=freqs,
            mode="markers",
            name=f"{label} binned",
            marker={"size": 7, "color": color, "symbol": "circle"},
            legendgroup=label,
        ),
        row=2,
        col=1,
    )
    if record.c0 is None or record.a is None or record.b is None:
        return
    fit_x = [float(deg) for deg in range(361)]
    c2 = record.c2 or 0.0
    d2 = record.d2 or 0.0
    fig.add_trace(
        go.Scatter(
            x=fit_x,
            y=[
                record.c0 + record.a * math.cos(math.radians(deg)) + record.b * math.sin(math.radians(deg))
                for deg in fit_x
            ],
            mode="lines",
            name=f"{label} 1θ",
            line={"color": color, "width": 1, "dash": "dot"},
            legendgroup=label,
        ),
        row=2,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=fit_x,
            y=[
                record.c0 + c2 * math.cos(2.0 * math.radians(deg)) + d2 * math.sin(2.0 * math.radians(deg))
                for deg in fit_x
            ],
            mode="lines",
            name=f"{label} 2θ",
            line={"color": color, "width": 1, "dash": "dashdot"},
            legendgroup=label,
        ),
        row=2,
        col=1,
    )
    dash = "dash" if not record.accepted else "solid"
    fig.add_trace(
        go.Scatter(
            x=fit_x,
            y=[
                record.c0
                + record.a * math.cos(math.radians(deg))
                + record.b * math.sin(math.radians(deg))
                + c2 * math.cos(2.0 * math.radians(deg))
                + d2 * math.sin(2.0 * math.radians(deg))
                for deg in fit_x
            ],
            mode="lines",
            name=f"{label} 1θ+2θ",
            line={"color": color, "width": 2, "dash": dash},
            legendgroup=label,
        ),
        row=2,
        col=1,
    )


def _pass_row(record: CircleHarmonicPassRecord) -> dict[str, str]:
    if record.kind == "bootstrap":
        fit_summary = "centroid"
        radius = "n/a"
    elif record.amp1 is not None and record.amp2 is not None:
        fit_summary = f"amp1={record.amp1:.1f} amp2={record.amp2:.1f}"
        radius = f"{record.radius:.3f}" if record.radius is not None else "n/a"
    else:
        fit_summary = "failed"
        radius = f"{record.radius:.3f}" if record.radius is not None else "n/a"
    if record.kind == "circle" and record.quality is not None:
        fit_summary = f"{fit_summary} q={record.quality:.2f}"
    return {
        "pass": str(record.pass_num),
        "kind": record.kind,
        "result": f"({record.move.result.x:+.4f}, {record.move.result.y:+.4f})",
        "moved": record.move.moved.to_delta_str(),
        "radius": radius,
        "fit": fit_summary,
        "status": "ok" if record.accepted else "rejected",
    }


@register_plotter("circle_harmonic")
class CircleHarmonicPlotter(StrategyPlotter):
    def render(
        self,
        records: Sequence[Any],
        *,
        search_for: Literal["min", "max"],
    ) -> Any | None:
        return render_circle_harmonic_figure(records, search_for=search_for)

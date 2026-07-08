"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Debug scan heatmap figure builders.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any, Literal

from ..common import Offset
from ..movement.handler import MotionSample
from ._plotly import (
    _DEBUG_ROW_HEIGHT_PX,
    COLORSCALE,
    THEME_COLORS,
    apply_axes_theme,
    go,
    header_table,
    make_subplots,
    multi_panel_layout,
    plotly_available,
)
from .debug_scan_analysis import (
    DebugScanAnalysis,
    analyze_debug_scan,
    format_optional,
    format_position,
    grid_tolerance,
    motion_samples,
    scaled_bin_result,
)
from .primitives import HeatmapRecord
from .registry import StrategyPlotter, register_plotter

ALT_BIN_SCALES = (2, 4, 8)

_ESTIMATOR_STYLES: dict[str, dict[str, Any]] = {
    "bin": {"color": "#EF553B", "symbol": "star", "size": 12},
    "centroid": {"color": "#00CC96", "symbol": "diamond", "size": 9},
    "axis": {"color": "#FFA15A", "symbol": "circle", "size": 9},
    "parabolic": {"color": "#AB63FA", "symbol": "cross", "size": 10},
}


def _bin_edges(centers: list[float], tolerance: float) -> list[float]:
    half = tolerance / 2.0
    if not centers:
        return []
    edges = [centers[0] - half]
    edges.extend(center + half for center in centers)
    return edges


def _z_for_display(z: Sequence[Sequence[float | None]]) -> list[list[float]]:
    return [
        [value if value is not None else float("nan") for value in row] for row in z
    ]


def _add_estimator_markers(
    fig: Any,
    *,
    row: int,
    col: int,
    analysis: DebugScanAnalysis,
    show_legend: bool,
) -> None:
    estimators: list[tuple[str, Offset | None]] = [
        ("bin", analysis.bin_peak),
        ("centroid", analysis.centroid),
        ("axis", analysis.axis),
        ("parabolic", analysis.parabolic),
    ]
    for name, position in estimators:
        if position is None:
            continue
        style = _ESTIMATOR_STYLES[name]
        fig.add_trace(
            go.Scatter(
                x=[position.x],
                y=[position.y],
                mode="markers",
                name=f"{name} est.",
                marker={
                    "size": style["size"],
                    "symbol": style["symbol"],
                    "color": style["color"],
                    "line": {"width": 1, "color": "white"},
                },
                legendgroup=f"est-{name}",
                showlegend=show_legend and name != "bin",
            ),
            row=row,
            col=col,
        )


def _add_heatmap_panel(
    fig: Any,
    *,
    row: int,
    col: int,
    z: list[list[float | None]] | list[list[int]],
    x_centers: list[float],
    y_centers: list[float],
    box: tuple[float, float, float, float],
    center: Offset,
    result: Offset | None,
    tolerance: float,
    show_colorbar: bool,
    show_samples: bool,
    samples: list[MotionSample],
    colorscale: str,
    colorbar_title: str,
    analysis: DebugScanAnalysis | None = None,
    show_estimators: bool = False,
    show_legend: bool = False,
) -> None:
    x_lo, x_hi, y_lo, y_hi = box
    x_edges = _bin_edges(x_centers, tolerance)
    y_edges = _bin_edges(y_centers, tolerance)
    if z and isinstance(z[0][0], int):
        display_z = [
            [float(value) if value is not None else float("nan") for value in row]
            for row in z
        ]
    else:
        display_z = _z_for_display(z)
    fig.add_trace(
        go.Heatmap(
            x=x_edges,
            y=y_edges,
            z=display_z,
            colorscale=colorscale,
            colorbar=(
                {
                    "title": {
                        "text": colorbar_title,
                        "font": {"color": THEME_COLORS.text, "size": 10},
                    },
                    "tickfont": {"color": THEME_COLORS.text, "size": 9},
                    "x": 1.02,
                    "xanchor": "left",
                    "len": 0.35,
                    "thickness": 12,
                }
                if show_colorbar
                else None
            ),
            showscale=show_colorbar,
            hovertemplate="x=%{x:.4f} y=%{y:.4f} %{z:.3f}<extra></extra>",
            name=f"{tolerance:.4g} mm bins",
            legendgroup=f"panel-{row}-{col}",
        ),
        row=row,
        col=col,
    )
    if show_samples:
        fig.add_trace(
            go.Scatter(
                x=[sample.offset.x for sample in samples],
                y=[sample.offset.y for sample in samples],
                mode="markers",
                name="raw samples",
                marker={"size": 2, "color": "rgba(255,255,255,0.35)"},
                hovertemplate="x=%{x:.4f} y=%{y:.4f}<extra></extra>",
                legendgroup=f"panel-{row}-{col}",
                showlegend=show_legend,
            ),
            row=row,
            col=col,
        )
    fig.add_shape(
        type="rect",
        x0=x_lo,
        x1=x_hi,
        y0=y_lo,
        y1=y_hi,
        line={"color": "#636EFA", "width": 1, "dash": "dot"},
        fillcolor="rgba(0,0,0,0)",
        row=row,
        col=col,
    )
    fig.add_trace(
        go.Scatter(
            x=[center.x],
            y=[center.y],
            mode="markers",
            name="start centre",
            marker={"size": 8, "symbol": "x", "color": "#636EFA"},
            legendgroup=f"panel-{row}-{col}",
            showlegend=show_legend,
        ),
        row=row,
        col=col,
    )
    if result is not None:
        fig.add_trace(
            go.Scatter(
                x=[result.x],
                y=[result.y],
                mode="markers",
                name="bin peak",
                marker={
                    "size": 12,
                    "symbol": "star",
                    "color": "#EF553B",
                    "line": {"width": 1, "color": "white"},
                },
                legendgroup=f"panel-{row}-{col}",
                showlegend=show_legend,
            ),
            row=row,
            col=col,
        )
    if show_estimators and analysis is not None:
        _add_estimator_markers(
            fig, row=row, col=col, analysis=analysis, show_legend=show_legend
        )


def _add_marginal_panel(
    fig: Any,
    *,
    row: int,
    col: int,
    profile: list[tuple[float, float]],
    axis_label: str,
    estimators: dict[str, float | None],
    fwhm: float | None,
    show_legend: bool,
) -> None:
    coords = [coord for coord, _ in profile]
    values = [value for _, value in profile]
    fig.add_trace(
        go.Scatter(
            x=coords,
            y=values,
            mode="lines+markers",
            name=axis_label,
            line={"color": "#636EFA", "width": 2},
            marker={"size": 4},
            hovertemplate="offset=%{x:.4f} weight=%{y:.3f}<extra></extra>",
            showlegend=show_legend,
        ),
        row=row,
        col=col,
    )
    peak_value = max(
        (value for value in values if not math.isnan(value)), default=float("nan")
    )
    if not math.isnan(peak_value) and peak_value > 0.0:
        half = peak_value / 2.0
        fig.add_hline(
            y=half,
            line={"color": "rgba(255,255,255,0.45)", "dash": "dot", "width": 1},
            row=row,
            col=col,
        )
    for name, coord in estimators.items():
        if coord is None:
            continue
        style = _ESTIMATOR_STYLES[name]
        fig.add_vline(
            x=coord,
            line={"color": style["color"], "width": 2},
            row=row,
            col=col,
        )
    if fwhm is not None:
        fig.add_annotation(
            text=f"FWHM={fwhm:.3g} mm",
            xref="x domain",
            yref="y domain",
            x=0.98,
            y=0.95,
            xanchor="right",
            yanchor="top",
            showarrow=False,
            font={"size": 9, "color": THEME_COLORS.muted},
            row=row,
            col=col,
        )


def render_debug_scan_figure(
    heatmap: HeatmapRecord,
    *,
    search_for: Literal["min", "max"],
) -> Any | None:
    if not plotly_available() or go is None or make_subplots is None:
        return None

    center = heatmap.move.center
    result = heatmap.move.result
    box = heatmap.bounds.as_box()
    z = [list(row) for row in heatmap.z]
    x_centers = list(heatmap.x_centers)
    y_centers = list(heatmap.y_centers)
    samples = motion_samples(heatmap.samples)

    analysis = analyze_debug_scan(heatmap, search_for)
    base_tolerance = grid_tolerance(x_centers, y_centers, box)
    panels: list[
        tuple[int, float, list[list[float | None]], list[float], list[float], Offset]
    ] = [
        (
            1,
            base_tolerance,
            z,
            x_centers,
            y_centers,
            result,
        ),
    ]
    for scale in ALT_BIN_SCALES:
        tolerance = base_tolerance * scale
        scaled_z, scaled_x, scaled_y, scaled_result = scaled_bin_result(
            heatmap, tolerance, search_for
        )
        panels.append((scale, tolerance, scaled_z, scaled_x, scaled_y, scaled_result))

    freqs = list(heatmap.samples.freqs or ())
    scale_rows = [
        {
            "scale": f"@{scale}×",
            "tolerance": f"{tol:.4g}",
            "result": f"({panel_result.x:+.4f}, {panel_result.y:+.4f})",
        }
        for scale, tol, _, _, _, panel_result in panels
    ]
    summary_rows = [
        {
            "metric": "samples",
            "value": str(len(samples)),
        },
        {
            "metric": "freq",
            "value": (f"[{min(freqs):.0f}, {max(freqs):.0f}] Hz" if freqs else "n/a"),
        },
        {
            "metric": "bin",
            "value": format_position(analysis.bin_peak),
        },
        {
            "metric": "centroid",
            "value": format_position(analysis.centroid),
        },
        {
            "metric": "axis",
            "value": format_position(analysis.axis),
        },
        {
            "metric": "parabolic",
            "value": format_position(analysis.parabolic),
        },
        {
            "metric": "prominence",
            "value": format_optional(analysis.prominence),
        },
        {
            "metric": "FWHM X",
            "value": format_optional(analysis.fwhm_x, unit=" mm"),
        },
        {
            "metric": "FWHM Y",
            "value": format_optional(analysis.fwhm_y, unit=" mm"),
        },
    ]
    final = f"Final (1×): ({result.x:+.4f}, {result.y:+.4f}) mm"

    peak_y = y_centers[analysis.peak_iy]
    peak_x = x_centers[analysis.peak_ix]
    n_rows = 5
    n_cols = 2

    fig = make_subplots(
        rows=n_rows,
        cols=n_cols,
        specs=[
            [{"colspan": 2}, None],
            [{"colspan": 2}, None],
            [{}, {}],
            [{}, {}],
            [{"colspan": 2}, None],
        ],
        subplot_titles=[
            "1× weight",
            "sample density",
            f"X slice @ Y={peak_y:+.3g} mm",
            f"Y slice @ X={peak_x:+.3g} mm",
            "2× bin",
            "4× bin",
            "8× bin",
            "",
            "",
            "",
        ],
        horizontal_spacing=0.06,
        vertical_spacing=0.05,
        shared_xaxes=False,
        shared_yaxes=False,
    )

    _add_heatmap_panel(
        fig,
        row=1,
        col=1,
        z=z,
        x_centers=x_centers,
        y_centers=y_centers,
        box=box,
        center=center,
        result=result,
        tolerance=base_tolerance,
        show_colorbar=True,
        show_samples=True,
        samples=samples,
        colorscale=COLORSCALE,
        colorbar_title="weight",
        analysis=analysis,
        show_estimators=True,
        show_legend=True,
    )
    _add_heatmap_panel(
        fig,
        row=2,
        col=1,
        z=analysis.density,
        x_centers=x_centers,
        y_centers=y_centers,
        box=box,
        center=center,
        result=None,
        tolerance=base_tolerance,
        show_colorbar=False,
        show_samples=False,
        samples=samples,
        colorscale="Blues",
        colorbar_title="count",
        show_legend=False,
    )
    _add_marginal_panel(
        fig,
        row=3,
        col=1,
        profile=analysis.x_marginal,
        axis_label="X slice",
        estimators={
            "bin": analysis.bin_peak.x,
            "centroid": analysis.centroid.x if analysis.centroid else None,
            "axis": analysis.axis.x if analysis.axis else None,
            "parabolic": analysis.parabolic.x if analysis.parabolic else None,
        },
        fwhm=analysis.fwhm_x,
        show_legend=False,
    )
    _add_marginal_panel(
        fig,
        row=3,
        col=2,
        profile=analysis.y_marginal,
        axis_label="Y slice",
        estimators={
            "bin": analysis.bin_peak.y,
            "centroid": analysis.centroid.y if analysis.centroid else None,
            "axis": analysis.axis.y if analysis.axis else None,
            "parabolic": analysis.parabolic.y if analysis.parabolic else None,
        },
        fwhm=analysis.fwhm_y,
        show_legend=False,
    )

    for (row, col), (_scale, tolerance, panel_z, panel_x, panel_y, panel_result) in zip(
        ((4, 1), (4, 2), (5, 1)),
        panels[1:],
        strict=True,
    ):
        _add_heatmap_panel(
            fig,
            row=row,
            col=col,
            z=panel_z,
            x_centers=panel_x,
            y_centers=panel_y,
            box=box,
            center=center,
            result=panel_result,
            tolerance=tolerance,
            show_colorbar=False,
            show_samples=False,
            samples=samples,
            colorscale=COLORSCALE,
            colorbar_title="weight",
            show_legend=False,
        )

    fig.update_layout(
        **multi_panel_layout(
            rows=n_rows,
            cols=n_cols,
            title=f"Debug scan  search={search_for}",
            tables=[
                header_table(
                    [
                        ("scale", "Scale"),
                        ("tolerance", "Tol (mm)"),
                        ("result", "Result (mm)"),
                    ],
                    scale_rows,
                ),
                header_table(
                    [("metric", "Metric"), ("value", "Value")],
                    summary_rows,
                ),
            ],
            final=final,
            row_height_px=_DEBUG_ROW_HEIGHT_PX,
        ),
    )
    for row, col in ((1, 1), (2, 1), (4, 1), (4, 2), (5, 1)):
        fig.update_xaxes(title_text="X offset (mm)", row=row, col=col)
        fig.update_yaxes(title_text="Y offset (mm)", row=row, col=col)
        fig.update_yaxes(scaleanchor="x", scaleratio=1, row=row, col=col)
    fig.update_xaxes(title_text="X offset (mm)", row=3, col=1)
    fig.update_yaxes(title_text="weight", row=3, col=1)
    fig.update_xaxes(title_text="Y offset (mm)", row=3, col=2)
    fig.update_yaxes(title_text="weight", row=3, col=2)
    apply_axes_theme(fig)
    return fig


@register_plotter("debug_scan")
class DebugScanPlotter(StrategyPlotter):
    def render(
        self,
        records: Sequence[Any],
        *,
        search_for: Literal["min", "max"],
    ) -> Any | None:
        heatmap = next(
            (record for record in records if isinstance(record, HeatmapRecord)),
            None,
        )
        if heatmap is None:
            return None
        return render_debug_scan_figure(heatmap, search_for=search_for)

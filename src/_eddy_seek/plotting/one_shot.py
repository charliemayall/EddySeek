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
from ._plotly import (
    go,
    make_subplots,
    plotly_available,
    session_stats_annotation,
    square_xy_plot_layout,
)

ALT_BIN_SCALES = (2, 4, 8)


@dataclass(frozen=True, slots=True)
class OneShotRecord:
    center: Position
    result: Position
    samples: list[MotionSample]
    box: tuple[float, float, float, float]
    z: list[list[float | None]]
    x_centers: list[float]
    y_centers: list[float]


def _bin_edges(centers: list[float], tolerance: float) -> list[float]:
    half = tolerance / 2.0
    if not centers:
        return []
    edges = [centers[0] - half]
    edges.extend(center + half for center in centers)
    return edges


def _grid_tolerance(
    x_centers: list[float],
    y_centers: list[float],
    box: tuple[float, float, float, float],
) -> float:
    if len(x_centers) >= 2:
        return x_centers[1] - x_centers[0]
    if len(y_centers) >= 2:
        return y_centers[1] - y_centers[0]
    x_lo, x_hi, y_lo, y_hi = box
    if x_centers:
        return (x_hi - x_lo) / len(x_centers)
    if y_centers:
        return (y_hi - y_lo) / len(y_centers)
    return 1.0


def _z_for_display(z: list[list[float | None]]) -> list[list[float]]:
    return [
        [value if value is not None else float("nan") for value in row] for row in z
    ]


def _scaled_bin_result(
    record: OneShotRecord,
    tolerance: float,
    search_for: Literal["min", "max"],
) -> tuple[list[list[float | None]], list[float], list[float], Position]:
    from ..strategy.one_shot import bin_frequencies, peak_bin_center

    z, x_centers, y_centers = bin_frequencies(
        record.samples, record.box, tolerance, record.center, search_for
    )
    peak = peak_bin_center(z, x_centers, y_centers)
    return z, x_centers, y_centers, peak if peak is not None else record.center


def _add_heatmap_panel(
    fig: Any,
    *,
    row: int,
    col: int,
    z: list[list[float | None]],
    x_centers: list[float],
    y_centers: list[float],
    box: tuple[float, float, float, float],
    center: Position,
    result: Position,
    tolerance: float,
    show_colorbar: bool,
    show_samples: bool,
    samples: list[MotionSample],
) -> None:
    x_lo, x_hi, y_lo, y_hi = box
    x_edges = _bin_edges(x_centers, tolerance)
    y_edges = _bin_edges(y_centers, tolerance)
    fig.add_trace(
        go.Heatmap(
            x=x_edges,
            y=y_edges,
            z=_z_for_display(z),
            colorscale="Viridis",
            colorbar=(
                {"title": "weight", "x": 1.02, "xanchor": "left", "len": 0.35}
                if show_colorbar
                else None
            ),
            showscale=show_colorbar,
            hovertemplate="x=%{x:.4f} y=%{y:.4f} weight=%{z:.3f}<extra></extra>",
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
                showlegend=row == 1 and col == 1,
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
            showlegend=row == 1 and col == 1,
        ),
        row=row,
        col=col,
    )
    fig.add_trace(
        go.Scatter(
            x=[result.x],
            y=[result.y],
            mode="markers",
            name="result",
            marker={
                "size": 12,
                "symbol": "star",
                "color": "#EF553B",
                "line": {"width": 1, "color": "white"},
            },
            legendgroup=f"panel-{row}-{col}",
            showlegend=row == 1 and col == 1,
        ),
        row=row,
        col=col,
    )


def write_one_shot_plot(
    *,
    record: OneShotRecord | dict[str, Any],
    search_for: Literal["min", "max"],
) -> Any | None:
    if not plotly_available() or go is None or make_subplots is None:
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

    base_tolerance = _grid_tolerance(record.x_centers, record.y_centers, record.box)
    panels: list[
        tuple[int, float, list[list[float | None]], list[float], list[float], Position]
    ] = [
        (
            1,
            base_tolerance,
            record.z,
            record.x_centers,
            record.y_centers,
            record.result,
        ),
    ]
    for scale in ALT_BIN_SCALES:
        tolerance = base_tolerance * scale
        z, x_centers, y_centers, result = _scaled_bin_result(
            record, tolerance, search_for
        )
        panels.append((scale, tolerance, z, x_centers, y_centers, result))

    freqs = [sample.freq for sample in record.samples]
    freq_range = (
        f"freq=[{min(freqs):.0f}, {max(freqs):.0f}] Hz" if freqs else "freq=n/a"
    )
    pass_lines = [
        f"@{scale}× ({tol:.4g} mm): ({result.x:+.4f}, {result.y:+.4f}) mm"
        for scale, tol, _, _, _, result in panels
    ]
    pass_lines.append(f"{len(record.samples)} samples  {freq_range}")
    final = f"Final (1×): ({record.result.x:+.4f}, {record.result.y:+.4f}) mm"

    subplot_titles = [
        f"{scale}× bin ({tol:.4g} mm)" for scale, tol, _, _, _, _ in panels
    ]
    fig = make_subplots(
        rows=2,
        cols=2,
        subplot_titles=subplot_titles,
        horizontal_spacing=0.06,
        vertical_spacing=0.12,
        shared_xaxes=True,
        shared_yaxes=True,
    )
    positions = ((1, 1), (1, 2), (2, 1), (2, 2))
    for (row, col), (scale, tolerance, z, x_centers, y_centers, result) in zip(
        positions, panels, strict=True
    ):
        _add_heatmap_panel(
            fig,
            row=row,
            col=col,
            z=z,
            x_centers=x_centers,
            y_centers=y_centers,
            box=record.box,
            center=record.center,
            result=result,
            tolerance=tolerance,
            show_colorbar=row == 1 and col == 1,
            show_samples=scale == 1,
            samples=record.samples,
        )

    stats_lines = 1 + len(pass_lines) + 1
    layout = square_xy_plot_layout(stats_lines=stats_lines)
    layout["width"] = layout["width"] * 2 - layout["margin"]["l"]
    layout["height"] = layout["height"] * 2 - layout["margin"]["t"] // 2
    layout["annotations"] = [
        session_stats_annotation(
            f"One shot  search={search_for}",
            pass_lines,
            final=final,
        )
    ]
    fig.update_layout(
        xaxis_title="X offset (mm)",
        yaxis_title="Y offset (mm)",
        **layout,
    )
    for row, col in positions:
        fig.update_yaxes(scaleanchor="x", scaleratio=1, row=row, col=col)
    return fig

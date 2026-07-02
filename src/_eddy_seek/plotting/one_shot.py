"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.

Heatmap debug plots for OneShotStrategy.
"""

from __future__ import annotations

import math
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

_ESTIMATOR_STYLES: dict[str, dict[str, Any]] = {
    "bin": {"color": "#EF553B", "symbol": "star", "size": 12},
    "centroid": {"color": "#00CC96", "symbol": "diamond", "size": 9},
    "axis": {"color": "#FFA15A", "symbol": "circle", "size": 9},
    "parabolic": {"color": "#AB63FA", "symbol": "cross", "size": 10},
}


@dataclass(frozen=True, slots=True)
class OneShotRecord:
    center: Position
    result: Position
    samples: list[MotionSample]
    box: tuple[float, float, float, float]
    z: list[list[float | None]]
    x_centers: list[float]
    y_centers: list[float]


@dataclass(frozen=True, slots=True)
class OneShotAnalysis:
    bin_peak: Position
    centroid: Position | None
    axis: Position | None
    parabolic: Position | None
    prominence: float | None
    fwhm_x: float | None
    fwhm_y: float | None
    peak_ix: int
    peak_iy: int
    x_marginal: list[tuple[float, float]]
    y_marginal: list[tuple[float, float]]
    density: list[list[int]]


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


def _peak_bin_indices(
    z: list[list[float | None]],
) -> tuple[int, int, float] | None:
    best_value: float | None = None
    best_ix: int | None = None
    best_iy: int | None = None
    for iy, row in enumerate(z):
        for ix, value in enumerate(row):
            if value is None:
                continue
            if best_value is None or value > best_value:
                best_value, best_ix, best_iy = value, ix, iy
    if best_ix is None or best_iy is None or best_value is None or best_value < 1e-9:
        return None
    return best_ix, best_iy, best_value


def _marginal_slice(
    z: list[list[float | None]],
    x_centers: list[float],
    y_centers: list[float],
    *,
    axis: Literal["x", "y"],
    index: int,
) -> list[tuple[float, float]]:
    if axis == "x":
        profile: list[tuple[float, float]] = []
        for ix, coord in enumerate(x_centers):
            value = z[index][ix]
            profile.append((coord, float("nan") if value is None else value))
        return profile
    profile = []
    for iy, coord in enumerate(y_centers):
        value = z[iy][index]
        profile.append((coord, float("nan") if value is None else value))
    return profile


def _marginal_max(
    z: list[list[float | None]],
    x_centers: list[float],
    y_centers: list[float],
    *,
    axis: Literal["x", "y"],
) -> list[tuple[float, float]]:
    if axis == "x":
        profile: list[tuple[float, float]] = []
        for ix, coord in enumerate(x_centers):
            values = [row[ix] for row in z if ix < len(row) and row[ix] is not None]
            profile.append((coord, max(values) if values else float("nan")))
        return profile
    profile = []
    for iy, coord in enumerate(y_centers):
        values = [cell for cell in z[iy] if cell is not None]
        profile.append((coord, max(values) if values else float("nan")))
    return profile


def _parabolic_peak(
    coords: list[float],
    profile: list[tuple[float, float]],
    peak_index: int,
) -> float | None:
    if peak_index <= 0 or peak_index >= len(coords) - 1:
        return None
    z_m = profile[peak_index - 1][1]
    z_0 = profile[peak_index][1]
    z_p = profile[peak_index + 1][1]
    if any(math.isnan(value) for value in (z_m, z_0, z_p)):
        return None
    denom = z_m - 2.0 * z_0 + z_p
    if abs(denom) < 1e-12:
        return coords[peak_index]
    step = coords[peak_index + 1] - coords[peak_index]
    return coords[peak_index] + 0.5 * (z_m - z_p) / denom * step


def _crossing(
    profile: list[tuple[float, float]], level: float, *, side: str
) -> float | None:
    peak_index = max(
        range(len(profile)),
        key=lambda index: (
            profile[index][1] if not math.isnan(profile[index][1]) else float("-inf")
        ),
    )
    peak_coord = profile[peak_index][0]
    if side == "left":
        segment = profile[: peak_index + 1]
        segment = list(reversed(segment))
    else:
        segment = profile[peak_index:]
    for index in range(1, len(segment)):
        c0, v0 = segment[index - 1]
        c1, v1 = segment[index]
        if (v0 - level) * (v1 - level) > 0:
            continue
        if abs(v1 - v0) < 1e-12:
            return c1
        fraction = (level - v0) / (v1 - v0)
        return c0 + fraction * (c1 - c0)
    return peak_coord if side == "left" else None


def _fwhm(profile: list[tuple[float, float]]) -> float | None:
    valid = [(coord, value) for coord, value in profile if not math.isnan(value)]
    if len(valid) < 2:
        return None
    peak_value = max(value for _, value in valid)
    if peak_value < 1e-9:
        return None
    half = peak_value / 2.0
    left = _crossing(valid, half, side="left")
    right = _crossing(valid, half, side="right")
    if left is None or right is None:
        return None
    width = right - left
    return width if width > 0.0 else None


def _bin_sample_counts(
    samples: list[MotionSample],
    box: tuple[float, float, float, float],
    tolerance: float,
    center: Position,
) -> list[list[int]]:
    x_lo, x_hi, y_lo, y_hi = box
    n_x_min = math.ceil((x_lo - center.x) / tolerance - 0.5)
    n_x_max = math.floor((x_hi - center.x) / tolerance + 0.5)
    n_y_min = math.ceil((y_lo - center.y) / tolerance - 0.5)
    n_y_max = math.floor((y_hi - center.y) / tolerance + 0.5)
    nx = n_x_max - n_x_min + 1
    ny = n_y_max - n_y_min + 1
    counts = [[0] * nx for _ in range(ny)]
    for sample in samples:
        x = sample.offset.x
        y = sample.offset.y
        if not (x_lo <= x <= x_hi and y_lo <= y <= y_hi):
            continue
        ix = math.floor((x - center.x) / tolerance + 0.5) - n_x_min
        iy = math.floor((y - center.y) / tolerance + 0.5) - n_y_min
        if 0 <= ix < nx and 0 <= iy < ny:
            counts[iy][ix] += 1
    return counts


def analyze_one_shot(
    record: OneShotRecord,
    search_for: Literal["min", "max"],
) -> OneShotAnalysis:
    from ..strategy.centroid import axis_weighted_centroid, weighted_centroid

    tolerance = _grid_tolerance(record.x_centers, record.y_centers, record.box)
    z = record.z
    peak = _peak_bin_indices(z)
    if peak is None:
        density = _bin_sample_counts(
            record.samples, record.box, tolerance, record.center
        )
        return OneShotAnalysis(
            bin_peak=record.result,
            centroid=None,
            axis=None,
            parabolic=None,
            prominence=None,
            fwhm_x=None,
            fwhm_y=None,
            peak_ix=0,
            peak_iy=0,
            x_marginal=_marginal_max(z, record.x_centers, record.y_centers, axis="x"),
            y_marginal=_marginal_max(z, record.x_centers, record.y_centers, axis="y"),
            density=density,
        )

    peak_ix, peak_iy, peak_value = peak
    x_marginal = _marginal_max(z, record.x_centers, record.y_centers, axis="x")
    y_marginal = _marginal_max(z, record.x_centers, record.y_centers, axis="y")
    x_profile = _marginal_slice(
        z, record.x_centers, record.y_centers, axis="x", index=peak_iy
    )
    y_profile = _marginal_slice(
        z, record.x_centers, record.y_centers, axis="y", index=peak_ix
    )

    flat_values = [value for row in z for value in row if value is not None]
    background = sorted(flat_values)[len(flat_values) // 2] if flat_values else 0.0
    prominence = peak_value - background

    probes = [(sample.offset, sample.freq) for sample in record.samples]
    centroid = weighted_centroid(probes, search_for)
    axis_x = axis_weighted_centroid(x_marginal, search_for)
    axis_y = axis_weighted_centroid(y_marginal, search_for)
    axis = (
        Position(axis_x, axis_y) if axis_x is not None and axis_y is not None else None
    )

    x_peak_index = peak_ix
    y_peak_index = peak_iy
    parabolic_x = _parabolic_peak(record.x_centers, x_profile, x_peak_index)
    parabolic_y = _parabolic_peak(record.y_centers, y_profile, y_peak_index)
    parabolic = (
        Position(parabolic_x, parabolic_y)
        if parabolic_x is not None and parabolic_y is not None
        else None
    )

    return OneShotAnalysis(
        bin_peak=Position(record.x_centers[peak_ix], record.y_centers[peak_iy]),
        centroid=centroid,
        axis=axis,
        parabolic=parabolic,
        prominence=prominence,
        fwhm_x=_fwhm(x_profile),
        fwhm_y=_fwhm(y_profile),
        peak_ix=peak_ix,
        peak_iy=peak_iy,
        x_marginal=x_profile,
        y_marginal=y_profile,
        density=_bin_sample_counts(
            record.samples, record.box, tolerance, record.center
        ),
    )


def _format_position(position: Position | None) -> str:
    if position is None:
        return "n/a"
    return f"({position.x:+.4f}, {position.y:+.4f}) mm"


def _format_optional(value: float | None, *, unit: str = "") -> str:
    if value is None:
        return "n/a"
    return f"{value:.4g}{unit}"


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


def _add_estimator_markers(
    fig: Any,
    *,
    row: int,
    col: int,
    analysis: OneShotAnalysis,
    show_legend: bool,
) -> None:
    estimators: list[tuple[str, Position | None]] = [
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
    center: Position,
    result: Position | None,
    tolerance: float,
    show_colorbar: bool,
    show_samples: bool,
    samples: list[MotionSample],
    colorscale: str,
    colorbar_title: str,
    analysis: OneShotAnalysis | None = None,
    show_estimators: bool = False,
    show_legend: bool = False,
) -> None:
    x_lo, x_hi, y_lo, y_hi = box
    x_edges = _bin_edges(x_centers, tolerance)
    y_edges = _bin_edges(y_centers, tolerance)
    if z and isinstance(z[0][0], int):
        display_z = [[float(value) for value in row] for row in z]
    else:
        display_z = _z_for_display(z)  # type: ignore[arg-type]
    fig.add_trace(
        go.Heatmap(
            x=x_edges,
            y=y_edges,
            z=display_z,
            colorscale=colorscale,
            colorbar=(
                {"title": colorbar_title, "x": 1.02, "xanchor": "left", "len": 0.35}
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
            font={"size": 10, "color": "#CCCCCC"},
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

    analysis = analyze_one_shot(record, search_for)
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
    pass_lines.append(
        "bin="
        f"{_format_position(analysis.bin_peak)}  "
        f"centroid={_format_position(analysis.centroid)}  "
        f"axis={_format_position(analysis.axis)}  "
        f"parabolic={_format_position(analysis.parabolic)}"
    )
    pass_lines.append(
        f"prominence={_format_optional(analysis.prominence)}  "
        f"FWHM X={_format_optional(analysis.fwhm_x, unit=' mm')}  "
        f"FWHM Y={_format_optional(analysis.fwhm_y, unit=' mm')}"
    )
    final = f"Final (1×): ({record.result.x:+.4f}, {record.result.y:+.4f}) mm"

    peak_y = record.y_centers[analysis.peak_iy]
    peak_x = record.x_centers[analysis.peak_ix]

    fig = make_subplots(
        rows=3,
        cols=3,
        subplot_titles=[
            "1× weight",
            "sample density",
            f"X slice @ Y={peak_y:+.3g} mm",
            "2× bin",
            "4× bin",
            f"Y slice @ X={peak_x:+.3g} mm",
            "8× bin",
            "",
            "",
        ],
        specs=[
            [{}, {}, {}],
            [{}, {}, {}],
            [{"colspan": 1}, None, None],
        ],
        horizontal_spacing=0.06,
        vertical_spacing=0.1,
        shared_xaxes=False,
        shared_yaxes=False,
    )

    _add_heatmap_panel(
        fig,
        row=1,
        col=1,
        z=record.z,
        x_centers=record.x_centers,
        y_centers=record.y_centers,
        box=record.box,
        center=record.center,
        result=record.result,
        tolerance=base_tolerance,
        show_colorbar=True,
        show_samples=True,
        samples=record.samples,
        colorscale="Viridis",
        colorbar_title="weight",
        analysis=analysis,
        show_estimators=True,
        show_legend=True,
    )
    _add_heatmap_panel(
        fig,
        row=1,
        col=2,
        z=analysis.density,
        x_centers=record.x_centers,
        y_centers=record.y_centers,
        box=record.box,
        center=record.center,
        result=None,
        tolerance=base_tolerance,
        show_colorbar=False,
        show_samples=False,
        samples=record.samples,
        colorscale="Blues",
        colorbar_title="count",
        show_legend=False,
    )
    _add_marginal_panel(
        fig,
        row=1,
        col=3,
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

    scale_positions = ((2, 1), (2, 2), (3, 1))
    for (row, col), (scale, tolerance, z, x_centers, y_centers, result) in zip(
        scale_positions, panels[1:], strict=True
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
            show_colorbar=False,
            show_samples=False,
            samples=record.samples,
            colorscale="Viridis",
            colorbar_title="weight",
            show_legend=False,
        )

    _add_marginal_panel(
        fig,
        row=2,
        col=3,
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

    stats_lines = 1 + len(pass_lines) + 1
    layout = square_xy_plot_layout(stats_lines=stats_lines)
    layout["width"] = layout["width"] * 3 - layout["margin"]["l"] * 2
    layout["height"] = layout["height"] * 3 - layout["margin"]["t"]
    layout["annotations"] = [
        session_stats_annotation(
            f"One shot  search={search_for}",
            pass_lines,
            final=final,
        )
    ]
    fig.update_layout(
        **layout,
    )
    fig.update_xaxes(title_text="X offset (mm)", row=1, col=1)
    fig.update_yaxes(title_text="Y offset (mm)", row=1, col=1)
    fig.update_xaxes(title_text="X offset (mm)", row=1, col=2)
    fig.update_yaxes(title_text="Y offset (mm)", row=1, col=2)
    fig.update_xaxes(title_text="X offset (mm)", row=1, col=3)
    fig.update_yaxes(title_text="weight", row=1, col=3)
    fig.update_xaxes(title_text="X offset (mm)", row=2, col=1)
    fig.update_yaxes(title_text="Y offset (mm)", row=2, col=1)
    fig.update_xaxes(title_text="X offset (mm)", row=2, col=2)
    fig.update_yaxes(title_text="Y offset (mm)", row=2, col=2)
    fig.update_xaxes(title_text="Y offset (mm)", row=2, col=3)
    fig.update_yaxes(title_text="weight", row=2, col=3)
    fig.update_xaxes(title_text="X offset (mm)", row=3, col=1)
    fig.update_yaxes(title_text="Y offset (mm)", row=3, col=1)
    square_panels = ((1, 1), (1, 2), (2, 1), (2, 2), (3, 1))
    axis_ids = ("", "2", "4", "5", "7")
    for (row, col), suffix in zip(square_panels, axis_ids, strict=True):
        fig.update_yaxes(scaleanchor=f"x{suffix}", scaleratio=1, row=row, col=col)
    return fig

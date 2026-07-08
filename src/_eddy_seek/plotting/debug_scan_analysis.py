"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Debug scan heatmap analysis helpers (no rendering).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from ..common import Offset
from ..movement.handler import MotionSample
from .primitives import HeatmapRecord, XYCloud


@dataclass(frozen=True, slots=True)
class DebugScanAnalysis:
    bin_peak: Offset
    centroid: Offset | None
    axis: Offset | None
    parabolic: Offset | None
    prominence: float | None
    fwhm_x: float | None
    fwhm_y: float | None
    peak_ix: int
    peak_iy: int
    x_marginal: list[tuple[float, float]]
    y_marginal: list[tuple[float, float]]
    density: list[list[int]]


def motion_samples(cloud: XYCloud) -> list[MotionSample]:
    freqs = cloud.freqs or (0.0,) * len(cloud.xs)
    return [
        MotionSample(Offset(x, y), freq, 0.0)
        for x, y, freq in zip(cloud.xs, cloud.ys, freqs, strict=True)
    ]


def grid_tolerance(
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
            non_none_values = [v for v in values if v is not None]
            profile.append(
                (coord, max(non_none_values) if non_none_values else float("nan"))
            )
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


def analyze_debug_scan(
    heatmap: HeatmapRecord,
    search_for: Literal["min", "max"],
) -> DebugScanAnalysis:
    from ..optimizer import (
        axis_weighted_centroid,
        bin_sample_counts,
        peak_bin_indices,
        weighted_centroid,
    )

    center = heatmap.move.center
    result = heatmap.move.result
    box = heatmap.bounds.as_box()
    z = [list(row) for row in heatmap.z]
    x_centers = list(heatmap.x_centers)
    y_centers = list(heatmap.y_centers)
    samples = motion_samples(heatmap.samples)

    tolerance = grid_tolerance(x_centers, y_centers, box)
    peak = peak_bin_indices(z)
    if peak is None:
        density = bin_sample_counts(samples, box, tolerance, center)
        return DebugScanAnalysis(
            bin_peak=result,
            centroid=None,
            axis=None,
            parabolic=None,
            prominence=None,
            fwhm_x=None,
            fwhm_y=None,
            peak_ix=0,
            peak_iy=0,
            x_marginal=_marginal_max(z, x_centers, y_centers, axis="x"),
            y_marginal=_marginal_max(z, x_centers, y_centers, axis="y"),
            density=density,
        )

    peak_ix, peak_iy, peak_value = peak
    x_marginal = _marginal_max(z, x_centers, y_centers, axis="x")
    y_marginal = _marginal_max(z, x_centers, y_centers, axis="y")
    x_profile = _marginal_slice(z, x_centers, y_centers, axis="x", index=peak_iy)
    y_profile = _marginal_slice(z, x_centers, y_centers, axis="y", index=peak_ix)

    flat_values = [value for row in z for value in row if value is not None]
    background = sorted(flat_values)[len(flat_values) // 2] if flat_values else 0.0
    prominence = peak_value - background

    probes = [(sample.offset, sample.freq) for sample in samples]
    centroid = weighted_centroid(probes, search_for)
    axis_x = axis_weighted_centroid(x_marginal, search_for)
    axis_y = axis_weighted_centroid(y_marginal, search_for)
    axis = Offset(axis_x, axis_y) if axis_x is not None and axis_y is not None else None

    parabolic_x = _parabolic_peak(x_centers, x_profile, peak_ix)
    parabolic_y = _parabolic_peak(y_centers, y_profile, peak_iy)
    parabolic = (
        Offset(parabolic_x, parabolic_y)
        if parabolic_x is not None and parabolic_y is not None
        else None
    )

    return DebugScanAnalysis(
        bin_peak=Offset(x_centers[peak_ix], y_centers[peak_iy]),
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
        density=bin_sample_counts(samples, box, tolerance, center),
    )


def format_position(position: Offset | None) -> str:
    if position is None:
        return "n/a"
    return f"({position.x:+.4f}, {position.y:+.4f}) mm"


def format_optional(value: float | None, *, unit: str = "") -> str:
    if value is None:
        return "n/a"
    return f"{value:.4g}{unit}"


def scaled_bin_result(
    heatmap: HeatmapRecord,
    tolerance: float,
    search_for: Literal["min", "max"],
) -> tuple[list[list[float | None]], list[float], list[float], Offset]:
    from ..optimizer import bin_frequencies, peak_bin_center

    samples = motion_samples(heatmap.samples)
    box = heatmap.bounds.as_box()
    center = heatmap.move.center
    z, x_centers, y_centers = bin_frequencies(
        samples, box, tolerance, center, search_for
    )
    peak = peak_bin_center(z, x_centers, y_centers)
    return z, x_centers, y_centers, peak if peak is not None else heatmap.move.result

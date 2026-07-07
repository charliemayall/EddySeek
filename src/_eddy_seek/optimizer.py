"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Peak finding and frequency-weighting math for seek strategies.
"""

from __future__ import annotations

import math
from typing import Literal

from .common import Offset
from .movement.handler import MotionSample


def frequency_weight(
    freq: float,
    f_min: float,
    f_max: float,
    search_for: Literal["min", "max"],
) -> float:
    if search_for == "min":
        return max(f_max - freq, 0.0)
    return max(freq - f_min, 0.0)


def frequency_is_better(
    f1: float,
    f2: float,
    search_for: Literal["min", "max"],
) -> bool:
    if search_for == "min":
        return f1 < f2
    return f1 > f2


def weighted_centroid(
    probes: list[tuple[Offset, float]],
    search_for: Literal["min", "max"],
) -> Offset | None:
    """Frequency-weighted XY centroid, or ``None`` when the response is flat."""
    if not probes:
        return None
    freqs = [freq for _, freq in probes]
    f_min = min(freqs)
    f_max = max(freqs)
    weights = [frequency_weight(freq, f_min, f_max, search_for) for freq in freqs]
    total_w = sum(weights)
    if total_w < 1e-9:  # prevent division by zero
        return None
    centroid_x = (
        sum(position.x * w for (position, _), w in zip(probes, weights)) / total_w
    )
    centroid_y = (
        sum(position.y * w for (position, _), w in zip(probes, weights)) / total_w
    )
    return Offset(centroid_x, centroid_y)


def axis_weighted_centroid(
    coords_and_freqs: list[tuple[float, float]],
    search_for: Literal["min", "max"],
) -> float | None:
    """1-D frequency-weighted centroid on a single axis profile."""
    if not coords_and_freqs:
        return None
    probes = [(Offset(coord, 0.0), freq) for coord, freq in coords_and_freqs]
    result = weighted_centroid(probes, search_for)
    return result.x if result is not None else None


def decoupled_centroid(
    x_profile: list[tuple[float, float]],
    y_profile: list[tuple[float, float]],
    search_for: Literal["min", "max"],
) -> Offset | None:
    """Independent X/Y weighted centroids from axis sweep profiles."""
    centroid_x = axis_weighted_centroid(x_profile, search_for)
    centroid_y = axis_weighted_centroid(y_profile, search_for)
    if centroid_x is None or centroid_y is None:
        return None
    return Offset(centroid_x, centroid_y)


def _grid_indices(
    box: tuple[float, float, float, float],
    tolerance: float,
    center: Offset,
) -> tuple[int, int, int, int, list[float], list[float]]:
    x_lo, x_hi, y_lo, y_hi = box
    if tolerance <= 0.0:
        raise ValueError("tolerance must be positive")
    n_x_min = math.ceil((x_lo - center.x) / tolerance - 0.5)
    n_x_max = math.floor((x_hi - center.x) / tolerance + 0.5)
    n_y_min = math.ceil((y_lo - center.y) / tolerance - 0.5)
    n_y_max = math.floor((y_hi - center.y) / tolerance + 0.5)
    x_centers = [center.x + index * tolerance for index in range(n_x_min, n_x_max + 1)]
    y_centers = [center.y + index * tolerance for index in range(n_y_min, n_y_max + 1)]
    nx = len(x_centers)
    ny = len(y_centers)
    return n_x_min, n_y_min, nx, ny, x_centers, y_centers


def bin_sample_counts(
    samples: list[MotionSample],
    box: tuple[float, float, float, float],
    tolerance: float,
    center: Offset,
) -> list[list[int]]:
    """Per-bin sample counts on the same grid as ``bin_frequencies``."""
    n_x_min, n_y_min, nx, ny, _, _ = _grid_indices(box, tolerance, center)
    x_lo, x_hi, y_lo, y_hi = box
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


def bin_frequencies(
    samples: list[MotionSample],
    box: tuple[float, float, float, float],
    tolerance: float,
    center: Offset,
    search_for: Literal["min", "max"],
) -> tuple[list[list[float | None]], list[float], list[float]]:
    """Return ``(z[ny][nx] mean weight or None, x_centers, y_centers)``."""
    n_x_min, n_y_min, nx, ny, x_centers, y_centers = _grid_indices(
        box, tolerance, center
    )
    x_lo, x_hi, y_lo, y_hi = box

    in_box_freqs = [
        sample.freq
        for sample in samples
        if x_lo <= sample.offset.x <= x_hi and y_lo <= sample.offset.y <= y_hi
    ]
    if not in_box_freqs:
        z: list[list[float | None]] = [[None] * nx for _ in range(ny)]
        return z, x_centers, y_centers
    f_min = min(in_box_freqs)
    f_max = max(in_box_freqs)

    sums = [[0.0] * nx for _ in range(ny)]
    counts = [[0] * nx for _ in range(ny)]
    for sample in samples:
        x = sample.offset.x
        y = sample.offset.y
        if not (x_lo <= x <= x_hi and y_lo <= y <= y_hi):
            continue
        ix = math.floor((x - center.x) / tolerance + 0.5) - n_x_min
        iy = math.floor((y - center.y) / tolerance + 0.5) - n_y_min
        if not (0 <= ix < nx and 0 <= iy < ny):
            continue
        weight = frequency_weight(sample.freq, f_min, f_max, search_for)
        sums[iy][ix] += weight
        counts[iy][ix] += 1

    z = [
        [sums[iy][ix] / counts[iy][ix] if counts[iy][ix] else None for ix in range(nx)]
        for iy in range(ny)
    ]
    return z, x_centers, y_centers


def peak_bin_indices(
    z: list[list[float | None]],
) -> tuple[int, int, float] | None:
    """Index and value of the bin with highest mean weight."""
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


def peak_bin_center(
    z: list[list[float | None]],
    x_centers: list[float],
    y_centers: list[float],
) -> Offset | None:
    """Bin with highest mean weight. Skip empty bins and flat response."""
    peak = peak_bin_indices(z)
    if peak is None:
        return None
    best_ix, best_iy, _ = peak
    return Offset(x_centers[best_ix], y_centers[best_iy])


def _assert_binning() -> None:
    tolerance = 0.1
    box = (-0.5, 0.5, -0.5, 0.5)
    peak_x, peak_y = 0.05, -0.05
    samples = [
        MotionSample(Offset(peak_x, peak_y), 100.0, 0.0),
        MotionSample(Offset(peak_x + 0.01, peak_y), 100.0, 0.1),
        MotionSample(Offset(-0.2, 0.2), 10.0, 0.2),
    ]
    center = Offset.zero()
    z, x_centers, y_centers = bin_frequencies(samples, box, tolerance, center, "max")
    peak = peak_bin_center(z, x_centers, y_centers)
    assert any(abs(x) <= tolerance / 2 for x in x_centers)
    assert any(abs(y) <= tolerance / 2 for y in y_centers)
    assert peak is not None
    assert abs(peak.x - peak_x) <= tolerance
    assert abs(peak.y - peak_y) <= tolerance
    z_min, _, _ = bin_frequencies(samples, box, tolerance, center, "min")
    low = peak_bin_center(z_min, x_centers, y_centers)
    assert low is not None
    assert low.x < peak.x or low.y > peak.y
    assert peak_bin_center([[]], [], []) is None


if __name__ == "__main__":
    _assert_binning()

"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Pure leg geometry: overscan, raster lines, traverse endpoints, and circle chords.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence

from ..common import Axis, Offset

_TWO_PI = 2.0 * math.pi
_LEAD_IN_FRACTION = 0.25

logger = logging.getLogger(__name__)

def min_leg_span_mm(legs: Sequence[tuple[Offset, Offset]]) -> float:
    if not legs:
        return 0.0
    return min(math.hypot(end.x - start.x, end.y - start.y) for start, end in legs)


def effective_overscan(overscan: float) -> float:
    return min(overscan, 8.0) if overscan > 0.0 else 0.0


def traversal_endpoints(
    axis: Axis,
    lo: float,
    hi: float,
    cross: float,
    overscan: float,
    *,
    reverse: bool = False,
) -> tuple[Offset, Offset]:
    """Session offsets for one continuous sweep traverse (includes overscan)."""
    if hi < lo:
        lo, hi = hi, lo
    ovs = effective_overscan(overscan)
    if not reverse:
        return (
            Offset.from_axis(axis, lo - ovs, cross),
            Offset.from_axis(axis, hi + ovs, cross),
        )
    return (
        Offset.from_axis(axis, hi + ovs, cross),
        Offset.from_axis(axis, lo - ovs, cross),
    )


def iter_cross_offsets(passes: int, offset: float) -> list[float]:
    """Staggered perpendicular offsets: 0, +offset, -offset, …"""
    if passes <= 1:
        return [0.0]
    result = [0.0]
    for i in range(1, passes):
        if i % 2 == 1:
            result.append(offset * ((i + 1) // 2))
        else:
            result.append(-offset * (i // 2))
    return result[:passes]


def y_lines(y_lo: float, y_hi: float, tolerance: float) -> list[float]:
    """Cross-axis coordinates for raster rows, spaced by ``tolerance`` (inclusive)."""
    if tolerance <= 0.0:
        raise ValueError("tolerance must be positive")
    if y_hi < y_lo:
        y_lo, y_hi = y_hi, y_lo
    count = int((y_hi - y_lo) / tolerance) + 1
    return [y_lo + index * tolerance for index in range(count)]


def plan_axis_legs(
    axis: Axis,
    lo: float,
    hi: float,
    cross_center: float,
    cross_offsets: list[float],
    overscan: float,
) -> list[tuple[Offset, Offset]]:
    """Parallel traverses on ``axis`` at staggered cross-axis offsets."""
    legs: list[tuple[Offset, Offset]] = []
    for cross_delta in cross_offsets:
        cross = cross_center + cross_delta
        legs.extend(
            [
                traversal_endpoints(axis, lo, hi, cross, overscan, reverse=reverse)
                for reverse in (False, True)
            ]
        )

    return legs


def plan_grid_legs(
    box: tuple[float, float, float, float],
    tolerance: float,
    overscan: float,
    *,
    axis: Axis = Axis.X,
    serpentine: bool = True,
) -> list[tuple[Offset, Offset]]:
    """Raster sweeps on ``axis`` at lines spaced by ``tolerance`` on the cross axis."""
    x_lo, x_hi, y_lo, y_hi = box
    if axis is Axis.X:
        lo, hi, cross_lo, cross_hi = x_lo, x_hi, y_lo, y_hi
    else:
        lo, hi, cross_lo, cross_hi = y_lo, y_hi, x_lo, x_hi
    legs: list[tuple[Offset, Offset]] = []
    for line_index, cross in enumerate(y_lines(cross_lo, cross_hi, tolerance)):
        if serpentine and line_index % 2 == 1:
            traverses = (True, False)
        else:
            traverses = (False, True)
        legs.extend(
            [
                traversal_endpoints(axis, lo, hi, cross, overscan, reverse=reverse)
                for reverse in traverses
            ]
        )

    return legs


def circle_arc_legs(
    center: Offset,
    radius: float,
    resolution: float,
) -> list[tuple[Offset, Offset]]:
    """
    One revolution as short chords. Segment count from arc length / resolution.

    Same rule as Klipper ``[gcode_arcs]``: ``floor(circumference / resolution)``,
    minimum 3 segments.
    """
    if radius <= 0.0 or resolution <= 0.0:
        return []
    segments = max(3, math.floor(_TWO_PI * radius / resolution))
    legs: list[tuple[Offset, Offset]] = []
    for index in range(segments):
        theta0 = _TWO_PI * index / segments
        theta1 = _TWO_PI * (index + 1) / segments
        start = Offset(
            center.x + radius * math.cos(theta0),
            center.y + radius * math.sin(theta0),
        )
        end = Offset(
            center.x + radius * math.cos(theta1),
            center.y + radius * math.sin(theta1),
        )
        legs.append((start, end))
    return legs


def circle_lead_in_legs(
    legs: Sequence[tuple[Offset, Offset]],
    fraction: float = _LEAD_IN_FRACTION,
) -> list[tuple[Offset, Offset]]:
    """Uncaptured arc chords ending at circle start (θ=0) for cruise warmup."""
    if fraction <= 0.0 or not legs:
        return []
    count = max(3, min(len(legs), math.ceil(len(legs) * fraction)))
    return list(legs[-count:])


def circle_in_jog_box(
    center: Offset,
    radius: float,
    max_x: float,
    max_y: float,
) -> tuple[Offset, float]:
    """Inset centre and shrink radius so the circle stays inside ±max jog."""
    safe_center = center.clamp(max_x - radius, max_y - radius)
    margin_x = max_x - abs(safe_center.x)
    margin_y = max_y - abs(safe_center.y)
    safe_radius = min(radius, margin_x, margin_y)
    if radius > max(margin_x, margin_y):
        logger.warning(
            f"Circle radius {radius} is too large for jog box {max_x}x{max_y}"
        )
    return safe_center, max(safe_radius, 0.0)

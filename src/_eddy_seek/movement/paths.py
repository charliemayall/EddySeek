"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Pure leg geometry: overscan, raster lines, and traverse endpoint planning.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from ..common import Axis, Offset

_COORD_EPS = 1e-9


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


def chord_legs_along_curve(points: Sequence[Offset]) -> list[tuple[Offset, Offset]]:
    """Connect sampled curve points into consecutive leg pairs."""
    if len(points) < 2:
        return []
    return [(points[index], points[index + 1]) for index in range(len(points) - 1)]


def _along_coord(point: Offset, axis: Axis) -> float:
    return point.x if axis is Axis.X else point.y


def _cross_coord(point: Offset, axis: Axis) -> float:
    return point.y if axis is Axis.X else point.x


def _axis_along_offset(axis: Axis, sign: float) -> Offset:
    if axis is Axis.X:
        return Offset(sign, 0.0)
    return Offset(0.0, sign)


def _cubic_bezier_point(
    p0: Offset, p1: Offset, p2: Offset, p3: Offset, t: float
) -> Offset:
    u = 1.0 - t
    uu = u * u
    tt = t * t
    return Offset(
        uu * u * p0.x + 3.0 * uu * t * p1.x + 3.0 * u * tt * p2.x + tt * t * p3.x,
        uu * u * p0.y + 3.0 * uu * t * p1.y + 3.0 * u * tt * p2.y + tt * t * p3.y,
    )


def _polyline_length(points: Sequence[Offset]) -> float:
    return sum(
        points[index].distance_to(points[index + 1]) for index in range(len(points) - 1)
    )


def cubic_bezier_chord_legs(
    p0: Offset,
    p1: Offset,
    p2: Offset,
    p3: Offset,
    resolution: float,
) -> list[tuple[Offset, Offset]]:
    """Sample a cubic Bezier into chord legs spaced by ``resolution``."""
    if resolution <= 0.0:
        return []
    # control-polygon length over-estimates tight curves; good enough for segment count
    segments = max(3, math.floor(_polyline_length((p0, p1, p2, p3)) / resolution))
    points = [
        _cubic_bezier_point(p0, p1, p2, p3, index / segments)
        for index in range(segments + 1)
    ]
    return chord_legs_along_curve(points)


def cross_pass_connector_legs(
    axis: Axis,
    from_pt: Offset,
    to_pt: Offset,
    *,
    lead_mm: float,
    resolution: float,
) -> list[tuple[Offset, Offset]]:
    """Curved connector from reverse end to the next forward start across cross offsets."""
    if from_pt.distance_to(to_pt) < _COORD_EPS:
        return []
    cross_delta = abs(_cross_coord(to_pt, axis) - _cross_coord(from_pt, axis))
    if cross_delta < _COORD_EPS or lead_mm <= 0.0:
        return []
    along_in = _axis_along_offset(axis, -1.0)
    along_out = _axis_along_offset(axis, 1.0)
    lead = Offset(along_in.x * lead_mm, along_in.y * lead_mm)
    p1 = from_pt + lead
    p2 = to_pt - Offset(along_out.x * lead_mm, along_out.y * lead_mm)
    return cubic_bezier_chord_legs(from_pt, p1, p2, to_pt, resolution)


def uturn_connector_legs(
    axis: Axis,
    pivot: Offset,
    *,
    lead_mm: float,
    bulge_mm: float,
    resolution: float,
) -> list[tuple[Offset, Offset]]:
    """Loopback cubic from sweep end to reverse start at the same pivot."""
    if lead_mm <= 0.0 or bulge_mm <= 0.0:
        return []
    if axis is Axis.X:
        p1 = Offset(pivot.x + lead_mm, pivot.y)
        p2 = Offset(pivot.x - lead_mm, pivot.y + bulge_mm)
    else:
        p1 = Offset(pivot.x, pivot.y + lead_mm)
        p2 = Offset(pivot.x + bulge_mm, pivot.y - lead_mm)
    return cubic_bezier_chord_legs(pivot, p1, p2, pivot, resolution)


def _leg_along_component(start: Offset, end: Offset, axis: Axis) -> float:
    delta = end - start
    return _along_coord(delta, axis)


def plan_axis_leg_connectors(
    legs: Sequence[tuple[Offset, Offset]],
    axis: Axis,
    *,
    overscan: float,
    cross_offset: float,
    resolution: float,
) -> list[list[tuple[Offset, Offset]] | None]:
    """Uncaptured connector chords between consecutive axis sweep legs."""
    if len(legs) < 2:
        return []
    ovs = effective_overscan(overscan)
    bulge_mm = min(cross_offset, ovs) if ovs > 0.0 else cross_offset
    lead_uturn = bulge_mm
    connectors: list[list[tuple[Offset, Offset]] | None] = []
    for index in range(len(legs) - 1):
        start_a, end_a = legs[index]
        start_b, end_b = legs[index + 1]
        along_a = _leg_along_component(start_a, end_a, axis)
        along_b = _leg_along_component(start_b, end_b, axis)
        if end_a.distance_to(start_b) < _COORD_EPS:
            if along_a * along_b < 0.0:
                chords = uturn_connector_legs(
                    axis,
                    end_a,
                    lead_mm=lead_uturn,
                    bulge_mm=bulge_mm,
                    resolution=resolution,
                )
            else:
                chords = []
        else:
            cross_delta = abs(_cross_coord(end_a, axis) - _cross_coord(start_b, axis))
            lead = min(ovs, cross_delta) if ovs > 0.0 else cross_delta
            chords = cross_pass_connector_legs(
                axis,
                end_a,
                start_b,
                lead_mm=lead,
                resolution=resolution,
            )
        connectors.append(chords or None)
    return connectors


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

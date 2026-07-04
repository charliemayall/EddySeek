"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Sweep path planning and session capture glue.
"""

from __future__ import annotations

from collections.abc import Sequence

from ...common import Axis, Offset
from ...motion_handler import MotionSample
from ...session import SeekSession

_LDC1612_BULK_HZ = 400.0  # batch bulk client nominal rate


def speed_clamp_for_min_samples(
    *,
    requested_mm_min: float,
    span_mm: float,
    min_samples: int,
    bulk_rate_hz: float = _LDC1612_BULK_HZ,
) -> float:
    """Cap feedrate so an in-range traverse can yield ``min_samples`` at ``bulk_rate_hz``."""
    if span_mm <= 0.0 or min_samples <= 0:
        return requested_mm_min
    cap = span_mm * bulk_rate_hz * 60.0 / min_samples
    return min(requested_mm_min, cap)


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
        for reverse in (False, True):
            legs.append(
                traversal_endpoints(axis, lo, hi, cross, overscan, reverse=reverse)
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
        for reverse in traverses:
            legs.append(
                traversal_endpoints(axis, lo, hi, cross, overscan, reverse=reverse)
            )
    return legs


def capture_legs(
    ctx: SeekSession,
    legs: Sequence[tuple[Offset, Offset]],
    speed: float,
) -> list[MotionSample]:
    """Run continuous capture legs and return merged session-relative samples."""
    handler = ctx.motion
    handler.begin(ctx.session_start)
    handler.run_capture_legs(legs, speed)
    ctx.sync_offset(handler.position)
    return handler.collect_samples()


def _assert_grid_leg_count() -> None:
    box = (-5.0, 5.0, -5.0, 5.0)
    tolerance = 0.1
    rows = len(y_lines(box[2], box[3], tolerance))
    legs = plan_grid_legs(box, tolerance, overscan=1.0)
    assert len(legs) == rows * 2


def _assert_speed_clamp_for_min_samples() -> None:
    cap = speed_clamp_for_min_samples(
        requested_mm_min=3000.0,
        span_mm=2.0,
        min_samples=20,
        bulk_rate_hz=400.0,
    )
    assert cap == 2400.0
    assert (
        speed_clamp_for_min_samples(
            requested_mm_min=1200.0,
            span_mm=2.0,
            min_samples=20,
            bulk_rate_hz=400.0,
        )
        == 1200.0
    )


_assert_grid_leg_count()
_assert_speed_clamp_for_min_samples()

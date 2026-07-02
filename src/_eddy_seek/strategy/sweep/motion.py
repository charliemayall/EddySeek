"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.

Continuous axis sweep path helpers.
"""

from __future__ import annotations

from ...common import Axis, Position  # noqa: TID252 — sweep subpackage


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
) -> tuple[Position, Position]:
    """Session offsets for one continuous sweep traverse (includes overscan)."""
    if hi < lo:
        lo, hi = hi, lo
    ovs = effective_overscan(overscan)
    if not reverse:
        return (
            Position.from_axis(axis, lo - ovs, cross),
            Position.from_axis(axis, hi + ovs, cross),
        )
    return (
        Position.from_axis(axis, hi + ovs, cross),
        Position.from_axis(axis, lo - ovs, cross),
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

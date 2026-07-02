"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.

2D raster sweep leg planning and capture for debug_scan.
"""

from __future__ import annotations

import logging

from ...common import Axis, Position
from ...continuous_motion import ContinuousMotionHandler, MotionSample
from ...session import SweepContext
from ..sweep_centroid import _search_box
from .motion import traversal_endpoints

logger = logging.getLogger(__name__)


def y_lines(y_lo: float, y_hi: float, tolerance: float) -> list[float]:
    """Y coordinates for horizontal raster rows, spaced by ``tolerance`` (inclusive)."""
    if tolerance <= 0.0:
        raise ValueError("tolerance must be positive")
    if y_hi < y_lo:
        y_lo, y_hi = y_hi, y_lo
    count = int((y_hi - y_lo) / tolerance) + 1
    return [y_lo + index * tolerance for index in range(count)]


def plan_grid_legs(
    box: tuple[float, float, float, float],
    tolerance: float,
    overscan: float,
    *,
    axis: Axis = Axis.X,
    serpentine: bool = True,
) -> list[tuple[Position, Position]]:
    """Raster sweeps on ``axis`` at lines spaced by ``tolerance`` on the cross axis."""
    x_lo, x_hi, y_lo, y_hi = box
    if axis is Axis.X:
        lo, hi, cross_lo, cross_hi = x_lo, x_hi, y_lo, y_hi
    else:
        lo, hi, cross_lo, cross_hi = y_lo, y_hi, x_lo, x_hi
    legs: list[tuple[Position, Position]] = []
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


def sweep_grid(
    ctx: SweepContext,
    handler: ContinuousMotionHandler,
    center: Position,
    speed: float,
    tolerance: float,
) -> tuple[list[MotionSample], tuple[float, float, float, float]]:
    """Raster the search box once; return samples clipped to the box bounds."""
    cfg = ctx.config
    box = _search_box(
        center, cfg.max_jog_x, cfg.max_jog_y, cfg.max_jog_x, cfg.max_jog_y
    )
    legs = plan_grid_legs(box, tolerance, cfg.sweep_overscan, axis=Axis.X)
    legs.extend(plan_grid_legs(box, tolerance, cfg.sweep_overscan, axis=Axis.Y))

    handler.begin(ctx.session_start)
    handler.run_capture_legs(legs, speed)
    ctx.sync_offset(handler.position)
    samples = handler.collect_samples()
    x_lo, x_hi, y_lo, y_hi = box
    in_box = [
        sample
        for sample in samples
        if x_lo <= sample.offset.x <= x_hi and y_lo <= sample.offset.y <= y_hi
    ]
    logger.debug(
        "eddy_seek: sweep_grid rows=%d legs=%d samples=%d in_box=%d",
        len(y_lines(y_lo, y_hi, tolerance)),
        len(legs),
        len(samples),
        len(in_box),
    )
    ctx.append_trace(
        {
            "type": "sweep_grid",
            "center": {"x": center.x, "y": center.y},
            "box": {"x_lo": x_lo, "x_hi": x_hi, "y_lo": y_lo, "y_hi": y_hi},
            "tolerance": tolerance,
            "rows": len(y_lines(y_lo, y_hi, tolerance)),
            "legs": len(legs),
            "samples": len(in_box),
        }
    )
    return in_box, box


def _assert_grid_leg_count() -> None:
    box = (-5.0, 5.0, -5.0, 5.0)
    tolerance = 0.1
    rows = len(y_lines(box[2], box[3], tolerance))
    legs = plan_grid_legs(box, tolerance, overscan=1.0)
    assert len(legs) == rows * 2


_assert_grid_leg_count()

"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

2D raster sweep capture for debug_scan.
"""

from __future__ import annotations

import logging

from ...common import Axis, Offset, samples_in_box, search_box
from ...motion_handler import MotionSample
from ...session import SeekSession
from .motion import capture_legs, plan_grid_legs, speed_clamp_for_min_samples, y_lines

logger = logging.getLogger(__name__)


def sweep_grid(
    ctx: SeekSession,
    center: Offset,
    speed: float,
    tolerance: float,
) -> tuple[list[MotionSample], tuple[float, float, float, float]]:
    """Raster the search box once; return samples clipped to the box bounds."""
    cfg = ctx.config
    box = search_box(center, cfg.max_jog_x, cfg.max_jog_y, cfg.max_jog_x, cfg.max_jog_y)
    legs = plan_grid_legs(box, tolerance, cfg.sweep_overscan, axis=Axis.X)
    legs.extend(plan_grid_legs(box, tolerance, cfg.sweep_overscan, axis=Axis.Y))

    x_lo, x_hi, y_lo, y_hi = box
    requested = speed
    span = min(x_hi - x_lo, y_hi - y_lo)
    speed = speed_clamp_for_min_samples(
        requested_mm_min=speed,
        span_mm=span,
        min_samples=cfg.min_sweep_samples,
    )
    if speed < requested:
        logger.debug(
            f"eddy_seek: sweep speed clamped {requested / 60.0:.2f} -> {speed / 60.0:.2f} mm/s "
            f"(span={span:.3f} mm, min_samples={cfg.min_sweep_samples})"
        )
    samples = capture_legs(ctx, legs, speed)
    in_box = samples_in_box(samples, box)
    rows = len(y_lines(y_lo, y_hi, tolerance))
    logger.debug(
        f"eddy_seek: sweep_grid rows={rows} legs={len(legs)} "
        f"samples={len(samples)} in_box={len(in_box)}"
    )
    ctx.append_trace(
        {
            "type": "sweep_grid",
            "center": {"x": center.x, "y": center.y},
            "box": {"x_lo": x_lo, "x_hi": x_hi, "y_lo": y_lo, "y_hi": y_hi},
            "tolerance": tolerance,
            "rows": rows,
            "legs": len(legs),
            "samples": len(in_box),
        }
    )
    return in_box, box

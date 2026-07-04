"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Continuous axis sweep execution and profile extraction.
"""

from __future__ import annotations

import logging

from ...common import Axis, Phase
from ...motion_handler import MotionSample, axis_profile
from ...session import SeekSession
from .motion import capture_legs, plan_axis_legs, speed_clamp_for_min_samples

logger = logging.getLogger(__name__)


def sweep_axis(
    ctx: SeekSession,
    axis: Axis,
    lo: float,
    hi: float,
    cross_center: float,
    cross_offsets: list[float],
    speed: float,
    phase: Phase,
    pass_num: int,
) -> tuple[list[tuple[float, float]], list[MotionSample]]:
    """Continuous ± traverses on ``axis``; merged profile in session offsets."""
    cfg = ctx.config
    legs = plan_axis_legs(axis, lo, hi, cross_center, cross_offsets, cfg.sweep_overscan)
    requested = speed
    span = hi - lo
    speed = speed_clamp_for_min_samples(
        requested_mm_min=speed,
        span_mm=span,
        min_samples=cfg.min_sweep_samples,
    )
    if speed < requested:
        if ctx.console is not None:
            ctx.console.detail(
                f"sweep speed clamped {requested / 60.0:.2f} -> {speed / 60.0:.2f} mm/s "
                f"(span={span:.3f} mm, min_samples={cfg.min_sweep_samples})"
            )
        logger.debug(
            f"eddy_seek: sweep speed clamped {requested / 60.0:.2f} -> {speed / 60.0:.2f} mm/s "
            f"(span={span:.3f} mm, min_samples={cfg.min_sweep_samples})"
        )
    samples = capture_legs(ctx, legs, speed)
    points = axis_profile(samples, axis, lo, hi)

    logger.debug(
        f"eddy_seek: sweep_axis {axis.value} pass {pass_num} {phase.value} "
        f"cross_passes={len(cross_offsets)} -> {len(points)} points"
    )
    ctx.append_trace(
        {
            "type": "sweep",
            "pass": pass_num,
            "phase": phase.value,
            "axis": axis.value,
            "cross_offsets": cross_offsets,
            "cross_center": cross_center,
            "lo": lo,
            "hi": hi,
            "samples": points,
        }
    )
    return points, samples

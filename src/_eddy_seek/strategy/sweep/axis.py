"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.

Continuous axis sweep execution and profile extraction.
"""

from __future__ import annotations

import logging

from ...common import Axis, Phase, Position
from ...session import SweepContext
from ...continuous_motion import ContinuousMotionHandler, SweepSample, axis_profile
from .motion import traversal_endpoints

logger = logging.getLogger(__name__)


def sweep_axis(
    ctx: SweepContext,
    handler: ContinuousMotionHandler,
    axis: Axis,
    lo: float,
    hi: float,
    cross_center: float,
    cross_offsets: list[float],
    speed: float,
    phase: Phase,
    pass_num: int,
) -> tuple[list[tuple[float, float]], list[SweepSample]]:
    """Continuous ± traverses on ``axis``; merged profile in session offsets."""
    cfg = ctx.config
    handler.begin(ctx.session_start)

    legs: list[tuple[Position, Position]] = []
    # Parallel lines on the cross axis: each sweep is a 1D slice of a 2D
    # field, so one line skews the peak if we're still off on the other axis.
    for cross_delta in cross_offsets:
        cross = cross_center + cross_delta
        for reverse in (False, True):
            legs.append(
                traversal_endpoints(
                    axis, lo, hi, cross, cfg.sweep_overscan, reverse=reverse
                )
            )

    handler.run_capture_legs(legs, speed)
    ctx.sync_offset(handler.position)
    samples = handler.collect_samples()
    points = axis_profile(samples, axis, lo, hi)

    logger.debug(
        "eddy_seek: sweep_axis %s pass %d %s cross_passes=%d -> %d points",
        axis.value,
        pass_num,
        phase.value,
        len(cross_offsets),
        len(points),
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

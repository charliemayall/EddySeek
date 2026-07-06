"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Axis sweep bootstrap pass for circle-harmonic search.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ...common import Offset, Phase
from ...movement.leg_planner import (
    MotionCapture,
    SweepSettings,
    axis_sweep_centroid,
)
from ...session import SeekSession

if TYPE_CHECKING:
    from .strategy import CircleHarmonicStrategy

logger = logging.getLogger(__name__)


def bootstrap_pass(
    strategy: CircleHarmonicStrategy,
    ctx: SeekSession,
    pass_num: int,
    best: Offset,
) -> Offset:
    cfg = ctx.config
    half_x = cfg.max_jog_x
    half_y = cfg.max_jog_y
    capture = MotionCapture(ctx.motion, ctx.session_start, ctx.sync_offset)
    settings = SweepSettings.from_config(cfg)
    sweep = axis_sweep_centroid(
        capture,
        settings,
        best,
        half_x=half_x,
        half_y=half_y,
        speed_mm_min=cfg.sweep_coarse_speed,
        phase=Phase.COARSE,
        pass_num=pass_num,
        label="circle_harmonic bootstrap",
        recorder=ctx.recorder,
    )
    strategy._x_profile = sweep.x_profile
    strategy._y_profile = sweep.y_profile
    result_or_none = sweep.centroid

    if result_or_none is None:
        logger.warning(
            f"eddy_seek: flat frequency on bootstrap - "
            f"keeping ({best.x:.4f}, {best.y:.4f})"
        )
        strategy._bootstrap = best
        strategy._record_bootstrap_plot(
            ctx,
            pass_num,
            best,
            best,
            sweep.in_box,
            sweep.box,
        )
        return best

    result = result_or_none.clamp(cfg.max_jog_x, cfg.max_jog_y)
    strategy._bootstrap = result
    logger.info(
        f"eddy_seek: circle_harmonic bootstrap -> ({result.x:.4f}, {result.y:.4f})"
    )
    strategy._record_bootstrap_plot(
        ctx,
        pass_num,
        best,
        result,
        sweep.in_box,
        sweep.box,
    )
    return result

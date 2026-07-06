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
from ...movement.leg_planner import MotionCapture, SweepSettings, axis_sweep_profiles
from ...optimizer import decoupled_centroid
from ...session import SeekSession
from .plateau import CircleHarmonicMode

if TYPE_CHECKING:
    from .strategy import CircleHarmonicStrategy

logger = logging.getLogger(__name__)


def bootstrap_pass(
    strategy: CircleHarmonicStrategy,
    ctx: SeekSession,
    pass_num: int,
    best: Offset,
    mode: CircleHarmonicMode,
) -> Offset:
    cfg = ctx.config
    half_x = cfg.max_jog_x
    half_y = cfg.max_jog_y
    capture = MotionCapture(ctx.motion, ctx.session_start, ctx.sync_offset)
    settings = SweepSettings.from_config(cfg)
    profiles = axis_sweep_profiles(
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
    strategy._x_profile = profiles.x_profile
    strategy._y_profile = profiles.y_profile
    result_or_none = decoupled_centroid(
        profiles.x_profile, profiles.y_profile, cfg.search_for
    )

    if mode.slope_only:
        strategy._bootstrap = best
        centroid = (
            result_or_none.clamp(cfg.max_jog_x, cfg.max_jog_y)
            if result_or_none is not None
            else None
        )
        if centroid is not None:
            logger.info(
                f"eddy_seek: circle_harmonic slope-only: "
                f"centroid=({centroid.x:.4f}, {centroid.y:.4f}) ignored, "
                f"holding ({best.x:.4f}, {best.y:.4f}) for circle passes"
            )
        else:
            logger.warning(
                f"eddy_seek: flat frequency on slope-only bootstrap - "
                f"holding ({best.x:.4f}, {best.y:.4f})"
            )
        strategy._record_bootstrap_plot(
            ctx,
            pass_num,
            best,
            best,
            profiles.in_box,
            profiles.box,
            skipped=centroid,
        )
        return best

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
            profiles.in_box,
            profiles.box,
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
        profiles.in_box,
        profiles.box,
    )
    return result

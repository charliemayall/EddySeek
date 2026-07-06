"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

from __future__ import annotations

import logging

from ..common import Offset
from ..kconsole import KConsole
from ..optimizer import weighted_centroid
from ..plotting.artifacts import finalize_strategy_plot
from ..plotting.primitives import (
    CentroidPassRecord,
    PassMove,
    XYCloud,
)
from ..session import SeekSession
from .base import SeekStrategy

logger = logging.getLogger(__name__)


class CentroidStrategy(SeekStrategy):
    @property
    def name(self) -> str:
        return "centroid"

    def announce_start(self, ctx: SeekSession, console: KConsole) -> None:
        cfg = ctx.config
        logger.info(
            f"eddy_seek: centroid grid_step=({cfg.grid_step_x:.4f}, {cfg.grid_step_y:.4f}) mm"
        )

    def on_session_end(self, ctx: SeekSession) -> str | None:
        return finalize_strategy_plot(ctx, self.name)

    def _step(self, ctx: SeekSession, pass_num: int, best: Offset) -> Offset:
        cfg = ctx.config
        shrink = 0.5 ** (pass_num - 1)
        return self._centroid_pass(
            ctx,
            pass_num,
            best,
            cfg.grid_step_x * shrink,
            cfg.grid_step_y * shrink,
        )

    def _pass_message(
        self,
        pass_num: int,
        new: Offset,
        moved: Offset,
        ctx: SeekSession,
    ) -> str:
        cfg = ctx.config
        shrink = 0.5 ** (pass_num - 1)
        step_x = cfg.grid_step_x * shrink
        step_y = cfg.grid_step_y * shrink
        logger.info(
            f"eddy_seek: centroid pass {pass_num} moved=({moved.x:.4f}, {moved.y:.4f}) "
            f"grid_step=({step_x:.4f}, {step_y:.4f})"
        )
        return f"Pass {pass_num}: {new.to_delta_str()}"

    def _centroid_pass(
        self,
        ctx: SeekSession,
        pass_num: int,
        center: Offset,
        step_x: float,
        step_y: float,
    ) -> Offset:
        cfg = ctx.config
        probes: list[tuple[Offset, float]] = []

        for dy_mul in (-1, 0, 1):
            for dx_mul in (-1, 0, 1):
                position = (center + Offset(dx_mul * step_x, dy_mul * step_y)).clamp(
                    cfg.max_jog_x, cfg.max_jog_y
                )
                freq = ctx.measure_at(position)
                probes.append((position, freq))

        result = weighted_centroid(probes, cfg.search_for)
        if result is None:
            logger.warning(
                f"eddy_seek: flat frequency response on centroid grid - "
                f"keeping centre ({center.x:.4f}, {center.y:.4f})"
            )
            _record_centroid_pass(ctx, pass_num, center, center, Offset.zero(), probes)
            return center

        freqs = [freq for _, freq in probes]
        clamped = result.clamp(cfg.max_jog_x, cfg.max_jog_y)
        logger.info(
            f"eddy_seek: centroid pass centre=({center.x:.4f}, {center.y:.4f}) "
            f"-> ({clamped.x:.4f}, {clamped.y:.4f}) "
            f"freq_range=[{min(freqs):.2f}, {max(freqs):.2f}] Hz"
        )
        _record_centroid_pass(
            ctx,
            pass_num,
            center,
            clamped,
            (clamped - center).abs_components(),
            probes,
        )
        return clamped


def _record_centroid_pass(
    ctx: SeekSession,
    pass_num: int,
    center: Offset,
    result: Offset,
    moved: Offset,
    probes: list[tuple[Offset, float]],
) -> None:
    ctx.recorder.record(
        CentroidPassRecord(
            pass_num=pass_num,
            move=PassMove.compute(center, result),
            probes=XYCloud.from_probes(probes),
        )
    )

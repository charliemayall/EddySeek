"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Continuous sweep motion with frequency-weighted centroid peak finding.
"""

from __future__ import annotations

import logging

from ..common import Offset, Phase
from ..config import SeekConfig
from ..kconsole import KConsole
from ..movement.handler import MotionSample
from ..movement.leg_planner import (
    MotionCapture,
    SweepSettings,
    axis_sweep_centroid,
)
from ..plotting.artifacts import finalize_strategy_plot
from ..plotting.primitives import (
    Bounds,
    PassMove,
    SweepCentroidPassRecord,
    XYCloud,
)
from ..session import SeekSession
from .base import SeekStrategy

logger = logging.getLogger(__name__)


class SweepCentroidStrategy(SeekStrategy):
    @property
    def name(self) -> str:
        return "sweep_centroid"

    def announce_start(self, ctx: SeekSession, console: KConsole) -> None:
        cfg = ctx.config
        logger.info(
            f"eddy_seek: sweep_centroid coarse={cfg.sweep_coarse_speed / 60.0:.2f} mm/s "
            f"fine={cfg.sweep_fine_speed / 60.0:.2f} mm/s "
            f"coarse_phases={cfg.coarse_phases} "
            f"cross_passes={cfg.coarse_cross_passes}/1"
        )

    def on_session_end(self, ctx: SeekSession) -> str | None:
        return finalize_strategy_plot(ctx, self.name)

    def _phase_for_pass(self, pass_num: int, cfg: SeekConfig) -> Phase:
        return Phase.COARSE if pass_num <= cfg.coarse_phases else Phase.FINE

    def should_check_divergence(self, ctx: SeekSession, pass_num: int) -> bool:
        return self._phase_for_pass(pass_num, ctx.config) is not Phase.COARSE

    def _step(self, ctx: SeekSession, pass_num: int, best: Offset) -> Offset:
        cfg = ctx.config
        phase = self._phase_for_pass(pass_num, cfg)
        if phase is Phase.COARSE:
            shrink = 1.0
            speed = cfg.sweep_coarse_speed
        else:
            shrink = cfg.fine_shrink ** (pass_num - cfg.coarse_phases)
            speed = cfg.sweep_fine_speed

        half_x = cfg.max_jog_x * shrink
        half_y = cfg.max_jog_y * shrink

        capture = MotionCapture(ctx.motion, ctx.session_start, ctx.sync_offset)
        settings = SweepSettings.from_config(cfg)
        sweep = axis_sweep_centroid(
            capture,
            settings,
            best,
            half_x=half_x,
            half_y=half_y,
            speed_mm_min=speed,
            phase=phase,
            pass_num=pass_num,
            label=f"sweep_centroid pass {pass_num}",
            recorder=ctx.recorder,
        )
        in_box = sweep.in_box
        x_profile = sweep.x_profile
        y_profile = sweep.y_profile
        result_or_none = sweep.centroid
        box = sweep.box

        if result_or_none is None:
            logger.warning(
                f"eddy_seek: flat frequency response on sweep pass {pass_num} - "
                f"keeping centre ({best.x:.4f}, {best.y:.4f})"
            )
            _record_sweep_centroid_pass(
                ctx, pass_num, phase, best, best, Offset.zero(), in_box, box
            )
            return best

        result = result_or_none.clamp(cfg.max_jog_x, cfg.max_jog_y)
        freqs = [freq for _, freq in x_profile + y_profile]
        logger.info(
            f"eddy_seek: sweep_centroid pass {pass_num} {phase.value} "
            f"-> ({result.x:.4f}, {result.y:.4f}) "
            f"freq_range=[{min(freqs):.2f}, {max(freqs):.2f}] Hz ({len(in_box)} samples)"
        )
        _record_sweep_centroid_pass(
            ctx,
            pass_num,
            phase,
            best,
            result,
            (result - best).abs_components(),
            in_box,
            box,
        )
        return result

    def _pass_message(
        self,
        pass_num: int,
        new: Offset,
        moved: Offset,
        ctx: SeekSession,
    ) -> str:
        phase = self._phase_for_pass(pass_num, ctx.config).value
        logger.info(
            f"eddy_seek: sweep_centroid pass {pass_num} ({phase}) "
            f"moved=({moved.x:.4f}, {moved.y:.4f})"
        )
        return f"Pass {pass_num} ({phase}): {new.to_console_str()}"


def _record_sweep_centroid_pass(
    ctx: SeekSession,
    pass_num: int,
    phase: Phase,
    center: Offset,
    result: Offset,
    moved: Offset,
    samples: list[MotionSample],
    box: tuple[float, float, float, float],
) -> None:
    ctx.recorder.record(
        SweepCentroidPassRecord(
            pass_num=pass_num,
            phase=phase.value,
            move=PassMove.compute(center, result),
            bounds=Bounds.from_box(box),
            samples=XYCloud.from_samples(samples),
        )
    )

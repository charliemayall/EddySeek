"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Continuous sweep motion with frequency-weighted centroid peak finding.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..common import Axis, Offset, Phase, samples_in_box, search_box
from ..kconsole import KConsole
from ..movement.handler import MotionSample
from ..movement.leg_planner import iter_cross_offsets, sweep_axis
from ..optimizer import decoupled_centroid
from ..plotting import PlotWriter
from ..session import SeekSession
from .base import SeekStrategy

logger = logging.getLogger(__name__)

_N_COARSE = 1


class SweepCentroidStrategy(SeekStrategy):
    def __init__(self) -> None:
        self._plotter: PlotWriter | None = None

    @property
    def name(self) -> str:
        return "sweep_centroid"

    def announce_start(self, ctx: SeekSession, console: KConsole) -> None:
        cfg = ctx.config
        if cfg.save_plots:
            self._plotter = PlotWriter(
                Path(cfg.result_folder),
                ctx.session_id,
                write_at=ctx.artifact_write_at,
                suffix=ctx.artifact_suffix(self.name),
                run_id=ctx.run_id,
            )
        logger.debug(
            f"eddy_seek: sweep_centroid coarse={cfg.sweep_coarse_speed / 60.0:.2f} mm/s "
            f"fine={cfg.sweep_fine_speed / 60.0:.2f} mm/s "
            f"cross_passes={cfg.sweep_cross_passes}"
        )

    def on_session_end(self, ctx: SeekSession) -> str | None:
        plotter = self._plotter
        self._plotter = None
        if plotter is None:
            self._last_plot_passes = 0
            return None
        self._last_plot_passes = plotter.sweep_centroid_pass_count
        return plotter.finalize_sweep_centroid(search_for=ctx.config.search_for)

    def _phase_for_pass(self, pass_num: int) -> Phase:
        return Phase.COARSE if pass_num <= _N_COARSE else Phase.FINE

    def _step(self, ctx: SeekSession, pass_num: int, best: Offset) -> Offset:
        cfg = ctx.config
        phase = self._phase_for_pass(pass_num)
        if phase is Phase.COARSE:
            shrink = 1.0
            speed = cfg.sweep_coarse_speed
        else:
            shrink = cfg.fine_shrink ** (pass_num - 2)
            speed = cfg.sweep_fine_speed

        half_x = cfg.max_jog_x * shrink
        half_y = cfg.max_jog_y * shrink

        _, samples_x = self._sweep_axis(
            ctx,
            Axis.X,
            best.x,
            half_x,
            best.y,
            pass_num,
            phase,
            speed,
        )
        _, samples_y = self._sweep_axis(
            ctx,
            Axis.Y,
            best.y,
            half_y,
            best.x,
            pass_num,
            phase,
            speed,
        )
        box = search_box(best, half_x, half_y, cfg.max_jog_x, cfg.max_jog_y)
        in_box_x = samples_in_box(samples_x, box)
        in_box_y = samples_in_box(samples_y, box)
        in_box = in_box_x + in_box_y

        if len(in_box) < cfg.min_sweep_samples:
            raise RuntimeError(
                f"eddy_seek: sweep_centroid pass {pass_num} collected "
                f"{len(in_box)} in-range samples "
                f"(need >= {cfg.min_sweep_samples}). "
                "Check sensor and sweep speed."
            )

        x_profile = [(sample.offset.x, sample.freq) for sample in in_box_x]
        y_profile = [(sample.offset.y, sample.freq) for sample in in_box_y]
        result_or_none = decoupled_centroid(x_profile, y_profile, cfg.search_for)
        if result_or_none is None:
            logger.warning(
                f"eddy_seek: flat frequency response on sweep pass {pass_num} - "
                f"keeping centre ({best.x:.4f}, {best.y:.4f})"
            )
            if self._plotter is not None:
                self._plotter.record_sweep_centroid_pass(
                    pass_num=pass_num,
                    phase=phase,
                    center=best,
                    result=best,
                    moved=Offset.zero(),
                    samples=in_box,
                    box=box,
                )
            return best

        result = result_or_none.clamp(cfg.max_jog_x, cfg.max_jog_y)
        freqs = [freq for _, freq in x_profile + y_profile]
        logger.debug(
            f"eddy_seek: sweep_centroid pass {pass_num} {phase.value} "
            f"-> ({result.x:.4f}, {result.y:.4f}) "
            f"freq_range=[{min(freqs):.2f}, {max(freqs):.2f}] Hz ({len(in_box)} samples)"
        )
        ctx.append_trace(
            {
                "type": "sweep_centroid",
                "pass": pass_num,
                "phase": phase.value,
                "centre": {"x": best.x, "y": best.y},
                "result": {"x": result.x, "y": result.y},
                "samples": len(in_box),
            }
        )
        if self._plotter is not None:
            self._plotter.record_sweep_centroid_pass(
                pass_num=pass_num,
                phase=phase,
                center=best,
                result=result,
                moved=(result - best).abs_components(),
                samples=in_box,
                box=box,
            )
        return result

    def _pass_message(
        self,
        pass_num: int,
        new: Offset,
        moved: Offset,
        ctx: SeekSession,
    ) -> str:
        phase = self._phase_for_pass(pass_num).value
        logger.debug(
            f"eddy_seek: sweep_centroid pass {pass_num} ({phase}) "
            f"moved=({moved.x:.4f}, {moved.y:.4f})"
        )
        return f"Pass {pass_num} ({phase}): X={new.x:+.4f} Y={new.y:+.4f} mm"

    def _sweep_axis(
        self,
        ctx: SeekSession,
        axis: Axis,
        center: float,
        half_range: float,
        cross_center: float,
        pass_num: int,
        phase: Phase,
        speed: float,
    ) -> tuple[list[tuple[float, float]], list[MotionSample]]:
        cfg = ctx.config
        jog_limit = cfg.max_jog_x if axis is Axis.X else cfg.max_jog_y
        lo = max(-jog_limit, center - half_range)
        hi = min(jog_limit, center + half_range)
        cross_offsets = iter_cross_offsets(
            cfg.sweep_cross_passes, cfg.sweep_cross_offset
        )
        points, samples = sweep_axis(
            ctx,
            axis=axis,
            lo=lo,
            hi=hi,
            cross_center=cross_center,
            cross_offsets=cross_offsets,
            speed=speed,
            phase=phase,
            pass_num=pass_num,
        )
        if len(points) < cfg.min_sweep_samples:
            raise RuntimeError(
                f"eddy_seek: sweep on {axis.value} collected {len(points)} samples "
                f"(need >= {cfg.min_sweep_samples}). "
                "Check sensor and sweep speed."
            )
        return points, samples

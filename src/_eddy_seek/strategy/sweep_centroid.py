"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.

Continuous sweep motion with frequency-weighted centroid peak finding.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import cast

from ..common import Axis, Phase, Position
from ..continuous_motion import ContinuousMotionHandler, MotionSample
from ..plotting import PlotWriter
from ..session import SeekContext, SeekReporter, SweepContext
from .base import SeekStrategy
from .centroid import weighted_centroid
from .sweep.axis import sweep_axis
from .sweep.motion import iter_cross_offsets

logger = logging.getLogger(__name__)


class SweepCentroidStrategy(SeekStrategy):
    def __init__(self) -> None:
        self._recorder: ContinuousMotionHandler | None = None
        self._plotter: PlotWriter | None = None

    @property
    def name(self) -> str:
        return "sweep_centroid"

    def announce_start(self, ctx: SeekContext, reporter: SeekReporter) -> None:
        sweep_ctx = cast(SweepContext, ctx)
        cfg = sweep_ctx.config
        self._recorder = ContinuousMotionHandler(
            sweep_ctx.host.printer, sweep_ctx.host.add_sensor_client
        )
        if cfg.save_plots:
            self._plotter = PlotWriter(Path(cfg.result_folder), ctx.session_id)
        reporter.info(
            f"EDDY_SEEK: sweep_centroid coarse={cfg.sweep_coarse_speed} mm/s  "
            f"fine={cfg.sweep_fine_speed} mm/s  "
            f"cross_passes={cfg.sweep_cross_passes}"
        )

    def on_session_end(self, ctx: SeekContext) -> str | None:
        plotter = self._plotter
        self._plotter = None
        if self._recorder is not None:
            self._recorder.close()
            self._recorder = None
        if plotter is None:
            self._last_plot_passes = 0
            return None
        self._last_plot_passes = plotter.sweep_centroid_pass_count
        return plotter.finalize_sweep_centroid(search_for=ctx.config.search_for)

    def _phase(self, pass_num: int) -> Phase:
        return Phase.COARSE if pass_num == 1 else Phase.FINE

    def _step(self, ctx: SeekContext, pass_num: int, best: Position) -> Position:
        sweep_ctx = cast(SweepContext, ctx)
        cfg = sweep_ctx.config
        phase = self._phase(pass_num)
        if phase is Phase.COARSE:
            shrink = 1.0
            speed = cfg.sweep_coarse_speed
        else:
            shrink = cfg.fine_shrink ** (pass_num - 2)
            speed = cfg.sweep_fine_speed

        half_x = cfg.max_jog_x * shrink
        half_y = cfg.max_jog_y * shrink

        _, samples_x = self._sweep_axis(
            sweep_ctx,
            Axis.X,
            best.x,
            half_x,
            best.y,
            pass_num,
            phase,
            speed,
        )
        _, samples_y = self._sweep_axis(
            sweep_ctx,
            Axis.Y,
            best.y,
            half_y,
            best.x,
            pass_num,
            phase,
            speed,
        )
        samples = samples_x + samples_y
        box = _search_box(best, half_x, half_y, cfg.max_jog_x, cfg.max_jog_y)
        in_box = _samples_in_box(samples, box)

        if len(in_box) < cfg.min_sweep_samples:
            raise RuntimeError(
                f"eddy_seek: sweep_centroid pass {pass_num} collected "
                f"{len(in_box)} in-range samples "
                f"(need >= {cfg.min_sweep_samples}). "
                "Check sensor and sweep speed."
            )

        probes = [(sample.offset, sample.freq) for sample in in_box]
        centroid = weighted_centroid(probes, cfg.search_for)
        if centroid is None:
            logger.warning(
                "eddy_seek: flat frequency response on sweep pass %d - "
                "keeping centre (%.4f, %.4f)",
                pass_num,
                best.x,
                best.y,
            )
            if self._plotter is not None:
                self._plotter.record_sweep_centroid_pass(
                    pass_num=pass_num,
                    phase=phase,
                    center=best,
                    result=best,
                    moved=Position.zero(),
                    samples=in_box,
                    box=box,
                )
            return best

        result = centroid.clamp(cfg.max_jog_x, cfg.max_jog_y)
        freqs = [freq for _, freq in probes]
        logger.debug(
            "eddy_seek: sweep_centroid pass %d %s -> (%.4f, %.4f) "
            "freq_range=[%.2f, %.2f] Hz (%d samples)",
            pass_num,
            phase.value,
            result.x,
            result.y,
            min(freqs),
            max(freqs),
            len(in_box),
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
        new: Position,
        moved: Position,
        ctx: SeekContext,
    ) -> str:
        return (
            f"EDDY_SEEK pass {pass_num} ({self._phase(pass_num).value}): "
            f"sweep_centroid ({new.x:+.4f}, {new.y:+.4f}) mm  "
            f"(moved {moved.x:.4f}, {moved.y:.4f})"
        )

    def _sweep_axis(
        self,
        ctx: SweepContext,
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
        if self._recorder is None:
            raise RuntimeError("eddy_seek: continuous motion handler not started")
        points, samples = sweep_axis(
            ctx,
            self._recorder,
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


def _search_box(
    center: Position,
    half_x: float,
    half_y: float,
    max_jog_x: float,
    max_jog_y: float,
) -> tuple[float, float, float, float]:
    x_lo = max(-max_jog_x, center.x - half_x)
    x_hi = min(max_jog_x, center.x + half_x)
    y_lo = max(-max_jog_y, center.y - half_y)
    y_hi = min(max_jog_y, center.y + half_y)
    return x_lo, x_hi, y_lo, y_hi


def _samples_in_box(
    samples: list[MotionSample],
    box: tuple[float, float, float, float],
) -> list[MotionSample]:
    x_lo, x_hi, y_lo, y_hi = box
    return [
        sample
        for sample in samples
        if x_lo <= sample.offset.x <= x_hi and y_lo <= sample.offset.y <= y_hi
    ]

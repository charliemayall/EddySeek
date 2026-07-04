"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Circle-harmonic strategy: sweep bootstrap + guarded first-harmonic nulling.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..common import Axis, Offset, Phase, samples_in_box, search_box
from ..harmonic import (
    bin_samples_by_angle,
    binned_to_motion_samples,
    circle_in_jog_box,
    circle_legs,
    circle_radius_for_pass,
    fit_first_harmonic,
    harmonic_converged,
    harmonic_model_accepted,
    harmonic_step_v2,
    radial_slope,
)
from ..kconsole import KConsole
from ..movement.handler import MotionSample
from ..movement.leg_planner import iter_cross_offsets, sweep_axis
from ..optimizer import decoupled_centroid
from ..plotting import PlotWriter
from ..session import SeekSession
from .base import SeekStrategy

logger = logging.getLogger(__name__)


class CircleHarmonicStrategy(SeekStrategy):
    """Sweep bootstrap, then circle harmonic nulling with model and bore gates."""

    def __init__(self) -> None:
        self._plotter: PlotWriter | None = None
        self._bootstrap: Offset | None = None
        self._x_profile: list[tuple[float, float]] = []
        self._y_profile: list[tuple[float, float]] = []
        self._frozen: Offset | None = None
        self._last_plot_passes = 0

    @property
    def name(self) -> str:
        return "circle_harmonic"

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
            f"eddy_seek: circle_harmonic coarse={cfg.sweep_coarse_speed / 60.0:.2f} mm/s "
            f"circle={cfg.circle_speed / 60.0:.2f} mm/s "
            f"segments={cfg.circle_segments}"
        )

    def on_session_end(self, ctx: SeekSession) -> str | None:
        self._plotter = None
        self._bootstrap = None
        self._frozen = None
        return None

    def _step(self, ctx: SeekSession, pass_num: int, best: Offset) -> Offset:
        if self._frozen is not None:
            return self._frozen

        if pass_num == 1:
            return self._bootstrap_pass(ctx, pass_num, best)

        return self._circle_pass(ctx, pass_num, best)

    def _pass_message(
        self,
        pass_num: int,
        new: Offset,
        moved: Offset,
        ctx: SeekSession,
    ) -> str:
        if pass_num == 1:
            return f"Pass {pass_num} (bootstrap): X={new.x:+.4f} Y={new.y:+.4f} mm"
        return f"Pass {pass_num} (circle): {new.to_delta_str()}"

    def _bootstrap_pass(
        self,
        ctx: SeekSession,
        pass_num: int,
        best: Offset,
    ) -> Offset:
        cfg = ctx.config
        half_x = cfg.max_jog_x
        half_y = cfg.max_jog_y
        speed = cfg.sweep_coarse_speed

        _, samples_x = self._sweep_axis(
            ctx, Axis.X, best.x, half_x, best.y, pass_num, Phase.COARSE, speed
        )
        _, samples_y = self._sweep_axis(
            ctx, Axis.Y, best.y, half_y, best.x, pass_num, Phase.COARSE, speed
        )
        box = search_box(best, half_x, half_y, cfg.max_jog_x, cfg.max_jog_y)
        in_box_x = samples_in_box(samples_x, box)
        in_box_y = samples_in_box(samples_y, box)

        if len(in_box_x) + len(in_box_y) < cfg.min_sweep_samples:
            raise RuntimeError(
                f"eddy_seek: circle_harmonic bootstrap collected "
                f"{len(in_box_x) + len(in_box_y)} samples "
                f"(need >= {cfg.min_sweep_samples})"
            )

        x_profile = [(s.offset.x, s.freq) for s in in_box_x]
        y_profile = [(s.offset.y, s.freq) for s in in_box_y]
        self._x_profile = x_profile
        self._y_profile = y_profile

        result_or_none = decoupled_centroid(x_profile, y_profile, cfg.search_for)
        if result_or_none is None:
            logger.warning(
                f"eddy_seek: flat frequency on bootstrap - "
                f"keeping ({best.x:.4f}, {best.y:.4f})"
            )
            self._bootstrap = best
            return best

        result = result_or_none.clamp(cfg.max_jog_x, cfg.max_jog_y)
        self._bootstrap = result
        logger.debug(
            f"eddy_seek: circle_harmonic bootstrap -> ({result.x:.4f}, {result.y:.4f})"
        )
        ctx.append_trace(
            {
                "type": "circle_harmonic_bootstrap",
                "pass": pass_num,
                "result": {"x": result.x, "y": result.y},
            }
        )
        return result

    def _circle_pass(
        self,
        ctx: SeekSession,
        pass_num: int,
        best: Offset,
    ) -> Offset:
        cfg = ctx.config
        bootstrap = self._bootstrap if self._bootstrap is not None else best

        radius = circle_radius_for_pass(
            pass_num,
            radius_start=cfg.circle_radius_start,
            radius_min=cfg.circle_radius_min,
            radius_shrink=cfg.circle_shrink,
        )
        trace_center, trace_radius = circle_in_jog_box(
            best, radius, cfg.max_jog_x, cfg.max_jog_y
        )
        if trace_radius < cfg.circle_radius_min:
            logger.warning(
                f"eddy_seek: circle_harmonic pass {pass_num} radius {trace_radius:.4f} "
                f"< min {cfg.circle_radius_min} - holding bootstrap"
            )
            self._frozen = bootstrap
            return bootstrap

        legs = circle_legs(trace_center, trace_radius, cfg.circle_segments)
        if not legs:
            self._frozen = bootstrap
            return bootstrap

        self._refresh_profiles(ctx, pass_num, trace_center, trace_radius)

        handler = ctx.motion
        handler.run_capture_legs(legs, cfg.circle_speed)
        ctx.sync_offset(handler.position)
        samples = handler.collect_samples()

        if len(samples) < 3:
            raise RuntimeError(
                f"eddy_seek: circle_harmonic pass {pass_num} collected "
                f"{len(samples)} samples (need >= 3)"
            )

        binned = bin_samples_by_angle(samples, trace_center, cfg.circle_segments)
        fit_samples = binned_to_motion_samples(trace_center, trace_radius, binned)
        fit = fit_first_harmonic(fit_samples, trace_center)
        if fit is None:
            logger.warning(
                f"eddy_seek: circle_harmonic pass {pass_num} fit failed - bootstrap"
            )
            self._frozen = bootstrap
            return bootstrap

        if not harmonic_model_accepted(
            fit,
            binned,
            noise_k=cfg.noise_k,
            min_quality=cfg.harmonic_min_quality,
        ):
            logger.warning(
                f"eddy_seek: circle_harmonic pass {pass_num} model rejected "
                f"(amp={fit.amplitude:.4f} noise={fit.noise:.4f}) - bootstrap"
            )
            self._frozen = bootstrap
            return bootstrap

        f_prime = radial_slope(self._x_profile, self._y_profile, trace_radius)
        step = harmonic_step_v2(
            fit,
            f_prime,
            step_gain=cfg.harmonic_step_gain,
            max_jog_x=cfg.max_jog_x,
            max_jog_y=cfg.max_jog_y,
        )
        unclamped = trace_center + step
        result = unclamped.clamp(cfg.max_jog_x, cfg.max_jog_y)

        if result.distance_to(bootstrap) > cfg.tolerance:
            logger.warning(
                f"eddy_seek: circle_harmonic pass {pass_num} diverged from bootstrap "
                f"({result.x:.4f}, {result.y:.4f}) vs ({bootstrap.x:.4f}, {bootstrap.y:.4f})"
            )
            self._frozen = bootstrap
            return bootstrap

        if harmonic_converged(fit, step, cfg.tolerance, cfg.noise_k):
            logger.debug(f"eddy_seek: circle_harmonic converged at pass {pass_num}")
            self._frozen = result

        ctx.append_trace(
            {
                "type": "circle_harmonic",
                "pass": pass_num,
                "radius": trace_radius,
                "result": {"x": result.x, "y": result.y},
                "harmonic": {"a": fit.a, "b": fit.b, "amp": fit.amplitude},
            }
        )
        return result

    def _refresh_profiles(
        self,
        ctx: SeekSession,
        pass_num: int,
        center: Offset,
        radius: float,
    ) -> None:
        cfg = ctx.config
        speed = cfg.sweep_coarse_speed
        _, samples_x = self._sweep_axis(
            ctx,
            Axis.X,
            center.x,
            radius,
            center.y,
            pass_num,
            Phase.FINE,
            speed,
        )
        _, samples_y = self._sweep_axis(
            ctx,
            Axis.Y,
            center.y,
            radius,
            center.x,
            pass_num,
            Phase.FINE,
            speed,
        )
        box = search_box(center, radius, radius, cfg.max_jog_x, cfg.max_jog_y)
        self._x_profile = [(s.offset.x, s.freq) for s in samples_in_box(samples_x, box)]
        self._y_profile = [(s.offset.y, s.freq) for s in samples_in_box(samples_y, box)]

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
                f"(need >= {cfg.min_sweep_samples})"
            )
        return points, samples

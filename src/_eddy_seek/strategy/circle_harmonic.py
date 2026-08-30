"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Circle-harmonic strategy: sweep bootstrap then 1st+2nd harmonic nulling.
"""

from __future__ import annotations

import logging
import math
from dataclasses import replace

from ..common import Offset, Phase
from ..harmonic import (
    HarmonicFit,
    axis_radial_slope,
    bin_samples_by_angle,
    fit_harmonics,
    harmonic_fit_quality,
    harmonic_step,
)
from ..kconsole import KConsole
from ..movement.handler import MotionSample
from ..movement.paths import circle_arc_legs, circle_in_jog_box, circle_lead_in_legs
from ..movement.sweep import (
    MotionCapture,
    SweepSettings,
    _require_min_sweep_samples,
    axis_sweep_centroid,
)
from ..plotting.artifacts import finalize_strategy_plot
from ..plotting.primitives import CircleHarmonicPassRecord, PassMove, XYCloud
from ..session import SeekSession
from .base import MaxPassesError, SeekStrategy

logger = logging.getLogger(__name__)

_LEAD_IN = 0.25
_RADIUS_EPS = 1e-6
_BIN_COUNT = 36


class CircleHarmonicStrategy(SeekStrategy):
    def __init__(self) -> None:
        self._x_profile: list[tuple[float, float]] = []
        self._y_profile: list[tuple[float, float]] = []

    @property
    def name(self) -> str:
        return "circle_harmonic"

    def announce_start(self, ctx: SeekSession, console: KConsole) -> None:
        cfg = ctx.config
        logger.info(
            f"eddy_seek: circle_harmonic coarse={cfg.sweep_coarse_speed / 60.0:.2f} mm/s "
            f"circle={cfg.circle_speed / 60.0:.2f} mm/s "
            f"r={cfg.circle_radius_start:.3f}->{cfg.circle_radius_min:.3f}"
        )

    def on_session_end(self, ctx: SeekSession) -> str | None:
        return finalize_strategy_plot(ctx, self.name)

    def search(self, ctx: SeekSession, console: KConsole) -> tuple[Offset, int]:
        cfg = ctx.config
        self._x_profile = []
        self._y_profile = []
        best = Offset.zero()
        best = self._bootstrap(ctx, 1, best)
        console.info(self._pass_message(1, best, best.abs_components(), ctx))
        if cfg.max_passes <= 1:
            return best, 1

        n_circle = cfg.max_passes - 1
        for index in range(n_circle):
            pass_num = index + 2
            radius = _radius_for_index(
                index,
                n_circle,
                cfg.circle_radius_start,
                cfg.circle_radius_min,
            )
            new, accepted, moved = self._circle_pass(ctx, pass_num, best, radius)
            console.info(self._pass_message(pass_num, new, moved, ctx))
            if accepted:
                best = new
                last_circle = index == n_circle - 1
                at_min = last_circle or radius <= cfg.circle_radius_min + _RADIUS_EPS
                if moved.x < cfg.tolerance and moved.y < cfg.tolerance and at_min:
                    logger.info(
                        f"eddy_seek: circle_harmonic converged after pass {pass_num}"
                    )
                    return best, pass_num
            elif radius <= cfg.circle_radius_min + _RADIUS_EPS:
                raise MaxPassesError(
                    self.name,
                    max_passes=cfg.max_passes,
                    tolerance=cfg.tolerance,
                )

        raise MaxPassesError(
            self.name,
            max_passes=cfg.max_passes,
            tolerance=cfg.tolerance,
        )

    def _step(self, ctx: SeekSession, pass_num: int, best: Offset) -> Offset:
        _ = (ctx, pass_num, best)
        raise RuntimeError("circle_harmonic.search owns the pass loop")

    def _pass_message(
        self,
        pass_num: int,
        new: Offset,
        moved: Offset,
        ctx: SeekSession,
    ) -> str:
        kind = "bootstrap" if pass_num == 1 else "circle"
        logger.info(
            f"eddy_seek: circle_harmonic pass {pass_num} ({kind}) "
            f"moved=({moved.x:.4f}, {moved.y:.4f})"
        )
        return f"Pass {pass_num} ({kind}): {new.to_delta_str()}"

    def _bootstrap(self, ctx: SeekSession, pass_num: int, best: Offset) -> Offset:
        cfg = ctx.config
        capture = MotionCapture(ctx.motion, ctx.session_start, ctx.sync_offset)
        settings = replace(SweepSettings.from_config(cfg), cross_passes=1)
        sweep = axis_sweep_centroid(
            capture,
            settings,
            best,
            half_x=cfg.max_jog_x,
            half_y=cfg.max_jog_y,
            speed_mm_min=cfg.sweep_coarse_speed,
            phase=Phase.COARSE,
            pass_num=pass_num,
            label="circle_harmonic bootstrap",
            recorder=ctx.recorder,
        )
        self._x_profile = sweep.x_profile
        self._y_profile = sweep.y_profile
        result = sweep.centroid
        if result is None:
            logger.warning(
                f"eddy_seek: flat frequency on bootstrap - "
                f"keeping ({best.x:.4f}, {best.y:.4f})"
            )
            result = best
        else:
            result = result.clamp(cfg.max_jog_x, cfg.max_jog_y)
        _record_pass(
            ctx,
            pass_num,
            kind="bootstrap",
            center=best,
            result=result,
            samples=sweep.in_box,
            orbit_center=best,
            radius=None,
        )
        return result

    def _circle_pass(
        self,
        ctx: SeekSession,
        pass_num: int,
        best: Offset,
        radius: float,
    ) -> tuple[Offset, bool, Offset]:
        cfg = ctx.config
        center, trace_r = circle_in_jog_box(best, radius, cfg.max_jog_x, cfg.max_jog_y)
        if trace_r <= 0.0:
            logger.warning("eddy_seek: circle radius collapsed inside jog box")
            _record_pass(
                ctx,
                pass_num,
                kind="circle",
                center=best,
                result=best,
                samples=[],
                orbit_center=best,
                radius=radius,
                accepted=False,
            )
            return best, False, Offset.zero()

        orbit = circle_arc_legs(center, trace_r, cfg.circle_arc_resolution)
        capture = MotionCapture(ctx.motion, ctx.session_start, ctx.sync_offset)
        samples = capture.run(
            [*circle_lead_in_legs(orbit, _LEAD_IN), *orbit],
            cfg.circle_speed,
            min_samples=cfg.min_sweep_samples,
            span_mm=2.0 * math.pi * trace_r,
        )
        _require_min_sweep_samples(
            len(samples), cfg.min_sweep_samples, label="circle_harmonic"
        )

        fit = fit_harmonics(samples, center)
        quality = harmonic_fit_quality(fit, samples, center) if fit is not None else 0.0
        accepted = fit is not None and quality >= cfg.harmonic_min_quality
        step = Offset.zero()
        result = best
        if accepted and fit is not None:
            step = harmonic_step(
                fit,
                axis_radial_slope(self._x_profile, trace_r, center.x),
                axis_radial_slope(self._y_profile, trace_r, center.y),
                step_gain=cfg.harmonic_step_gain,
                radius=trace_r,
                search_for=cfg.search_for,
                max_jog_x=cfg.max_jog_x,
                max_jog_y=cfg.max_jog_y,
            )
            result = (center + step).clamp(cfg.max_jog_x, cfg.max_jog_y)
        moved = (result - best).abs_components()
        _record_pass(
            ctx,
            pass_num,
            kind="circle",
            center=best,
            result=result,
            samples=samples,
            orbit_center=center,
            radius=trace_r,
            fit=fit,
            quality=quality,
            accepted=accepted,
        )
        logger.info(
            f"eddy_seek: circle_harmonic pass {pass_num} r={trace_r:.3f} "
            f"q={quality:.2f} accepted={accepted} "
            f"-> ({result.x:.4f}, {result.y:.4f})"
        )
        return result, accepted, moved


def _radius_for_index(
    index: int,
    n_circle: int,
    radius_start: float,
    radius_min: float,
) -> float:
    if n_circle <= 1:
        return radius_start
    t = index / (n_circle - 1)
    return radius_start + t * (radius_min - radius_start)


def _record_pass(
    ctx: SeekSession,
    pass_num: int,
    *,
    kind: str,
    center: Offset,
    result: Offset,
    samples: list[MotionSample],
    orbit_center: Offset,
    radius: float | None,
    fit: HarmonicFit | None = None,
    quality: float | None = None,
    accepted: bool = True,
) -> None:
    binned: tuple[tuple[float, float], ...] = ()
    if samples:
        binned = tuple(bin_samples_by_angle(samples, orbit_center, _BIN_COUNT))
    ctx.recorder.record(
        CircleHarmonicPassRecord(
            pass_num=pass_num,
            kind=kind,
            move=PassMove.compute(center, result),
            samples=XYCloud.from_samples(samples),
            radius=radius,
            binned=binned,
            amp1=None if fit is None else fit.amplitude,
            amp2=None if fit is None else fit.amplitude2,
            quality=quality,
            accepted=accepted,
            c0=None if fit is None else fit.c0,
            a=None if fit is None else fit.a,
            b=None if fit is None else fit.b,
            c2=None if fit is None else fit.c2,
            d2=None if fit is None else fit.d2,
        )
    )

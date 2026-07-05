"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Circle-harmonic strategy: sweep bootstrap + guarded first-harmonic nulling.
"""

from __future__ import annotations

import logging
import math

from ..common import Axis, Offset, Phase, samples_in_box, search_box
from ..harmonic import (
    HarmonicFit,
    bin_samples_by_angle,
    binned_to_motion_samples,
    circle_arc_legs,
    circle_in_jog_box,
    circle_radius_for_pass,
    fit_first_harmonic,
    harmonic_bootstrap_diverged,
    harmonic_bootstrap_divergence_limit,
    harmonic_converged,
    harmonic_reject_reasons,
    harmonic_step_v2,
    radial_slope,
)
from ..kconsole import KConsole
from ..movement.handler import MotionSample, get_clamped_speed_for_min_samples_over_span
from ..movement.leg_planner import (
    clamped_sweep_axis,
    get_samples_from_capture_legs,
)
from ..optimizer import decoupled_centroid
from ..plotting.primitives import (
    BinnedProfile,
    Bounds,
    CircleBootstrapRecord,
    CircleHarmonicPassRecord,
    PassMove,
    XYCloud,
)
from ..plotting.renderer import finalize_strategy_plot
from ..session import SeekSession
from .base import SeekStrategy, _check_pass_divergence

logger = logging.getLogger(__name__)


class CircleHarmonicStrategy(SeekStrategy):
    """Sweep bootstrap, then circle harmonic nulling with model and bore gates."""

    def __init__(self) -> None:
        self._bootstrap: Offset | None = None
        self._x_profile: list[tuple[float, float]] = []
        self._y_profile: list[tuple[float, float]] = []
        self._frozen: Offset | None = None
        self._last_pass_rejected = False

    @property
    def name(self) -> str:
        return "circle_harmonic"

    def announce_start(self, ctx: SeekSession, console: KConsole) -> None:
        cfg = ctx.config
        logger.info(
            f"eddy_seek: circle_harmonic coarse={cfg.sweep_coarse_speed / 60.0:.2f} mm/s "
            f"circle={cfg.circle_speed / 60.0:.2f} mm/s "
            f"arc_res={cfg.circle_arc_resolution} "
            f"refresh_sweeps={cfg.circle_refresh_sweeps} "
            f"skip_bootstrap={cfg.circle_skip_bootstrap} "
            f"slope_only={cfg.circle_bootstrap_slope_only}"
        )

    def on_session_end(self, ctx: SeekSession) -> str | None:
        self._bootstrap = None
        self._frozen = None
        self._last_pass_rejected = False
        return finalize_strategy_plot(ctx, self.name)

    def search(self, ctx: SeekSession, console: KConsole) -> tuple[Offset, int]:
        """Keep trying smaller circle radii after a rejected harmonic fit."""

        cfg = ctx.config
        best = Offset.zero()
        positions = [best]
        passes_run = 0

        for pass_num in range(1, cfg.max_passes + 1):
            passes_run = pass_num
            logger.info(
                f"eddy_seek: {self.name} pass {pass_num} start "
                f"best=({best.x:.4f}, {best.y:.4f})"
            )
            self._last_pass_rejected = False
            new = self._step(ctx, pass_num, best)
            moved = (new - best).abs_components()
            console.info(self._pass_message(pass_num, new, moved, ctx))
            positions.append(new)
            if not self._last_pass_rejected:
                _check_pass_divergence(
                    positions, tolerance=cfg.tolerance, pass_num=pass_num
                )
            best = new

            if self._frozen is not None:
                logger.info(
                    f"eddy_seek: {self.name} finished after pass {pass_num} "
                    f"(frozen at {best.x:.4f}, {best.y:.4f})"
                )
                break

            if moved.x < cfg.tolerance and moved.y < cfg.tolerance:
                if self._last_pass_rejected:
                    logger.info(
                        f"eddy_seek: {self.name} pass {pass_num} rejected "
                        f"- retrying with smaller circle if passes remain"
                    )
                    continue
                if cfg.circle_bootstrap_slope_only and pass_num == 1:
                    logger.info(
                        f"eddy_seek: {self.name} slope-only bootstrap done "
                        f"- continuing to circle passes"
                    )
                    continue
                logger.info(
                    f"eddy_seek: {self.name} converged after pass {pass_num} "
                    f"(moved {moved.x:.4f}, {moved.y:.4f})"
                )
                break
        else:
            logger.info(
                f"eddy_seek: {self.name} hit max_passes={cfg.max_passes} "
                "without convergence"
            )

        return best, passes_run

    def _step(self, ctx: SeekSession, pass_num: int, best: Offset) -> Offset:
        if self._frozen is not None:
            return self._frozen

        if ctx.config.circle_skip_bootstrap:
            if self._bootstrap is None:
                self._bootstrap = best
            return self._circle_pass(ctx, pass_num, best)

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
            if ctx.config.circle_bootstrap_slope_only:
                return (
                    f"Pass {pass_num} (slope cal): "
                    f"holding X={new.x:+.4f} Y={new.y:+.4f} mm"
                )
            if not ctx.config.circle_skip_bootstrap:
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

        _, samples_x = clamped_sweep_axis(
            ctx, Axis.X, best.x, half_x, best.y, speed, Phase.COARSE, pass_num
        )
        _, samples_y = clamped_sweep_axis(
            ctx, Axis.Y, best.y, half_y, best.x, speed, Phase.COARSE, pass_num
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
        if cfg.circle_bootstrap_slope_only:
            self._bootstrap = best
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
            self._record_bootstrap_plot(
                ctx,
                pass_num,
                best,
                best,
                [*in_box_x, *in_box_y],
                box,
                skipped=centroid,
            )
            return best

        if result_or_none is None:
            logger.warning(
                f"eddy_seek: flat frequency on bootstrap - "
                f"keeping ({best.x:.4f}, {best.y:.4f})"
            )
            self._bootstrap = best
            self._record_bootstrap_plot(
                ctx,
                pass_num,
                best,
                best,
                [*in_box_x, *in_box_y],
                box,
            )
            return best

        result = result_or_none.clamp(cfg.max_jog_x, cfg.max_jog_y)
        self._bootstrap = result
        logger.info(
            f"eddy_seek: circle_harmonic bootstrap -> ({result.x:.4f}, {result.y:.4f})"
        )
        self._record_bootstrap_plot(
            ctx,
            pass_num,
            best,
            result,
            [*in_box_x, *in_box_y],
            box,
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

        circle_pass_num = pass_num if cfg.circle_skip_bootstrap else pass_num - 1
        radius = circle_radius_for_pass(
            circle_pass_num + 1,
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
            self._frozen = best
            return best

        legs = circle_arc_legs(trace_center, trace_radius, cfg.circle_arc_resolution)
        if not legs:
            self._frozen = best
            return best
        circumfrence = 2 * math.pi * trace_radius
        clamped_speed = get_clamped_speed_for_min_samples_over_span(
            requested_mm_min=cfg.circle_speed,
            span_mm=circumfrence,  # approx equal to the polygon we move on
            min_samples=max(
                cfg.min_sweep_samples, 2 * len(legs)
            ),  # try to force ~2 samples per segment
        )
        logger.info(
            f"eddy_seek: circle_harmonic pass {pass_num} "
            f"arc_segments={len(legs)} clamped_speed={clamped_speed:.4f} mm/s"
        )

        if cfg.circle_refresh_sweeps:
            self._refresh_profiles(ctx, pass_num, trace_center, trace_radius)

        samples = get_samples_from_capture_legs(ctx, legs, clamped_speed)
        logger.info(
            f"eddy_seek: circle_harmonic pass {pass_num} collected {len(samples)} samples --> {len(samples) / len(legs):.2f} samples per segment"
        )

        if len(samples) < 3:
            raise RuntimeError(
                f"eddy_seek: circle_harmonic pass {pass_num} collected "
                f"{len(samples)} samples (need >= 3)"
            )

        binned = bin_samples_by_angle(samples, trace_center, len(legs))
        logger.info(f"eddy_seek: circle_harmonic pass {pass_num} bins = {len(binned)}")
        fit_samples = binned_to_motion_samples(trace_center, trace_radius, binned)
        fit = fit_first_harmonic(fit_samples, trace_center)
        if fit is None:
            self._last_pass_rejected = True
            logger.warning(
                f"eddy_seek: circle_harmonic pass {pass_num} fit failed "
                f"(r={trace_radius:.3f}) - holding bootstrap"
            )
            self._record_circle_plot(
                ctx,
                pass_num,
                best,
                bootstrap,
                trace_center,
                trace_radius,
                samples,
                binned,
                fit=None,
                rejected=True,
                reject_reasons="fit failed",
            )
            return best

        reject_reasons = harmonic_reject_reasons(
            fit,
            binned,
            noise_k=cfg.noise_k,
            min_quality=cfg.harmonic_min_quality,
        )
        if reject_reasons:
            self._last_pass_rejected = True
            logger.warning(
                f"eddy_seek: circle_harmonic pass {pass_num} model rejected "
                f"(r={trace_radius:.3f} amp={fit.amplitude:.4f} "
                f"noise={fit.noise:.4f}): {', '.join(reject_reasons)}"
            )
            self._record_circle_plot(
                ctx,
                pass_num,
                best,
                bootstrap,
                trace_center,
                trace_radius,
                samples,
                binned,
                fit=fit,
                rejected=True,
                reject_reasons=", ".join(reject_reasons),
            )
            return best

        f_prime = radial_slope(
            self._x_profile, self._y_profile, trace_radius, center=trace_center
        )
        step = harmonic_step_v2(
            fit,
            f_prime,
            step_gain=cfg.harmonic_step_gain,
            radius=trace_radius,
            search_for=cfg.search_for,
            max_jog_x=cfg.max_jog_x,
            max_jog_y=cfg.max_jog_y,
        )
        unclamped = trace_center + step
        result = unclamped.clamp(cfg.max_jog_x, cfg.max_jog_y)

        divergence = result.distance_to(bootstrap)
        anchor_floor = (
            math.hypot(cfg.max_jog_x, cfg.max_jog_y)
            if cfg.circle_skip_bootstrap or cfg.circle_bootstrap_slope_only
            else 0.0
        )
        divergence_limit = harmonic_bootstrap_divergence_limit(
            bootstrap, trace_radius, cfg.tolerance, anchor_floor=anchor_floor
        )
        if harmonic_bootstrap_diverged(
            result,
            bootstrap,
            trace_radius,
            cfg.tolerance,
            anchor_floor=anchor_floor,
        ):
            self._last_pass_rejected = True
            logger.warning(
                f"eddy_seek: circle_harmonic pass {pass_num} diverged from bootstrap "
                f"Δ={divergence:.4f} > limit {divergence_limit:.4f} "
                f"({result.x:.4f}, {result.y:.4f}) vs ({bootstrap.x:.4f}, {bootstrap.y:.4f})"
            )
            self._record_circle_plot(
                ctx,
                pass_num,
                best,
                bootstrap,
                trace_center,
                trace_radius,
                samples,
                binned,
                fit=fit,
                rejected=True,
                reject_reasons=(
                    f"diverged from bootstrap (Δ={divergence:.4f} > {divergence_limit:.4f})"
                ),
            )
            return best

        if harmonic_converged(fit, step, cfg.tolerance, cfg.noise_k):
            logger.info(f"eddy_seek: circle_harmonic converged at pass {pass_num}")
            self._frozen = result

        self._record_circle_plot(
            ctx,
            pass_num,
            best,
            result,
            trace_center,
            trace_radius,
            samples,
            binned,
            fit=fit,
            rejected=False,
        )
        return result

    def _record_bootstrap_plot(
        self,
        ctx: SeekSession,
        pass_num: int,
        center: Offset,
        result: Offset,
        samples: list[MotionSample],
        box: tuple[float, float, float, float],
        *,
        skipped: Offset | None = None,
    ) -> None:
        rec = ctx.recorder
        if not rec.active:
            return
        rec.record(
            CircleBootstrapRecord(
                pass_num=pass_num,
                move=PassMove.compute(center, result),
                samples=XYCloud(
                    tuple(sample.offset.x for sample in samples),
                    tuple(sample.offset.y for sample in samples),
                    tuple(sample.freq for sample in samples),
                ),
                bounds=Bounds.from_box(box),
                skipped=skipped,
            )
        )

    def _record_circle_plot(
        self,
        ctx: SeekSession,
        pass_num: int,
        best: Offset,
        result: Offset,
        trace_center: Offset,
        trace_radius: float,
        samples: list[MotionSample],
        binned: list[tuple[float, float]],
        *,
        fit: HarmonicFit | None,
        rejected: bool,
        reject_reasons: str = "",
    ) -> None:
        rec = ctx.recorder
        if not rec.active:
            return
        rec.record(
            CircleHarmonicPassRecord(
                pass_num=pass_num,
                trace_center=trace_center,
                radius=trace_radius,
                move=PassMove.compute(best, result),
                samples=XYCloud(
                    tuple(sample.offset.x for sample in samples),
                    tuple(sample.offset.y for sample in samples),
                    tuple(sample.freq for sample in samples),
                ),
                binned=BinnedProfile(
                    tuple(theta for theta, _ in binned),
                    tuple(freq for _, freq in binned),
                ),
                fit=fit,
                rejected=rejected,
                reject_reasons=reject_reasons,
            )
        )

    def _refresh_profiles(
        self,
        ctx: SeekSession,
        pass_num: int,
        center: Offset,
        radius: float,
    ) -> None:
        cfg = ctx.config
        speed = cfg.sweep_coarse_speed
        length = abs(radius)

        clamped_speed = get_clamped_speed_for_min_samples_over_span(
            requested_mm_min=speed,
            span_mm=length,
            min_samples=cfg.min_sweep_samples,
        )
        _, samples_x = clamped_sweep_axis(
            ctx,
            Axis.X,
            center.x,
            radius,
            center.y,
            clamped_speed,
            Phase.FINE,
            pass_num,
        )
        _, samples_y = clamped_sweep_axis(
            ctx,
            Axis.Y,
            center.y,
            radius,
            center.x,
            clamped_speed,
            Phase.FINE,
            pass_num,
        )
        box = search_box(center, radius, radius, cfg.max_jog_x, cfg.max_jog_y)
        self._x_profile = [(s.offset.x, s.freq) for s in samples_in_box(samples_x, box)]
        self._y_profile = [(s.offset.y, s.freq) for s in samples_in_box(samples_y, box)]

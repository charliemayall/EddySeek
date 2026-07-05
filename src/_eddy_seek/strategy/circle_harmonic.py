"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Circle-harmonic strategy: sweep bootstrap + guarded first-harmonic nulling.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from ..common import Offset, Phase
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
    kalman_filter_freqs,
    radial_slope,
)
from ..kconsole import KConsole
from ..movement.handler import MotionSample, get_clamped_speed_for_min_samples_over_span
from ..movement.leg_planner import (
    _axis_sweep_profiles,
    axis_sweep_centroid,
    get_samples_from_capture_legs,
)
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
from .base import SeekStrategy

logger = logging.getLogger(__name__)

MIN_SAMPLES_PER_SPAN = 3
KALMAN_PROCESS_VAR = 1.0
KALMAN_MEASURE_VAR = 100.0
# ponytail: experiment — repeat radius while passes ok, shrink after first reject
RADIUS_PLATEAU_MODE = True


@dataclass(frozen=True, slots=True)
class _CirclePassOutcome:
    result: Offset
    trace_center: Offset
    trace_radius: float
    samples: list[MotionSample]
    binned: list[tuple[float, float]]
    fit: HarmonicFit | None
    rejected: bool
    reject_reasons: str
    freeze: bool


def _outcome_hold(
    best: Offset,
    trace_center: Offset,
    trace_radius: float,
    *,
    samples: list[MotionSample] | None = None,
    binned: list[tuple[float, float]] | None = None,
) -> _CirclePassOutcome:
    """Radius too small / no legs — freeze, no plot."""
    return _CirclePassOutcome(
        result=best,
        trace_center=trace_center,
        trace_radius=trace_radius,
        samples=samples or [],
        binned=binned or [],
        fit=None,
        rejected=False,
        reject_reasons="",
        freeze=True,
    )


def _outcome_reject(
    best: Offset,
    trace_center: Offset,
    trace_radius: float,
    samples: list[MotionSample],
    binned: list[tuple[float, float]],
    *,
    fit: HarmonicFit | None,
    reason: str,
) -> _CirclePassOutcome:
    """Rejected fit — hold best, plot bootstrap."""
    return _CirclePassOutcome(
        result=best,
        trace_center=trace_center,
        trace_radius=trace_radius,
        samples=samples,
        binned=binned,
        fit=fit,
        rejected=True,
        reject_reasons=reason,
        freeze=False,
    )


def _outcome_accept(
    result: Offset,
    trace_center: Offset,
    trace_radius: float,
    samples: list[MotionSample],
    binned: list[tuple[float, float]],
    fit: HarmonicFit,
    *,
    freeze: bool,
) -> _CirclePassOutcome:
    """Successful step — plot result, freeze when search is done."""
    return _CirclePassOutcome(
        result=result,
        trace_center=trace_center,
        trace_radius=trace_radius,
        samples=samples,
        binned=binned,
        fit=fit,
        rejected=False,
        reject_reasons="",
        freeze=freeze,
    )


class CircleHarmonicStrategy(SeekStrategy):
    """Sweep bootstrap, then circle harmonic nulling with model and bore gates."""

    def __init__(self) -> None:
        self._bootstrap: Offset | None = None
        self._x_profile: list[tuple[float, float]] = []
        self._y_profile: list[tuple[float, float]] = []
        self._frozen: Offset | None = None
        self._last_pass_rejected = False
        self._last_tier_shrink = False
        self._radius_tier = 0
        self._tier_had_ok = False
        self._last_ok: Offset | None = None

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
        self._last_tier_shrink = False
        self._radius_tier = 0
        self._tier_had_ok = False
        self._last_ok = None
        return finalize_strategy_plot(ctx, self.name)

    def _before_pass(self, ctx: SeekSession, pass_num: int) -> None:
        self._last_pass_rejected = False
        self._last_tier_shrink = False

    def should_check_divergence(self, ctx: SeekSession, pass_num: int) -> bool:
        return not self._last_pass_rejected

    def should_stop(
        self,
        ctx: SeekSession,
        pass_num: int,
        best: Offset,
        moved: Offset,
    ) -> bool:
        if self._frozen is None:
            return False
        logger.info(
            f"eddy_seek: {self.name} finished after pass {pass_num} "
            f"(frozen at {best.x:.4f}, {best.y:.4f})"
        )
        return True

    def continue_after_convergence(
        self,
        ctx: SeekSession,
        pass_num: int,
        moved: Offset,
    ) -> bool:
        cfg = ctx.config
        if self._last_pass_rejected:
            if RADIUS_PLATEAU_MODE and self._last_tier_shrink:
                logger.info(
                    f"eddy_seek: {self.name} pass {pass_num} rejected after ok "
                    f"- continuing at smaller circle"
                )
            else:
                logger.info(
                    f"eddy_seek: {self.name} pass {pass_num} rejected "
                    f"- retrying at same radius if passes remain"
                )
            return True
        if cfg.circle_bootstrap_slope_only and pass_num == 1:
            logger.info(
                f"eddy_seek: {self.name} slope-only bootstrap done "
                f"- continuing to circle passes"
            )
            return True
        return False

    def _circle_center(self, best: Offset) -> Offset:
        if RADIUS_PLATEAU_MODE and self._last_ok is not None:
            return self._last_ok
        return best

    def _step(self, ctx: SeekSession, pass_num: int, best: Offset) -> Offset:
        if ctx.config.circle_skip_bootstrap:
            if self._bootstrap is None:
                self._bootstrap = best
            outcome = self._compute_circle_pass(ctx, pass_num, best)
            return self._finish_circle_pass(ctx, pass_num, best, outcome)

        if pass_num == 1:
            return self._bootstrap_pass(ctx, pass_num, best)

        outcome = self._compute_circle_pass(ctx, pass_num, best)
        return self._finish_circle_pass(ctx, pass_num, best, outcome)

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
        sweep = axis_sweep_centroid(
            ctx,
            best,
            half_x=cfg.max_jog_x,
            half_y=cfg.max_jog_y,
            speed=cfg.sweep_coarse_speed,
            phase=Phase.COARSE,
            pass_num=pass_num,
            label="circle_harmonic bootstrap",
        )
        self._x_profile = sweep.x_profile
        self._y_profile = sweep.y_profile
        result_or_none = sweep.centroid
        box = sweep.box
        in_box = sweep.in_box

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
                in_box,
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
                in_box,
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
            in_box,
            box,
        )
        return result

    def _compute_circle_pass(
        self,
        ctx: SeekSession,
        pass_num: int,
        best: Offset,
    ) -> _CirclePassOutcome:
        cfg = ctx.config
        bootstrap = self._bootstrap if self._bootstrap is not None else best

        if RADIUS_PLATEAU_MODE:
            radius = circle_radius_for_pass(
                self._radius_tier + 2,
                radius_start=cfg.circle_radius_start,
                radius_min=cfg.circle_radius_min,
                radius_shrink=cfg.circle_shrink,
            )
        else:
            circle_pass_num = pass_num if cfg.circle_skip_bootstrap else pass_num - 1
            radius = circle_radius_for_pass(
                circle_pass_num + 1,
                radius_start=cfg.circle_radius_start,
                radius_min=cfg.circle_radius_min,
                radius_shrink=cfg.circle_shrink,
            )
        trace_center, trace_radius = circle_in_jog_box(
            self._circle_center(best), radius, cfg.max_jog_x, cfg.max_jog_y
        )
        if trace_radius < cfg.circle_radius_min:
            logger.warning(
                f"eddy_seek: circle_harmonic pass {pass_num} radius {trace_radius:.4f} "
                f"< min {cfg.circle_radius_min} - holding bootstrap"
            )
            return _outcome_hold(best, trace_center, trace_radius)

        legs = circle_arc_legs(trace_center, trace_radius, cfg.circle_arc_resolution)
        if not legs:
            return _outcome_hold(best, trace_center, trace_radius)

        circumference = 2 * math.pi * trace_radius
        clamped_speed = get_clamped_speed_for_min_samples_over_span(
            requested_mm_min=cfg.circle_speed,
            span_mm=circumference,
            min_samples=max(cfg.min_sweep_samples, MIN_SAMPLES_PER_SPAN * len(legs)),
        )
        logger.info(
            f"eddy_seek: circle_harmonic pass {pass_num} "
            f"arc_segments={len(legs)} clamped_speed={clamped_speed:.4f} mm/s"
        )

        if cfg.circle_refresh_sweeps:
            self._refresh_profiles(ctx, pass_num, trace_center, trace_radius)

        samples = get_samples_from_capture_legs(ctx, legs, clamped_speed, flat=True)
        samples = kalman_filter_freqs(
            samples,
            process_var=KALMAN_PROCESS_VAR,
            measure_var=KALMAN_MEASURE_VAR,
        )
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
            logger.warning(
                f"eddy_seek: circle_harmonic pass {pass_num} fit failed "
                f"(r={trace_radius:.3f}) - holding bootstrap"
            )
            return _outcome_reject(
                best,
                trace_center,
                trace_radius,
                samples,
                binned,
                fit=None,
                reason="fit failed",
            )

        reject_reasons = harmonic_reject_reasons(
            fit,
            binned,
            noise_k=cfg.noise_k,
            min_quality=cfg.harmonic_min_quality,
        )
        if reject_reasons:
            logger.warning(
                f"eddy_seek: circle_harmonic pass {pass_num} model rejected "
                f"(r={trace_radius:.3f} amp={fit.amplitude:.4f} "
                f"noise={fit.noise:.4f}): {', '.join(reject_reasons)}"
            )
            return _outcome_reject(
                best,
                trace_center,
                trace_radius,
                samples,
                binned,
                fit=fit,
                reason=", ".join(reject_reasons),
            )

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
            logger.warning(
                f"eddy_seek: circle_harmonic pass {pass_num} diverged from bootstrap "
                f"Δ={divergence:.4f} > limit {divergence_limit:.4f} "
                f"({result.x:.4f}, {result.y:.4f}) vs ({bootstrap.x:.4f}, {bootstrap.y:.4f})"
            )
            return _outcome_reject(
                best,
                trace_center,
                trace_radius,
                samples,
                binned,
                fit=fit,
                reason=(
                    f"diverged from bootstrap (Δ={divergence:.4f} > {divergence_limit:.4f})"
                ),
            )

        converged = harmonic_converged(fit, step, cfg.tolerance, cfg.noise_k)
        if converged:
            logger.info(f"eddy_seek: circle_harmonic converged at pass {pass_num}")
        if RADIUS_PLATEAU_MODE:
            at_min = trace_radius <= cfg.circle_radius_min + 1e-9
            freeze = converged and at_min
        else:
            freeze = converged
        return _outcome_accept(
            result,
            trace_center,
            trace_radius,
            samples,
            binned,
            fit,
            freeze=freeze,
        )

    def _finish_circle_pass(
        self,
        ctx: SeekSession,
        pass_num: int,
        best: Offset,
        outcome: _CirclePassOutcome,
    ) -> Offset:
        bootstrap = self._bootstrap if self._bootstrap is not None else best
        if outcome.rejected:
            self._last_pass_rejected = True
            if RADIUS_PLATEAU_MODE and self._tier_had_ok:
                self._radius_tier += 1
                self._tier_had_ok = False
                self._last_tier_shrink = True
                logger.info(
                    f"eddy_seek: circle_harmonic radius tier -> {self._radius_tier} "
                    f"(reject after ok)"
                )
        elif RADIUS_PLATEAU_MODE and not outcome.freeze:
            self._tier_had_ok = True
            self._last_ok = outcome.result
        if outcome.freeze:
            self._frozen = outcome.result
        freeze_without_plot = outcome.freeze and not outcome.samples
        if outcome.rejected or (outcome.samples and not freeze_without_plot):
            if outcome.rejected:
                plot = (
                    self._last_ok
                    if RADIUS_PLATEAU_MODE and self._last_ok is not None
                    else bootstrap
                )
            else:
                plot = outcome.result
            self._record_circle_plot(
                ctx,
                pass_num,
                best,
                plot,
                outcome.trace_center,
                outcome.trace_radius,
                outcome.samples,
                outcome.binned,
                fit=outcome.fit,
                rejected=outcome.rejected,
                reject_reasons=outcome.reject_reasons,
            )
        if outcome.rejected and RADIUS_PLATEAU_MODE and self._last_ok is not None:
            return self._last_ok
        return outcome.result

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
        ctx.recorder.record_if_active(
            CircleBootstrapRecord(
                pass_num=pass_num,
                move=PassMove.compute(center, result),
                samples=XYCloud.from_samples(samples),
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
        ctx.recorder.record_if_active(
            CircleHarmonicPassRecord(
                pass_num=pass_num,
                trace_center=trace_center,
                radius=trace_radius,
                move=PassMove.compute(best, result),
                samples=XYCloud.from_samples(samples),
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
        _, self._x_profile, self._y_profile, _ = _axis_sweep_profiles(
            ctx,
            center,
            half_x=radius,
            half_y=radius,
            speed=ctx.config.sweep_coarse_speed,
            phase=Phase.FINE,
            pass_num=pass_num,
        )

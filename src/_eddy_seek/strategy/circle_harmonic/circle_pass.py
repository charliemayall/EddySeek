"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Circle arc capture, harmonic fit, and pass outcome for circle-harmonic search.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...common import Offset, Phase
from ...harmonic import (
    HarmonicFit,
    bin_samples_by_angle,
    binned_to_motion_samples,
    circle_arc_legs,
    circle_in_jog_box,
    circle_lead_in_legs,
    circle_radius_for_tier,
    fit_first_harmonic,
    harmonic_bootstrap_diverged,
    harmonic_bootstrap_divergence_limit,
    harmonic_converged,
    harmonic_reject_reasons,
    harmonic_step_v2,
    kalman_filter_freqs,
    radial_slope,
)
from ...movement.handler import MotionSample
from ...movement.leg_planner import MotionCapture, SweepSettings, axis_sweep_profiles
from ...session import SeekSession
from .bootstrap import _COARSE_CROSS_PASSES
from .plateau import (
    CircleHarmonicMode,
    PlateauState,
    is_below_min_radius,
)

if TYPE_CHECKING:
    from .strategy import CircleHarmonicStrategy

logger = logging.getLogger(__name__)

MIN_SAMPLES_PER_SPAN = 3

KALMAN_PROCESS_VAR = 1.0
KALMAN_MEASURE_VAR = 100.0


def _inner_leg_samples(
    leg_batches: list[list[MotionSample]],
    samples: list[MotionSample],
) -> list[MotionSample]:
    """Drop first/last arc legs; both meet at θ=0 and skew edge bins."""
    if len(leg_batches) <= 2:
        return samples
    keys = {
        (round(s.print_time, 6), round(s.offset.x, 6), round(s.offset.y, 6))
        for batch in leg_batches[1:-1]
        for s in batch
    }
    return [
        s
        for s in samples
        if (round(s.print_time, 6), round(s.offset.x, 6), round(s.offset.y, 6)) in keys
    ]


@dataclass(frozen=True, slots=True)
class CirclePassOutcome:
    result: Offset
    trace_center: Offset
    trace_radius: float
    samples: list[MotionSample]
    binned: list[tuple[float, float]]
    fit: HarmonicFit | None
    rejected: bool
    reject_reasons: str
    freeze: bool


def outcome_hold(
    best: Offset,
    trace_center: Offset,
    trace_radius: float,
    *,
    samples: list[MotionSample] | None = None,
    binned: list[tuple[float, float]] | None = None,
) -> CirclePassOutcome:
    return CirclePassOutcome(
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


def outcome_reject(
    best: Offset,
    trace_center: Offset,
    trace_radius: float,
    samples: list[MotionSample],
    binned: list[tuple[float, float]],
    *,
    fit: HarmonicFit | None,
    reason: str,
) -> CirclePassOutcome:
    return CirclePassOutcome(
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


def outcome_accept(
    result: Offset,
    trace_center: Offset,
    trace_radius: float,
    samples: list[MotionSample],
    binned: list[tuple[float, float]],
    fit: HarmonicFit,
    *,
    freeze: bool,
) -> CirclePassOutcome:
    return CirclePassOutcome(
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


def refresh_profiles(
    strategy: CircleHarmonicStrategy,
    ctx: SeekSession,
    pass_num: int,
    center: Offset,
    radius: float,
) -> None:
    capture = MotionCapture(ctx.motion, ctx.session_start, ctx.sync_offset)
    settings = SweepSettings.from_config(
        ctx.config, coarse_cross_passes=_COARSE_CROSS_PASSES
    )
    profiles = axis_sweep_profiles(
        capture,
        settings,
        center,
        half_x=radius,
        half_y=radius,
        speed_mm_min=ctx.config.sweep_coarse_speed,
        phase=Phase.FINE,
        pass_num=pass_num,
        recorder=ctx.recorder,
    )
    strategy._x_profile = profiles.x_profile
    strategy._y_profile = profiles.y_profile


def _circle_trace_geometry(
    plateau: PlateauState,
    best: Offset,
    cfg,
    pass_num: int,
) -> CirclePassOutcome | tuple[Offset, float, list]:
    radius = circle_radius_for_tier(
        plateau.tier,
        radius_start=cfg.circle_radius_start,
        radius_min=cfg.circle_radius_min,
        radius_shrink=cfg.circle_shrink,
    )
    trace_center, trace_radius = circle_in_jog_box(
        plateau.estimate(best), radius, cfg.max_jog_x, cfg.max_jog_y
    )
    if is_below_min_radius(trace_radius, cfg.circle_radius_min):
        logger.warning(
            f"eddy_seek: circle_harmonic pass {pass_num} radius {trace_radius:.4f} "
            f"< min {cfg.circle_radius_min} - holding bootstrap"
        )
        return outcome_hold(best, trace_center, trace_radius)

    legs = circle_arc_legs(trace_center, trace_radius, cfg.circle_arc_resolution)
    if not legs:
        return outcome_hold(best, trace_center, trace_radius)
    return trace_center, trace_radius, legs


def _collect_circle_harmonic(
    ctx: SeekSession,
    pass_num: int,
    best: Offset,
    trace_center: Offset,
    trace_radius: float,
    legs: list,
    lead_in: list | None,
    segment_span: float,
) -> (
    CirclePassOutcome
    | tuple[list[MotionSample], list[tuple[float, float]], HarmonicFit]
):
    cfg = ctx.config
    capture = MotionCapture(ctx.motion, ctx.session_start, ctx.sync_offset)
    leg_batches = capture.run(
        legs,
        cfg.circle_speed,
        flat=False,
        min_samples=MIN_SAMPLES_PER_SPAN,
        span_mm=segment_span,
        lead_in_legs=lead_in,
    )
    for i, batch in enumerate(leg_batches):
        if len(batch) < MIN_SAMPLES_PER_SPAN:
            logger.warning(
                f"eddy_seek: circle_harmonic pass {pass_num} "
                f"leg {i} has {len(batch)} samples "
                f"(need >= {MIN_SAMPLES_PER_SPAN})"
            )
    samples_raw = [s for batch in leg_batches for s in batch]
    samples = kalman_filter_freqs(
        samples_raw,
        process_var=KALMAN_PROCESS_VAR,
        measure_var=KALMAN_MEASURE_VAR,
    )
    fit_raw = _inner_leg_samples(leg_batches, samples_raw)
    if len(fit_raw) < 3:
        fit_raw = samples_raw
    fit_filtered = kalman_filter_freqs(
        fit_raw,
        process_var=KALMAN_PROCESS_VAR,
        measure_var=KALMAN_MEASURE_VAR,
    )
    leg_counts = [len(batch) for batch in leg_batches]
    min_per_leg = min(leg_counts) if leg_counts else 0
    empty_legs = sum(1 for n in leg_counts if n == 0)
    logger.info(
        f"eddy_seek: circle_harmonic pass {pass_num} collected "
        f"{len(samples)} samples - samples per segment: "
        f"avg={len(samples) / len(legs):.1f} min={min_per_leg} empty={empty_legs}"
    )

    if len(samples) < 3:
        raise RuntimeError(
            f"eddy_seek: circle_harmonic pass {pass_num} collected "
            f"{len(samples)} samples (need >= 3)"
        )

    binned = bin_samples_by_angle(fit_filtered, trace_center, len(legs))
    logger.info(f"eddy_seek: circle_harmonic pass {pass_num} bins = {len(binned)}")
    fit_samples = binned_to_motion_samples(trace_center, trace_radius, binned)
    fit = fit_first_harmonic(fit_samples, trace_center)
    if fit is None:
        logger.warning(
            f"eddy_seek: circle_harmonic pass {pass_num} fit failed "
            f"(r={trace_radius:.3f}) - holding bootstrap"
        )
        return outcome_reject(
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
        return outcome_reject(
            best,
            trace_center,
            trace_radius,
            samples,
            binned,
            fit=fit,
            reason=", ".join(reject_reasons),
        )
    return samples, binned, fit


def _harmonic_correction_outcome(
    strategy: CircleHarmonicStrategy,
    ctx: SeekSession,
    pass_num: int,
    best: Offset,
    bootstrap: Offset,
    mode: CircleHarmonicMode,
    trace_center: Offset,
    trace_radius: float,
    samples: list[MotionSample],
    binned: list[tuple[float, float]],
    fit: HarmonicFit,
) -> CirclePassOutcome:
    cfg = ctx.config
    f_prime = radial_slope(
        strategy._x_profile, strategy._y_profile, trace_radius, center=trace_center
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
    result = (trace_center + step).clamp(cfg.max_jog_x, cfg.max_jog_y)

    divergence = result.distance_to(bootstrap)
    anchor_floor = (
        math.hypot(cfg.max_jog_x, cfg.max_jog_y) if mode.skip_bootstrap else 0.0
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
        return outcome_reject(
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

    freeze = harmonic_converged(fit, step, cfg.tolerance, cfg.noise_k)
    if freeze:
        logger.info(f"eddy_seek: circle_harmonic converged at pass {pass_num}")
    return outcome_accept(
        result,
        trace_center,
        trace_radius,
        samples,
        binned,
        fit,
        freeze=freeze,
    )


def compute_circle_pass(
    strategy: CircleHarmonicStrategy,
    ctx: SeekSession,
    pass_num: int,
    best: Offset,
    mode: CircleHarmonicMode,
    plateau: PlateauState,
) -> CirclePassOutcome:
    cfg = ctx.config
    bootstrap = strategy._bootstrap if strategy._bootstrap is not None else best

    geometry = _circle_trace_geometry(plateau, best, cfg, pass_num)
    if isinstance(geometry, CirclePassOutcome):
        return geometry
    trace_center, trace_radius, legs = geometry

    circumference = 2 * math.pi * trace_radius
    segment_span = circumference / len(legs)
    logger.info(
        f"eddy_seek: circle_harmonic pass {pass_num} "
        f"arc_segments={len(legs)} speed={cfg.circle_speed:.4f} mm/s"
    )

    if mode.refresh_sweeps:
        strategy._refresh_profiles(ctx, pass_num, trace_center, trace_radius)

    lead_in = circle_lead_in_legs(legs, cfg.circle_lead_in)
    if lead_in:
        logger.info(
            f"eddy_seek: circle_harmonic pass {pass_num} "
            f"lead_in={len(lead_in)}/{len(legs)} segments"
        )

    collected = _collect_circle_harmonic(
        ctx,
        pass_num,
        best,
        trace_center,
        trace_radius,
        legs,
        lead_in or None,
        segment_span,
    )
    if isinstance(collected, CirclePassOutcome):
        return collected
    samples, binned, fit = collected
    return _harmonic_correction_outcome(
        strategy,
        ctx,
        pass_num,
        best,
        bootstrap,
        mode,
        trace_center,
        trace_radius,
        samples,
        binned,
        fit,
    )

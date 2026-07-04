"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

import math

import pytest

from _eddy_seek.common import Offset
from _eddy_seek.config import SeekConfig
from _eddy_seek.harmonic import (
    HarmonicFit,
    bin_samples_by_angle,
    binned_to_motion_samples,
    circle_in_jog_box,
    circle_legs,
    fit_first_harmonic,
    fit_second_harmonic_amplitude,
    harmonic_bootstrap_diverged,
    harmonic_bootstrap_divergence_limit,
    harmonic_converged,
    harmonic_fit_quality,
    harmonic_model_accepted,
    harmonic_reject_reasons,
    harmonic_step_v2,
    radial_slope,
)
from _eddy_seek.movement.handler import MotionSample


def _circle_samples(
    center: Offset,
    radius: float,
    field,
    *,
    segments: int = 36,
) -> list[MotionSample]:
    return [
        MotionSample(
            Offset(
                center.x + radius * math.cos(theta),
                center.y + radius * math.sin(theta),
            ),
            field(
                center.x + radius * math.cos(theta),
                center.y + radius * math.sin(theta),
            ),
            0.0,
        )
        for theta in [2.0 * math.pi * i / segments for i in range(segments)]
    ]


def test_circle_in_jog_box_clips_boundary():
    center = Offset(4.8, 0.0)
    trace_center, trace_radius = circle_in_jog_box(center, 0.5, 5.0, 5.0)
    assert trace_center.x < center.x
    assert trace_radius <= 0.5
    assert circle_legs(trace_center, trace_radius, 36)


def test_fit_first_harmonic_recovers_known_offset():
    radius = 1.0
    phase = 0.35
    amplitude = 3.0
    dc = 42.0
    samples = [
        MotionSample(
            Offset(radius * math.cos(theta), radius * math.sin(theta)),
            dc + amplitude * math.cos(theta - phase),
            0.0,
        )
        for theta in [2.0 * math.pi * i / 36.0 for i in range(36)]
    ]
    fit = fit_first_harmonic(samples, Offset.zero())
    assert fit is not None
    assert abs(fit.c0 - dc) < 0.05
    assert abs(fit.amplitude - amplitude) < 0.05
    assert abs(fit.a - amplitude * math.cos(phase)) < 0.05
    assert abs(fit.b - amplitude * math.sin(phase)) < 0.05


def test_bin_samples_by_angle_debiases_corners():
    radius = 1.0
    dc = 50.0
    amplitude = 2.0
    phase = 0.2
    uniform = [
        MotionSample(
            Offset(radius * math.cos(theta), radius * math.sin(theta)),
            dc + amplitude * math.cos(theta - phase),
            0.0,
        )
        for theta in [2.0 * math.pi * i / 36.0 for i in range(36)]
    ]
    heavy = [
        *uniform,
        MotionSample(Offset(1.0, 0.0), dc + amplitude, 0.0),
        MotionSample(Offset(1.0, 0.01), dc + amplitude, 0.0),
    ]
    binned = bin_samples_by_angle(heavy, Offset.zero(), 36)
    fit_raw = fit_first_harmonic(uniform, Offset.zero())
    fit_binned = fit_first_harmonic(
        binned_to_motion_samples(Offset.zero(), radius, binned), Offset.zero()
    )
    assert fit_raw is not None and fit_binned is not None
    assert abs(fit_binned.amplitude - fit_raw.amplitude) < 0.2


def test_harmonic_step_v2_uses_gain_when_f_prime_small():
    fit = HarmonicFit(c0=0.0, a=1.0, b=0.0, amplitude=1.0, noise=0.1, n=36)
    step = harmonic_step_v2(fit, 1e-6, step_gain=0.2, max_jog_x=5.0, max_jog_y=5.0)
    assert step.x == pytest.approx(-0.2, abs=0.01)
    assert step.y == pytest.approx(0.0, abs=0.01)


def test_harmonic_step_v2_scales_with_radial_slope():
    fit = fit_first_harmonic(
        _circle_samples(
            Offset.zero(),
            1.0,
            lambda x, y: 100.0 - 5.0 * (x * x + y * y),
        ),
        Offset(0.2, -0.1),
    )
    assert fit is not None
    step = harmonic_step_v2(fit, -10.0, step_gain=0.15, max_jog_x=5.0, max_jog_y=5.0)
    assert step.x == pytest.approx(fit.a / 10.0, abs=0.05)
    assert step.y == pytest.approx(fit.b / 10.0, abs=0.05)


def test_harmonic_nulling_recovers_offset_on_paraboloid():
    estimate = Offset(0.3, 0.2)
    radius = 1.0
    samples = _circle_samples(
        estimate, radius, lambda x, y: 100.0 - 5.0 * (x * x + y * y)
    )
    fit = fit_first_harmonic(samples, estimate)
    assert fit is not None
    step = harmonic_step_v2(fit, -10.0, step_gain=0.15, max_jog_x=5.0, max_jog_y=5.0)
    corrected = estimate + step
    assert corrected.distance_to(Offset.zero()) < estimate.distance_to(Offset.zero())
    assert corrected.x == pytest.approx(0.0, abs=0.08)
    assert corrected.y == pytest.approx(0.0, abs=0.08)


def test_composite_bore_rejected_by_model_gate():
    hole = Offset(0.10, 0.05)
    estimate = Offset(0.2, 0.12)

    def field(x: float, y: float) -> float:
        body = 100.0 - 3.0 * (x * x + y * y)
        dip = -40.0 * math.exp(-((x - hole.x) ** 2 + (y - hole.y) ** 2) / 0.0015)
        return body + dip

    samples = _circle_samples(estimate, 0.08, field)
    binned = bin_samples_by_angle(samples, estimate, 36)
    fit = fit_first_harmonic(binned_to_motion_samples(estimate, 0.08, binned), estimate)
    assert fit is not None
    assert not harmonic_model_accepted(fit, binned, noise_k=2.0, min_quality=0.5)


def test_composite_field_large_radius_accepted():
    hole = Offset(0.10, 0.05)
    estimate = Offset(0.2, 0.12)

    def field(x: float, y: float) -> float:
        body = 100.0 - 3.0 * (x * x + y * y)
        dip = -40.0 * math.exp(-((x - hole.x) ** 2 + (y - hole.y) ** 2) / 0.0015)
        return body + dip

    samples = _circle_samples(estimate, 1.0, field)
    binned = bin_samples_by_angle(samples, estimate, 36)
    fit = fit_first_harmonic(binned_to_motion_samples(estimate, 1.0, binned), estimate)
    assert fit is not None
    assert harmonic_fit_quality(fit, binned) > 0.3
    assert fit_second_harmonic_amplitude(binned) < fit.amplitude * 2.0


def test_harmonic_converged_noise_floor():
    fit = HarmonicFit(c0=0.0, a=0.0, b=0.0, amplitude=0.5, noise=0.5, n=36)
    assert harmonic_converged(fit, Offset(1.0, 1.0), 0.05, 2.0)


def test_harmonic_bootstrap_divergence_scales_with_offset_and_radius():
    bootstrap = Offset(-0.6655, -0.0026)
    limit = harmonic_bootstrap_divergence_limit(
        bootstrap, trace_radius=0.5, tolerance=0.05
    )
    assert limit == pytest.approx(bootstrap.distance_to(Offset.zero()))

    refined = Offset(-0.60, -0.0026)
    assert not harmonic_bootstrap_diverged(
        refined, bootstrap, trace_radius=0.5, tolerance=0.05
    )

    wild = Offset(0.5, 0.5)
    assert harmonic_bootstrap_diverged(
        wild, bootstrap, trace_radius=0.5, tolerance=0.05
    )


def test_harmonic_reject_reasons_lists_failures():
    fit = HarmonicFit(c0=0.0, a=1.0, b=0.0, amplitude=1.0, noise=1.0, n=36)
    binned = [
        (2.0 * math.pi * i / 36.0, 1.0 + math.cos(2.0 * math.pi * i / 36.0))
        for i in range(36)
    ]
    reasons = harmonic_reject_reasons(fit, binned, noise_k=2.0, min_quality=0.5)
    assert reasons
    assert any("snr" in reason for reason in reasons)


def test_circle_harmonic_search_retries_after_rejected_pass():
    from _eddy_seek.strategy.circle_harmonic import CircleHarmonicStrategy

    class _FakeReporter:
        def info(self, msg: str) -> None:
            pass

    class _RetrySession:
        config = SeekConfig(max_passes=4, tolerance=0.05)

    strategy = CircleHarmonicStrategy()
    calls: list[int] = []

    def fake_step(_ctx, pass_num, best):
        calls.append(pass_num)
        if pass_num == 1:
            return Offset(1.0, 0.0)
        if pass_num == 2:
            strategy._last_pass_rejected = True
            return Offset(1.0, 0.0)
        if pass_num == 3:
            return Offset(0.96, 0.0)
        return best

    strategy._step = fake_step  # type: ignore[method-assign]
    best, passes_run = strategy.search(_RetrySession(), _FakeReporter())  # type: ignore[arg-type]
    assert passes_run == 3
    assert calls == [1, 2, 3]
    assert best.x == pytest.approx(0.96)


def test_radial_slope_asymmetric_paraboloid():
    profile = [
        (x, 100.0 - 5.0 * x * x - 2.0 * x) for x in [i * 0.1 for i in range(-10, 11)]
    ]
    slope = radial_slope(profile, profile, 0.5)
    assert slope is not None
    assert slope < 0.0

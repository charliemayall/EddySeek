"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Circle-harmonic centering math: first-harmonic fit, angle binning, and guarded steps.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from logging import getLogger

from .common import Offset
from .movement.handler import MotionSample

logger = getLogger(__name__)

_MIN_RADIAL_SLOPE = 1e-3  # minimum radial slope to use Newton step
_TWO_PI = 2.0 * math.pi


@dataclass(frozen=True, slots=True)
class HarmonicFit:
    """Least-squares fit of ``freq ≈ c0 + a·cosθ + b·sinθ``."""

    c0: float
    a: float
    b: float
    amplitude: float
    noise: float
    n: int


def circle_arc_legs(
    center: Offset,
    radius: float,
    resolution: float,
) -> list[tuple[Offset, Offset]]:
    """One revolution as short chords; segment count from arc length / ``resolution``.

    Same rule as Klipper ``[gcode_arcs]``: ``floor(circumference / resolution)``,
    minimum 3 segments.
    """
    if radius <= 0.0 or resolution <= 0.0:
        return []
    segments = max(3, math.floor(_TWO_PI * radius / resolution))
    legs: list[tuple[Offset, Offset]] = []
    for index in range(segments):
        theta0 = _TWO_PI * index / segments
        theta1 = _TWO_PI * (index + 1) / segments
        start = Offset(
            center.x + radius * math.cos(theta0),
            center.y + radius * math.sin(theta0),
        )
        end = Offset(
            center.x + radius * math.cos(theta1),
            center.y + radius * math.sin(theta1),
        )
        legs.append((start, end))
    return legs


def circle_in_jog_box(
    center: Offset,
    radius: float,
    max_x: float,
    max_y: float,
) -> tuple[Offset, float]:
    """Inset centre and shrink radius so the circle stays inside ±max jog."""
    safe_center = center.clamp(max_x - radius, max_y - radius)
    margin_x = max_x - abs(safe_center.x)
    margin_y = max_y - abs(safe_center.y)
    safe_radius = min(radius, margin_x, margin_y)
    if radius > max(margin_x, margin_y):
        logger.warning(
            f"Circle radius {radius} is too large for jog box {max_x}x{max_y}"
        )
    return safe_center, max(safe_radius, 0.0)


def bin_samples_by_angle(
    samples: Sequence[MotionSample],
    center: Offset,
    bins: int,
) -> list[tuple[float, float]]:
    """Median frequency per equal-angle bin (centre = ``center``)."""
    if bins < 1 or not samples:
        return []
    bucket_freqs: list[list[float]] = [[] for _ in range(bins)]
    for sample in samples:
        dx = sample.offset.x - center.x
        dy = sample.offset.y - center.y
        theta = math.atan2(dy, dx)
        if theta < 0.0:
            theta += _TWO_PI
        index = min(int(theta / _TWO_PI * bins), bins - 1)
        bucket_freqs[index].append(sample.freq)
    result: list[tuple[float, float]] = []
    for index, freqs in enumerate(bucket_freqs):
        if freqs:
            theta = _TWO_PI * (index + 0.5) / bins
            result.append((theta, sum(freqs) / len(freqs)))
    return result


def binned_to_motion_samples(
    center: Offset,
    radius: float,
    binned: Sequence[tuple[float, float]],
) -> list[MotionSample]:
    return [
        MotionSample(
            Offset(
                center.x + radius * math.cos(theta),
                center.y + radius * math.sin(theta),
            ),
            freq,
            0.0,
        )
        for theta, freq in binned
    ]


def fit_first_harmonic(
    samples: Sequence[MotionSample],
    center: Offset,
) -> HarmonicFit | None:
    """Closed-form LSQ fit ``f(θ) ≈ c0 + a·cosθ + b·sinθ`` for circle samples."""
    if len(samples) < 3:
        return None
    if not all(
        math.isfinite(sample.freq)
        and math.isfinite(sample.offset.x)
        and math.isfinite(sample.offset.y)
        for sample in samples
    ):
        return None

    n = len(samples)
    s1 = float(n)
    sc = ss = scc = sss = scs = 0.0
    sy = scy = ssy = 0.0
    for sample in samples:
        dx = sample.offset.x - center.x
        dy = sample.offset.y - center.y
        theta = math.atan2(dy, dx)
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)
        y = sample.freq

        sc += cos_t
        ss += sin_t
        scc += cos_t * cos_t
        sss += sin_t * sin_t
        scs += cos_t * sin_t
        sy += y
        scy += cos_t * y
        ssy += sin_t * y

    beta = _solve_symmetric_3x3(
        (s1, sc, ss),
        (sc, scc, scs),
        (ss, scs, sss),
        (sy, scy, ssy),
    )
    if beta is None:
        return None
    c0, a, b = beta

    residual_sum = 0.0
    for sample in samples:
        dx = sample.offset.x - center.x
        dy = sample.offset.y - center.y
        theta = math.atan2(dy, dx)
        fitted = c0 + a * math.cos(theta) + b * math.sin(theta)
        residual_sum += (sample.freq - fitted) ** 2

    dof = n - 3
    noise = math.sqrt(residual_sum / dof) if dof > 0 else 0.0
    if not all(math.isfinite(v) for v in (c0, a, b, noise)):
        return None
    return HarmonicFit(
        c0=c0,
        a=a,
        b=b,
        amplitude=math.hypot(a, b),
        noise=noise,
        n=n,
    )


def harmonic_fit_quality(
    fit: HarmonicFit,
    binned: Sequence[tuple[float, float]],
) -> float:
    """Fraction of variance explained by the first-harmonic fit."""
    if len(binned) < 2:
        return 0.0
    freqs = [freq for _, freq in binned]
    mean = sum(freqs) / len(freqs)
    ss_tot = sum((freq - mean) ** 2 for freq in freqs)
    if ss_tot < 1e-12:
        return 0.0
    ss_res = 0.0
    for theta, freq in binned:
        fitted = fit.c0 + fit.a * math.cos(theta) + fit.b * math.sin(theta)
        ss_res += (freq - fitted) ** 2
    return max(0.0, 1.0 - ss_res / ss_tot)


def fit_second_harmonic_amplitude(
    binned: Sequence[tuple[float, float]],
) -> float:
    """Amplitude of the 2nd harmonic (``cos 2θ``, ``sin 2θ``) term."""
    if len(binned) < 4:
        return 0.0
    n = len(binned)
    s1 = float(n)
    sc2 = ss2 = scc2 = sss2 = scs2 = 0.0
    sy = sc2y = ss2y = 0.0
    for theta, freq in binned:
        cos2 = math.cos(2.0 * theta)
        sin2 = math.sin(2.0 * theta)
        sc2 += cos2
        ss2 += sin2
        scc2 += cos2 * cos2
        sss2 += sin2 * sin2
        scs2 += cos2 * sin2
        sy += freq
        sc2y += cos2 * freq
        ss2y += sin2 * freq

    beta = _solve_symmetric_3x3(
        (s1, sc2, ss2),
        (sc2, scc2, scs2),
        (ss2, scs2, sss2),
        (sy, sc2y, ss2y),
    )
    if beta is None:
        return 0.0
    _, a2, b2 = beta
    return math.hypot(a2, b2)


def harmonic_reject_reasons(
    fit: HarmonicFit,
    binned: Sequence[tuple[float, float]],
    *,
    noise_k: float,
    min_quality: float,
) -> list[str]:
    """Human-readable reasons ``harmonic_model_accepted`` would reject the fit."""
    reasons: list[str] = []
    snr_floor = noise_k * fit.noise
    if fit.amplitude < snr_floor:
        reasons.append(
            f"snr (amp={fit.amplitude:.2f} < {noise_k}×noise={snr_floor:.2f})"
        )
    quality = harmonic_fit_quality(fit, binned)
    if quality < min_quality:
        reasons.append(f"quality ({quality:.2f} < {min_quality})")
    h2 = fit_second_harmonic_amplitude(binned)
    if h2 >= fit.amplitude:
        reasons.append(f"h2 ({h2:.2f} >= amp={fit.amplitude:.2f})")
    return reasons


def harmonic_model_accepted(
    fit: HarmonicFit,
    binned: Sequence[tuple[float, float]],
    *,
    noise_k: float,
    min_quality: float,
) -> bool:
    return not harmonic_reject_reasons(
        fit, binned, noise_k=noise_k, min_quality=min_quality
    )


def radial_slope(
    x_profile: Sequence[tuple[float, float]],
    y_profile: Sequence[tuple[float, float]],
    radius: float,
    center: Offset = Offset(0.0, 0.0),
) -> float | None:
    """Estimate ``f'(r)`` from axis sweep profiles, radius measured from ``center``."""
    slopes = [
        slope
        for slope in (
            _axis_radial_slope(x_profile, radius, center.x),
            _axis_radial_slope(y_profile, radius, center.y),
        )
        if slope is not None
    ]
    if not slopes:
        return None
    return sum(slopes) / len(slopes)


def harmonic_step_v2(
    fit: HarmonicFit,
    f_prime: float | None,
    *,
    step_gain: float,
    radius: float,
    search_for: str,
    max_jog_x: float,
    max_jog_y: float,
) -> Offset:
    """Convert harmonic coefficients into a centre correction.

    With a radial slope ``f'`` the Newton step is ``-(a, b)/f'`` (sign of ``f'``
    carries the search direction).  Without it, step a fraction of the circle
    radius toward the extremum: for a ``max`` field ``f'<0`` so the correction
    points along ``+(a, b)``; for ``min`` along ``-(a, b)``.
    """
    if not math.isfinite(fit.a) or not math.isfinite(fit.b) or fit.amplitude <= 0.0:
        return Offset.zero()

    if (
        f_prime is not None
        and math.isfinite(f_prime)
        and abs(f_prime) >= _MIN_RADIAL_SLOPE
    ):
        step = Offset(-fit.a / f_prime, -fit.b / f_prime)
    else:
        sign = 1.0 if search_for == "max" else -1.0
        scale = sign * step_gain * radius / fit.amplitude
        step = Offset(fit.a * scale, fit.b * scale)
    # Trust region: the first-harmonic linearisation only holds within the
    # traced circle, and a far-off centre biases f' low (Newton overshoot).
    magnitude = math.hypot(step.x, step.y)
    if radius > 0.0 and magnitude > radius:
        step = Offset(step.x * radius / magnitude, step.y * radius / magnitude)
    return step.clamp(max_jog_x, max_jog_y)


def harmonic_bootstrap_divergence_limit(
    bootstrap: Offset,
    trace_radius: float,
    tolerance: float,
    *,
    anchor_floor: float = 0.0,
) -> float:
    """Max |result - bootstrap| before the harmonic correction is distrusted."""
    return max(
        tolerance,
        trace_radius,
        bootstrap.distance_to(Offset.zero()),
        anchor_floor,
    )


def harmonic_bootstrap_diverged(
    result: Offset,
    bootstrap: Offset,
    trace_radius: float,
    tolerance: float,
    *,
    anchor_floor: float = 0.0,
) -> bool:
    return result.distance_to(bootstrap) > harmonic_bootstrap_divergence_limit(
        bootstrap, trace_radius, tolerance, anchor_floor=anchor_floor
    )


def harmonic_converged(
    fit: HarmonicFit,
    step: Offset,
    tolerance: float,
    noise_k: float,
) -> bool:
    moved = step.abs_components()
    if moved.x < tolerance and moved.y < tolerance:
        return True
    if fit.noise <= 0.0:
        return fit.amplitude <= 0.0
    return fit.amplitude < noise_k * fit.noise


def circle_radius_for_pass(
    pass_num: int,
    *,
    radius_start: float,
    radius_min: float,
    radius_shrink: float,
) -> float:
    """Circle radius for harmonic pass ``pass_num`` (pass 1 = bootstrap, pass 2+ = circle)."""
    if pass_num < 2:
        return radius_start
    exponent = pass_num - 2
    return max(radius_start * (radius_shrink**exponent), radius_min)


def _axis_radial_slope(
    profile: Sequence[tuple[float, float]],
    radius: float,
    center_coord: float = 0.0,
) -> float | None:
    """``df/dr`` at distance ``radius`` from ``center_coord``, from one axis profile.

    Central difference at ``±radius`` on both branches; radial distance grows
    as the coordinate decreases on the negative branch, so its difference is
    mirrored.  A secant from the centre would halve the slope of a parabola
    and make every Newton step overshoot 2x.
    """
    if len(profile) < 2 or radius <= 0.0:
        return None
    coords = [float(coord) - center_coord for coord, _ in profile]
    freqs = [float(freq) for _, freq in profile]
    order = sorted(range(len(coords)), key=coords.__getitem__)
    coords = [coords[i] for i in order]
    freqs = [freqs[i] for i in order]
    h = min(radius / 2.0, coords[-1] - radius, -coords[0] - radius)
    if h <= 1e-9:
        return None
    f = lambda x: _interp_linear(coords, freqs, x)
    slope_pos = (f(radius + h) - f(radius - h)) / (2.0 * h)
    slope_neg = (f(-radius - h) - f(-radius + h)) / (2.0 * h)
    return (slope_pos + slope_neg) / 2.0


def _interp_linear(coords: Sequence[float], freqs: Sequence[float], x: float) -> float:
    if x <= coords[0]:
        return freqs[0]
    if x >= coords[-1]:
        return freqs[-1]
    lo = 0
    hi = len(coords) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if coords[mid] <= x:
            lo = mid
        else:
            hi = mid
    span = coords[hi] - coords[lo]
    if span <= 0.0:
        return freqs[lo]
    t = (x - coords[lo]) / span
    return freqs[lo] + t * (freqs[hi] - freqs[lo])


def _solve_symmetric_3x3(
    row0: tuple[float, float, float],
    row1: tuple[float, float, float],
    row2: tuple[float, float, float],
    rhs: tuple[float, float, float],
) -> tuple[float, float, float] | None:
    det = _det3(row0, row1, row2)
    if abs(det) < 1e-15:
        return None
    return (
        _det3(rhs, row1, row2) / det,
        _det3(row0, rhs, row2) / det,
        _det3(row0, row1, rhs) / det,
    )


def _det3(
    row0: tuple[float, float, float],
    row1: tuple[float, float, float],
    row2: tuple[float, float, float],
) -> float:
    return (
        row0[0] * (row1[1] * row2[2] - row1[2] * row2[1])
        - row0[1] * (row1[0] * row2[2] - row1[2] * row2[0])
        + row0[2] * (row1[0] * row2[1] - row1[1] * row2[0])
    )


def _self_check() -> None:
    radius = 1.0
    phase = 0.4
    amplitude = 2.5
    dc = 50.0
    samples = [
        MotionSample(
            Offset(radius * math.cos(theta), radius * math.sin(theta)),
            dc + amplitude * math.cos(theta - phase),
            0.0,
        )
        for theta in [_TWO_PI * i / 36.0 for i in range(36)]
    ]
    fit = fit_first_harmonic(samples, Offset.zero())
    assert fit is not None
    assert abs(fit.c0 - dc) < 0.05
    assert abs(fit.amplitude - amplitude) < 0.05

    heavy = [
        *samples,
        MotionSample(Offset(1.0, 0.0), dc + amplitude, 0.0),
        MotionSample(Offset(1.0, 0.01), dc + amplitude, 0.0),
        MotionSample(Offset(1.0, 0.02), dc + amplitude, 0.0),
    ]
    binned = bin_samples_by_angle(heavy, Offset.zero(), 36)
    uniform = binned_to_motion_samples(Offset.zero(), radius, binned)
    fit_b = fit_first_harmonic(uniform, Offset.zero())
    assert fit_b is not None
    assert abs(fit_b.amplitude - amplitude) < 0.15

    hole = Offset(0.10, 0.05)
    estimate = Offset(0.2, 0.12)

    def field(x: float, y: float) -> float:
        body = 100.0 - 3.0 * (x * x + y * y)
        dip = -40.0 * math.exp(-((x - hole.x) ** 2 + (y - hole.y) ** 2) / 0.0015)
        return body + dip

    small = [
        MotionSample(
            Offset(
                estimate.x + 0.08 * math.cos(theta),
                estimate.y + 0.08 * math.sin(theta),
            ),
            field(
                estimate.x + 0.08 * math.cos(theta),
                estimate.y + 0.08 * math.sin(theta),
            ),
            0.0,
        )
        for theta in [_TWO_PI * i / 36.0 for i in range(36)]
    ]
    binned_small = bin_samples_by_angle(small, estimate, 36)
    fit_small = fit_first_harmonic(
        binned_to_motion_samples(estimate, 0.08, binned_small), estimate
    )
    assert fit_small is not None
    assert not harmonic_model_accepted(
        fit_small, binned_small, noise_k=2.0, min_quality=0.5
    )

    # End-to-end nulling: peak field, offset estimate, slope from a sweep
    # profile centred on the estimate.  One step must land near the target.
    target = Offset(0.15, -0.25)
    guess = Offset(0.45, 0.10)
    peak = lambda x, y: (
        100_000.0 - 8_000.0 * ((x - target.x) ** 2 + (y - target.y) ** 2)
    )
    r = 0.5
    orbit = [
        MotionSample(
            Offset(guess.x + r * math.cos(t), guess.y + r * math.sin(t)),
            peak(guess.x + r * math.cos(t), guess.y + r * math.sin(t)),
            0.0,
        )
        for t in [_TWO_PI * i / 36.0 for i in range(36)]
    ]
    fit_orbit = fit_first_harmonic(orbit, guess)
    assert fit_orbit is not None
    x_prof = [(guess.x + d, peak(guess.x + d, guess.y)) for d in _grid(-1.0, 1.0, 41)]
    y_prof = [(guess.y + d, peak(guess.x, guess.y + d)) for d in _grid(-1.0, 1.0, 41)]
    slope = radial_slope(x_prof, y_prof, r, center=guess)
    assert slope is not None and slope < 0.0
    step = harmonic_step_v2(
        fit_orbit,
        slope,
        step_gain=0.15,
        radius=r,
        search_for="max",
        max_jog_x=5.0,
        max_jog_y=5.0,
    )
    assert (guess + step).distance_to(target) < 0.05
    # Slope-free fallback must still trend toward the target.
    fallback = harmonic_step_v2(
        fit_orbit,
        None,
        step_gain=0.15,
        radius=r,
        search_for="max",
        max_jog_x=5.0,
        max_jog_y=5.0,
    )
    assert (guess + fallback).distance_to(target) < guess.distance_to(target)


def _grid(lo: float, hi: float, n: int) -> list[float]:
    return [lo + (hi - lo) * i / (n - 1) for i in range(n)]


_self_check()

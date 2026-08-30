"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Circle-harmonic centering math: 1st+2nd harmonic fit and per-axis Newton step.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from .common import Offset
from .movement.handler import MotionSample

_MIN_RADIAL_SLOPE = 1e-3
_MIN_AMPLITUDE = 1e-6
_TWO_PI = 2.0 * math.pi


@dataclass(frozen=True, slots=True)
class HarmonicFit:
    """Least-squares fit of ``freq ≈ c0 + a·cosθ + b·sinθ + c2·cos2θ + d2·sin2θ``."""

    c0: float
    a: float
    b: float
    c2: float
    d2: float
    amplitude: float
    amplitude2: float
    noise: float
    n: int


def bin_samples_by_angle(
    samples: Sequence[MotionSample],
    center: Offset,
    bins: int,
) -> list[tuple[float, float]]:
    """Mean frequency per equal-angle bin (centre = ``center``)."""
    if bins < 1 or not samples:
        return []
    sums = [0.0] * bins
    counts = [0] * bins
    for sample in samples:
        dx = sample.offset.x - center.x
        dy = sample.offset.y - center.y
        theta = math.atan2(dy, dx)
        if theta < 0.0:
            theta += _TWO_PI
        index = min(int(theta / _TWO_PI * bins), bins - 1)
        sums[index] += sample.freq
        counts[index] += 1
    result: list[tuple[float, float]] = []
    for index in range(bins):
        if counts[index]:
            theta = _TWO_PI * (index + 0.5) / bins
            result.append((theta, sums[index] / counts[index]))
    return result


def fit_harmonics(
    samples: Sequence[MotionSample],
    center: Offset,
) -> HarmonicFit | None:
    """Closed-form LSQ fit of 1st and 2nd harmonics for circle samples."""
    if len(samples) < 5:
        return None
    if not all(
        math.isfinite(sample.freq)
        and math.isfinite(sample.offset.x)
        and math.isfinite(sample.offset.y)
        for sample in samples
    ):
        return None

    n = len(samples)
    ata = [[0.0] * 5 for _ in range(5)]
    aty = [0.0] * 5
    for sample in samples:
        dx = sample.offset.x - center.x
        dy = sample.offset.y - center.y
        theta = math.atan2(dy, dx)
        row = (
            1.0,
            math.cos(theta),
            math.sin(theta),
            math.cos(2.0 * theta),
            math.sin(2.0 * theta),
        )
        y = sample.freq
        for i in range(5):
            aty[i] += row[i] * y
            for j in range(5):
                ata[i][j] += row[i] * row[j]

    beta = _solve_symmetric(ata, aty)
    if beta is None:
        return None
    c0, a, b, c2, d2 = beta

    residual_sum = 0.0
    for sample in samples:
        dx = sample.offset.x - center.x
        dy = sample.offset.y - center.y
        theta = math.atan2(dy, dx)
        fitted = _eval_fit(c0, a, b, c2, d2, theta)
        residual_sum += (sample.freq - fitted) ** 2

    dof = n - 5
    noise = math.sqrt(residual_sum / dof) if dof > 0 else 0.0
    if not all(math.isfinite(v) for v in (c0, a, b, c2, d2, noise)):
        return None
    return HarmonicFit(
        c0=c0,
        a=a,
        b=b,
        c2=c2,
        d2=d2,
        amplitude=math.hypot(a, b),
        amplitude2=math.hypot(c2, d2),
        noise=noise,
        n=n,
    )


def harmonic_fit_quality(
    fit: HarmonicFit,
    samples: Sequence[MotionSample],
    center: Offset,
) -> float:
    """Fraction of variance explained by the 1st+2nd harmonic fit."""
    if len(samples) < 2:
        return 0.0
    freqs = [sample.freq for sample in samples]
    mean = sum(freqs) / len(freqs)
    ss_tot = sum((freq - mean) ** 2 for freq in freqs)
    if ss_tot < 1e-12:
        return 0.0
    ss_res = 0.0
    for sample in samples:
        dx = sample.offset.x - center.x
        dy = sample.offset.y - center.y
        theta = math.atan2(dy, dx)
        fitted = _eval_fit(fit.c0, fit.a, fit.b, fit.c2, fit.d2, theta)
        ss_res += (sample.freq - fitted) ** 2
    return max(0.0, 1.0 - ss_res / ss_tot)


def first_harmonic_quality(
    fit: HarmonicFit,
    samples: Sequence[MotionSample],
    center: Offset,
) -> float:
    """R² of the 1st-harmonic part only (for tests / plots)."""
    if len(samples) < 2:
        return 0.0
    freqs = [sample.freq for sample in samples]
    mean = sum(freqs) / len(freqs)
    ss_tot = sum((freq - mean) ** 2 for freq in freqs)
    if ss_tot < 1e-12:
        return 0.0
    ss_res = 0.0
    for sample in samples:
        dx = sample.offset.x - center.x
        dy = sample.offset.y - center.y
        theta = math.atan2(dy, dx)
        fitted = fit.c0 + fit.a * math.cos(theta) + fit.b * math.sin(theta)
        ss_res += (sample.freq - fitted) ** 2
    return max(0.0, 1.0 - ss_res / ss_tot)


def axis_radial_slope(
    profile: Sequence[tuple[float, float]],
    radius: float,
    center_coord: float = 0.0,
) -> float | None:
    """``df/dr`` at distance ``radius`` from ``center_coord``, from one axis profile."""
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
    slope_pos = (_interp_linear(coords, freqs, radius + h) - _interp_linear(coords, freqs, radius - h)) / (
        2.0 * h
    )
    slope_neg = (
        _interp_linear(coords, freqs, -radius - h)
        - _interp_linear(coords, freqs, -radius + h)
    ) / (2.0 * h)
    return (slope_pos + slope_neg) / 2.0


def harmonic_step(
    fit: HarmonicFit,
    f_prime_x: float | None,
    f_prime_y: float | None,
    *,
    step_gain: float,
    radius: float,
    search_for: str,
    max_jog_x: float,
    max_jog_y: float,
) -> Offset:
    """Convert 1st-harmonic coefficients into a centre correction.

    Newton step is ``(-a/f'_x, -b/f'_y)`` when that axis has a usable slope.
    Missing slopes fall back to a fraction of the circle radius. Do not average
    the two slopes - a rectangular coil is stretched.
    """
    if (
        not math.isfinite(fit.a)
        or not math.isfinite(fit.b)
        or fit.amplitude < _MIN_AMPLITUDE
    ):
        return Offset.zero()

    sign = 1.0 if search_for == "max" else -1.0
    fallback = sign * step_gain * radius / fit.amplitude
    dx = _axis_step(fit.a, f_prime_x, fallback)
    dy = _axis_step(fit.b, f_prime_y, fallback)
    step = Offset(dx, dy)
    magnitude = math.hypot(step.x, step.y)
    if radius > 0.0 and magnitude > radius:
        step = Offset(step.x * radius / magnitude, step.y * radius / magnitude)
    return step.clamp(max_jog_x, max_jog_y)


def _axis_step(coeff: float, f_prime: float | None, fallback: float) -> float:
    if (
        f_prime is not None
        and math.isfinite(f_prime)
        and abs(f_prime) >= _MIN_RADIAL_SLOPE
    ):
        return -coeff / f_prime
    return coeff * fallback


def _eval_fit(c0: float, a: float, b: float, c2: float, d2: float, theta: float) -> float:
    return (
        c0
        + a * math.cos(theta)
        + b * math.sin(theta)
        + c2 * math.cos(2.0 * theta)
        + d2 * math.sin(2.0 * theta)
    )


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


def _solve_symmetric(
    ata: list[list[float]],
    aty: list[float],
) -> tuple[float, ...] | None:
    n = len(aty)
    aug = [[*ata[i][:], aty[i]] for i in range(n)]
    for i in range(n):
        piv = max(range(i, n), key=lambda row: abs(aug[row][i]))
        aug[i], aug[piv] = aug[piv], aug[i]
        if abs(aug[i][i]) < 1e-15:
            return None
        scale = aug[i][i]
        for j in range(i, n + 1):
            aug[i][j] /= scale
        for row in range(n):
            if row == i:
                continue
            factor = aug[row][i]
            for j in range(i, n + 1):
                aug[row][j] -= factor * aug[i][j]
    result = tuple(aug[i][n] for i in range(n))
    if not all(math.isfinite(v) for v in result):
        return None
    return result

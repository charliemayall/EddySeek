"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from _eddy_seek.common import Offset
from _eddy_seek.harmonic import (
    axis_radial_slope,
    first_harmonic_quality,
    fit_harmonics,
    harmonic_fit_quality,
    harmonic_step,
)
from _eddy_seek.movement.handler import MotionSample

_DATA = Path(__file__).parent / "data"
_TWO_PI = 2.0 * math.pi


def _orbit(radius: float, n: int, freq_fn) -> list[MotionSample]:
    return [
        MotionSample(
            Offset(radius * math.cos(theta), radius * math.sin(theta)),
            freq_fn(theta),
            0.0,
        )
        for theta in [_TWO_PI * i / n for i in range(n)]
    ]


def _load_fixture(name: str) -> tuple[float, list[MotionSample], Offset]:
    payload = json.loads((_DATA / name).read_text())
    samples = [
        MotionSample(Offset(x, y), freq, 0.0)
        for x, y, freq in zip(payload["xs"], payload["ys"], payload["freqs"], strict=True)
    ]
    center = Offset(
        sum(payload["xs"]) / len(payload["xs"]),
        sum(payload["ys"]) / len(payload["ys"]),
    )
    return float(payload["radius"]), samples, center


def _amp4(samples: list[MotionSample], center: Offset) -> float:
    fit = fit_harmonics(samples, center)
    assert fit is not None
    ata = [[0.0] * 3 for _ in range(3)]
    aty = [0.0] * 3
    for sample in samples:
        theta = math.atan2(sample.offset.y - center.y, sample.offset.x - center.x)
        residual = sample.freq - (
            fit.c0
            + fit.a * math.cos(theta)
            + fit.b * math.sin(theta)
            + fit.c2 * math.cos(2.0 * theta)
            + fit.d2 * math.sin(2.0 * theta)
        )
        row = (1.0, math.cos(4.0 * theta), math.sin(4.0 * theta))
        for i in range(3):
            aty[i] += row[i] * residual
            for j in range(3):
                ata[i][j] += row[i] * row[j]
    # 3x3 gaussian
    aug = [[*ata[i][:], aty[i]] for i in range(3)]
    for i in range(3):
        piv = max(range(i, 3), key=lambda r: abs(aug[r][i]))
        aug[i], aug[piv] = aug[piv], aug[i]
        if abs(aug[i][i]) < 1e-12:
            return 0.0
        scale = aug[i][i]
        for j in range(i, 4):
            aug[i][j] /= scale
        for r in range(3):
            if r == i:
                continue
            fac = aug[r][i]
            for j in range(i, 4):
                aug[r][j] -= fac * aug[i][j]
    return math.hypot(aug[1][3], aug[2][3])


def test_centered_rectangle_has_no_first_harmonic():
    samples = _orbit(1.0, 72, lambda th: 50.0 + 8.0 * math.cos(2.0 * th))
    fit = fit_harmonics(samples, Offset.zero())
    assert fit is not None
    assert fit.amplitude < 0.05
    assert abs(fit.amplitude2 - 8.0) < 0.05
    assert harmonic_fit_quality(fit, samples, Offset.zero()) > 0.99
    step = harmonic_step(
        fit,
        None,
        None,
        step_gain=0.15,
        radius=1.0,
        search_for="max",
        max_jog_x=2.5,
        max_jog_y=2.5,
    )
    assert step.distance_to(Offset.zero()) < 1e-9


def test_offset_plus_2theta_recovers_offset_direction():
    # 1θ peak at θ=0 (offset toward +X) plus rectangle 2θ
    samples = _orbit(
        1.0,
        72,
        lambda th: 50.0 + 4.0 * math.cos(th) + 8.0 * math.cos(2.0 * th),
    )
    fit = fit_harmonics(samples, Offset.zero())
    assert fit is not None
    assert abs(fit.a - 4.0) < 0.05
    assert abs(fit.b) < 0.05
    assert abs(fit.c2 - 8.0) < 0.05
    step = harmonic_step(
        fit,
        -10.0,
        -10.0,
        step_gain=0.15,
        radius=1.0,
        search_for="max",
        max_jog_x=2.5,
        max_jog_y=2.5,
    )
    assert step.x > 0.0
    assert abs(step.y) < 0.05


def test_anisotropic_newton_uses_per_axis_slopes():
    samples = _orbit(1.0, 36, lambda th: 50.0 + 4.0 * math.cos(th) + 3.0 * math.sin(th))
    fit = fit_harmonics(samples, Offset.zero())
    assert fit is not None
    averaged = harmonic_step(
        fit,
        -10.0,
        -10.0,
        step_gain=0.15,
        radius=1.0,
        search_for="max",
        max_jog_x=5.0,
        max_jog_y=5.0,
    )
    split = harmonic_step(
        fit,
        -10.0,
        -5.0,
        step_gain=0.15,
        radius=1.0,
        search_for="max",
        max_jog_x=5.0,
        max_jog_y=5.0,
    )
    assert abs(split.x - averaged.x) < 1e-9
    assert abs(split.y - 2.0 * averaged.y) < 1e-6


def test_axis_radial_slope_parabola():
    # f = -(x^2) so df/dx = -2x; at r=1 on +x, slope wrt r is -2
    profile = [(x, -(x * x)) for x in [i * 0.1 for i in range(-20, 21)]]
    slope = axis_radial_slope(profile, 1.0, 0.0)
    assert slope is not None
    assert abs(slope - (-2.0)) < 0.05


def test_replay_r2_orbit_second_harmonic_dominates_fourth():
    _, samples, center = _load_fixture("circle_orbit_r2.json")
    fit = fit_harmonics(samples, center)
    assert fit is not None
    amp4 = _amp4(samples, center)
    assert fit.amplitude2 > 40.0
    assert amp4 < 5.0
    assert fit.amplitude2 > 8.0 * amp4
    q12 = harmonic_fit_quality(fit, samples, center)
    q1 = first_harmonic_quality(fit, samples, center)
    assert q12 > 0.95
    assert q12 > q1 + 0.15
    phase2 = math.degrees(math.atan2(fit.d2, fit.c2) / 2.0)
    assert abs(phase2) < 20.0


def test_replay_r150_orbit_has_2theta_along_x():
    _, samples, center = _load_fixture("circle_orbit_r150.json")
    fit = fit_harmonics(samples, center)
    assert fit is not None
    amp4 = _amp4(samples, center)
    assert fit.amplitude2 > amp4
    q12 = harmonic_fit_quality(fit, samples, center)
    q1 = first_harmonic_quality(fit, samples, center)
    assert q12 >= q1
    phase2 = math.degrees(math.atan2(fit.d2, fit.c2) / 2.0)
    assert abs(phase2) < 25.0

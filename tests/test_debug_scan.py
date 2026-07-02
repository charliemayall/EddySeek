"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.
"""

import pytest

from _eddy_seek.common import Axis, Position
from _eddy_seek.continuous_motion import MotionSample
from _eddy_seek.strategy.debug_scan import bin_frequencies, peak_bin_center
from _eddy_seek.strategy.sweep.grid import plan_grid_legs, y_lines


def test_y_lines_spaced_by_tolerance():
    assert y_lines(0.0, 0.2, 0.1) == pytest.approx([0.0, 0.1, 0.2])


def test_plan_grid_legs_row_count():
    box = (-5.0, 5.0, -5.0, 5.0)
    tolerance = 0.1
    rows = len(y_lines(box[2], box[3], tolerance))
    cols = len(y_lines(box[0], box[1], tolerance))
    x_legs = plan_grid_legs(box, tolerance, overscan=1.0, axis=Axis.X)
    y_legs = plan_grid_legs(box, tolerance, overscan=1.0, axis=Axis.Y)
    assert len(x_legs) == rows * 2
    assert len(y_legs) == cols * 2


def test_bin_frequencies_finds_peak():
    tolerance = 0.1
    box = (-0.5, 0.5, -0.5, 0.5)
    center = Position.zero()
    peak_x, peak_y = 0.05, -0.05
    samples = [
        MotionSample(Position(peak_x, peak_y), 100.0, 0.0),
        MotionSample(Position(peak_x + 0.01, peak_y), 100.0, 0.1),
        MotionSample(Position(-0.2, 0.2), 10.0, 0.2),
    ]
    z, x_centers, y_centers = bin_frequencies(samples, box, tolerance, center, "max")
    peak = peak_bin_center(z, x_centers, y_centers)
    assert peak is not None
    assert abs(peak.x - peak_x) <= tolerance
    assert abs(peak.y - peak_y) <= tolerance


def test_bin_frequencies_grid_centered_on_search_center():
    tolerance = 0.1
    box = (-0.5, 0.5, -0.5, 0.5)
    center = Position.zero()
    _, x_centers, y_centers = bin_frequencies([], box, tolerance, center, "max")
    assert any(abs(x) <= tolerance / 2 for x in x_centers)
    assert any(abs(y) <= tolerance / 2 for y in y_centers)


def test_peak_bin_center_min_picks_lowest():
    tolerance = 0.1
    box = (-0.5, 0.5, -0.5, 0.5)
    center = Position.zero()
    samples = [
        MotionSample(Position(0.05, -0.05), 100.0, 0.0),
        MotionSample(Position(-0.2, 0.2), 10.0, 0.1),
    ]
    z_max, x_centers, y_centers = bin_frequencies(
        samples, box, tolerance, center, "max"
    )
    z_min, _, _ = bin_frequencies(samples, box, tolerance, center, "min")
    low = peak_bin_center(z_min, x_centers, y_centers)
    high = peak_bin_center(z_max, x_centers, y_centers)
    assert low is not None
    assert high is not None
    assert low.x != high.x or low.y != high.y


def test_peak_bin_center_empty_returns_none():
    assert peak_bin_center([[]], [], []) is None

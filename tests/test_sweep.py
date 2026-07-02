"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.
"""

from unittest.mock import MagicMock, patch

from pytest import raises

from _eddy_seek.common import Axis, Position
from _eddy_seek.continuous_motion import (
    ContinuousMotionHandler,
    MotionSample,
    align_measurements,
    axis_profile,
)
from _eddy_seek.strategy.sweep.motion import iter_cross_offsets, traversal_endpoints
from _eddy_seek.strategy.sweep_centroid import _samples_in_box, _search_box


def test_iter_cross_offsets_three_passes():
    assert iter_cross_offsets(3, 0.3) == [0.0, 0.3, -0.3]


def test_traversal_endpoints_plus():
    start, end = traversal_endpoints(
        Axis.X, -2.0, 2.0, cross=0.5, overscan=1.0, reverse=False
    )
    assert start == Position(-3.0, 0.5)
    assert end == Position(3.0, 0.5)


def test_traversal_endpoints_minus():
    start, end = traversal_endpoints(
        Axis.Y, 0.0, 4.0, cross=-1.0, overscan=0.5, reverse=True
    )
    assert start == Position(-1.0, 4.5)
    assert end == Position(-1.0, -0.5)


def test_capture_leg_registers_sample_window():
    toolhead = MagicMock()
    toolhead.get_last_move_time.return_value = 1.0
    callbacks: list = []
    toolhead.register_lookahead_callback.side_effect = callbacks.append

    printer = MagicMock()
    printer.lookup_object.return_value = toolhead

    handler = ContinuousMotionHandler(printer, lambda _cb: None)
    handler.begin(Position(10.0, 20.0))
    handler.capture_leg(Position(0.0, 0.0), Position(1.0, 0.0), 40.0)

    assert toolhead.manual_move.call_args_list == [
        (([10.0, 20.0], 40.0),),
        (([11.0, 20.0], 40.0),),
    ]
    assert len(callbacks) == 1
    callbacks[0](2.0)
    assert handler._capture_windows == [(1.0, 2.0)]
    assert handler.position == Position(1.0, 0.0)


def test_align_measurements_uses_toolhead_lookup():
    toolhead = MagicMock()
    toolhead.get_kinematics.return_value.get_steppers.return_value = []
    toolhead.get_kinematics.return_value.calc_position.return_value = [10.5, 20.0]

    with patch(
        "_eddy_seek.continuous_motion.lookup_toolhead_position",
        return_value=Position(10.5, 20.0),
    ) as lookup:
        samples = align_measurements(toolhead, Position(10.0, 20.0), [(1.0, 100.0)])

    lookup.assert_called_once_with(toolhead, 1.0)
    assert samples == [MotionSample(Position(0.5, 0.0), 100.0, 1.0)]


def test_capture_leg_requires_active_session():
    handler = ContinuousMotionHandler(MagicMock(), lambda _cb: None)
    with raises(RuntimeError, match="not active"):
        handler.capture_leg(Position.zero(), Position(1.0, 0.0), 40.0)


def test_axis_profile_filters_sweep_range():
    samples = [
        MotionSample(Position(-3.0, 0.0), 1.0, 0.0),
        MotionSample(Position(-1.0, 0.0), 2.0, 0.1),
        MotionSample(Position(0.0, 0.0), 3.0, 0.2),
        MotionSample(Position(2.0, 0.0), 4.0, 0.3),
    ]
    points = axis_profile(samples, Axis.X, lo=-2.0, hi=2.0)
    assert points == [(-1.0, 2.0), (0.0, 3.0), (2.0, 4.0)]


def test_samples_in_box_filters_xy():
    box = _search_box(Position(0.0, 0.0), 1.0, 1.0, 5.0, 5.0)
    samples = [
        MotionSample(Position(-0.5, 0.0), 1.0, 0.0),
        MotionSample(Position(2.0, 0.0), 2.0, 0.1),
        MotionSample(Position(0.0, -0.5), 3.0, 0.2),
    ]
    in_box = _samples_in_box(samples, box)
    assert len(in_box) == 2
    assert in_box[0].offset == Position(-0.5, 0.0)
    assert in_box[1].offset == Position(0.0, -0.5)
